"""系统：健康检查、登录、后台任务、数据源状态、AI 与推送设置。"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
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


@router.get("/system/setup")
def system_setup(config: dict = Depends(get_config)) -> dict[str, Any]:
    """首次配置向导：各配置项的完成情况。"""
    from src.services.setup_status import setup_status
    return setup_status(config)


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


# ---------- 定时任务面板 ----------

@router.get("/system/scheduler")
def scheduler_status(request: Request) -> dict[str, Any]:
    from src.scheduler import describe_jobs

    scheduler = getattr(request.app.state, "scheduler", None)
    running = scheduler is not None
    return {
        "running": running,
        "message": "" if running else "本进程没有运行定时任务（--no-scheduler 或 web.scheduler=false），可能由 main.py 负责",
        "jobs": describe_jobs(scheduler),
    }


@router.post("/system/scheduler/{job_id}/run")
def run_job_now(job_id: str, tasks: TaskManager = Depends(get_tasks),
                pipeline: PipelineService = Depends(get_pipeline)) -> dict[str, Any]:
    """立即运行一个定时任务（后台任务）；行情和分析类任务在非交易日会自行跳过"""
    from src.scheduler import JOBS

    if job_id not in JOBS:
        raise not_found("未知的定时任务")
    name, fn = JOBS[job_id]
    return tasks.submit("job", fn, pipeline.config, dedupe_key=f"job:{job_id}", label=f"立即运行：{name}")


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


@router.get("/system/capabilities")
def data_capabilities(config: dict = Depends(get_config)) -> list[dict[str, Any]]:
    """各数据集的回退顺序、配置情况与健康状态"""
    from src.services.data_capabilities import capabilities

    return capabilities(config)


# ---------- AI 设置 ----------

def _mask(llm: dict[str, Any]) -> dict[str, Any]:
    """Key 只留后 4 位：多个 Key 返回列表，单个返回字符串。"""
    from src.analyzers.llm_client import parse_keys

    masked = copy.deepcopy(llm)
    for role in ("primary", "backup", "vision"):
        if not isinstance(masked.get(role), dict):
            continue
        keys = parse_keys(masked[role].get("api_key"))
        if len(keys) > 1:
            masked[role]["api_key"] = [MASK + k[-4:] for k in keys]
        elif keys:
            masked[role]["api_key"] = MASK + keys[0][-4:]
    return masked


@router.get("/settings/llm")
def get_llm_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.analyzers.llm_platforms import AI_PLATFORMS

    return {"llm": _mask(config.get("llm") or {}), "platforms": AI_PLATFORMS}


class LLMSettingsBody(BaseModel):
    llm: dict[str, Any]


def _restore_keys(incoming_key: Any, old_key: Any) -> "str | list[str]":
    """掩码 Key 按后 4 位在当前配置的 Key 里还原（找不到丢弃），新 Key 原样保留；一个存字符串，多个存列表。"""
    from src.analyzers.llm_client import parse_keys

    old_keys = parse_keys(old_key)
    keys: list[str] = []
    for key in parse_keys(incoming_key):
        if key.startswith(MASK):
            key = next((k for k in old_keys if k.endswith(key[len(MASK):])), "")
        if key and key not in keys:
            keys.append(key)
    return keys[0] if len(keys) == 1 else keys if keys else ""


def _merge_llm(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """前端回传的 api_key（字符串或列表）里仍是掩码的按后 4 位还原为原值。"""
    merged = {**current, **incoming}
    for role in ("primary", "backup", "vision"):
        if role in incoming:
            new, old = dict(incoming[role] or {}), current.get(role) or {}
            if "api_key" in new:
                new["api_key"] = _restore_keys(new["api_key"], old.get("api_key"))
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


class LLMModelsBody(BaseModel):
    role: str = "primary"
    config: dict[str, Any] = {}


@router.post("/settings/llm/models")
def llm_models(body: LLMModelsBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    """按表单里的平台、地址和 Key 获取该平台的模型列表（掩码 Key 先还原）。"""
    from src.analyzers.llm_client import list_models

    if body.role not in ("primary", "backup", "vision"):
        raise bad_request("role 只能是 primary、backup 或 vision")
    role_cfg = _merge_llm(config.get("llm") or {}, {body.role: body.config})[body.role]
    try:
        return {"models": list_models(role_cfg)}
    except RuntimeError as e:
        raise bad_request(str(e))


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


# ---------- RSS 资讯源设置 ----------

INTELLIGENCE_DEFAULTS: dict[str, Any] = {
    "enabled": True, "interval_minutes": 30, "max_items_per_source": 30, "keep_days": 7, "sources": [],
}


class IntelligenceSettingsBody(BaseModel):
    intelligence: dict[str, Any]


class IntelligenceTestBody(BaseModel):
    url: str


def _intelligence_with_defaults(section: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**INTELLIGENCE_DEFAULTS, **(section or {})}
    merged["sources"] = [
        {"name": str(s.get("name") or ""), "url": str(s.get("url") or ""), "enabled": s.get("enabled", True) is not False}
        for s in (merged.get("sources") or []) if isinstance(s, dict)
    ]
    return merged


def _int_in_range(value: Any, label: str, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=422, detail=f"{label}必须是整数")
    if value < low or (high is not None and value > high):
        raise HTTPException(status_code=422, detail=f"{label}必须在 {low}~{high} 之间" if high else f"{label}不能小于 {low}")
    return value


def _validate_intelligence(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """校验并补齐资讯源设置，非法时抛 422（中文说明）"""
    merged = {**_intelligence_with_defaults(current), **{k: v for k, v in incoming.items() if k != "sources"}}
    if not isinstance(merged["enabled"], bool):
        raise HTTPException(status_code=422, detail="enabled 必须是布尔值")
    merged["interval_minutes"] = _int_in_range(merged["interval_minutes"], "采集间隔", 5)
    merged["max_items_per_source"] = _int_in_range(merged["max_items_per_source"], "每源条数", 1, 200)
    merged["keep_days"] = _int_in_range(merged["keep_days"], "去重回看天数", 1)
    raw_sources = incoming.get("sources", merged["sources"])
    if not isinstance(raw_sources, list):
        raise HTTPException(status_code=422, detail="sources 必须是列表")
    sources, names = [], set()
    for i, s in enumerate(raw_sources, 1):
        if not isinstance(s, dict):
            raise HTTPException(status_code=422, detail=f"第 {i} 个资讯源格式不正确")
        name, url = str(s.get("name") or "").strip(), str(s.get("url") or "").strip()
        enabled = s.get("enabled", True)
        if not name:
            raise HTTPException(status_code=422, detail=f"第 {i} 个资讯源的名称不能为空")
        if name in names:
            raise HTTPException(status_code=422, detail=f"资讯源名称重复：{name}")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail=f"资讯源「{name}」的地址必须以 http:// 或 https:// 开头")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=422, detail=f"资讯源「{name}」的 enabled 必须是布尔值")
        names.add(name)
        sources.append({"name": name, "url": url, "enabled": enabled})
    merged["sources"] = sources
    return merged


@router.get("/settings/intelligence")
def get_intelligence_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.settings_store import read_settings

    # 以 settings.yaml 里已保存的为准（内存配置可能是保存前的缓存）
    saved = read_settings().get("intelligence")
    return {"intelligence": _intelligence_with_defaults(saved if isinstance(saved, dict) else config.get("intelligence"))}


@router.put("/settings/intelligence")
def save_intelligence_settings(body: IntelligenceSettingsBody, request: Request, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.config_loader import reload_config
    from src.settings_store import read_settings, save_section

    from api.app import apply_config

    save_section("intelligence", _validate_intelligence(body.intelligence, config.get("intelligence") or {}))
    new_config = reload_config()
    apply_config(request.app, new_config)
    return {"intelligence": _intelligence_with_defaults(read_settings().get("intelligence"))}


@router.post("/settings/intelligence/test")
def test_intelligence_source(body: IntelligenceTestBody, config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.collectors import rss

    return rss.test_feed(body.url, config)


# ---------- 推送设置 ----------

@router.get("/settings/notifier")
def get_notifier_settings(config: dict = Depends(get_config)) -> dict[str, Any]:
    from src.notifier import CHANNEL_FIELDS, CHANNEL_LABELS, IMAGE_CHANNELS, MESSAGE_KINDS, secret_fields

    notifier = copy.deepcopy(config.get("notifier") or {})
    for name in CHANNEL_LABELS:
        section = notifier.get(name)
        if not isinstance(section, dict):
            continue
        for key in secret_fields(name):
            if section.get(key):
                section[key] = MASK
    return {"notifier": notifier, "channels": CHANNEL_LABELS, "kinds": MESSAGE_KINDS, "fields": CHANNEL_FIELDS,
            "image_channels": sorted(IMAGE_CHANNELS)}


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


# ---------- 配置备份与恢复 ----------

class ImportBody(BaseModel):
    yaml: str


@router.get("/settings/export")
def export_settings_file(include_secrets: bool = False) -> PlainTextResponse:
    from datetime import date

    from src.settings_store import export_settings

    filename = f"settings-{date.today():%Y%m%d}.yaml"
    return PlainTextResponse(export_settings(include_secrets), media_type="text/yaml; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/settings/import")
def import_settings_file(body: ImportBody, request: Request) -> dict[str, Any]:
    """导入配置覆盖 settings.yaml；值为 ****** 的密钥沿用当前配置"""
    from src.config_loader import reload_config
    from src.settings_store import import_settings

    from api.app import apply_config

    try:
        result = import_settings(body.yaml)
    except ValueError as e:
        raise bad_request(str(e))
    apply_config(request.app, reload_config())
    return result
