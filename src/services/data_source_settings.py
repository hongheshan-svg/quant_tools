"""A 股行情源配置与回退顺序；供采集器、设置接口和能力页共用。"""

from __future__ import annotations

from urllib.parse import urlsplit

REALTIME_DEFAULT = ["tencent", "eastmoney", "sina", "efinance", "pytdx"]
DAILY_DEFAULT = ["tencent", "sina", "eastmoney", "baostock", "pytdx", "efinance", "tushare"]
REALTIME_NAMES = [*REALTIME_DEFAULT, "tickflow", "tushare"]
DAILY_NAMES = [*DAILY_DEFAULT, "tickflow"]
DEFAULTS = {
    "realtime": REALTIME_DEFAULT, "daily_history": DAILY_DEFAULT,
    "tushare_token": "", "tushare_http_url": "", "tickflow_api_key": "",
    "tickflow_kline_adjust": "forward", "request_timeout_seconds": 15, "stage_timeout_seconds": 60,
    "minimum_realtime_rows": 2500, "pytdx_servers": [],
    "isolate_collection": True, "collect_timeout_seconds": 240, "require_auxiliary_sources": False,
    "miaoxiang_api_key": "",
}
ADJUSTS = {"none", "forward", "backward", "forward_additive", "backward_additive"}


def source_order(config: dict, dataset: str) -> list[str]:
    raw = (config.get("data_sources") or {}).get(dataset)
    if isinstance(raw, str):
        raw = raw.replace("\n", ",").split(",")
    allowed = REALTIME_NAMES if dataset == "realtime" else DAILY_NAMES
    order = list(dict.fromkeys(str(n).strip() for n in (raw or []) if str(n).strip() in allowed))
    return order or list(DEFAULTS[dataset])


def source_configured(config: dict, name: str) -> bool:
    key = {"tickflow": "tickflow_api_key", "tushare": "tushare_token", "miaoxiang": "miaoxiang_api_key"}.get(name)
    return not key or bool(str((config.get("data_sources") or {}).get(key) or "").strip())


def validate_settings(value: dict) -> dict:
    merged = {**DEFAULTS, **value}
    for key, allowed in (("realtime", REALTIME_NAMES), ("daily_history", DAILY_NAMES)):
        order = merged[key]
        if not isinstance(order, list) or not order or any(not isinstance(n, str) or n not in allowed for n in order):
            raise ValueError(f"{key} 必须是非空的数据源列表")
        if len(set(order)) != len(order):
            raise ValueError(f"{key} 不能包含重复的数据源")
    for key in ("tushare_token", "tickflow_api_key", "tickflow_kline_adjust", "tushare_http_url", "miaoxiang_api_key"):
        if not isinstance(merged[key], str):
            raise ValueError(f"{key} 必须是字符串")
    url = merged["tushare_http_url"].strip()
    parsed = urlsplit(url)
    if url and (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Tushare 网关必须是无账号、参数和片段的 HTTP(S) 地址")
    merged["tushare_http_url"] = url
    if not isinstance(merged["pytdx_servers"], list) or any(not isinstance(s, str) for s in merged["pytdx_servers"]):
        raise ValueError("通达信服务器必须是地址列表")
    if merged["tickflow_kline_adjust"] not in ADJUSTS:
        raise ValueError("TickFlow 复权方式无效")
    for key in ("isolate_collection", "require_auxiliary_sources"):
        if not isinstance(merged[key], bool):
            raise ValueError(f"{key} 必须为布尔值")
    for key, lower, upper in (("request_timeout_seconds", 1, 60), ("stage_timeout_seconds", 1, 600), ("collect_timeout_seconds", 1, 900), ("minimum_realtime_rows", 0, 10000)):
        number = merged[key]
        if isinstance(number, bool) or not isinstance(number, int) or not lower <= number <= upper:
            raise ValueError(f"{key} 应在 {lower}–{upper} 之间")
    return merged


def probe_daily_source(source: str, code: str) -> dict:
    """读取单只股票最近日线，只更新健康记录，不写行情库。"""
    from datetime import datetime, timedelta
    from src.collectors.daily_history import fetch_daily_df_with_fallback, records_from_daily_df
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=45)
    marker, frame = fetch_daily_df_with_fallback(code, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"), order=[source])
    rows = records_from_daily_df(code, "", marker, frame)
    return {"source": source, "code": code, "bars": len(rows), "latest_date": max(row["trade_date"] for row in rows), "ok": True}
