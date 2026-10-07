"""数据能力总览：各数据集的回退顺序、是否已配置，以及来自 source_health 的最近健康状态。

只读内存中的健康记录和配置，不联网。数据集名和来源名必须与采集代码调用
source_health.record() / fetch_with_fallback() 时用的名字一致，否则读不到健康记录。
"""

from __future__ import annotations

from typing import Any

from src.collectors import daily_history, news_search
from src.collectors.source_chain import source_health
from src.services.data_source_settings import source_order, source_configured

# 实时行情：配置名 → (展示名, 健康记录里的来源名，与 stock_data 的 available 一致)
REALTIME_LABELS: dict[str, tuple[str, str]] = {
    "tencent": ("腾讯财经", "腾讯财经(HTTP)"),
    "eastmoney": ("东方财富", "东方财富(Playwright)"),
    "sina": ("新浪", "新浪(AKShare)"),
    "efinance": ("efinance", "efinance"),
    "pytdx": ("通达信", "通达信(pytdx)"),
    "tickflow": ("TickFlow", "TickFlow"),
    "tushare": ("Tushare", "Tushare"),
}
DEFAULT_REALTIME = ("tencent", "eastmoney", "sina", "efinance", "pytdx")

# 个股日线：配置名 → 展示名（健康记录用 DAILY_SOURCES 里的来源标记）
DAILY_LABELS = {
    "tencent": "腾讯", "sina": "新浪", "eastmoney": "东方财富", "baostock": "baostock",
    "pytdx": "通达信", "efinance": "efinance", "tushare": "Tushare",
    "tickflow": "TickFlow",
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
    order = source_order(config, "realtime")
    result.append({"dataset": "实时行情", "label": "实时行情", "sources": [
        _source(index, "实时行情", name, REALTIME_LABELS[name][0], health_name=REALTIME_LABELS[name][1],
                configured=source_configured(config, name),
                note="需要密钥和全市场实时权限" if name in {"tickflow", "tushare"} else
                "全市场行情，按顺序回退" if i == 0 else "") for i, name in enumerate(order)
    ]})

    # 个股日线
    has_token = bool(str(ds_conf.get("tushare_token") or "").strip())
    order = source_order(config, "daily_history")
    daily = []
    for name in order:
        marker = daily_history.DAILY_SOURCES[name][0]
        note = ""
        configured = True
        if name == "tushare":
            configured = has_token
            note = "日线不复权" if has_token else "需要填写 data_sources.tushare_token；日线不复权"
        elif name == "pytdx":
            note = "日线不复权"
        elif name == "baostock":
            note = "不支持北交所"
        elif name == "tickflow":
            configured = source_configured(config, name)
            note = "需要 API Key；复权：" + str(ds_conf.get("tickflow_kline_adjust", "forward"))
        daily.append(_source(index, "个股日线", name, DAILY_LABELS.get(name, name), health_name=marker,
                             configured=configured, note=note))
    result.append({"dataset": "个股日线", "label": "个股日线", "sources": daily})
    result.append({"dataset": "季度基本面", "label": "季度财报与分红", "sources": [
        _source(index, "季度基本面", "sina", "新浪财报摘要", note="季度累计口径，保留报告期与采集时间"),
        _source(index, "季度基本面", "indicator", "新浪财务指标", note="摘要不可用时回退"),
    ]})
    result.append({"dataset": "分红事件", "label": "分红事件", "sources": [
        _source(index, "分红事件", "新浪", "新浪分红历史", note="税前现金分红，区分公告与已实施"),
    ]})

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
    result.append({"dataset": "个股资金流补充", "label": "单股票资金流补充", "sources": [
        _source(index, "个股资金流补充", "miaoxiang", "妙想", health_name="妙想", configured=source_configured(config, "miaoxiang"), note="仅 A 股个股；不替代全市场资金流，5/10 日合计须有完整观测窗口"),
    ]})
    result.append({"dataset": "筹码分布", "label": "筹码分布", "sources": [
        _source(index, "筹码分布", "eastmoney", "东方财富", health_name="东方财富"),
        _source(index, "筹码分布", "miaoxiang", "妙想", health_name="妙想", configured=source_configured(config, "miaoxiang"), note="仅 A 股个股核心筹码指标，成本区间可能未知"),
        _source(index, "筹码分布", "local", "本地估算", health_name="本地估算", note="估算值，不属于提供方观测"),
    ]})
    result.append({"dataset": "基金日线", "label": "ETF / 国内指数日线", "sources": [
        _source(index, "基金日线", "tencent", "腾讯财经", health_name="腾讯", note="ETF 前复权；指数不复权，仅注册表支持的代码"),
        _source(index, "基金日线", "csindex", "中证指数", health_name="中证指数", note="仅中证专属指数"),
        _source(index, "基金日线", "cnindex", "国证指数", health_name="国证指数", note="仅国证专属指数"),
    ]})

    result.append({"dataset": "股东数据", "label": "股东数据（东方财富 F10）", "sources": [
        _source(index, "股东数据", "东方财富", "东方财富 F10", note="个股诊断和问股按需获取，诊断需开启 diagnosis.shareholders"),
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


def data_center(config: dict) -> dict:
    """只读能力矩阵与本地可用性；不探测供应商、不修改配置、密钥不出现在结果中。"""
    import os
    from datetime import datetime
    from src.database.db import get_db_session
    from src.database.models import StockDaily, FundDaily, StockFundFlow
    from src.services.data_freshness import daily_quality
    datasets = capabilities(config)
    matrices = []
    for dataset in datasets:
        name = dataset["dataset"]
        kinds = ["etf", "index"] if name == "基金日线" else ["market"] if name in {"涨停池", "涨停原因"} else ["stock"] if name not in {"联网搜索", "RSS 资讯源"} else ["stock", "etf", "index", "market"]
        scenarios = ["watchlist", "diagnosis", "chat"]
        if name in {"实时行情", "个股日线", "季度基本面", "涨停池", "涨停原因", "个股资金流"}:
            scenarios += ["stock_screening"]
        if name in {"实时行情", "个股日线", "基金日线"}: scenarios += ["portfolio_valuation"]
        for priority, source in enumerate(dataset["sources"], 1):
            supported = ["index"] if source["name"] in {"csindex", "cnindex"} else kinds
            order_key = {"实时行情": "REALTIME", "个股日线": "DAILY_HISTORY"}.get(name)
            credential_key = {"tushare": "TUSHARE_TOKEN", "tickflow": "TICKFLOW_API_KEY", "miaoxiang": "MIAOXIANG_API_KEY"}.get(source["name"])
            env_keys = ["QUANT__DATA_SOURCES__" + key for key in (order_key, credential_key) if key]
            origin = "environment_override" if any(key in os.environ for key in env_keys) else "file_or_default"
            matrices.append({"provider": source["name"], "provider_label": source["label"], "dataset": name, "markets": ["CN"],
                             "asset_kinds": supported, "scenarios": scenarios, "priority": priority,
                             "configuration_origin": origin, "configured": source["configured"], "health": source["health"], "limitations": source["note"],
                             "north_exchange_supported": source["name"] != "baostock" if name == "个股日线" else None,
                             "observation_timestamp": None, "fetched_at": source["health"]["last_success"]})
    snapshots = []
    for model, dataset in ((StockDaily, "个股日线"), (FundDaily, "基金日线"), (StockFundFlow, "个股资金流")):
        with get_db_session((config.get("database") or {}).get("sqlite_path", "data/quant.db")) as session:
            row = session.query(model).order_by(model.trade_date.desc(), model.updated_at.desc()).first()
            quality = daily_quality(row.trade_date if row else None, row.updated_at if row else None)
            count = session.query(model).filter(model.trade_date == row.trade_date).count() if row else 0
            snapshots.append({"dataset": dataset, **quality, "rows_on_date": count,
                              "scope": "database_latest_date", "note": "最新日期的样本行不能证明全市场完整覆盖；配置及健康成功不能替代数据质量"})
    return {"as_of": datetime.now().isoformat(), "read_only": True, "datasets": datasets, "matrix": matrices, "snapshots": snapshots,
            "unsupported": ["overseas_equities", "multi_currency_portfolio", "miaoxiang_daily", "miaoxiang_whole_market_quotes"]}
