"""行情与大盘：首页快照、资讯流、市场概况、大盘环境、大盘复盘、主线、流程操作、信号绩效。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from api.deps import get_config, get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService

router = APIRouter(tags=["market"])


def _query(pipeline: PipelineService):
    from src.services.data_query_service import DataQueryService

    return DataQueryService(pipeline.db_path)


@router.get("/dashboard")
def dashboard(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """交易决策首页：Top 评分、涨停池、信号、交易焦点、AI 预测、市场概况（资讯流单独取）。"""
    snapshot = _query(pipeline).get_dashboard_snapshot()
    for key in ("unified_news", "xueqiu_data", "jiuyan_data", "global_news"):
        snapshot.pop(key, None)
    return snapshot


@router.get("/news")
def news(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return _query(pipeline).get_unified_news()


@router.get("/market/overview")
def market_overview(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return _query(pipeline).get_market_overview()


@router.post("/market/overview/refresh")
def refresh_overview(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("refresh_overview", pipeline.refresh_market_overview, dedupe_key="refresh_overview", label="刷新市场概况")


@router.get("/market/regime")
def market_regime(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.market_regime()


@router.get("/market/review")
def latest_review(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any] | None:
    return pipeline.latest_market_review()


@router.post("/market/review")
def generate_review(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("market_review", pipeline.market_review, True, dedupe_key="market_review", label="生成大盘复盘")


@router.get("/market/themes")
def themes(dimension: str = Query("concept", pattern="^(concept|industry)$"),
           pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.main_themes(dimension)


# ---------- 流程操作 ----------

@router.post("/pipeline/collect")
def collect(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("collect", pipeline.collect, dedupe_key="collect", label="采集全部数据")


def _collect_rss(config: dict) -> dict[str, int]:
    """立即采集 RSS 资讯源并入库"""
    from src.collectors.rss import RSSCollector, save_items

    cfg = config.get("intelligence") or {}
    collector = RSSCollector(config)
    try:
        items = collector.collect()
    finally:
        collector.close()
    inserted = save_items(items, config.get("database", {}).get("sqlite_path", "data/quant.db"), int(cfg.get("keep_days", 7)))
    return {"fetched": len(items), "inserted": inserted}


@router.post("/pipeline/collect-rss")
def collect_rss(tasks: TaskManager = Depends(get_tasks), config: dict = Depends(get_config)) -> dict[str, Any]:
    return tasks.submit("collect_rss", _collect_rss, config, dedupe_key="collect_rss", label="采集 RSS 资讯")


@router.post("/pipeline/predict")
def predict(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("predict", pipeline.premarket_predict, dedupe_key="predict", label="AI 涨停预测")


@router.post("/pipeline/run-full")
def run_full(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("run_full", pipeline.run_full, dedupe_key="run_full", label="完整流程")


@router.post("/pipeline/daily-report")
def push_daily_report(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("daily_report", pipeline.push_daily_report, dedupe_key="daily_report", label="推送日报")


# ---------- 绩效 ----------

@router.get("/performance/signals")
def signal_performance(days: int = Query(60, ge=5, le=365), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.signal_performance(days)


@router.get("/performance/diagnosis")
def diagnosis_outcomes(days: int = Query(60, ge=5, le=365), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.diagnosis_outcomes(days)


@router.get("/alerts")
def alerts(limit: int = Query(200, ge=1, le=1000), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.recent_alerts(limit)


@router.post("/alerts/check")
def check_alerts(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("check_alerts", pipeline.check_alerts, dedupe_key="check_alerts", label="检查盘中提醒")


ALERT_SETTING_KEYS = ("enabled", "cooldown_minutes", "big_drop_pct", "near_stop_pct", "market_regime", "regime_score_drop", "watchlist")
ALERT_SETTING_DEFAULTS: dict[str, Any] = {
    "enabled": True, "cooldown_minutes": 30, "big_drop_pct": -7, "near_stop_pct": 2,
    "market_regime": True, "regime_score_drop": 15, "watchlist": [],
}
ALERT_SETTING_LABELS = {
    "cooldown_minutes": "冷却时间", "big_drop_pct": "大跌阈值", "near_stop_pct": "接近止损距离", "regime_score_drop": "大盘评分下降",
}


def _apply_saved_config(request: Request, values: dict[str, Any]) -> None:
    """保存 alerts 段后清掉配置缓存，并把新值同步给应用内的服务（其余配置沿用运行中的，不整体重读文件）。"""
    from api.app import apply_config
    from src.config_loader import reload_config

    reload_config()
    current = request.app.state.pipeline.config
    apply_config(request.app, {**current, "alerts": {**(current.get("alerts") or {}), **values}})


@router.get("/alerts/rules")
def alert_rules(config: dict[str, Any] = Depends(get_config)) -> dict[str, Any]:
    """当前的自定义提醒规则和可用的规则类型（供 Web 动态渲染表单）。"""
    from src.services.alert_service import RULE_TYPES

    return {"rules": (config.get("alerts") or {}).get("rules") or [], "types": RULE_TYPES}


@router.put("/alerts/rules")
def save_alert_rules(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """整体保存自定义提醒规则：逐条校验，第一条错误返回 422。"""
    from src.services.alert_service import validate_rule
    from src.settings_store import save_section

    raw = body.get("rules")
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="rules 必须是列表")
    rules = []
    for i, rule in enumerate(raw, 1):
        try:
            rules.append(validate_rule(rule))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"第 {i} 条：{e}") from None
    save_section("alerts", {"rules": rules}, merge=True)
    _apply_saved_config(request, {"rules": rules})
    return {"rules": rules}


@router.post("/alerts/rules/test")
def test_alert_rule(body: dict[str, Any] = Body(...), config: dict[str, Any] = Depends(get_config)) -> dict[str, Any]:
    """试算一条规则（不写记录、不推送）。"""
    from src.services.alert_service import AlertService, validate_rule

    rule = body.get("rule")
    if not isinstance(rule, dict):
        raise HTTPException(status_code=422, detail="缺少 rule")
    try:
        return AlertService(config).test_rule(validate_rule(rule))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/alerts/settings")
def alert_settings(config: dict[str, Any] = Depends(get_config)) -> dict[str, Any]:
    cfg = config.get("alerts") or {}
    return {k: cfg.get(k, ALERT_SETTING_DEFAULTS[k]) for k in ALERT_SETTING_KEYS}


@router.put("/alerts/settings")
def save_alert_settings(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """保存提醒的标量设置（只写这些键，不覆盖 rules）。"""
    from src.settings_store import save_section
    from src.services.alert_service import normalize_code

    values: dict[str, Any] = {}
    for key in ALERT_SETTING_KEYS:
        if key not in body:
            continue
        v = body[key]
        if key in ("enabled", "market_regime"):
            if not isinstance(v, bool):
                raise HTTPException(status_code=422, detail=f"{key} 必须是布尔值")
        elif key == "watchlist":
            if not isinstance(v, list):
                raise HTTPException(status_code=422, detail="关注股票必须是代码列表")
            codes = [normalize_code(c) for c in v]
            bad = [c for c in codes if not (len(c) == 6 and c.isdigit())]
            if bad:
                raise HTTPException(status_code=422, detail=f"股票代码不正确：{'、'.join(bad)}")
            v = list(dict.fromkeys(codes))
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
                raise HTTPException(status_code=422, detail=f"{ALERT_SETTING_LABELS.get(key, key)}必须是数字")
            if key in ("cooldown_minutes", "near_stop_pct", "regime_score_drop") and v < 0:
                raise HTTPException(status_code=422, detail=f"{ALERT_SETTING_LABELS[key]}不能为负数")
            if key == "big_drop_pct" and v >= 0:
                raise HTTPException(status_code=422, detail="大跌阈值必须是负数，如 -7")
        values[key] = v
    if values:
        save_section("alerts", values, merge=True)
        _apply_saved_config(request, values)
    return alert_settings(request.app.state.pipeline.config)
