"""
Web / API 服务入口（参考 daily_stock_analysis 的 server.py）

    python server.py                        # 按 config 的 web.host / web.port 启动（默认 127.0.0.1:8000）
    python server.py --host 0.0.0.0 --port 8000
    python server.py --no-scheduler         # 只提供接口，不运行定时任务（已经在运行 main.py 时用）

    python server.py --workdir D:/quant      # 以指定目录存放 config/、data/、logs/

同时托管前端（apps/web/dist），浏览器打开 http://127.0.0.1:8000 即可使用。
Electron 桌面端（apps/desktop）会用 --port 指定空闲端口、--workdir 指定用户数据目录启动它。
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from loguru import logger  # noqa: E402

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def bundle_dir() -> Path | None:
    """PyInstaller 打包后内置资源（默认配置、前端）所在目录；源码运行时为 None"""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


def prepare_workdir(workdir: str | Path, bundle: Path | None = None) -> None:
    """切换到数据目录，并把内置的默认配置放进去。

    示例配置每次覆盖（新版本新增的配置项靠它提供默认值），股票池规则只在缺失时复制，
    不覆盖用户改过的版本。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)
    Path("config").mkdir(exist_ok=True)
    if bundle is None:
        return
    example = bundle / "config" / "settings.yaml.example"
    if example.exists():
        shutil.copyfile(example, "config/settings.yaml.example")
    pool = bundle / "config" / "stock_pool.yaml"
    if pool.exists() and not Path("config/stock_pool.yaml").exists():
        shutil.copyfile(pool, "config/stock_pool.yaml")


def install_playwright_browser() -> None:
    """下载 Playwright 的 Chromium（东方财富、同花顺、社交平台采集需要），已安装时很快返回。

    打包后用户没法自己执行 playwright install，所以由服务在后台完成。
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        result = subprocess.run(
            [*compute_driver_executable(), "install", "chromium"], env=get_driver_env(),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            logger.warning(f"Chromium 下载失败，依赖浏览器的采集会改用其他数据源：{(result.stderr or result.stdout)[-500:]}")
        elif result.stdout.strip():
            logger.info("Chromium 已下载")
    except Exception as e:
        logger.warning(f"Chromium 下载失败，依赖浏览器的采集会改用其他数据源：{e}")


def main() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--workdir")
    known, _ = pre.parse_known_args()
    # 打包后没有指定 --workdir 时，以可执行文件所在目录为数据目录（和旧版 EXE 一致）
    workdir = known.workdir or (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None)
    if workdir:
        prepare_workdir(workdir, bundle_dir())

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
    parser.add_argument("--workdir", help="存放 config/、data/、logs/ 的目录（默认当前目录）")
    args = parser.parse_args()

    setup_logging(config)
    init_db(config.get("database", {}).get("sqlite_path", "data/quant.db"))
    if args.host not in LOCAL_HOSTS and not web.get("auth_enabled"):
        logger.warning(f"监听 {args.host} 但没有开启 Web 登录：局域网内其他设备的请求会被拒绝，需要时在 web.auth_enabled 开启登录")
    bundle = bundle_dir()
    if bundle:
        threading.Thread(target=install_playwright_browser, name="playwright-install", daemon=True).start()
    app = create_app(config, start_scheduler=False if args.no_scheduler else None,
                     static_dir=bundle / "web" if bundle else None)
    logger.info(f"Web 服务启动：http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
