"""App Server 只读研究会话。恢复使用本地已完成消息，禁止重放失败的工具动作。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from src.analyzers.local_runtime import child_env, terminate, runtime_config, MAX_OUTPUT
from src.services.overseas_research import OverseasResearchService

SAFE_TOOLS = {"context_pack", "resolve_stock", "quote", "daily_bars", "diagnosis", "watchlist", "screening"}
PROFILE = "quant_research"
_active: dict[str, threading.Event] = {}
_running: set[str] = set()
_lock = threading.Lock()


def reserve(session_id: str) -> None:
    with _lock:
        if session_id in _active:
            return
        if len(_active) >= 2:
            raise RuntimeError("本地模型已有两个会话等待处理")
        _active[session_id] = threading.Event()


def create_session(config: dict, session_id: str | None = None) -> str:
    store = OverseasResearchService(config)
    if session_id:
        if not store.cached("runtime-chat:" + session_id):
            raise ValueError("本地模型会话不存在")
        return session_id
    session_id = uuid.uuid4().hex
    store.save_cache("runtime-chat:" + session_id, {"messages": [], "tools_version": 1})
    return session_id


def cancel(session_id: str) -> bool:
    with _lock:
        event = _active.get(session_id)
        if event:
            event.set()
        return event is not None


class AppServer:
    def __init__(self, config: dict, folder: Path, event: threading.Event):
        self.config, self.event = config, event
        self.folder = folder / "workspace"
        self.folder.mkdir(mode=0o700)
        cfg = runtime_config(config)
        self.deadline = time.monotonic() + min(3600, max(1, int(cfg.get("timeout_seconds", 300))))
        executable = shutil.which(str(cfg.get("codex_cli") or "codex"))
        if not executable:
            raise RuntimeError("未安装 Codex 或找不到可执行文件")
        # 临时配置只继承登录凭据，不加载主机 hooks、MCP、插件、skills 或仓库指令。
        home = folder / "codex-home"
        home.mkdir(mode=0o700)
        original = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        auth = original / "auth.json"
        if auth.is_file():
            shutil.copyfile(auth, home / "auth.json")
            (home / "auth.json").chmod(0o600)
        env = child_env()
        env["CODEX_HOME"] = str(home)
        overrides = [f'default_permissions="{PROFILE}"', f'permissions.{PROFILE}.filesystem={{":minimal"="read",":workspace_roots"={{"."="read"}}}}',
                     f'permissions.{PROFILE}.network.enabled=false', 'features.apps=false', 'features.plugins=false',
                     'web_search="disabled"']
        command = [executable, "app-server"]
        for value in overrides:
            command.extend(["-c", value])
        self.process = subprocess.Popen(command, cwd=self.folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=os.name == "posix")
        self.messages = queue.Queue(maxsize=1024)
        self.counter, self.bytes_read = 0, 0
        self.notifications = []
        self.calls = []
        self.thread_id = ""
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_OUTPUT + 1)
                self.bytes_read += len(line)
                if not line:
                    self.messages.put_nowait({"fatal": "运行进程提前结束"})
                    return
                if self.bytes_read > MAX_OUTPUT:
                    self.messages.put_nowait({"fatal": "运行输出超过限制"})
                    return
                self.messages.put_nowait(json.loads(line))
        except (ValueError, queue.Full, OSError):
            terminate(self.process)

    def send(self, payload: dict):
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        self.process.stdin.flush()

    def next(self) -> dict:
        while True:
            if self.event.is_set():
                raise RuntimeError("本地模型会话已取消")
            if time.monotonic() >= self.deadline:
                raise RuntimeError("本地模型会话超时")
            try:
                message = self.messages.get(timeout=.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError("本地模型程序退出，请检查登录和协议版本")
                continue
            if message.get("fatal"):
                raise RuntimeError(message["fatal"])
            if "method" in message and "id" in message:
                self._tool(message)
                continue
            return message

    def request(self, method: str, params: dict) -> dict:
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.next()
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("App Server 协议或登录失败，请更新本地 Codex 后重试")
                return message.get("result") or {}
            self.notifications.append(message)

    def _tool(self, message: dict):
        if message["method"] != "item/tool/call":
            self.send({"id": message["id"], "error": {"code": -32601, "message": "此会话不接受授权、交互或系统工具请求"}})
            raise RuntimeError("模型请求了未开放的权限或工具，会话已停止")
        params = message.get("params") or {}
        name = params.get("tool")
        if name not in SAFE_TOOLS or params.get("threadId") != self.thread_id or len(self.calls) >= 12:
            raise RuntimeError("模型工具超出只读范围或调用次数限制")
        from src.services.chat_tools import ChatTools, native_tool_schemas
        from jsonschema import validate
        args = params.get("arguments") or {}
        schema = next(s["function"]["parameters"] for s in native_tool_schemas() if s["function"]["name"] == name)
        try:
            validate(args, schema)
            content = ChatTools(self.config).call(name, args)
            success = not content.startswith(("工具执行失败", "没有名为", "工具被拒绝"))
        except Exception:
            content, success = "工具参数无效或读取失败", False
        self.calls.append({"name": name, "success": success})
        self.send({"id": message["id"], "result": {"contentItems": [{"type": "inputText", "text": content}], "success": success}})

    def run(self, question: str, history: list[dict]) -> str:
        from src.services.chat_tools import native_tool_schemas
        self.request("initialize", {"clientInfo": {"name": "quant-research", "version": "1.0"}, "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized", "params": {}})
        tools = [{"type": "function", "name": s["function"]["name"], "description": s["function"]["description"],
                  "inputSchema": s["function"]["parameters"]} for s in native_tool_schemas() if s["function"]["name"] in SAFE_TOOLS]
        result = self.request("thread/start", {"approvalPolicy": "never", "cwd": str(self.folder), "ephemeral": True,
            "permissions": PROFILE, "environments": [], "runtimeWorkspaceRoots": [str(self.folder)], "dynamicTools": tools,
            "baseInstructions": "你是只读证券研究助手。只使用列出的研究工具，不读写文件、不运行命令、不调用未注册工具。缺失与过期证据必须说明，不执行交易。"})
        if (result.get("activePermissionProfile") or {}).get("id") != PROFILE:
            raise RuntimeError("App Server 未启用指定只读权限，会话已停止")
        self.thread_id = (result.get("thread") or {}).get("id", "")
        if not self.thread_id:
            raise RuntimeError("App Server 未返回会话标识")
        if history:
            self.request("thread/inject_items", {"threadId": self.thread_id, "items": [{"type": "message", "role": row["role"],
                "content": [{"type": "input_text" if row["role"] == "user" else "output_text", "text": row["content"]}]} for row in history[-20:]]})
        started = self.request("turn/start", {"threadId": self.thread_id, "input": [{"type": "text", "text": question}],
            "approvalPolicy": "never", "permissions": PROFILE, "environments": []})
        turn_id = (started.get("turn") or {}).get("id")
        answers = []
        while True:
            message = self.notifications.pop(0) if self.notifications else self.next()
            params = message.get("params") or {}
            if params.get("threadId") != self.thread_id:
                continue
            item = params.get("item") or {}
            if message.get("method") == "item/completed" and params.get("turnId") == turn_id:
                if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                    answers.append(item.get("text", ""))
                elif item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "webSearch"):
                    raise RuntimeError("模型使用了未开放的系统工具，会话已停止")
            if message.get("method") == "turn/completed" and (params.get("turn") or {}).get("id") == turn_id:
                if params["turn"].get("status") != "completed":
                    raise RuntimeError("本地模型未完成回答")
                text = "\n".join(answers).strip()
                if not text:
                    raise RuntimeError("本地模型返回空回答")
                return text

    def close(self):
        terminate(self.process)
        self.reader.join(timeout=2)
        for handle in (self.process.stdin, self.process.stdout):
            if handle:
                handle.close()


def ask(config: dict, question: str, session_id: str) -> dict:
    store = OverseasResearchService(config)
    saved = store.cached("runtime-chat:" + session_id)
    if not saved:
        raise ValueError("本地模型会话不存在")
    with _lock:
        if session_id in _running or len(_running) >= 2:
            raise RuntimeError("本地模型会话忙，请等待当前回答完成")
        event = _active.setdefault(session_id, threading.Event())
        _running.add(session_id)
    runtime = None
    try:
        if event.is_set():
            raise RuntimeError("本地模型会话已取消")
        with tempfile.TemporaryDirectory(prefix="quant-app-server-") as temporary:
            try:
                runtime = AppServer(config, Path(temporary), event)
                answer = runtime.run(question, saved["messages"])
                saved["messages"] = (saved["messages"] + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}])[-20:]
                saved["last_run"] = {"status": "completed", "tools": runtime.calls, "resumed": bool(len(saved["messages"]) > 2)}
                store.save_cache("runtime-chat:" + session_id, saved)
                return {"session_id": session_id, "answer": answer, "tools": runtime.calls}
            finally:
                if runtime:
                    runtime.close()
    except Exception:
        saved["last_run"] = {"status": "cancelled" if event.is_set() else "failed", "tools": runtime.calls if runtime else []}
        store.save_cache("runtime-chat:" + session_id, saved)
        raise
    finally:
        with _lock:
            _active.pop(session_id, None)
            _running.discard(session_id)
