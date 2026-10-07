"""
个股资金流向（参考 daily_stock_analysis 个股分析中的资金流数据块）

- 主源：同花顺个股资金流排行（即时），全市场一次取回；净额 = 流入资金 - 流出资金
- 备用：东方财富资金流排行，主力净流入（超大单 + 大单）；每页最多 100 条需要翻页，
  接口有反爬限流，中途失败时保留已取到的部分
每个进程最多 10 分钟采集一次；收盘后（15:05 以后）再补采一次最终数据。
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.collectors.source_chain import fetch_with_fallback
from src.database.db import get_db_session
from src.database.models import StockFundFlow
from src.utils.stock_code import bare_code, code_candidates

THROTTLE_MINUTES = 10
FINAL_SNAPSHOT_HHMM = (15, 5)
EM_PAGE_SIZE = 100
EM_MAX_PAGES = 70
EM_FUND_FLOW_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EM_MARKETS = "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:81+s:2048"
_UNITS = {"亿": 1e8, "万": 1e4}
_AMOUNT = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(亿|万)?")

_lock = threading.Lock()
_last_success: datetime | None = None


def parse_cn_amount(value: Any) -> float | None:
    """把 "2.99亿" / "-2753.86万" / 12345 转为元，无法识别返回 None。"""
    if isinstance(value, (int, float)):
        return float(value)
    match = _AMOUNT.match(str(value or ""))
    if not match:
        return None
    return float(match.group(1)) * _UNITS.get(match.group(2) or "", 1.0)


def fetch_ths_fund_flow() -> list[dict[str, Any]]:
    import akshare as ak

    df = ak.stock_fund_flow_individual(symbol="即时")
    rows = []
    for r in df.to_dict("records"):
        code = str(r.get("股票代码") or "").strip().zfill(6)
        net, amount = parse_cn_amount(r.get("净额")), parse_cn_amount(r.get("成交额"))
        if not code.isdigit() or net is None:
            continue
        rows.append({
            "code": code, "name": str(r.get("股票简称") or ""), "net_inflow": net, "amount": amount,
            "net_ratio": round(net / amount * 100, 2) if amount else None, "source": "同花顺",
        })
    return rows


def fetch_em_fund_flow() -> list[dict[str, Any]]:
    from src.collectors.em_client import get_em_client

    em = get_em_client()
    rows: list[dict[str, Any]] = []
    for page in range(1, EM_MAX_PAGES + 1):
        data = em.request_json(EM_FUND_FLOW_URL, params={
            "fid": "f62", "po": 1, "np": 1, "fltt": 2, "invt": 2, "pz": EM_PAGE_SIZE, "pn": page,
            "fs": EM_MARKETS, "fields": "f12,f14,f6,f62,f184",
        })
        diff = ((data or {}).get("data") or {}).get("diff") or []
        for d in diff:
            if isinstance(d.get("f62"), (int, float)):
                rows.append({
                    "code": str(d.get("f12")), "name": str(d.get("f14") or ""), "net_inflow": float(d["f62"]),
                    "amount": d.get("f6") if isinstance(d.get("f6"), (int, float)) else None,
                    "net_ratio": d.get("f184") if isinstance(d.get("f184"), (int, float)) else None, "source": "东方财富",
                })
        total = ((data or {}).get("data") or {}).get("total") or 0
        if not diff or page * EM_PAGE_SIZE >= total:
            break
    return rows


def _due(now: datetime) -> bool:
    if _last_success is None:
        return True
    if now - _last_success >= timedelta(minutes=THROTTLE_MINUTES):
        return True
    final = now.replace(hour=FINAL_SNAPSHOT_HHMM[0], minute=FINAL_SNAPSHOT_HHMM[1], second=0, microsecond=0)
    return now >= final > _last_success


def collect_fund_flow(trade_date: str, db_path: str, force: bool = False) -> int:
    """采集全市场资金流并写入 trade_date 当天的记录（覆盖旧值），返回写入条数；节流期间返回 0。"""
    global _last_success
    with _lock:
        now = datetime.now()
        if not force and not _due(now):
            return 0
        fetched = fetch_with_fallback("个股资金流", [("同花顺", fetch_ths_fund_flow), ("东方财富", fetch_em_fund_flow)])
        if not fetched.ok:
            raise RuntimeError("个股资金流采集失败：" + str(fetched.errors))
        rows = {r["code"]: r for r in fetched.data}.values()
        with get_db_session(db_path) as session:
            session.query(StockFundFlow).filter(StockFundFlow.trade_date == trade_date).delete()
            session.bulk_save_objects([StockFundFlow(trade_date=trade_date, updated_at=now, **r) for r in rows])
        _last_success = now
        logger.info(f"个股资金流 {trade_date}: {len(rows)} 只（{fetched.source}）")
        return len(rows)


def latest_fund_flow(session, code: str, trade_date: str | None = None) -> StockFundFlow | None:
    """某只股票最近一个交易日（不晚于 trade_date）的资金流记录。"""
    query = session.query(StockFundFlow).filter(StockFundFlow.code.in_(code_candidates(bare_code(code))))
    if trade_date:
        query = query.filter(StockFundFlow.trade_date <= trade_date)
    return query.order_by(StockFundFlow.trade_date.desc()).first()


def supplement_flow(code: str, config: dict) -> StockFundFlow | None:
    """单股票补充；绝不替代全市场采集结果，保持提供方交易日，不伪造今日。"""
    if not (config.get("data_sources") or {}).get("miaoxiang_api_key"):
        return None
    from src.collectors.miaoxiang import MiaoxiangClient
    result = fetch_with_fallback("个股资金流补充", [("妙想", lambda: MiaoxiangClient(config).query(code, "flow"))], cache_key=bare_code(code), timeout_seconds=10)
    if not result.ok:
        return None
    data = result.data
    return StockFundFlow(code=bare_code(code), trade_date=data["trade_date"], net_inflow=data["net_inflow"], source="妙想", updated_at=datetime.fromisoformat(data["fetched_at"]))


def describe(flow: StockFundFlow | None) -> str:
    """一句话描述，如 "净流入2.75亿（占成交额8.2%，同花顺）"。"""
    if flow is None or flow.net_inflow is None:
        return ""
    direction = "净流入" if flow.net_inflow >= 0 else "净流出"
    ratio = f"占成交额{abs(flow.net_ratio):.1f}%，" if flow.net_ratio is not None else ""
    return f"{direction}{abs(flow.net_inflow) / 1e8:.2f}亿（{ratio}{flow.source}，{flow.trade_date}）"
