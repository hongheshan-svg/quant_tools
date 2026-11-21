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


@router.get('/integrations/longbridge/status')
def longbridge_status(config: dict = Depends(get_config)):
    from src.services.longbridge_auth import diagnostics
    return diagnostics(config)


@router.get("/integrations/runtime")
def runtime(config: dict = Depends(get_config)):
    from src.analyzers.local_runtime import diagnostics
    return diagnostics(config)


# Codex App Server 只读问股已移除（同步 DSA 88ec4bae）。旧页面或脚本调用时明确返回 410，
# 不让它落到「接口不存在」或 405；本地数据库里的旧会话记录保持原样，不删除。
APP_SERVER_REMOVED = "Codex App Server 只读问股已移除，请使用「AI 问股」；本地 CLI 模型仍可在【设置 → AI 模型】选择"


@router.post("/integrations/runtime/chat", status_code=410)
@router.post("/integrations/runtime/cancel", status_code=410)
def runtime_app_server_removed():
    raise HTTPException(410, APP_SERVER_REMOVED)


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
    from functools import partial
    from src.services.overseas_research import OverseasResearchService
    from src.utils.stock_code import resolve_identity
    try:
        identity = resolve_identity(code)
        if identity.market == "CN":
            raise ValueError("请使用国内证券入口")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return request.app.state.tasks.submit("overseas_quotes", partial(OverseasResearchService(config).refresh, realtime=True), identity.code,
                                          dedupe_key="overseas:" + identity.code)


@router.post("/overseas/{code}/evidence")
def overseas_evidence(code: str, request: Request, config: dict = Depends(get_config)):
    from src.services.overseas_evidence import OverseasEvidenceService
    from src.utils.stock_code import resolve_identity
    try:
        identity = resolve_identity(code)
        if identity.market == 'CN':
            raise ValueError('请使用国内证券入口')
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return request.app.state.tasks.submit('overseas_evidence', OverseasEvidenceService(config).refresh, identity.code,
                                          dedupe_key='overseas_evidence:' + identity.code)


@router.get("/overseas/{code}/social")
def overseas_social(code: str, config: dict = Depends(get_config)):
    from src.services.social_sentiment import social_context
    try:
        return social_context(config, code)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
