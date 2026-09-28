"""自选股：增删、批量导入、决策仪表盘。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field

from api.deps import bad_request, get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/watchlist", tags=["watchlist"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UPLOAD_SUFFIXES = (".csv", ".txt", ".xlsx", ".xls")


class AddBody(BaseModel):
    text: str = Field(min_length=1, max_length=40)


class ImportBody(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


async def save_upload(file: UploadFile) -> Path:
    """把上传文件存到临时文件（保留扩展名，读取逻辑按扩展名区分 CSV 和 Excel）。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise bad_request("只支持 CSV、Excel、TXT 文件")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise bad_request("文件不能超过 5MB")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
    return Path(tmp.name)


@router.get("")
def overview(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.watchlist_overview()


@router.post("")
def add(body: AddBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.watchlist_add(body.text)


@router.delete("/{code}")
def remove(code: str, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return {"ok": pipeline.watchlist_remove(code)}


@router.post("/import")
def import_text(body: ImportBody, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return pipeline.watchlist_import(text=body.text)


@router.post("/import-file")
async def import_file(file: UploadFile = File(...), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    path = await save_upload(file)
    try:
        return pipeline.watchlist_import(path=str(path))
    finally:
        path.unlink(missing_ok=True)


@router.get("/report")
def latest_report(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any] | None:
    return pipeline.latest_watchlist_report()


@router.post("/report")
def run_report(push: bool = True, tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("watchlist_report", pipeline.watchlist_report, push, dedupe_key="watchlist_report", label="自选股决策仪表盘")
