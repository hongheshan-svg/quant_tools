"""
后台任务（参考 daily_stock_analysis 的 task_queue）：采集、预测、诊断、回测、问股这类耗时操作提交成任务，
接口立即返回任务 ID，前端轮询 /api/v1/tasks/{id} 查看进度和结果。

- 同一「去重键」的任务正在运行时直接返回那个任务，避免重复点击触发两次采集
- 被执行的函数如果有 progress 参数，会收到进度回调：progress(已完成, 总数) 或 progress("正在查询：…")
- 只保留最近 200 个任务（内存中，服务重启后清空）
"""

from __future__ import annotations

import inspect
import threading
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from loguru import logger

MAX_TASKS = 200
TERMINAL = ("done", "error")


class TaskManager:
    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="api-task")
        self._tasks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable, *args, dedupe_key: str | None = None, label: str = "", **kwargs) -> dict[str, Any]:
        key = dedupe_key or ""
        with self._lock:
            if key:
                running = next((t for t in self._tasks.values() if t["dedupe_key"] == key and t["status"] not in TERMINAL), None)
                if running:
                    return self._public(running)
            task = {
                "id": uuid.uuid4().hex, "kind": kind, "label": label or kind, "dedupe_key": key, "status": "pending",
                "progress": None, "result": None, "error": "", "created_at": datetime.now().isoformat(timespec="seconds"),
                "started_at": None, "finished_at": None,
            }
            self._tasks[task["id"]] = task
            while len(self._tasks) > MAX_TASKS:
                oldest = next(iter(self._tasks))
                if self._tasks[oldest]["status"] not in TERMINAL:
                    break
                self._tasks.popitem(last=False)
        if "progress" in inspect.signature(fn).parameters:
            kwargs["progress"] = lambda *p: self._set_progress(task["id"], p)
        self._pool.submit(self._run, task["id"], fn, args, kwargs)
        return self._public(task)

    def _set_progress(self, task_id: str, values: tuple) -> None:
        if len(values) == 2 and all(isinstance(v, int) for v in values):
            progress = {"done": values[0], "total": values[1]}
        else:
            progress = {"text": str(values[0]) if values else ""}
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["progress"] = progress

    def _run(self, task_id: str, fn: Callable, args: tuple, kwargs: dict) -> None:
        self._update(task_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
        try:
            result = jsonable_encoder(fn(*args, **kwargs))
            self._update(task_id, status="done", result=result)
        except Exception as e:
            logger.error(f"后台任务失败 [{task_id}]: {e}\n{traceback.format_exc(limit=5)}")
            self._update(task_id, status="error", error=str(e)[:500] or type(e).__name__)
        finally:
            self._update(task_id, finished_at=datetime.now().isoformat(timespec="seconds"))

    def _update(self, task_id: str, **fields) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(fields)

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in task.items() if k != "dedupe_key"}

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return self._public(dict(task)) if task else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())[-limit:]
        return [self._public({**t, "result": None}) for t in reversed(tasks)]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
