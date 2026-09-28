"""API v1 路由汇总。"""

from fastapi import APIRouter

from api.v1 import chat, market, screening, stocks, system, trading, watchlist

router = APIRouter(prefix="/api/v1")
for module in (system, market, stocks, screening, chat, watchlist, trading):
    router.include_router(module.router)
