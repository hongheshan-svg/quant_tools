"""诊断运行日志：记录一次诊断各步骤（取数、模型调用、护栏调整）的成败与耗时。

RunLog 由 diagnose() 创建并逐层传递，不保存在服务实例上（诊断会被多线程共用同一个服务）。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

MAX_STEPS = 60
MAX_DETAIL = 200


class _Step:
    """step() 上下文里可写的对象，用来设置 detail。"""

    def __init__(self) -> None:
        self.detail = ""


class RunLog:
    """按顺序记录步骤；线程内使用，不做并发保护。"""

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = []
        self._started = time.perf_counter()
        self.model = ""

    def _append(self, name: str, ok: bool, ms: int, detail: str, kind: str) -> None:
        if len(self._steps) >= MAX_STEPS:
            return
        self._steps.append({"name": name, "kind": kind, "ok": bool(ok), "ms": int(ms), "detail": str(detail or "")[:MAX_DETAIL]})

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
        self._append(name, True, (time.perf_counter() - start) * 1000, s.detail, "data")

    def add(self, name: str, ok: bool = True, ms: int = 0, detail: str = "") -> None:
        self._append(name, ok, ms, detail, "data")

    def llm(self, name: str, model: str, ok: bool, ms: int) -> None:
        """记录一次模型调用。"""
        if model and not self.model:
            self.model = str(model)
        self._append(name, ok, ms, model or "", "llm")

    def note(self, text: str) -> None:
        """记录护栏调整等说明。"""
        self._append(text, True, 0, text, "note")  # name 与 detail 都放说明文字，界面只显示一次

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": list(self._steps),
            "total_ms": int((time.perf_counter() - self._started) * 1000),
            "model": self.model,
        }
