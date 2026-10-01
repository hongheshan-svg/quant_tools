"""自选股：增删、批量导入、决策仪表盘。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from api.deps import bad_request, get_config, get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services import image_import
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


@router.post("/import-image")
async def import_image(
    file: UploadFile = File(...),
    tasks: TaskManager = Depends(get_tasks),
    config: dict = Depends(get_config),
) -> dict[str, Any]:
    """截图识别股票：校验后提交后台任务，任务结果为候选列表，确认后再调用添加接口。"""
    mime = (file.content_type or "").lower().split(";")[0].strip()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in image_import.ALLOWED_TYPES:
        raise bad_request("只支持 PNG、JPEG、WebP、GIF 图片")
    data = await file.read(image_import.MAX_BYTES + 1)
    if not data:
        raise bad_request("图片内容为空")
    if len(data) > image_import.MAX_BYTES:
        raise bad_request("图片不能超过 5MB")
    return tasks.submit("image_import", image_import.extract_stocks, data, mime, config, label="识别截图中的股票")


@router.get("/report")
def latest_report(pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any] | None:
    return pipeline.latest_watchlist_report()


@router.get("/report/image")
def report_image(pipeline: PipelineService = Depends(get_pipeline), config: dict = Depends(get_config)) -> Response:
    """最近一份自选股决策仪表盘的分享图 PNG；没有仪表盘 404，渲染失败 503。"""
    from src.services import report_image as image_service

    report = pipeline.latest_watchlist_report()
    if not report or not report.get("markdown"):
        raise HTTPException(status_code=404, detail="没有自选股决策仪表盘")
    title = f"自选股决策仪表盘 {report.get('trade_date') or ''}".strip()
    try:
        png = image_service.render_markdown_image(title, report["markdown"], **image_service.share_options(config))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(png, media_type="image/png",
                    headers={"Content-Disposition": f'attachment; filename="watchlist-report-{report.get("trade_date", "")}.png"'})


@router.post("/report")
def run_report(push: bool = True, tasks: TaskManager = Depends(get_tasks), pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return tasks.submit("watchlist_report", pipeline.watchlist_report, push, dedupe_key="watchlist_report", label="自选股决策仪表盘")
