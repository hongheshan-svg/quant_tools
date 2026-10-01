"""
股东数据（东方财富 F10，普通 HTTP）：十大股东、十大流通股东、股东户数、机构持仓

- 只在个股诊断和问股时按需获取；成功结果按代码进程内缓存 12 小时，失败缓存 30 分钟
- 接口失败、非 A 股代码、ETF/指数返回 None，异常不外抛，每次调用记入数据源健康状态
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from src.collectors.source_chain import source_health
from src.utils.stock_code import bare_code, diagnosis_code, exchange_of

URL = "https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
TIMEOUT_SECONDS = 10
DATASET = "股东数据"
SOURCE = "东方财富"
OK_CACHE_HOURS = 12
FAIL_CACHE_MINUTES = 30
TREND_PERIODS = 4
CHANGE_ALERT_PCT = 10.0
ETF_PREFIXES = ("51", "56", "58", "15")
INSTITUTION_KEYWORDS = ("基金", "证券", "保险", "社保", "QFII", "资管", "信托")

_cache: dict[str, tuple[datetime, dict[str, Any] | None]] = {}
_cache_lock = threading.Lock()


def reset_cache() -> None:
    """清空进程级缓存（测试用）。"""
    with _cache_lock:
        _cache.clear()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> str | None:
    text = str(value or "")[:10]
    return text if re.match(r"^\d{4}-\d{2}-\d{2}$", text) else None


def _rows(payload: dict, key: str) -> list[dict]:
    rows = payload.get(key)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _ratio(value: Any) -> float | None:
    n = _num(value)
    return round(n, 2) if n is not None else None


def _change(value: Any) -> str:
    return "" if value is None else str(value)


def _parse(payload: dict) -> dict[str, Any] | None:
    gdrs = sorted(_rows(payload, "gdrs"), key=lambda r: str(r.get("END_DATE") or ""), reverse=True)
    sdgd = _rows(payload, "sdgd")
    sdltgd = _rows(payload, "sdltgd")
    jgcc = _rows(payload, "jgcc")

    top10 = [{"name": str(r.get("HOLDER_NAME") or ""), "shares": _num(r.get("HOLD_NUM")), "ratio": _ratio(r.get("HOLD_NUM_RATIO")),
              "change": _change(r.get("HOLD_NUM_CHANGE"))} for r in sdgd if r.get("HOLDER_NAME")]
    top10_float = [{"name": str(r.get("HOLDER_NAME") or ""), "type": str(r.get("HOLDER_TYPE") or ""), "shares": _num(r.get("HOLD_NUM")),
                    "ratio": _ratio(r.get("FREE_HOLDNUM_RATIO")), "change": _change(r.get("HOLD_NUM_CHANGE"))}
                   for r in sdltgd if r.get("HOLDER_NAME")]
    ratios = [r["ratio"] for r in top10_float if r["ratio"] is not None]

    trend = []
    for r in gdrs:
        count, day = _num(r.get("HOLDER_TOTAL_NUM")), _date(r.get("END_DATE"))
        if count is not None and day:
            trend.append({"date": day, "count": int(count)})
    trend = sorted(trend, key=lambda x: x["date"])[-TREND_PERIODS:]

    latest = gdrs[0] if gdrs else {}
    count = _num(latest.get("HOLDER_TOTAL_NUM"))
    org = next((r for r in jgcc if str(r.get("ORG_TYPE")) == "00"), None)
    org_count = _num(org.get("TOTAL_ORG_NUM")) if org else None

    report_date = _date((sdltgd[0] if sdltgd else {}).get("END_DATE")) or _date((sdgd[0] if sdgd else {}).get("END_DATE")) or ""
    data = {
        "report_date": report_date,
        "holder_count": int(count) if count is not None else None,
        "holder_count_date": _date(latest.get("END_DATE")) if count is not None else None,
        "holder_count_change_pct": _num(latest.get("TOTAL_NUM_RATIO")),
        "holder_trend": trend,
        "concentration": str(latest.get("HOLD_FOCUS") or ""),
        "top10": top10,
        "top10_float": top10_float,
        "top10_float_ratio": round(sum(ratios), 2) if ratios else None,
        "institution": {
            "count": int(org_count) if org_count is not None else None,
            "ratio": _ratio(org.get("TOTAL_SHARES_RATIO")) if org else None,
            "date": _date(org.get("REPORT_DATE")) if org else None,
        },
        "source": SOURCE,
    }
    if not (top10 or top10_float or trend or count is not None or org_count is not None):
        return None
    return data


def _request(code: str) -> dict | None:
    bare = bare_code(code)
    if len(bare) != 6 or not bare.isdigit():
        return None
    resp = httpx.get(URL, params={"code": f"{exchange_of(bare).upper()}{bare}"},
                     headers={"User-Agent": USER_AGENT, "Referer": "https://emweb.securities.eastmoney.com/"}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def fetch_shareholders(code: str) -> dict[str, Any] | None:
    """获取股东数据；没有数据或出错返回 None（不抛异常），结果带进程级缓存。"""
    bare = bare_code(code)
    if diagnosis_code(code) != bare or bare.startswith(ETF_PREFIXES):  # 指数（带交易所前缀）和 ETF 没有股东数据，不联网
        return None
    now = datetime.now()
    with _cache_lock:
        hit = _cache.get(bare)
        if hit and hit[0] > now:
            return hit[1]
    started = time.time()
    data: dict[str, Any] | None = None
    ok, error = True, ""
    try:
        payload = _request(bare)
        data = _parse(payload) if payload is not None else None
        if data is None:
            ok, error = False, "无股东数据"
    except Exception as e:  # noqa: BLE001
        ok, error = False, str(e)[:200]
        logger.debug(f"股东数据获取失败 {bare}: {e}")
    source_health.record(DATASET, SOURCE, ok, error, time.time() - started)
    ttl = timedelta(hours=OK_CACHE_HOURS) if data else timedelta(minutes=FAIL_CACHE_MINUTES)
    with _cache_lock:
        _cache[bare] = (now + ttl, data)
    return data


def _count_text(count: int) -> str:
    return f"{count / 10000:.1f} 万" if count >= 10000 else f"{count} 户"


def describe_shareholders(data: dict[str, Any] | None) -> str:
    """股东数据一句话摘要；没有数据返回空字符串。"""
    if not data:
        return ""
    parts = []
    count = data.get("holder_count")
    if count is not None:
        extra = [d for d in (
            data.get("holder_count_date"),
            f"较上期 {data['holder_count_change_pct']:+.2f}%" if data.get("holder_count_change_pct") is not None else "",
            f"筹码{data['concentration']}" if data.get("concentration") else "",
        ) if d]
        parts.append(f"股东户数 {_count_text(count)}" + (f"（{'，'.join(extra)}）" if extra else ""))
    float_top = data.get("top10_float") or []
    if float_top:
        head = f"十大流通股东合计占 {data['top10_float_ratio']:.1f}%" if data.get("top10_float_ratio") is not None else "十大流通股东"
        top3 = "、".join(f"{h['name']} {h['ratio']:.2f}%" + (f"（{h['change']}）" if h.get("change") else "")
                        if h.get("ratio") is not None else h["name"] for h in float_top[:3])
        parts.append(f"{head}，前三：{top3}")
    elif data.get("top10"):
        top3 = "、".join(f"{h['name']} {h['ratio']:.2f}%" if h.get("ratio") is not None else h["name"] for h in data["top10"][:3])
        parts.append(f"十大股东前三：{top3}")
    inst = data.get("institution") or {}
    if inst.get("count") is not None:
        ratio = f"，占流通股 {inst['ratio']:.2f}%" if inst.get("ratio") is not None else ""
        day = f"（{inst['date']}）" if inst.get("date") else ""
        parts.append(f"机构 {inst['count']} 家{ratio}{day}")
    return "；".join(parts)


def holder_signals(data: dict[str, Any] | None) -> list[str]:
    """规则提示：户数大幅变化、机构新进。"""
    if not data:
        return []
    signals = []
    change = data.get("holder_count_change_pct")
    if change is not None:
        if change <= -CHANGE_ALERT_PCT:
            signals.append(f"股东户数环比下降 {abs(change):.2f}%，筹码趋于集中")
        elif change >= CHANGE_ALERT_PCT:
            signals.append(f"股东户数环比上升 {change:.2f}%，筹码趋于分散")
    fresh = [h["name"] for h in data.get("top10_float") or []
             if h.get("change") == "新进" and any(k in h["name"] for k in INSTITUTION_KEYWORDS)]
    if fresh:
        signals.append("新进：" + "、".join(fresh[:3]))
    return signals
