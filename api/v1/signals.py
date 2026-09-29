"""决策信号：列表、统计、单票复盘、详情、用户反馈、立即评估。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from api.deps import get_config, get_tasks, not_found
from api.tasks import TaskManager
from src.services.decision_signals import DecisionSignalService

router = APIRouter(prefix="/signals", tags=["signals"])


class FeedbackBody(BaseModel):
    feedback: Literal["useful", "not_useful"] | None = None
    note: str = Field(default="", max_length=500)


@router.get("")
def list_signals(
    status: str | None = None, action: str | None = None, code: str | None = None,
    days: int = Query(90, ge=0, le=3650), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    config: dict[str, Any] = Depends(get_config),
):
    return DecisionSignalService(config).list(status=status, action=action, code=code, days=days, limit=limit, offset=offset)


@router.get("/stats")
def signal_stats(days: int = Query(90, ge=1, le=3650), config: dict[str, Any] = Depends(get_config)):
    return DecisionSignalService(config).stats(days=days)


@router.get("/review/{code}")
def signal_review(code: str, config: dict[str, Any] = Depends(get_config)):
    from src.utils.stock_code import diagnosis_code

    return DecisionSignalService(config).review(diagnosis_code(code))


@router.post("/evaluate")
def evaluate_signals(config: dict[str, Any] = Depends(get_config), tasks: TaskManager = Depends(get_tasks)):
    return tasks.submit(
        "signal_evaluate", lambda: DecisionSignalService(config).evaluate(),
        dedupe_key="signal_evaluate", label="决策信号评估",
    )


@router.get("/{signal_id}")
def get_signal(signal_id: int, config: dict[str, Any] = Depends(get_config)):
    item = DecisionSignalService(config).get(signal_id)
    if not item:
        raise not_found("决策信号不存在")
    return item


@router.put("/{signal_id}/feedback")
def set_feedback(signal_id: int, body: FeedbackBody, config: dict[str, Any] = Depends(get_config)):
    service = DecisionSignalService(config)
    if not service.set_feedback(signal_id, body.feedback, body.note):
        raise not_found("决策信号不存在")
    return service.get(signal_id)
