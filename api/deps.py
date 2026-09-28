"""路由共用的依赖：应用级的 PipelineService、任务管理器、鉴权存储。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from api.auth import AuthStore
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService


def get_pipeline(request: Request) -> PipelineService:
    return request.app.state.pipeline


def get_tasks(request: Request) -> TaskManager:
    return request.app.state.tasks


def get_auth(request: Request) -> AuthStore:
    return request.app.state.auth


def get_config(request: Request) -> dict[str, Any]:
    return request.app.state.pipeline.config


def not_found(message: str = "不存在") -> HTTPException:
    return HTTPException(status_code=404, detail=message)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)
