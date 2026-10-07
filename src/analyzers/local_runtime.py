"""受约束的本地生成后端：独立目录、固定参数、输出上限及进程清理。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

PROVIDERS = ("codex_cli", "claude_code_cli", "opencode_cli", "hermes")
EXECUTABLES = {"codex_cli": "codex", "claude_code_cli": "claude", "opencode_cli": "opencode"}
MAX_OUTPUT = 1024 * 1024
_slots = threading.BoundedSemaphore(1)


def runtime_config(config: dict | None = None) -> dict:
    if config is None:
        from src.config_loader import load_config
        config = load_config()
    return ((config.get("integrations") or {}).get("runtime") or {})


def validate_hermes_url(url: str) -> str:
    value = urlsplit(url)
    if (value.scheme not in ("http", "https") or value.hostname not in ("localhost", "127.0.0.1", "::1")
            or value.username or value.password or value.query or value.fragment or value.path.rstrip("/") != "/v1"):
        raise ValueError("Hermes 仅支持本机 /v1 地址，不接受认证、查询参数或远程主机")
    return url.rstrip("/")


def validate_model_settings(llm: dict) -> None:
    """保存与导入共用，不启动程序也不联网。"""
    from src.analyzers.llm_client import parse_keys
    for role in ("primary", "backup", "vision"):
        section = llm.get(role) or {}
        provider = str(section.get("provider") or "").lower()
        if provider not in PROVIDERS:
            continue
        if parse_keys(section.get("api_key")):
            raise ValueError("本地后端不接收模型 API Key，请使用本地登录或服务配置")
        if role == "vision":
            raise ValueError("本地后端尚不支持图片识别，请为视觉模型选择其他平台")
        if provider == "hermes" and section.get("base_url"):
            validate_hermes_url(str(section["base_url"]))


def child_env() -> dict:
    allowed = {"PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "CODEX_HOME",
               "SYSTEMROOT", "WINDIR", "PATHEXT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
               "SSL_CERT_FILE", "SSL_CERT_DIR"}
    return {k: v for k, v in os.environ.items() if k in allowed or k.startswith("LC_")}


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def diagnostics(config: dict) -> list[dict]:
    cfg = runtime_config(config)
    result = [{"provider": name, "available": bool(shutil.which(str(cfg.get(name) or default))),
               "capabilities": ["generation"], "reason": "已安装，登录状态需调用验证" if shutil.which(str(cfg.get(name) or default)) else "未找到本地程序"}
              for name, default in EXECUTABLES.items()]
    result.append({"provider": "codex_app_server", "available": result[0]["available"],
                   "capabilities": ["generation", "read_only_tools", "resume", "cancel"], "reason": "共用 Codex；协议兼容性需调用验证"})
    try:
        validate_hermes_url(cfg.get("hermes_url", "http://127.0.0.1:8642/v1"))
        reason = "本机地址有效，服务连通性需调用验证"
    except ValueError as error:
        reason = str(error)
    result.append({"provider": "hermes", "available": None, "capabilities": ["generation"], "reason": reason})
    return result


def _command(provider: str, executable: str, model: str, folder: Path) -> list[str]:
    model_args = ["--model", model] if model and model != "default" else []
    if provider == "codex_cli":
        return [executable, "--ask-for-approval", "never", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                "--color", "never", "--ephemeral", *model_args, "--output-last-message", str(folder / "answer.txt"), "-"]
    if provider == "claude_code_cli":
        return [executable, "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
                "--no-session-persistence", "--output-format", "json", *model_args, "-p", "仅根据标准输入生成回答，不调用工具。"]
    if provider == "opencode_cli":
        (folder / "opencode.json").write_text(json.dumps({"share": "disabled", "autoupdate": False,
            "snapshot": False, "mcp": {}, "plugin": [], "instructions": [], "permission": {"*": "deny"},
            "tools": {k: False for k in ("bash", "edit", "write", "read", "glob", "grep", "webfetch", "websearch", "task")}}))
        return [executable, "--pure", "run", "--format", "json", *model_args, "仅根据附件回答，不调用工具。", "--file", str(folder / "prompt.txt")]
    raise ValueError("未知本地运行后端")


def generate(provider: str, model: str, messages: list[dict], *, config: dict | None = None,
             base_url: str | None = None, cancel_event=None) -> str:
    if any(not isinstance(m.get("content"), str) for m in messages):
        raise ValueError("此后端仅支持文本生成")
    cfg = runtime_config(config)
    timeout = min(3600, max(1, int(cfg.get("timeout_seconds", 300))))
    if provider == "hermes":
        import httpx
        url = validate_hermes_url(base_url or cfg.get("hermes_url", "http://127.0.0.1:8642/v1"))
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.post(url + "/chat/completions", json={"model": model or "hermes-agent", "messages": messages, "stream": False})
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"])
    if provider not in EXECUTABLES:
        raise ValueError("未知本地生成后端")
    executable = shutil.which(str(cfg.get(provider) or EXECUTABLES[provider]))
    if not executable:
        raise RuntimeError("本地模型程序未安装或不可执行")
    if not _slots.acquire(timeout=min(timeout, 5)):
        raise RuntimeError("本地生成忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="quant-generation-") as temporary:
            folder = Path(temporary)
            prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
            (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
            env = child_env()
            if provider == "opencode_cli":
                env.update({k: "true" for k in ("OPENCODE_DISABLE_DEFAULT_PLUGINS", "OPENCODE_DISABLE_CLAUDE_CODE",
                    "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_LSP_DOWNLOAD")})
            with open(folder / "prompt.txt", "rb") as stdin, open(folder / "stdout", "w+b") as out, open(folder / "stderr", "w+b") as err:
                process = subprocess.Popen(_command(provider, executable, model, folder), cwd=folder, env=env,
                    stdin=stdin, stdout=out, stderr=err, start_new_session=os.name == "posix")
                try:
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        if cancel_event and cancel_event.is_set():
                            raise RuntimeError("生成已取消")
                        if time.monotonic() >= deadline:
                            raise RuntimeError("本地模型调用超时")
                        if sum(p.stat().st_size for p in folder.iterdir() if p.name in ("stdout", "stderr", "answer.txt")) > MAX_OUTPUT:
                            raise RuntimeError("本地模型输出超过限制")
                        time.sleep(.03)
                    if process.returncode:
                        raise RuntimeError("本地模型调用失败，请检查登录状态和程序版本")
                finally:
                    terminate(process)
            target = folder / ("answer.txt" if provider == "codex_cli" else "stdout")
            if not target.exists() or target.stat().st_size > MAX_OUTPUT:
                raise RuntimeError("本地模型输出缺失或超过限制")
            text = target.read_text(encoding="utf-8").strip()
            if provider == "claude_code_cli":
                result = json.loads(text)
                if result.get("is_error"):
                    raise RuntimeError("Claude Code 返回失败")
                text = result.get("result", "")
            elif provider == "opencode_cli":
                events = [json.loads(line) for line in text.splitlines() if line.strip()]
                if any(e.get("type") in ("error", "permission", "permission_request") for e in events):
                    raise RuntimeError("OpenCode 请求失败或需要授权")
                text = "".join(e.get("part", {}).get("text", "") for e in events if e.get("type") == "text")
            if not text:
                raise RuntimeError("本地模型返回空结果")
            return text
    finally:
        _slots.release()
