"""
打包后的后台服务冒烟测试：启动 dist/backend/quant_server，确认健康检查和几个依赖内置数据文件的接口能返回。

    python scripts/smoke_backend.py            # 默认 dist/backend/quant_server/quant_server(.exe)
    python scripts/smoke_backend.py --exe PATH

缺文件、缺隐藏导入只有运行到那段代码才会报错，所以除了健康检查，还请求了用到 AKShare、平台预设、
内置策略、内置报告模板的接口。用临时数据目录运行，不启动定时任务，不需要联网。失败时打印后台日志并返回 1。
"""

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXE = ROOT / "dist" / "backend" / "quant_server" / ("quant_server.exe" if sys.platform == "win32" else "quant_server")
# 依次请求：健康检查 → 首页（行情采集器、AKShare）→ 大模型设置（平台预设）→ 问股策略（内置 YAML）→ 报告模板（内置 Jinja2）
PATHS = ["/api/v1/health", "/api/v1/dashboard", "/api/v1/settings/llm", "/api/v1/chat/skills", "/api/v1/settings/templates", "/"]
STARTUP_TIMEOUT = 180


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, timeout: float = 30) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status


def main() -> int:
    parser = argparse.ArgumentParser(description="打包后台服务冒烟测试")
    parser.add_argument("--exe", default=str(DEFAULT_EXE))
    args = parser.parse_args()
    exe = Path(args.exe)
    if not exe.exists():
        print(f"找不到后台程序：{exe}")
        return 1

    workdir = Path(tempfile.mkdtemp(prefix="quant-smoke-"))
    log_path = workdir / "server.log"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen([str(exe), "--host", "127.0.0.1", "--port", str(port), "--no-scheduler", "--workdir", str(workdir)],
                                stdout=log, stderr=subprocess.STDOUT, cwd=exe.parent)
    ok = False
    try:
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"后台服务提前退出，代码 {proc.returncode}")
                break
            try:
                if get(base + PATHS[0], timeout=3) == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            print(f"{STARTUP_TIMEOUT} 秒内没有就绪")
        if proc.poll() is None:
            failures = []
            for path in PATHS:
                try:
                    status = get(base + path)
                except Exception as e:
                    status = f"{type(e).__name__}: {e}"
                print(f"{path} -> {status}")
                if status != 200:
                    failures.append(path)
            ok = not failures
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not ok:
        print("----- 后台日志 -----")
        print(log_path.read_text(encoding="utf-8", errors="replace")[-8000:])
    shutil.rmtree(workdir, ignore_errors=True)
    print("冒烟测试通过" if ok else "冒烟测试失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
