"""个股：搜索、日线、新闻公告、AI 诊断、按需补齐日线。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.deps import get_pipeline, get_tasks
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService
from src.utils.stock_code import bare_code

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search")
def search(q: str = Query("", max_length=40), limit: int = Query(15, ge=1, le=50),
           pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.search_stocks(q, limit)


@router.get("/diagnoses")
def list_diagnoses(code: str = Query("", max_length=20), action: str = Query("", max_length=20), days: int = Query(30, ge=0, le=3650),
                   limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                   pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """诊断历史（新的在前）；days=0 不限时间。"""
    return pipeline.list_diagnoses(code or None, action or None, days, limit, offset)


def _get_or_404(pipeline: PipelineService, diagnosis_id: int) -> dict[str, Any]:
    row = pipeline.get_diagnosis(diagnosis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    return row


def _attachment(row: dict[str, Any], ext: str) -> str:
    """Content-Disposition：ASCII 回退文件名 + RFC 5987 的 UTF-8 文件名。"""
    day = (row.get("created_at") or "")[:10]
    filename = f"诊断_{row.get('name') or row['code']}_{row['code']}_{day}.{ext}"
    ascii_name = f"diagnosis_{row['code']}_{day}.{ext}"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    return _get_or_404(pipeline, diagnosis_id)


@router.delete("/diagnoses/{diagnosis_id}")
def delete_diagnosis(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, bool]:
    if not pipeline.delete_diagnosis(diagnosis_id):
        raise HTTPException(status_code=404, detail="诊断记录不存在")
    return {"ok": True}


def _diagnosis_markdown(row: dict[str, Any]) -> tuple[str, str]:
    """返回（标题, markdown 正文）。"""
    from src.services.stock_diagnosis import render_markdown

    title = f"{row['name'] or row['code']}({row['code']}) AI 诊断 · {row['created_at']}"
    try:
        body = render_markdown(row["result"])
    except (KeyError, TypeError):
        body = f"**诊断记录数据不完整**：{row['result'].get('one_sentence', '')}"
    return title, body


@router.get("/diagnoses/{diagnosis_id}/markdown")
def diagnosis_markdown(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> Response:
    row = _get_or_404(pipeline, diagnosis_id)
    title, body = _diagnosis_markdown(row)
    return Response(f"# {title}\n\n{body}\n", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": _attachment(row, "md")})


@router.get("/diagnoses/{diagnosis_id}/image")
def diagnosis_image(diagnosis_id: int, pipeline: PipelineService = Depends(get_pipeline)) -> Response:
    from src.services import report_image

    row = _get_or_404(pipeline, diagnosis_id)
    title, body = _diagnosis_markdown(row)
    try:
        png = report_image.render_markdown_image(title, body, "AI 诊断仅供参考，不构成投资建议")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return Response(png, media_type="image/png", headers={"Content-Disposition": _attachment(row, "png")})


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
