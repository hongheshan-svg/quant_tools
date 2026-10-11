"""AI 问股：会话列表、提问（后台任务）、导出、推送。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from api.deps import bad_request, get_pipeline, get_tasks, not_found
from api.tasks import TaskManager, business_result_error
from src.services.chat_sessions import CONFLICT_TEXT, ChatSessionStore
from src.services.pipeline_service import PipelineService

router = APIRouter(prefix="/chat", tags=["chat"])


def get_store(request: Request) -> ChatSessionStore:
    return request.app.state.chat_store


class NewSessionBody(BaseModel):
    perspective: str = "综合"


class StockContext(BaseModel):
    code: str = Field(min_length=1, max_length=20)

    @field_validator("code")
    @classmethod
    def canonical_code(cls, code: str) -> str:
        from src.utils.stock_code import diagnosis_code
        return diagnosis_code(code)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    perspective: str | None = None
    stock_context: StockContext | None = None
    # 三态：省略/null 沿用会话已保存的选择，[] 显式清空，列表保存为新的选择。
    skills: list[str] | None = Field(default=None, max_length=4)
    # 客户端已知的会话状态版本；与服务端不一致时返回 409，请刷新后手动重试。
    state_version: int | None = Field(default=None, ge=0)

    @field_validator("skills")
    @classmethod
    def known_skills(cls, skills: list[str] | None) -> list[str] | None:
        if skills is None:
            return None
        from src.services.strategy_skills import get_skill
        if any(get_skill(key) is None for key in skills):
            raise ValueError("包含未知分析策略")
        return list(dict.fromkeys(skills))


def conflict() -> HTTPException:
    return HTTPException(status_code=409, detail=CONFLICT_TEXT, headers={"X-Error-Code": "session_state_conflict"})


def _check_state(store: ChatSessionStore, session_id: str, body: AskBody) -> None:
    """会话不存在 404；客户端带的版本已过期 409（准备期间的冲突由接受事务再判断一次）。"""
    record = store.get(session_id)
    if record is None:
        raise not_found("会话不存在")
    if body.state_version is not None and body.state_version != record["session_state_version"]:
        raise conflict()


@router.get("/perspectives")
def perspectives() -> dict[str, str]:
    """display_name → 一句话说明（兼容旧接口，新界面用 /chat/skills）。"""
    from src.services.stock_chat import perspectives as load_perspectives

    return load_perspectives()


@router.get("/skills")
def skills() -> list[dict[str, Any]]:
    """全部问股策略（内置 + config/strategies 自定义），按优先级排序。"""
    from src.services.strategy_skills import load_skills

    return [s.to_dict() for s in load_skills()]


@router.get("/skills/performance")
def skills_performance(days: int = Query(90, ge=7, le=365), pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    """各策略会诊观点的 5 日后验表现（样本、命中率、平均收益、当前权重）。"""
    from src.services.skill_consult import SkillOpinionService

    return SkillOpinionService(pipeline.config).performance(days)


@router.get("/sessions")
def sessions(store: ChatSessionStore = Depends(get_store)) -> list[dict[str, Any]]:
    return store.list()


@router.post("/sessions")
def create(body: NewSessionBody, store: ChatSessionStore = Depends(get_store)) -> dict[str, Any]:
    from src.services.stock_chat import normalize_perspective

    return store.create(normalize_perspective(body.perspective))


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
    _check_state(store, session_id, body)
    from src.services.stock_chat import normalize_perspective

    perspective = normalize_perspective(body.perspective) if body.perspective else None  # 支持英文名和别名
    return tasks.submit("chat", store.ask, session_id, body.question, perspective, stock_context=body.stock_context.model_dump() if body.stock_context else None,
                        skills=body.skills, expected_version=body.state_version, result_error=business_result_error, label="AI 问股")


@router.post("/sessions/{session_id}/ask/stream")
def ask_stream(session_id: str, body: AskBody, store: ChatSessionStore = Depends(get_store)) -> StreamingResponse:
    """流式提问（SSE）：每个事件一行 data: {json}。先发 accepted（问题与会话状态已保存），最后一个事件是 done；
    接受前的版本冲突只发 error（error_code=session_state_conflict），不发 accepted 和 done。"""
    _check_state(store, session_id, body)
    from src.services.stock_chat import normalize_perspective

    perspective = normalize_perspective(body.perspective) if body.perspective else None
    try:
        events = store.ask_stream(session_id, body.question, perspective,
                                  stock_context=body.stock_context.model_dump() if body.stock_context else None,
                                  skills=body.skills, expected_version=body.state_version)
    except KeyError:
        raise not_found("会话不存在")

    def generate():
        try:
            for event in events:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            events.close()

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/sessions/{session_id}/cancel")
def cancel(session_id: str, store: ChatSessionStore = Depends(get_store)) -> dict[str, Any]:
    return {"ok": store.cancel(session_id)}


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
