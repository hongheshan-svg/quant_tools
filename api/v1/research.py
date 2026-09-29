"""深度研究：提交后台任务、历史报告、Markdown 下载。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel

from api.deps import bad_request, get_config, get_tasks, not_found
from api.tasks import TaskManager
from src.services.research import ResearchService

router = APIRouter(prefix="/research", tags=["research"])
MAX_TOPIC = 100


class ResearchBody(BaseModel):
    topic: str = ""


@router.post("")
def start(body: ResearchBody, tasks: TaskManager = Depends(get_tasks), config: dict = Depends(get_config)) -> dict[str, Any]:
    topic = body.topic.strip()
    if not topic:
        raise bad_request("请输入研究主题")
    if len(topic) > MAX_TOPIC:
        raise bad_request(f"研究主题不能超过 {MAX_TOPIC} 个字")
    service = ResearchService(config)

    def run(progress) -> dict:
        return service.run(topic, progress)

    return tasks.submit("research", run, label=f"深度研究：{topic}")


@router.get("")
def list_reports(limit: int = Query(50, ge=1, le=200), config: dict = Depends(get_config)) -> list[dict[str, Any]]:
    return ResearchService(config).list(limit)


@router.get("/{report_id}")
def get_report(report_id: int, config: dict = Depends(get_config)) -> dict[str, Any]:
    row = ResearchService(config).get(report_id)
    if row is None:
        raise not_found("研究报告不存在")
    return row


@router.delete("/{report_id}")
def delete_report(report_id: int, config: dict = Depends(get_config)) -> dict[str, bool]:
    if not ResearchService(config).delete(report_id):
        raise not_found("研究报告不存在")
    return {"ok": True}


@router.get("/{report_id}/markdown")
def report_markdown(report_id: int, config: dict = Depends(get_config)) -> Response:
    row = ResearchService(config).get(report_id)
    if row is None:
        raise not_found("研究报告不存在")
    day = (row.get("created_at") or "")[:10]
    filename = f"研究_{row['topic'][:30]}_{day}.md"
    disposition = f"attachment; filename=\"research_{report_id}_{day}.md\"; filename*=UTF-8''{quote(filename)}"
    body = f"# {row['topic']}\n\n{row['markdown']}\n"
    return Response(body, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": disposition})
