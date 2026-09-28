"""系统：健康检查、登录、后台任务、数据源状态、AI 与推送设置。"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from api.auth import COOKIE_NAME, AuthStore
from api.deps import bad_request, get_auth, get_config, get_pipeline, get_tasks, not_found
from api.tasks import TaskManager
from src.services.pipeline_service import PipelineService

router = APIRouter(tags=["system"])
MASK = "******"


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


# ---------- 登录 ----------

class PasswordBody(BaseModel):
    password: str


@router.get("/auth/status")
def auth_status(request: Request, auth: AuthStore = Depends(get_auth), config: dict = Depends(get_config)) -> dict[str, Any]:
    web = config.get("web") or {}
    return {"auth_enabled": bool(web.get("auth_enabled")), "password_set": auth.has_password(),
            "logged_in": auth.verify_session(request.cookies.get(COOKIE_NAME))}


@router.post("/auth/login")
def login(body: PasswordBody, response: Response, auth: AuthStore = Depends(get_auth), config: dict = Depends(get_config)) -> dict[str, Any]:
    if not auth.has_password():
        if len(body.password) < 6:
            raise bad_request("首次登录请设置至少 6 位的密码")
        auth.set_password(body.password)
    elif not auth.verify_password(body.password):
        raise bad_request("密码错误")
    days = float((config.get("web") or {}).get("session_days", 7))
    response.set_cookie(COOKIE_NAME, auth.issue_session(days), max_age=int(days * 86400), httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/settings/password")
def change_password(body: ChangePasswordBody, auth: AuthStore = Depends(get_auth)) -> dict[str, Any]:
    if auth.has_password() and not auth.verify_password(body.current_password):
        raise bad_request("当前密码错误")
    if len(body.new_password) < 6:
        raise bad_request("新密码至少 6 位")
    auth.set_password(body.new_password)
    return {"ok": True}


# ---------- 后台任务 ----------

@router.get("/tasks")
def list_tasks(tasks: TaskManager = Depends(get_tasks)) -> list[dict[str, Any]]:
    return tasks.list()


@router.get("/tasks/{task_id}")
def get_task(task_id: str, tasks: TaskManager = Depends(get_tasks)) -> dict[str, Any]:
    task = tasks.get(task_id)
    if task is None:
        raise not_found("任务不存在或已过期")
    return task


# ---------- 大模型用量 ----------

@router.get("/usage")
def llm_usage(days: int = 30, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.analyzers.llm_usage import usage_summary

    llm = config.get("llm") or {}
    return usage_summary(llm.get("usage_path") or llm.get("cache_path", "data/llm_cache.sqlite3"), max(1, min(days, 365)))


# ---------- 数据源 ----------

@router.get("/system/sources")
def data_sources(pipeline: PipelineService = Depends(get_pipeline)) -> list[dict[str, Any]]:
    return pipeline.data_source_status()


# ---------- AI 设置 ----------

def _mask(llm: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(llm)
    for role in ("primary", "backup"):
        key = str((masked.get(role) or {}).get("api_key") or "")
        if key and not key.startswith("your-"):
            masked[role]["api_key"] = MASK + key[-4:]
    return masked


@router.get("/settings/llm")
def get_llm_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.analyzers.llm_platforms import AI_PLATFORMS

    return {"llm": _mask(config.get("llm") or {}), "platforms": AI_PLATFORMS}


class LLMSettingsBody(BaseModel):
    llm: dict[str, Any]


def _merge_llm(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """前端回传的 api_key 仍是掩码时保留原值。"""
    merged = {**current, **incoming}
    for role in ("primary", "backup"):
        if role in incoming:
            new, old = dict(incoming[role] or {}), current.get(role) or {}
            if str(new.get("api_key", "")).startswith(MASK):
                new["api_key"] = old.get("api_key", "")
            merged[role] = {**old, **new}
    return merged


@router.put("/settings/llm")
def save_llm_settings(body: LLMSettingsBody, request: Request, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.config_loader import reload_config
    from src.settings_store import save_section

    from api.app import apply_config

    save_section("llm", _merge_llm(config.get("llm") or {}, body.llm))
    apply_config(request.app, reload_config())
    return {"ok": True}


@router.post("/settings/llm/test")
def test_llm(body: LLMSettingsBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.analyzers.llm_client import LLMClient

    llm = _merge_llm(config.get("llm") or {}, body.llm)
    try:
        reply = LLMClient({**llm, "cache_enabled": False, "backup": {}}).chat("只回复两个字：正常", max_tokens=10)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    return {"ok": bool(reply), "reply": (reply or "")[:50]}


# ---------- 推送设置 ----------

@router.get("/settings/notifier")
def get_notifier_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.notifier import CHANNEL_LABELS, MESSAGE_KINDS

    notifier = copy.deepcopy(config.get("notifier") or {})
    if (notifier.get("email") or {}).get("password"):
        notifier["email"]["password"] = MASK
    return {"notifier": notifier, "channels": CHANNEL_LABELS, "kinds": MESSAGE_KINDS}


class NotifierBody(BaseModel):
    notifier: dict[str, Any]


def _merge_notifier(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**current, **incoming}
    email = incoming.get("email")
    if isinstance(email, dict) and email.get("password") == MASK:
        merged["email"] = {**email, "password": (current.get("email") or {}).get("password", "")}
    return merged


@router.put("/settings/notifier")
def save_notifier(body: NotifierBody, request: Request, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.config_loader import reload_config
    from src.notifier.settings import save_notifier_settings

    from api.app import apply_config

    save_notifier_settings(_merge_notifier(config.get("notifier") or {}, body.notifier))
    apply_config(request.app, reload_config())
    return {"ok": True}


@router.post("/settings/notifier/diagnose")
def diagnose_notifier(body: NotifierBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.notifier import diagnose

    return diagnose({**config, "notifier": _merge_notifier(config.get("notifier") or {}, body.notifier)})


@router.post("/settings/notifier/test/{channel}")
def test_notifier(channel: str, body: NotifierBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.notifier import test_channel

    return test_channel({**config, "notifier": _merge_notifier(config.get("notifier") or {}, body.notifier)}, channel)
