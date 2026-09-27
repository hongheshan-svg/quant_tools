"""
涨停原因（概念题材）采集
数据来源：同花顺涨停池（data.10jqka.com.cn），每只涨停股的 reason_type 为题材标签，用 + 连接，
如 "海峡两岸+工程机械+盾构机"。东方财富涨停池只有所属行业，识别跨行业的题材主线要靠它。
"""

from __future__ import annotations

import httpx
from loguru import logger

THS_LIMIT_UP_URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
THS_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"
PAGE_SIZE = 200
MAX_PAGES = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html",
}


def fetch_ths_limit_up_reasons(trade_date: str, timeout: float = 15) -> dict[str, str]:
    """同花顺涨停池的涨停原因 {6位代码: "题材1+题材2"}；trade_date 为 YYYY-MM-DD 或 YYYYMMDD。
    请求失败时抛出异常；当天没有涨停（或非交易日）返回空字典。"""
    day = trade_date.replace("-", "")
    reasons: dict[str, str] = {}
    for page in range(1, MAX_PAGES + 1):
        params = {"page": page, "limit": PAGE_SIZE, "field": THS_FIELDS, "filter": "HS,GEM2STAR",
                  "order_field": "330324", "order_type": 0, "date": day}
        resp = httpx.get(THS_LIMIT_UP_URL, params=params, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status_code") not in (0, None):
            raise RuntimeError(f"同花顺涨停池返回错误: {payload.get('status_msg')}")
        data = payload.get("data") or {}
        if str(data.get("date", day)) != day:
            raise RuntimeError(f"同花顺涨停池返回了 {data.get('date')} 的数据，请求的是 {day}")
        items = data.get("info") or []
        for item in items:
            code = str(item.get("code") or "").strip()
            reason = str(item.get("reason_type") or "").strip()
            if code and reason:
                reasons[code] = reason
        total = int((data.get("page") or {}).get("total") or 0)
        if not items or page * PAGE_SIZE >= total:
            break
    logger.info(f"同花顺涨停原因 {trade_date}: {len(reasons)} 只")
    return reasons


def split_concepts(text: str | None) -> list[str]:
    """把 "海峡两岸+工程机械" 拆成题材列表（去空白、去重，保持顺序）。"""
    parts = [p.strip() for p in (text or "").replace("＋", "+").split("+")]
    return list(dict.fromkeys(p for p in parts if p))
