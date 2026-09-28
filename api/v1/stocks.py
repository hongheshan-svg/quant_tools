"""个股：搜索、日线、新闻公告、AI 诊断、按需补齐日线。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from api.deps import get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService
from src.utils.stock_code import bare_code

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search(q: str = Query("", max_length=40), limit: int = Query(15, ge=1, le=50),
           pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.search_stocks(q, limit)


@router.get("/{code}/daily")
def daily(code: str, limit: int | None = Query(None, ge=1, le=5000), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    from src.services.data_query_service import DataQueryService

    return DataQueryService(pipeline.db_path).get_stock_daily_history(bare_code(code), limit)


@router.post("/{code}/history")
def ensure_history(code: str, name: str = "", pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """本地日线不足时联网补齐，返回新写入的根数。"""
    return {"added": pipeline.ensure_history(bare_code(code), name)}


@router.get("/{code}/news")
def news(code: str, refresh: bool = False, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.stock_news(bare_code(code), refresh)


@router.get("/{code}/diagnosis")
def latest_diagnosis(code: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any] | None:
    return pipeline.latest_diagnosis(bare_code(code))


@router.post("/{code}/diagnosis")
def diagnose(code: str, tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    code = bare_code(code)
    return tasks.submit("diagnosis", pipeline.diagnose_stock, code, True, dedupe_key=f"diagnosis:{code}", label=f"AI 诊断 {code}")
