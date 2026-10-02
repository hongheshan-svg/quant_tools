"""
API 服务入口（参考 daily_stock_analysis 的 api/app.py）：

- /api/v1/*  业务接口（见 api/v1），耗时操作走后台任务（api/tasks.py）
- 其他路径    托管前端构建产物 apps/web/dist（单页应用，找不到的路径返回 index.html）
- 访问控制    api/auth.py：未开启登录时只允许本机访问
- 定时任务    web.scheduler 为 true 时在后台运行与 main.py 相同的定时任务（两者不要同时运行）
启动：python server.py，或 uvicorn api.app:create_app --factory
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from api.auth import COOKIE_NAME, AuthStore, check_request
from api.tasks import TaskManager
from src import __version__
from src.config_loader import load_config

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent / "apps" / "web" / "dist"
DEFAULT_CORS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def apply_config(app: FastAPI, config: dict[str, Any]) -> None:
    """设置保存后把新配置同步给应用内的服务。"""
    app.state.pipeline.config = config
    from src.collectors.request_budget import configure_policy
    configure_policy(config)
    collector = getattr(app.state.pipeline, "collector", None)
    if collector is not None:
        collector.config = config
    app.state.chat_store.config = config
    from src.scheduler import refresh_scheduler
    refresh_scheduler(getattr(app.state, "scheduler", None), config)


def create_app(config: dict[str, Any] | None = None, *, pipeline=None, start_scheduler: bool | None = None,
               static_dir: Path | None = None, auth: AuthStore | None = None) -> FastAPI:
    config = config or load_config()
    web = config.get("web") or {}
    run_scheduler = web.get("scheduler", True) if start_scheduler is None else start_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from src import trading_calendar

        db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
        trading_calendar.load(db_path, refresh=False)
        threading.Thread(target=trading_calendar.load, args=(db_path,), daemon=True).start()
        scheduler = None
        if run_scheduler:
            from apscheduler.schedulers.background import BackgroundScheduler

            from src.scheduler import build_scheduler

            scheduler = build_scheduler(config, BackgroundScheduler())
            scheduler.start()
            app.state.scheduler = scheduler
            logger.info("API 服务已启动定时任务")
            # 聊天机器人跟定时任务一起：--no-scheduler 表示 main.py 在运行，由它负责。
            # 在后台线程里启动：SDK（尤其飞书）导入较慢，不能拖慢服务就绪
            from src.bot.manager import start_bots

            threading.Thread(target=start_bots, args=(config, app.state.pipeline), name="bot-start", daemon=True).start()
            from src.services.fund_registry import refresh_etf_list_background

            refresh_etf_list_background(db_path)  # 股票和 ETF 列表（搜索用）
            # 首页市场概况只在行情采集后计算、存在进程内；启动时先算一次，节假日重启后也有最近交易日的数据
            threading.Thread(target=app.state.pipeline.refresh_market_overview, name="overview-refresh", daemon=True).start()
        yield
        if scheduler:
            app.state.scheduler = None
            scheduler.shutdown(wait=False)
        app.state.tasks.shutdown()
        from src.collectors.source_chain import source_health
        source_health.reset()

    app = FastAPI(title="A股量化交易系统 API", version=__version__, lifespan=lifespan)
    from src.utils.stock_code import StockCodeError

    @app.exception_handler(StockCodeError)
    async def stock_code_error(request: Request, error: StockCodeError):
        return JSONResponse({"detail": str(error)}, status_code=400)
    if pipeline is None:
        from src.services.pipeline_service import PipelineService

        pipeline = PipelineService(config)
        from src.collectors.source_chain import source_health
        from src.collectors.request_budget import configure_policy
        source_health.reset()
        source_health.configure(pipeline.db_path)
        configure_policy(config)
    from src.services.chat_sessions import ChatSessionStore

    app.state.pipeline = pipeline
    app.state.scheduler = None  # lifespan 启动定时任务后赋值
    app.state.background = run_scheduler  # 本进程是否运行定时任务和聊天机器人
    app.state.tasks = TaskManager(workers=int(web.get("task_workers", 4)), db_path=(config.get("database") or {}).get("sqlite_path"))
    app.state.auth = auth or AuthStore()
    app.state.chat_store = ChatSessionStore(config)

    app.add_middleware(CORSMiddleware, allow_origins=web.get("cors_origins") or DEFAULT_CORS,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def auth_guard(request: Request, call_next):
        if request.method != "OPTIONS":
            reason = check_request(app.state.pipeline.config.get("web") or {}, app.state.auth, request.url.path,
                                   request.client.host if request.client else "", request.cookies.get(COOKIE_NAME),
                                   request.headers.get("authorization"))
            if reason:
                return JSONResponse({"detail": reason}, status_code=401)
        return await call_next(request)

    from api.v1 import router

    app.include_router(router)
    _mount_frontend(app, static_dir or DEFAULT_STATIC_DIR)
    return app


def _mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """托管前端：存在的文件直接返回，其余非 API 路径返回 index.html（前端路由）。"""
    root = static_dir.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "接口不存在"}, status_code=404)
        index = root / "index.html"
        if not index.exists():
            return JSONResponse({"detail": "前端还没有构建：cd apps/web && npm install && npm run build"}, status_code=404)
        target = (root / path).resolve()
        if path and target.is_file() and root in target.parents:
            return FileResponse(target)
        return FileResponse(index)
