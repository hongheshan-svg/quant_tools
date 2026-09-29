"""
桌面端打包（Windows / macOS / Linux 通用）：Web 前端 → 后台服务（PyInstaller）→ Electron 安装包

    python scripts/build_desktop.py                  # 全部，产物在 apps/desktop/dist/
    python scripts/build_desktop.py --backend-only   # 只打包后台服务到 dist/backend/quant_server/
    python scripts/build_desktop.py --skip-web       # 前端已经构建过
    python scripts/build_desktop.py --dir            # Electron 只输出免安装目录，不生成安装包

后台服务是 server.py 的 onedir 打包，内置默认配置和前端构建产物；桌面端启动它时用 --workdir
指定用户数据目录。Chromium 不打进安装包，后台服务首次启动时自动下载。
需要 Node.js（npm）和装好 requirements.txt 的 Python 环境（用哪个 Python 运行本脚本就用哪个打包）。
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "apps" / "web"
DESKTOP_DIR = ROOT / "apps" / "desktop"
BACKEND_NAME = "quant_server"
BACKEND_DIST = ROOT / "dist" / "backend"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"

# 运行时按字符串导入、PyInstaller 静态分析找不到的模块
HIDDEN_IMPORTS = ["tiktoken_ext", "tiktoken_ext.openai_public", "multipart"]
# 带数据文件（价格表、日历、字典等）的包
COLLECT_DATA = ["litellm", "akshare", "tiktoken_ext", "efinance"]
# 大量按需导入子模块的包
COLLECT_SUBMODULES = ["litellm", "uvicorn"]
# 带原生库的包（akshare 的交易日历等接口用 py_mini_racer 执行 JS）
COLLECT_ALL = ["py_mini_racer"]
# 后台服务用不到的大包（Qt 只有旧桌面端使用）
EXCLUDES = ["PyQt6", "tkinter", "matplotlib", "IPython", "pytest"]


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"$ {' '.join(cmd)}  (cwd={cwd})", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def build_web() -> None:
    if not (WEB_DIR / "node_modules").exists():
        run([NPM, "ci"], WEB_DIR)
    run([NPM, "run", "build"], WEB_DIR)


def pyinstaller_args() -> list[str]:
    web_dist = WEB_DIR / "dist"
    if not (web_dist / "index.html").exists():
        raise SystemExit(f"前端还没有构建：{web_dist}（去掉 --skip-web 重新运行）")
    sep = os.pathsep  # --add-data 的分隔符：Windows 为 ;，其他为 :
    args = [
        str(ROOT / "server.py"),
        "--name", BACKEND_NAME,
        "--onedir",
        "--console",  # 保留标准输出，桌面端把它写进日志；Electron 启动时会隐藏控制台窗口
        "--noconfirm",
        "--clean",
        "--distpath", str(BACKEND_DIST),
        "--workpath", str(ROOT / "build" / BACKEND_NAME),
        "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'config' / 'settings.yaml.example'}{sep}config",
        "--add-data", f"{ROOT / 'config' / 'stock_pool.yaml'}{sep}config",
        "--add-data", f"{web_dist}{sep}web",
    ]
    for name in HIDDEN_IMPORTS:
        args += ["--hidden-import", name]
    for name in COLLECT_DATA:
        args += ["--collect-data", name]
    for name in COLLECT_SUBMODULES:
        args += ["--collect-submodules", name]
    for name in COLLECT_ALL:
        args += ["--collect-all", name]
    for name in EXCLUDES:
        args += ["--exclude-module", name]
    return args


def backend_executable() -> Path:
    exe = f"{BACKEND_NAME}.exe" if sys.platform == "win32" else BACKEND_NAME
    return BACKEND_DIST / BACKEND_NAME / exe


def build_backend() -> None:
    shutil.rmtree(BACKEND_DIST / BACKEND_NAME, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", *pyinstaller_args()])
    exe = backend_executable()
    if not exe.exists():
        raise SystemExit(f"后台服务打包失败：找不到 {exe}")
    with tempfile.TemporaryDirectory() as workdir:  # 冒烟：能导入依赖并解析参数；不在产物目录里留下 config/
        run([str(exe), "--help", "--workdir", workdir])
    print(f"后台服务：{exe}")


def build_electron(unpacked: bool) -> None:
    if not (DESKTOP_DIR / "node_modules").exists():
        run([NPM, "ci"], DESKTOP_DIR)
    run([NPM, "run", "dist:dir" if unpacked else "dist"], DESKTOP_DIR)
    print(f"桌面端：{DESKTOP_DIR / 'dist'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="打包 Electron 桌面端")
    parser.add_argument("--skip-web", action="store_true", help="不重新构建前端")
    parser.add_argument("--backend-only", action="store_true", help="只打包后台服务")
    parser.add_argument("--dir", action="store_true", help="Electron 只输出免安装目录")
    args = parser.parse_args()

    if not args.skip_web:
        build_web()
    build_backend()
    if not args.backend_only:
        build_electron(args.dir)


if __name__ == "__main__":
    main()
