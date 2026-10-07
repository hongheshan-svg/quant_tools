"""策略选股与历史回测。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.deps import get_pipeline, get_tasks
from api.tasks import TaskManager, business_result_error
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/screening", tags=["screening"])


class RotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    risk_assets: list[str] | None = Field(None, min_length=1, max_length=30)
    safe_asset: str | None = None
    start: str | None = None
    end: str | None = None
    lookback_days: int | None = Field(None, ge=1, le=1000)
    top_n: int | None = Field(None, ge=1, le=30)
    rebalance: str | None = Field(None, pattern="^(weekly|monthly)$")
    switch_buffer_pct: float | None = Field(None, ge=0, le=100)
    cost_bps: float | None = Field(None, ge=0, lt=10000)
    min_years: float | None = Field(None, ge=1, le=50)
    refresh: bool = False


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: dict
    strategies: list[str] | None = Field(None, max_length=30)

    @field_validator("snapshot")
    @classmethod
    def snapshot_is_bounded(cls, value):
        from src.strategy.snapshot_diagnostics import validate_snapshot
        return validate_snapshot(value)


@router.post("/snapshot/check")
def snapshot_check(body: SnapshotRequest, pipeline: PipelineService = Depends(get_pipeline)):
    from src.strategy.snapshot_diagnostics import diagnose
    from src.strategy.screener import StrategyScreener
    try:
        return diagnose(body.snapshot, StrategyScreener(pipeline.config), body.strategies)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/rotation/settings")
def rotation_settings(pipeline: PipelineService = Depends(get_pipeline)):
    from src.services.etf_rotation import ETFRotationService
    return ETFRotationService(pipeline.config).settings()


@router.post("/rotation/run")
def rotation_run(body: RotationRequest, tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)):
    from src.services.etf_rotation import ETFRotationService
    return tasks.submit("etf_rotation", ETFRotationService(pipeline.config).run, body.model_dump(exclude_none=True), dedupe_key="etf_rotation", label="ETF双动量轮动")


@router.get("")
def latest(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """最近一次选股结果（含次日涨幅）、近 30 天策略次日表现、最近一次历史回测。"""
    return pipeline.latest_screening()


@router.get("/dates")
def dates(limit: int = Query(60, ge=1, le=250), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """有选股结果的历史交易日（倒序，含入选数与次日表现）和策略列表。"""
    from src.strategy.screener import StrategyScreener

    return {"dates": pipeline.screening_dates(limit), "strategies": [{"name": s.name, "label": s.label} for s in StrategyScreener(pipeline.config).strategies]}


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
    return tasks.submit("screening", pipeline.screen_stocks, dedupe_key="screening", label="策略选股", result_error=business_result_error)


@router.post("/backtest")
def backtest(days: int = Query(60, ge=10, le=365), tasks: TaskManager = Depends(get_tasks),
             pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("backtest", pipeline.backtest_strategies, days, dedupe_key="backtest", label=f"历史回测 {days} 天")


@router.get("/runs")
def screening_runs(limit: int = Query(20, ge=1, le=100), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict]:
    from src.strategy.screener import StrategyScreener
    return StrategyScreener(pipeline.config).runs(limit)


@router.get("/source-history")
def screening_source_history(limit: int = Query(30, ge=1, le=100), pipeline: PipelineService = Depends(get_pipeline)) -> dict:
    from src.strategy.screener import StrategyScreener
    return StrategyScreener(pipeline.config).source_history(limit)
