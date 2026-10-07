"""
筹码分布与业绩数据（参考 daily_stock_analysis 个股分析中的 chip / fundamentals 数据块）

- 筹码分布：优先 AKShare stock_cyq_em（东方财富，接口有限流），失败时用数据库日线在本地按同样思路估算：
  每天按换手率把旧筹码衰减，再把当日成交按三角分布摊到当日最低~最高价之间
- 业绩：东方财富业绩预告（全市场）与业绩快报，按报告期整表下载，同一进程每天只下载一次
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

from loguru import logger

from src.collectors.source_chain import fetch_with_fallback
from src.database.db import get_db_session
from src.database.models import StockDaily
from src.utils.stock_code import bare_code, code_candidates

CHIP_MIN_BARS = 60
CHIP_BUCKETS = 150
EARNINGS_RISK_TYPES = {"预减", "首亏", "续亏", "略减", "增亏"}


# ---------- 筹码分布 ----------

def _chip_from_akshare(code: str) -> dict[str, Any] | None:
    import akshare as ak

    df = ak.stock_cyq_em(symbol=bare_code(code), adjust="")
    if df is None or df.empty:
        return None
    r = df.iloc[-1]
    return {
        "date": str(r["日期"])[:10],
        "profit_ratio": round(float(r["获利比例"]) * 100, 1),
        "avg_cost": round(float(r["平均成本"]), 2),
        "cost_90_low": round(float(r["90成本-低"]), 2),
        "cost_90_high": round(float(r["90成本-高"]), 2),
        "concentration_90": round(float(r["90集中度"]) * 100, 1),
        "source": "东方财富",
    }


def compute_chip_distribution(bars: list[tuple[str, float, float, float, float, float]]) -> dict[str, Any] | None:
    """bars 为按日期正序的 (日期, 开, 高, 低, 收, 换手率%)，至少 60 根且需有换手率。"""
    bars = [b for b in bars if all(v is not None for v in b[1:]) and b[2] >= b[3] > 0]
    if len(bars) < CHIP_MIN_BARS:
        return None
    lo, hi = min(b[3] for b in bars), max(b[2] for b in bars)
    step = (hi - lo) / CHIP_BUCKETS or 1.0
    prices = [lo + i * step for i in range(CHIP_BUCKETS + 1)]
    chips = [0.0] * len(prices)
    for _, o, h, low, c, turnover in bars:
        t = min(max(turnover / 100, 0.0), 1.0)
        chips = [x * (1 - t) for x in chips]
        avg = (o + h + low + c) / 4
        start, end = int((low - lo) / step), min(int((h - lo) / step), CHIP_BUCKETS)
        weights = []
        for i in range(start, end + 1):
            p = prices[i]
            if h == low:
                w = 1.0
            elif p <= avg:
                w = (p - low) / (avg - low) if avg > low else 1.0
            else:
                w = (h - p) / (h - avg) if h > avg else 1.0
            weights.append(max(w, 0.0))
        total_w = sum(weights) or 1.0
        for offset, w in enumerate(weights):
            chips[start + offset] += t * w / total_w
    total = sum(chips)
    if total <= 0:
        return None
    last_close = bars[-1][4]
    cumulative, low90, high90 = 0.0, None, None
    for p, x in zip(prices, chips):
        cumulative += x / total
        if low90 is None and cumulative >= 0.05:
            low90 = p
        if high90 is None and cumulative >= 0.95:
            high90 = p
    low90, high90 = low90 or lo, high90 or hi
    return {
        "date": bars[-1][0],
        "profit_ratio": round(sum(x for p, x in zip(prices, chips) if p <= last_close) / total * 100, 1),
        "avg_cost": round(sum(p * x for p, x in zip(prices, chips)) / total, 2),
        "cost_90_low": round(low90, 2),
        "cost_90_high": round(high90, 2),
        "concentration_90": round((high90 - low90) / (high90 + low90) * 100, 1) if high90 + low90 else None,
        "source": "本地估算",
    }


def _chip_from_local(code: str, db_path: str) -> dict[str, Any] | None:
    with get_db_session(db_path) as session:
        rows = (
            session.query(StockDaily.trade_date, StockDaily.open, StockDaily.high, StockDaily.low, StockDaily.close, StockDaily.turnover)
            .filter(StockDaily.code.in_(code_candidates(code)))
            .order_by(StockDaily.trade_date.desc()).limit(120).all()
        )
    return compute_chip_distribution(list(reversed([tuple(r) for r in rows])))


def fetch_chip_summary(code: str, db_path: str, config: dict | None = None) -> dict[str, Any] | None:
    """最新筹码分布：获利比例 %、平均成本、90% 筹码成本区间与集中度 %。"""
    sources = [("东方财富", lambda: _chip_from_akshare(code))]
    if (config or {}).get("data_sources", {}).get("miaoxiang_api_key"):
        from src.collectors.miaoxiang import MiaoxiangClient
        sources.append(("妙想", lambda: MiaoxiangClient(config).query(code, "chips")))
    sources.append(("本地估算", lambda: _chip_from_local(code, db_path)))
    result = fetch_with_fallback(
        "筹码分布",
        sources,
        cache_key=bare_code(code),
    )
    return result.data


def describe_chips(chip: dict[str, Any] | None) -> str:
    if not chip:
        return ""
    return (
        (f"获利盘{chip['profit_ratio']:.0f}%" if chip.get('profit_ratio') is not None else "获利比例未知")
        + f"，平均成本{chip['avg_cost']}，90%筹码在{chip.get('cost_90_low') or '未知'}~{chip.get('cost_90_high') or '未知'}"
        + (f"（集中度{chip['concentration_90']:.1f}%）" if chip.get("concentration_90") is not None else "")
        + f"，{chip['source']}"
    )


# ---------- 业绩 ----------

def recent_report_periods(today: date | None = None, count: int = 3) -> list[str]:
    """最近的几个报告期末（含当前季度），如 2026-09-28 → 20260930、20260630、20260331。"""
    today = today or date.today()
    year, quarter_end_month = today.year, ((today.month - 1) // 3 + 1) * 3
    periods = []
    for _ in range(count):
        day = 31 if quarter_end_month in (3, 12) else 30
        periods.append(f"{year}{quarter_end_month:02d}{day:02d}")
        quarter_end_month -= 3
        if quarter_end_month <= 0:
            quarter_end_month += 12
            year -= 1
    return periods


class EarningsCache:
    """全市场业绩预告/快报缓存（进程级，每天刷新一次）。"""

    _lock = threading.Lock()
    _loaded_on: date | None = None
    _data: dict[str, dict[str, Any]] = {}

    @classmethod
    def get(cls, code: str) -> dict[str, Any] | None:
        with cls._lock:
            if cls._loaded_on != date.today():
                cls._data = cls._load()
                cls._loaded_on = date.today()
            return cls._data.get(bare_code(code))

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._data, cls._loaded_on = {}, None

    @staticmethod
    def _load() -> dict[str, dict[str, Any]]:
        import akshare as ak

        latest: dict[str, dict[str, Any]] = {}

        def keep(code: str, record: dict[str, Any]) -> None:
            if code not in latest or record["notice_date"] > latest[code]["notice_date"]:
                latest[code] = record

        for period in recent_report_periods():
            try:
                df = ak.stock_yjyg_em(date=period)
            except Exception as e:
                logger.debug(f"业绩预告 {period} 获取失败: {e}")
                df = None
            for r in (df.to_dict("records") if df is not None else []):
                keep(str(r.get("股票代码", "")).zfill(6), {
                    "type": "业绩预告", "period": period, "change_type": str(r.get("预告类型") or ""),
                    "summary": str(r.get("业绩变动") or "")[:80], "change_pct": r.get("业绩变动幅度"),
                    "notice_date": str(r.get("公告日期") or ""),
                })
            try:
                kb = ak.stock_yjkb_em(date=period)
            except Exception as e:
                logger.debug(f"业绩快报 {period} 获取失败: {e}")
                kb = None
            for r in (kb.to_dict("records") if kb is not None else []):
                growth = r.get("净利润-同比增长")
                keep(str(r.get("股票代码", "")).zfill(6), {
                    "type": "业绩快报", "period": period,
                    "change_type": "" if growth is None else ("增长" if growth >= 0 else "下降"),
                    "summary": f"营收同比{r.get('营业收入-同比增长') or 0:+.1f}%，净利润同比{growth or 0:+.1f}%，ROE {r.get('净资产收益率') or '-'}%",
                    "change_pct": growth, "notice_date": str(r.get("公告日期") or ""),
                })
        logger.info(f"业绩预告/快报缓存: {len(latest)} 只")
        return latest


def describe_earnings(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    return f"{record['type']}（{record['period'][:4]}-{record['period'][4:6]}，{record['notice_date']}）{record['change_type']}：{record['summary']}"


def earnings_risk(record: dict[str, Any] | None) -> str:
    """业绩预减、首亏、续亏等返回风险描述。"""
    if record and record.get("change_type") in EARNINGS_RISK_TYPES:
        return f"最新{record['type']}为「{record['change_type']}」"
    return ""
