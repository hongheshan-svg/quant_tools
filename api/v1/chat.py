"""AI 问股：会话列表、提问（后台任务）、导出、推送。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.deps import bad_request, get_pipeline, get_tasks, not_found
from api.tasks import TaskManager
from src.services.chat_sessions import ChatSessionStore
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/chat", tags=["chat"])


def get_store(request: Request) -> ChatSessionStore:
    return request.app.state.chat_store


class NewSessionBody(BaseModel):
    perspective: str = "综合"


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    perspective: str | None = None


@router.get("/perspectives")
def perspectives() -> dict[str, str]:
    from src.services.stock_chat import PERSPECTIVES

    return PERSPECTIVES


@router.get("/sessions")
def sessions(store: ChatSessionStore = Depends(get_store)) -> list[dict[str, Any]]:
    return store.list()


@router.post("/sessions")
def create(body: NewSessionBody, store: ChatSessionStore = Depends(get_store)) -> dict[str, Any]:
    return store.create(body.perspective)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, store: ChatSessionStore = Depends(get_store)) -> dict[str, Any]:
    record = store.get(session_id)
    if record is None:
        raise not_found("会话不存在")
    return record


@router.delete("/sessions/{session_id}")
def delete(session_id: str, store: ChatSessionStore = Depends(get_store)) -> dict[str, Any]:
    return {"ok": store.delete(session_id)}


@router.post("/sessions/{session_id}/ask")
def ask(session_id: str, body: AskBody, store: ChatSessionStore = Depends(get_store),
        tasks: TaskManager = Depends(get_tasks)) -> dict[str, Any]:
    if store.get(session_id) is None:
        raise not_found("会话不存在")
    return tasks.submit("chat", store.ask, session_id, body.question, body.perspective, label="AI 问股")


@router.get("/sessions/{session_id}/export", response_class=PlainTextResponse)
def export(session_id: str, store: ChatSessionStore = Depends(get_store)) -> str:
    text = store.export_markdown(session_id)
    if text is None:
        raise not_found("会话不存在")
    return text


@router.post("/sessions/{session_id}/push")
def push_last_answer(session_id: str, store: ChatSessionStore = Depends(get_store),
                     pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    record = store.get(session_id)
    if record is None:
        raise not_found("会话不存在")
    answered = [t for t in record["turns"] if t.get("answer")]
    if not answered:
        raise bad_request("还没有回答可以推送")
    last = answered[-1]
    return pipeline.push_message(f"AI 问股：{last['question'][:30]}", last["answer"])
