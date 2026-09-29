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


class WebAuthBody(BaseModel):
    auth_enabled: bool
    password: str = ""


@router.put("/settings/web-auth")
def set_web_auth(body: WebAuthBody, request: Request, response: Response, auth: AuthStore = Depends(get_auth)) -> dict[str, Any]:
    """开关 Web 登录；开启时如果还没有密码，必须同时设置（至少 6 位），并直接登录当前浏览器。"""
    from api.app import apply_config
    from src.config_loader import reload_config
    from src.settings_store import save_section

    if body.auth_enabled and not auth.has_password():
        if len(body.password) < 6:
            raise bad_request("开启登录前请设置至少 6 位的密码")
        auth.set_password(body.password)
    save_section("web", {"auth_enabled": body.auth_enabled}, merge=True)
    config = reload_config()
    apply_config(request.app, config)
    if body.auth_enabled:
        days = float((config.get("web") or {}).get("session_days", 7))
        response.set_cookie(COOKIE_NAME, auth.issue_session(days), max_age=int(days * 86400), httponly=True, samesite="lax")
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
    for role in ("primary", "backup", "vision"):
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
    for role in ("primary", "backup", "vision"):
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


# ---------- 联网搜索设置 ----------

SEARCH_KEY_PROVIDERS = ("bocha", "tavily", "serpapi", "brave")


def _key_list(value: Any) -> list[str]:
    from src.collectors.news_search import _as_list

    return _as_list(value)


def _mask_search(search: dict[str, Any]) -> dict[str, Any]:
    masked = copy.deepcopy(search)
    for name in SEARCH_KEY_PROVIDERS:
        conf = masked.get(name)
        if isinstance(conf, dict):
            conf["api_keys"] = [MASK + k[-4:] for k in _key_list(conf.get("api_keys"))]
    return masked


def _merge_search(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """前端回传掩码 Key 时按后 4 位还原为当前配置里的原值，找不到就丢弃。"""
    merged = {**current, **incoming}
    for name in SEARCH_KEY_PROVIDERS:
        if name not in incoming:
            continue
        new = dict(incoming[name] or {})
        old_keys = _key_list((current.get(name) or {}).get("api_keys"))
        keys: list[str] = []
        for key in _key_list(new.get("api_keys")):
            if key.startswith(MASK):
                key = next((k for k in old_keys if k.endswith(key[len(MASK):])), "")
            if key and key not in keys:
                keys.append(key)
        merged[name] = {**(current.get(name) or {}), **new, "api_keys": keys}
    return merged


class SearchSettingsBody(BaseModel):
    search: dict[str, Any]


class SearchTestBody(SearchSettingsBody):
    query: str = "贵州茅台"


@router.get("/settings/search")
def get_search_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.collectors.news_search import PROVIDERS

    return {"search": _mask_search(config.get("search") or {}), "providers": PROVIDERS}


@router.put("/settings/search")
def save_search_settings(body: SearchSettingsBody, request: Request, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.config_loader import reload_config
    from src.settings_store import save_section

    from api.app import apply_config

    save_section("search", _merge_search(config.get("search") or {}, body.search))
    new_config = reload_config()
    apply_config(request.app, new_config)
    return {"search": _mask_search(new_config.get("search") or {})}


@router.post("/settings/search/test")
def test_search_settings(body: SearchTestBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.collectors import news_search

    merged = {**config, "search": _merge_search(config.get("search") or {}, body.search)}
    return {"results": news_search.test_providers(merged, body.query.strip() or "贵州茅台")}


# ---------- 推送设置 ----------

@router.get("/settings/notifier")
def get_notifier_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.notifier import CHANNEL_FIELDS, CHANNEL_LABELS, MESSAGE_KINDS, secret_fields

    notifier = copy.deepcopy(config.get("notifier") or {})
    for name in CHANNEL_LABELS:
        section = notifier.get(name)
        if not isinstance(section, dict):
            continue
        for key in secret_fields(name):
            if section.get(key):
                section[key] = MASK
    return {"notifier": notifier, "channels": CHANNEL_LABELS, "kinds": MESSAGE_KINDS, "fields": CHANNEL_FIELDS}


class NotifierBody(BaseModel):
    notifier: dict[str, Any]


def _merge_notifier(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    from src.notifier import CHANNEL_LABELS, secret_fields

    merged = {**current, **incoming}
    for name in CHANNEL_LABELS:
        section = incoming.get(name)
        if not isinstance(section, dict):
            continue
        restored = dict(section)
        for key in secret_fields(name):
            if section.get(key) == MASK:
                restored[key] = (current.get(name) or {}).get(key, "")
        merged[name] = restored
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


# ---------- 聊天机器人 ----------

BOT_SECRETS = (("dingtalk", "client_secret"), ("feishu", "app_secret"))


@router.get("/settings/bot")
def get_bot_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.bot.manager import running_bots

    bot = copy.deepcopy(config.get("bot") or {})
    for platform, key in BOT_SECRETS:
        if (bot.get(platform) or {}).get(key):
            bot[platform][key] = MASK
    return {"bot": bot, "running": running_bots()}


class BotBody(BaseModel):
    bot: dict[str, Any]


def _merge_bot(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """前端回传的密钥仍是掩码时保留原值"""
    merged = {**current, **incoming}
    for platform, key in BOT_SECRETS:
        new = incoming.get(platform)
        if isinstance(new, dict):
            old = current.get(platform) or {}
            if new.get(key) == MASK:
                new = {**new, key: old.get(key, "")}
            merged[platform] = {**old, **new}
    return merged


@router.put("/settings/bot")
def save_bot_settings(body: BotBody, request: Request, config: dict = Depends(get_config)) -> dict[str, Any]:
    """保存后启动新启用的机器人；已在运行的机器人改了凭证要重启服务才生效"""
    from src.bot.manager import running_bots, start_bots
    from src.config_loader import reload_config
    from src.settings_store import save_section

    from api.app import apply_config

    save_section("bot", _merge_bot(config.get("bot") or {}, body.bot))
    new_config = reload_config()
    apply_config(request.app, new_config)
    was_running = running_bots()
    started = start_bots(new_config, request.app.state.pipeline) if request.app.state.background else []
    return {"ok": True, "started": started, "restart_required": bool(was_running), "background": request.app.state.background}
