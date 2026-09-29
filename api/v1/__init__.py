"""API v1 路由汇总。"""

from fastapi import APIRouter

from api.v1 import chat, market, screening, signals, stocks, system, trading, watchlist

router = APIRouter(prefix="/api/v1")
for module in (system, market, stocks, screening, chat, watchlist, trading, signals):
    router.include_router(module.router)
