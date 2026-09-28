"""策略选股与历史回测。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from api.deps import get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/screening", tags=["screening"])


@router.get("")
def latest(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """最近一次选股结果（含次日涨幅）、近 30 天策略次日表现、最近一次历史回测。"""
    return pipeline.latest_screening()


@router.post("/run")
def run(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("screening", pipeline.screen_stocks, dedupe_key="screening", label="策略选股")


@router.post("/backtest")
def backtest(days: int = Query(60, ge=10, le=365), tasks: TaskManager = Depends(get_tasks),
             pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("backtest", pipeline.backtest_strategies, days, dedupe_key="backtest", label=f"历史回测 {days} 天")
