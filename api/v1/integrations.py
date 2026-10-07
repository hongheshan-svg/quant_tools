"""扩展服务入口，与现有鉴权和任务队列共用运行边界。"""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from api.deps import get_config

router = APIRouter(tags=["Integrations"])


@router.get("/settings/integrations")
def get_settings(config: dict = Depends(get_config)):
    from src.services.integration_settings import settings, metadata
    return {"settings": settings(config), "groups": metadata()}


@router.put("/settings/integrations")
def put_settings(body: dict, request: Request, config: dict = Depends(get_config)):
    from src.services.integration_settings import merge_settings
    from src.settings_store import save_section
    from api.app import apply_config
    try:
        value = merge_settings(config, body)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    save_section("integrations", value)
    proposed = {**config, "integrations": value}
    apply_config(request.app, proposed)
    return get_settings(proposed)


@router.get("/integrations/macro/operations")
def macro_catalogue():
    from src.services.macro_tools import catalogue
    return catalogue()


class MacroRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


@router.post("/integrations/macro/execute")
def macro_execute(body: MacroRequest, config: dict = Depends(get_config)):
    from src.services.macro_tools import execute
    return execute(config, body.operation, body.arguments)


@router.get("/integrations/runtime")
def runtime(config: dict = Depends(get_config)):
    from src.analyzers.local_runtime import diagnostics
    return diagnostics(config)


class RuntimeQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=20000)
    session_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")


@router.post("/integrations/runtime/chat")
def runtime_chat(body: RuntimeQuestion, request: Request, config: dict = Depends(get_config)):
    from src.services.codex_runtime import create_session, ask, reserve
    try:
        session_id = create_session(config, body.session_id)
        reserve(session_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    task = request.app.state.tasks.submit("codex_runtime", ask, config, body.question, session_id,
                                         dedupe_key="codex:" + session_id)
    return {**task, "session_id": session_id}


class RuntimeCancel(BaseModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")


@router.post("/integrations/runtime/cancel")
def runtime_cancel(body: RuntimeCancel):
    from src.services.codex_runtime import cancel
    return {"ok": cancel(body.session_id)}


@router.get("/integrations/runtime/sessions/{session_id}")
def runtime_session(session_id: str, config: dict = Depends(get_config)):
    from src.services.overseas_research import OverseasResearchService
    result = OverseasResearchService(config).cached("runtime-chat:" + session_id)
    if not result:
        raise HTTPException(404, "本地模型会话不存在")
    return result


@router.get("/integrations/runtime/sessions")
def runtime_sessions(config: dict = Depends(get_config)):
    import json
    from src.database.db import get_db_session
    from src.database.models import ResearchCache
    with get_db_session(config.get("database", {}).get("sqlite_path", "data/quant.db")) as session:
        results = []
        for row in session.query(ResearchCache).filter(ResearchCache.key.startswith("runtime-chat:")).order_by(ResearchCache.updated_at.desc()).limit(100):
            value = json.loads(row.payload_json)
            questions = [m["content"] for m in value.get("messages", []) if m["role"] == "user"]
            results.append({"session_id": row.key.removeprefix("runtime-chat:"), "label": questions[-1][:100] if questions else "未完成会话",
                            "status": value.get("last_run", {}).get("status", "pending")})
        return results


@router.get("/overseas/portfolio")
def overseas_portfolio(config: dict = Depends(get_config)):
    from src.services.overseas_portfolio import OverseasPortfolioService
    return OverseasPortfolioService(config).portfolio()


@router.post("/overseas/portfolio/refresh")
def refresh_portfolio(request: Request, config: dict = Depends(get_config)):
    from src.services.overseas_portfolio import OverseasPortfolioService
    return request.app.state.tasks.submit("overseas_portfolio", OverseasPortfolioService(config).portfolio,
                                          refresh=True, dedupe_key="overseas_portfolio")


class HoldingsImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2_000_000)
    account: str = Field(min_length=1, max_length=100)
    as_of: str
    preview: bool = True


@router.post("/overseas/portfolio/import")
def import_holdings(body: HoldingsImport, config: dict = Depends(get_config)):
    from src.services.overseas_portfolio import OverseasPortfolioService
    try:
        return OverseasPortfolioService(config).import_csv(body.text, body.account, body.as_of, preview=body.preview)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/overseas/futu/symbols")
def futu_symbols(config: dict = Depends(get_config)):
    from src.services.overseas_portfolio import OverseasPortfolioService
    try:
        return OverseasPortfolioService(config).futu_symbols()
    except (ValueError, ImportError) as error:
        raise HTTPException(400, str(error)) from error


@router.get("/overseas/{code}")
def overseas_snapshot(code: str, config: dict = Depends(get_config)):
    from src.services.overseas_research import OverseasResearchService
    try:
        return OverseasResearchService(config).snapshot(code)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post("/overseas/{code}/refresh")
def overseas_refresh(code: str, request: Request, config: dict = Depends(get_config)):
    from src.services.overseas_research import OverseasResearchService
    from src.utils.stock_code import resolve_identity
    try:
        identity = resolve_identity(code)
        if identity.market == "CN":
            raise ValueError("请使用国内证券入口")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return request.app.state.tasks.submit("overseas_quotes", OverseasResearchService(config).refresh, identity.code,
                                          dedupe_key="overseas:" + identity.code)


@router.get("/overseas/{code}/social")
def overseas_social(code: str, config: dict = Depends(get_config)):
    from src.services.social_sentiment import social_context
    try:
        return social_context(config, code)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
