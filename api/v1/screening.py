"""策略选股与历史回测。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/screening", tags=["screening"])


@router.get("")
def latest(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """最近一次选股结果（含次日涨幅）、近 30 天策略次日表现、最近一次历史回测。"""
    return pipeline.latest_screening()


@router.get("/dates")
def dates(limit: int = Query(60, ge=1, le=250), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """有选股结果的历史交易日（倒序，含入选数与次日表现）和策略列表。"""
    from src.strategy.screener import STRATEGIES

    return {"dates": pipeline.screening_dates(limit), "strategies": [{"name": s.name, "label": s.label} for s in STRATEGIES]}


@router.get("/picks")
def picks(trade_date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), strategy: str | None = Query(None),
          pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """指定交易日（空为最近一天）的选股结果，可按策略筛选。"""
    if trade_date:
        try:
            datetime.strptime(trade_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "trade_date 日期无效，格式应为 YYYY-MM-DD")
    rows = pipeline.screening_picks(trade_date, strategy or None)
    actual = rows[0]["trade_date"] if rows else None
    return {"trade_date": actual, "picks": rows}


@router.post("/run")
def run(tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("screening", pipeline.screen_stocks, dedupe_key="screening", label="策略选股")


@router.post("/backtest")
def backtest(days: int = Query(60, ge=10, le=365), tasks: TaskManager = Depends(get_tasks),
             pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("backtest", pipeline.backtest_strategies, days, dedupe_key="backtest", label=f"历史回测 {days} 天")
