"""数据能力总览：各数据集的回退顺序、是否已配置，以及来自 source_health 的最近健康状态。

只读内存中的健康记录和配置，不联网。数据集名和来源名必须与采集代码调用
source_health.record() / fetch_with_fallback() 时用的名字一致，否则读不到健康记录。
"""

from __future__ import annotations

from typing import Any

from src.collectors import daily_history, news_search
from src.collectors.source_chain import source_health

# 实时行情：配置名 → (展示名, 健康记录里的来源名，与 stock_data 的 available 一致)
REALTIME_LABELS: dict[str, tuple[str, str]] = {
    "tencent": ("腾讯财经", "腾讯财经(HTTP)"),
    "eastmoney": ("东方财富", "东方财富(Playwright)"),
    "sina": ("新浪", "新浪(AKShare)"),
    "efinance": ("efinance", "efinance"),
    "pytdx": ("通达信", "通达信(pytdx)"),
}
DEFAULT_REALTIME = ("tencent", "eastmoney", "sina", "efinance", "pytdx")

# 个股日线：配置名 → 展示名（健康记录用 DAILY_SOURCES 里的来源标记）
DAILY_LABELS = {
    "tencent": "腾讯", "sina": "新浪", "eastmoney": "东方财富", "baostock": "baostock",
    "pytdx": "通达信", "efinance": "efinance", "tushare": "Tushare",
}

UNKNOWN_HEALTH: dict[str, Any] = {
    "status": "unknown", "last_success": None, "last_failure": None, "consecutive_failures": 0, "last_error": "",
}


def _health_index() -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["dataset"], r["source"]): r for r in source_health.snapshot()}


def _health(index: dict[tuple[str, str], dict[str, Any]], dataset: str, source: str) -> dict[str, Any]:
    rec = index.get((dataset, source))
    if not rec:
        return dict(UNKNOWN_HEALTH)
    status = {"ok": "ok", "failing": "failing", "circuit_open": "open"}.get(rec["status"], "unknown")
    return {
        "status": status,
        "last_success": rec["last_success"].isoformat(timespec="seconds") if rec.get("last_success") else None,
        "last_failure": rec["last_failure"].isoformat(timespec="seconds") if rec.get("last_failure") else None,
        "consecutive_failures": rec.get("consecutive_failures", 0),
        "last_error": rec.get("last_error", ""),
    }


def _source(index, dataset: str, name: str, label: str, *, health_name: str | None = None,
            configured: bool = True, note: str = "") -> dict[str, Any]:
    return {
        "name": name, "label": label, "configured": configured, "note": note,
        "health": _health(index, dataset, health_name or name),
    }


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v for v in value.replace("\n", ",").split(",")]
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def capabilities(config: dict) -> list[dict[str, Any]]:
    """各数据集的来源清单（按实际回退顺序）与健康状态。"""
    config = config or {}
    index = _health_index()
    ds_conf = config.get("data_sources") or {}
    result: list[dict[str, Any]] = []

    # 实时行情
    order = [n for n in _list(ds_conf.get("realtime")) if n in REALTIME_LABELS] or list(DEFAULT_REALTIME)
    result.append({"dataset": "实时行情", "label": "实时行情", "sources": [
        _source(index, "实时行情", name, REALTIME_LABELS[name][0], health_name=REALTIME_LABELS[name][1],
                note="全市场行情，按顺序回退" if i == 0 else "") for i, name in enumerate(order)
    ]})

    # 个股日线
    has_token = bool(str(ds_conf.get("tushare_token") or "").strip())
    order = [n for n in _list(ds_conf.get("daily_history")) if n in daily_history.DAILY_SOURCES] or list(daily_history.DAILY_SOURCES)
    daily = []
    for name in order:
        marker = daily_history.DAILY_SOURCES[name][0]
        note = ""
        configured = True
        if name == "tushare":
            configured = has_token
            note = "" if has_token else "需要填写 data_sources.tushare_token"
        elif name in ("pytdx", "tushare"):
            note = "日线不复权"
        elif name == "baostock":
            note = "不支持北交所"
        daily.append(_source(index, "个股日线", name, DAILY_LABELS.get(name, name), health_name=marker,
                             configured=configured, note=note))
    result.append({"dataset": "个股日线", "label": "个股日线", "sources": daily})

    # 涨停池、涨停原因、资金流（固定回退顺序）
    result.append({"dataset": "涨停池", "label": "涨停池", "sources": [
        _source(index, "涨停池", "东方财富涨停池", "东方财富涨停池"),
        _source(index, "涨停池", "东方财富强势股池(仅涨停)", "东方财富强势股池", note="只保留涨幅达到涨停幅度的股票"),
    ]})
    result.append({"dataset": "涨停原因", "label": "涨停原因", "sources": [
        _source(index, "涨停原因", "同花顺", "同花顺", note="失败不影响涨停池入库"),
    ]})
    result.append({"dataset": "个股资金流", "label": "个股资金流", "sources": [
        _source(index, "个股资金流", "同花顺", "同花顺"),
        _source(index, "个股资金流", "东方财富", "东方财富"),
    ]})

    # 联网搜索：只列已配置的 provider
    search_conf = config.get("search") or {}
    enabled = bool(search_conf.get("enabled"))
    note = "" if enabled else "联网搜索未启用（search.enabled 为 false）"
    result.append({"dataset": "联网搜索", "label": "联网搜索", "sources": [
        _source(index, news_search.DATASET, name, news_search.PROVIDERS[name], configured=True, note=note)
        for name in news_search.configured_providers(config)
    ]})

    # RSS 资讯源：启用的源
    rss_conf = config.get("intelligence") or {}
    rss_note = "" if rss_conf.get("enabled", True) is not False else "RSS 采集未启用（intelligence.enabled 为 false）"
    result.append({"dataset": "RSS 资讯源", "label": "RSS 资讯源", "sources": [
        _source(index, "rss", str(s.get("name") or ""), str(s.get("name") or ""), note=rss_note)
        for s in (rss_conf.get("sources") or []) if isinstance(s, dict) and s.get("enabled", True) is not False and s.get("name")
    ]})
    return result
