"""
Web / API 服务入口（参考 daily_stock_analysis 的 server.py）

    python server.py                        # 按 config 的 web.host / web.port 启动（默认 127.0.0.1:8000）
    python server.py --host 0.0.0.0 --port 8000
    python server.py --no-scheduler         # 只提供接口，不运行定时任务（已经在运行 main.py 时用）

同时托管前端（apps/web/dist），浏览器打开 http://127.0.0.1:8000 即可使用。
Electron 桌面端会用 --port 指定空闲端口启动它。
"""

import argparse
import io
import os
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

if getattr(sys, "frozen", False):  # PyInstaller 打包后，以可执行文件所在目录为工作目录
    os.chdir(os.path.dirname(sys.executable))

from loguru import logger  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    import uvicorn

    from api.app import create_app
    from main import setup_logging
    from src.config_loader import load_config
    from src.database.db import init_db

    config = load_config()
    web = config.get("web") or {}
    parser = argparse.ArgumentParser(description="A股量化交易系统 Web / API 服务")
    parser.add_argument("--host", default=web.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(web.get("port", 8000)))
    parser.add_argument("--no-scheduler", action="store_true", help="不运行定时任务")
    args = parser.parse_args()

    setup_logging(config)
    init_db(config.get("database", {}).get("sqlite_path", "data/quant.db"))
    if args.host not in LOCAL_HOSTS and not web.get("auth_enabled"):
        logger.warning(f"监听 {args.host} 但没有开启 Web 登录：局域网内其他设备的请求会被拒绝，需要时在 web.auth_enabled 开启登录")
    app = create_app(config, start_scheduler=False if args.no_scheduler else None)
    logger.info(f"Web 服务启动：http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
