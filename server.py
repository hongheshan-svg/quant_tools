"""
Web / API 服务入口（参考 daily_stock_analysis 的 server.py）

    python server.py                        # 按 config 的 web.host / web.port 启动（默认 127.0.0.1:8000）
    python server.py --host 0.0.0.0 --port 8000
    python server.py --no-scheduler         # 只提供接口，不运行定时任务和聊天机器人（已经在运行 main.py 时用）

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


def use_system_browser_dir() -> None:
    """打包运行时让 Playwright 到系统缓存目录找浏览器。

    Playwright 检测到自己被 PyInstaller 打包时，默认到安装包内找浏览器（PLAYWRIGHT_BROWSERS_PATH=0），
    而后台下载和配置向导用的是系统缓存目录，结果向导显示已安装、采集却启动不了浏览器。
    统一指向系统缓存目录：浏览器不写进只读的安装包，升级后也不用重新下载。已设置该环境变量时不覆盖。
    """
    from src.services.setup_status import default_browsers_dir

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", default_browsers_dir())


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


def self_check() -> int:
    """打包产物自检：实际执行依赖原生库和内置数据文件的代码（缺文件、版本错配只有运行到才会报错），
    全部通过返回 0。打包冒烟测试（scripts/smoke_backend.py）先运行它；输出在 CI 日志里显示，用英文。"""

    def mini_racer() -> None:  # AKShare 的交易日历、新浪行情等接口用它执行 JS
        from py_mini_racer import MiniRacer

        assert MiniRacer().eval("[1, 2, 3].map(x => x * 2).join(',')") == "2,4,6"

    def litellm_prices() -> None:  # 本地价格表（导入时不联网下载）
        from src.analyzers.llm_usage import import_litellm

        assert len(import_litellm().model_cost) > 100

    def pinyin() -> None:  # 股票搜索的拼音首字母
        from src.services.stock_search import name_initials

        assert name_initials("贵州茅台")[0] == "gzmt"

    def strategy_skills() -> None:
        from src.services.strategy_skills import BUILTIN_DIR, load_skills

        assert len(list(BUILTIN_DIR.glob("*.yaml"))) >= 19 and len(load_skills()) >= 19

    def report_templates() -> None:
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        from src.services.report_templates import BUILTIN_DIR

        assert len(list(BUILTIN_DIR.glob("*.md.j2"))) >= 4
        assert ImmutableSandboxedEnvironment().from_string("{{ x }}").render(x=1) == "1"

    def qr_code() -> None:  # 分享图底部二维码
        from src.services.report_image import _qr_data_uri

        assert _qr_data_uri("https://example.com").startswith("data:image/png;base64,")

    failed = 0
    for check in (mini_racer, litellm_prices, pinyin, strategy_skills, report_templates, qr_code):
        try:
            check()
            print(f"ok    {check.__name__}", flush=True)
        except Exception as e:
            failed += 1
            print(f"FAIL  {check.__name__}: {type(e).__name__}: {e}", flush=True)
    print("Self-check passed" if not failed else f"Self-check FAILED ({failed})", flush=True)
    return 1 if failed else 0


def main() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--workdir")
    pre.add_argument("--self-check", action="store_true")
    known, _ = pre.parse_known_args()
    if known.self_check:
        sys.exit(self_check())
    # 打包后没有指定 --workdir 时，以可执行文件所在目录为数据目录（便携运行）
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
    parser.add_argument("--no-scheduler", action="store_true", help="不运行定时任务和聊天机器人")
    parser.add_argument("--workdir", help="存放 config/、data/、logs/ 的目录（默认当前目录）")
    parser.add_argument("--self-check", action="store_true", help="自检打包产物（原生库、内置数据文件）后退出")
    args = parser.parse_args()

    setup_logging(config)
    from src.services.config_check import log_startup_issues

    log_startup_issues(config)
    init_db(config.get("database", {}).get("sqlite_path", "data/quant.db"))
    if args.host not in LOCAL_HOSTS and not web.get("auth_enabled"):
        logger.warning(f"监听 {args.host} 但没有开启 Web 登录：局域网内其他设备的请求会被拒绝，需要时在 web.auth_enabled 开启登录")
    bundle = bundle_dir()
    if bundle:
        use_system_browser_dir()
        threading.Thread(target=install_playwright_browser, name="playwright-install", daemon=True).start()
    app = create_app(config, start_scheduler=False if args.no_scheduler else None,
                     static_dir=bundle / "web" if bundle else None)
    logger.info(f"Web 服务启动：http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
