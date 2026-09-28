"""行情与大盘：首页快照、资讯流、市场概况、大盘环境、大盘复盘、主线、流程操作、信号绩效。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from api.deps import get_pipeline, get_tasks
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
