"""
策略选股历史回测（参考 daily_stock_analysis 的回测与 AlphaEvo 的策略验证）

对区间内每个交易日按当时能看到的数据重新选股（日线、涨停题材、主线、大盘环境都只用当天及以前的数据，
不读进程内的实时行情缓存，策略权重也不生效），再统计入选股票之后的表现：
- 入场：次日开盘价（开盘价缺失时用选股日收盘价）
- 1/3/5 日收益：入场后第 1/3/5 个交易日的收盘价相对入场价
- 次日涨停率、适配大盘环境时的次日平均收益
- 每日等权组合：每天买入当天该策略的全部入选股票、次日收盘卖出，得到累计收益和最大回撤
结果保存到 strategy_backtest 表。策略权重：次日收益样本不少于 30 个的策略，按其次日平均收益与全部策略的差值
温和调整（每差 1 个百分点调 0.05，限制在 0.8~1.2），选股排序时乘到策略得分上。
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable

from loguru import logger
from sqlalchemy import func

from src import trading_calendar
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import StockDaily, StrategyBacktest
from src.strategy.screener import LIMIT_TOLERANCE, STRATEGIES, StrategyScreener
from src.utils.stock_code import bare_code, code_candidates, daily_limit_pct

HORIZONS = (1, 3, 5)
MIN_UNIVERSE = 1000        # 当天行情少于该数量的日期不回测（不是全市场）
FORWARD_CALENDAR_DAYS = 15  # 取入场后 5 个交易日所需的自然日跨度
MIN_WEIGHT_SAMPLES = 30
WEIGHT_PER_PCT = 0.05
WEIGHT_MIN, WEIGHT_MAX = 0.8, 1.2
CHUNK = 500


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _win_rate(values: list[float]) -> float | None:
    return round(sum(1 for v in values if v > 0) / len(values) * 100, 1) if values else None


def max_drawdown(daily_returns: list[float]) -> float:
    """按日收益（%）复利的净值曲线的最大回撤（%，负数）。"""
    nav = peak = 1.0
    worst = 0.0
    for r in daily_returns:
        nav *= 1 + r / 100
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 2)


def strategy_weights(stats: list[dict[str, Any]]) -> dict[str, float]:
    """样本足够的策略按次日平均收益相对全体的差值调整权重；样本不足的不调整。"""
    total = sum(s["evaluated"] for s in stats)
    if not total:
        return {}
    overall = sum((s["avg_1d"] or 0) * s["evaluated"] for s in stats) / total
    weights = {}
    for s in stats:
        if s["evaluated"] >= MIN_WEIGHT_SAMPLES and s["avg_1d"] is not None:
            weight = 1 + (s["avg_1d"] - overall) * WEIGHT_PER_PCT
            weights[s["strategy"]] = round(min(WEIGHT_MAX, max(WEIGHT_MIN, weight)), 2)
    return weights


class StrategyBacktester:
    def __init__(self, config: dict | None = None, min_universe: int = MIN_UNIVERSE):
        self.config = config or load_config()
        self.min_universe = min_universe
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        # 回测要保留每个策略自己的前 N 只，不做合并后的总数截断
        screening = {**(self.config.get("screening") or {}), "max_total": 10_000}
        self.screener = StrategyScreener({**self.config, "screening": screening})

    def trade_dates(self, start: str, end: str) -> tuple[list[str], int]:
        """区间内行情接近全市场的交易日，以及因行情不全跳过的天数。"""
        trading_calendar.load(self.db_path, refresh=False)
        with get_db_session(self.db_path) as session:
            counts = (
                session.query(StockDaily.trade_date, func.count(StockDaily.id))
                .filter(StockDaily.trade_date >= start, StockDaily.trade_date <= end)
                .group_by(StockDaily.trade_date).all()
            )
        days = sorted(d for d, _ in counts if trading_calendar.is_trade_day(d))
        full = [d for d, n in sorted(counts) if d in days and n >= self.min_universe]
        return full, len(days) - len(full)

    def run(self, days: int = 60, end: str | None = None, save: bool = True,
            progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
        """回测最近 days 个自然日（到 end 为止）。progress(已完成, 总数) 用于界面显示进度。"""
        started = time.monotonic()
        end = end or datetime.now().strftime("%Y-%m-%d")
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        dates, skipped = self.trade_dates(start, end)
        labels = {s.name: s for s in STRATEGIES}
        samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for i, trade_date in enumerate(dates, 1):
            try:
                result = self.screener.run(trade_date, save=False, point_in_time=True)
                forward = self._forward_bars(trade_date, {p.code for p in result.picks})
                for p in result.picks:
                    outcome = self._outcome(p.code, p.name, p.close, forward.get(p.code) or [])
                    for strategy in p.strategies:
                        samples[strategy].append({"date": trade_date, "fits": p.fits_regime, **outcome})
            except Exception as e:
                logger.warning(f"策略回测 {trade_date} 失败: {e}")
            if progress:
                progress(i, len(dates))

        stats = [self._summarize(name, labels[name], samples.get(name, [])) for name in labels]
        weights = strategy_weights(stats)
        for s in stats:
            s["weight"] = weights.get(s["strategy"], 1.0)
        report = {
            "start": dates[0] if dates else start, "end": dates[-1] if dates else end, "dates": len(dates),
            "skipped_dates": skipped, "elapsed": round(time.monotonic() - started, 1),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "strategies": stats, "weights": weights,
        }
        if not dates:
            report["note"] = "区间内没有全市场行情，先运行 python scripts/fetch_history.py --mode daily --start-date <日期> 回补日线"
        if save and dates:
            with get_db_session(self.db_path) as session:
                session.add(StrategyBacktest(start_date=report["start"], end_date=report["end"],
                                             result_json=json.dumps(report, ensure_ascii=False)))
        logger.info(f"策略回测完成：{report['start']}~{report['end']} 共 {len(dates)} 天，耗时 {report['elapsed']}s")
        return report

    def latest(self) -> dict[str, Any] | None:
        with get_db_session(self.db_path) as session:
            row = session.query(StrategyBacktest.result_json).order_by(StrategyBacktest.created_at.desc()).first()
        return json.loads(row[0]) if row else None

    # ---- 数据 ----

    def _forward_bars(self, trade_date: str, codes: set[str]) -> dict[str, list]:
        """选股日之后 5 个交易日的日线。"""
        end = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=FORWARD_CALENDAR_DAYS)).strftime("%Y-%m-%d")
        variants = [v for c in codes for v in code_candidates(c)]
        by_code: dict[str, dict[str, Any]] = defaultdict(dict)
        with get_db_session(self.db_path) as session:
            for i in range(0, len(variants), CHUNK):
                rows = (
                    session.query(StockDaily.code, StockDaily.trade_date, StockDaily.open, StockDaily.high,
                                  StockDaily.close, StockDaily.change_pct)
                    .filter(StockDaily.code.in_(variants[i:i + CHUNK]), StockDaily.trade_date > trade_date,
                            StockDaily.trade_date <= end, StockDaily.close > 0)
                    .all()
                )
                for r in rows:
                    by_code[bare_code(r.code)][r.trade_date] = r
        return {code: [days[d] for d in sorted(trading_calendar.trade_days_only(days))[:max(HORIZONS)]]
                for code, days in by_code.items()}

    @staticmethod
    def _outcome(code: str, name: str, close: float, bars: list) -> dict[str, Any]:
        if not bars:
            return {}
        entry = bars[0].open or close
        outcome: dict[str, Any] = {
            "limit_up": (bars[0].change_pct or 0) >= daily_limit_pct(code, name) * 100 - LIMIT_TOLERANCE,
        }
        for n in HORIZONS:
            if len(bars) >= n and entry:
                outcome[f"r{n}"] = (bars[n - 1].close / entry - 1) * 100
        return outcome

    @staticmethod
    def _summarize(name: str, strategy, rows: list[dict[str, Any]]) -> dict[str, Any]:
        evaluated = [r for r in rows if "r1" in r]
        stats: dict[str, Any] = {
            "strategy": name, "label": strategy.label, "regimes": "/".join(strategy.regimes),
            "picks": len(rows), "days": len({r["date"] for r in rows}), "evaluated": len(evaluated),
        }
        for n in HORIZONS:
            values = [r[f"r{n}"] for r in rows if f"r{n}" in r]
            stats[f"avg_{n}d"], stats[f"win_{n}d"] = _mean(values), _win_rate(values)
        stats["limit_up_rate"] = round(sum(1 for r in evaluated if r["limit_up"]) / len(evaluated) * 100, 1) if evaluated else None
        stats["avg_1d_fit"] = _mean([r["r1"] for r in evaluated if r["fits"]])
        daily: dict[str, list[float]] = defaultdict(list)
        for r in evaluated:
            daily[r["date"]].append(r["r1"])
        curve = [sum(v) / len(v) for _, v in sorted(daily.items())]
        nav = 1.0
        for r in curve:
            nav *= 1 + r / 100
        stats["total_return"] = round((nav - 1) * 100, 2) if curve else None
        stats["max_drawdown"] = max_drawdown(curve) if curve else None
        return stats
