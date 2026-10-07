"""诊断运行日志：记录一次诊断各步骤（取数、模型调用、护栏调整）的成败与耗时。

RunLog 由 diagnose() 创建并逐层传递，不保存在服务实例上（诊断会被多线程共用同一个服务）。
"""

from __future__ import annotations

import time
import threading
import uuid
from datetime import datetime, timedelta
from loguru import logger
from contextvars import ContextVar
from functools import wraps
from contextlib import contextmanager
from typing import Any, Iterator
from src.utils.redaction import redact_text

TRACE_ID = ContextVar("research_trace_id", default="")
EVENT_SINK = ContextVar("research_event_sink", default=None)
ACTIVE_LOG = ContextVar("research_run_log", default=None)


def emit_event(event: dict, sink=None) -> None:
    """观察者失败不能改变业务结果；仅记录脱敏警告。"""
    target = sink if sink is not None else EVENT_SINK.get()
    if target is not None:
        try:
            from src.utils.redaction import redact
            target(redact(event))
        except Exception as error:
            logger.warning("运行事件接收失败：{}", redact_text(error, 160))


@contextmanager
def execution_trace(trace_id: str, sink=None):
    trace_token, sink_token = TRACE_ID.set(trace_id), EVENT_SINK.set(sink)
    try:
        yield
    finally:
        TRACE_ID.reset(trace_token)
        EVENT_SINK.reset(sink_token)


def run_scope(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        token = ACTIVE_LOG.set(None)
        try:
            return fn(*args, **kwargs)
        finally:
            ACTIVE_LOG.reset(token)
    return wrapped


def provider_attempt(provider: str, model: str, attempt: int, ok: bool, ms: float, detail: str = ""):
    log = ACTIVE_LOG.get()
    if log is not None:
        log._append(f"模型请求 {provider} / {model}（尝试 {attempt}）", ok, ms, detail, "llm")


MAX_STEPS = 60
MAX_DETAIL = 200


class _Step:
    """step() 上下文里可写的对象，用来设置 detail；不抛异常也可以把 ok 设为 False 记为失败。"""

    def __init__(self) -> None:
        self.detail = ""
        self.ok = True


class RunLog:
    """记录步骤并发送实时事件；并发分析员共享同一轨迹。"""

    def __init__(self, activate: bool = True) -> None:
        self._steps: list[dict[str, Any]] = []
        self._started = time.perf_counter()
        self.model = ""
        self.truncated = False
        self.trace_id = TRACE_ID.get() or uuid.uuid4().hex
        self._lock = threading.RLock()
        self._sink = EVENT_SINK.get()
        if activate:
            ACTIVE_LOG.set(self)

    def _append(self, name: str, ok: bool, ms: int, detail: str, kind: str, metadata: dict | None = None) -> None:
        event = {"name": redact_text(name, MAX_DETAIL), "kind": kind, "ok": bool(ok), "ms": int(ms), "detail": redact_text(detail, MAX_DETAIL)}
        if metadata is not None:
            from src.utils.redaction import redact
            event["metadata"] = redact(metadata)
        with self._lock:
            if len(self._steps) >= MAX_STEPS:
                self.truncated = True
                return
            ended = datetime.now()
            event.update(id=f"{self.trace_id}:step:{len(self._steps) + 1}",
                         started_at=(ended - timedelta(milliseconds=max(ms, 0))).isoformat(), ended_at=ended.isoformat())
            self._steps.append(event)
        if self._sink:
            emit_event({"type": "step", "trace_id": self.trace_id, **event}, self._sink)

    @contextmanager
    def step(self, name: str) -> Iterator[_Step]:
        """记录一个取数步骤；块内异常照常抛出，步骤记为失败。"""
        s = _Step()
        start = time.perf_counter()
        try:
            yield s
        except BaseException as e:
            self._append(name, False, (time.perf_counter() - start) * 1000, str(e) or type(e).__name__, "data")
            raise
        self._append(name, s.ok, (time.perf_counter() - start) * 1000, s.detail, "data")

    def add(self, name: str, ok: bool = True, ms: int = 0, detail: str = "") -> None:
        self._append(name, ok, ms, detail, "data")

    def llm(self, name: str, model: str, ok: bool, ms: int) -> None:
        """记录一次模型调用。"""
        if model and not self.model:
            self.model = redact_text(model)
        self._append(name, ok, ms, model or "", "llm")

    def note(self, text: str) -> None:
        """记录护栏调整等说明。"""
        self._append(text, True, 0, text, "note")  # name 与 detail 都放说明文字，界面只显示一次

    def data_attempt(self, dataset: str, attempt: dict) -> None:
        from src.utils.redaction import redact
        metadata = redact({"dataset": dataset, **attempt})
        self._append(f"{dataset} · {attempt['source']}", attempt["ok"], attempt.get("ms", 0),
                     str(attempt.get("error") or ""), "provider", metadata)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "version": 1, "truncated": self.truncated,
            "trace_id": self.trace_id,
            "steps": list(self._steps),
            "total_ms": int((time.perf_counter() - self._started) * 1000),
            "model": self.model,
        }
        from src.services.run_diagnostics import snapshot
        result["diagnostics"] = snapshot(result)
        return result
