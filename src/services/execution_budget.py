"""诊断与问股的总预算，使用单调时钟；可选阶段不能耗尽决策预算。"""

from __future__ import annotations

import time
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable


class BudgetExpired(TimeoutError):
    """整条分析已用完预算。"""


class ExecutionBudget:
    def __init__(self, seconds: float = 180, *, deadline: float | None = None):
        self.deadline = deadline if deadline is not None else time.monotonic() + max(0.01, seconds)

    @classmethod
    def from_config(cls, config: dict) -> "ExecutionBudget":
        seconds = float((config.get("diagnosis") or {}).get("timeout_seconds", 180))
        return cls(seconds, deadline=config.get("_execution_deadline"))

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def check(self, stage: str = "分析") -> None:
        if not self.remaining():
            raise BudgetExpired(f"{stage}超时：已达到分析总时长上限")

    def parallel(self, fn: Callable, items, seconds: float = 60, reserve: float = 0) -> list[Any]:
        """保留已完成结果；超时后不等待尚未完成的分析员。外层进程负责硬终止。"""
        items = list(items)
        if not items or self.remaining() <= reserve:
            return []
        pool = ThreadPoolExecutor(max_workers=len(items), thread_name_prefix="analysis")
        futures = [pool.submit(copy_context().run, fn, item) for item in items]
        try:
            done, _ = wait(futures, timeout=min(seconds, self.remaining() - reserve))
            return [f.result() for f in futures if f in done]
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
