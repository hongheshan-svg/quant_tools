"""
后台任务（参考 daily_stock_analysis 的 task_queue）：采集、预测、诊断、回测、问股这类耗时操作提交成任务，
接口立即返回任务 ID，前端轮询 /api/v1/tasks/{id} 查看进度和结果。

- 同一「去重键」的任务正在运行时直接返回那个任务，避免重复点击触发两次采集
- 被执行的函数如果有 progress 参数，会收到进度回调：progress(已完成, 总数) 或 progress("正在查询：…")
- 最近 200 个任务保留在内存；配置数据库后同时持久化，重启时标记中断
"""

from __future__ import annotations

import inspect
import json
import threading
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from fastapi.encoders import jsonable_encoder
from loguru import logger
from src.utils.redaction import redact, redact_text

MAX_TASKS = 200
TERMINAL = ("done", "error")


def business_result_error(result: Any) -> str:
    """由业务接口显式选择的失败契约；部分成功批量结果不按此契约处理。"""
    return str(result.get("error") or "") if isinstance(result, dict) else ""


def collection_result_error(result: Any) -> str:
    error = business_result_error(result)
    if error:
        return error
    if isinstance(result, dict) and result.get("all_sources_ok") is False:
        return "数据采集不完整：" + "、".join(result.get("missing_sources") or ["必需数据源未就绪"])
    return ""


class TaskManager:
    def __init__(self, workers: int = 4, db_path: str | None = None):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="api-task")
        self._tasks: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._db_path = db_path
        self._restore()

    def _restore(self) -> None:
        if not self._db_path:
            return
        from src.database.db import get_db_session
        from src.database.models import TaskRun
        with get_db_session(self._db_path) as session:
            rows = session.query(TaskRun).order_by(TaskRun.created_at.desc()).limit(MAX_TASKS).all()
            for row in reversed(rows):
                try:
                    task = json.loads(row.payload_json)
                    if not isinstance(task, dict) or task.get("id") != row.id or "status" not in task:
                        raise ValueError("invalid task payload")
                except (ValueError, TypeError):
                    logger.warning("忽略损坏的历史任务记录: {}", row.id)
                    continue
                if task["status"] not in TERMINAL:
                    task.update(status="error", error="服务重启，未完成的任务已中断，请重新运行", finished_at=datetime.now().isoformat(timespec="seconds"))
                    task["revision"] = task.get("revision", 0) + 1
                    row.payload_json = json.dumps(task, ensure_ascii=False)
                self._tasks[task["id"]] = {**task, "dedupe_key": ""}

    def _persist(self, task) -> None:
        if not self._db_path:
            return
        from src.database.db import get_db_session
        from src.database.models import TaskRun
        try:
            with get_db_session(self._db_path) as session:
                row = session.get(TaskRun, task["id"])
                if row is None:
                    row = TaskRun(id=task["id"])
                    session.add(row)
                row.payload_json = json.dumps(self._public(task), ensure_ascii=False)
        except Exception as error:
            logger.warning(redact_text(f"保存任务状态失败: {error}"))

    def submit(self, kind: str, fn: Callable, *args, dedupe_key: str | None = None, label: str = "", subject: dict | None = None,
               result_error: Callable[[Any], str] | None = None, **kwargs) -> dict[str, Any]:
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
                "revision": 1, "trace_id": uuid.uuid4().hex,
                "subject": subject,
            }
            self._tasks[task["id"]] = task
            self._persist(task)
            while len(self._tasks) > MAX_TASKS:
                oldest = next(iter(self._tasks))
                if self._tasks[oldest]["status"] not in TERMINAL:
                    break
                self._tasks.popitem(last=False)
        if "progress" in inspect.signature(fn).parameters:
            kwargs["progress"] = lambda *p: self._set_progress(task["id"], p)
        self._pool.submit(self._run, task["id"], fn, args, kwargs, result_error)
        return self._public(task)

    def _set_progress(self, task_id: str, values: tuple) -> None:
        if len(values) == 2 and all(isinstance(v, int) for v in values):
            progress = {"done": values[0], "total": values[1]}
        elif len(values) == 1 and isinstance(values[0], dict):
            progress = {"text": values[0].get("name") or values[0].get("text", ""), "event": redact(values[0])}
        else:
            progress = {"text": str(values[0]) if values else ""}
        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                event = dict(progress.get("event") or {"type": "progress", **progress})
                event.setdefault("id", f"{task['trace_id']}:event:{task['revision'] + 1}")
                event.setdefault("at", datetime.now().isoformat())
                history = task.setdefault("flow_events", [])
                history.append(event)
                if len(history) > 200:
                    del history[0]
                    task["flow_events_dropped"] = task.get("flow_events_dropped", 0) + 1
                task["progress"] = progress
                self._tasks[task_id]["revision"] += 1
                self._persist(self._tasks[task_id])
                self._changed.notify_all()

    def _run(self, task_id: str, fn: Callable, args: tuple, kwargs: dict, result_error=None) -> None:
        self._update(task_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
        try:
            from src.services.run_log import execution_trace
            with execution_trace(self._tasks[task_id]["trace_id"], lambda event: self._set_progress(task_id, (event,))):
                result = jsonable_encoder(fn(*args, **kwargs))
            error = result_error(result) if result_error else ""
            self._update(task_id, status="error" if error else "done", result=result, error=redact_text(error, 500),
                         finished_at=datetime.now().isoformat(timespec="seconds"))
        except Exception as e:
            logger.error(redact_text(f"后台任务失败 [{task_id}]: {e}\n{traceback.format_exc(limit=5)}"))
            self._update(task_id, status="error", error=redact_text(e, 500) or type(e).__name__,
                         finished_at=datetime.now().isoformat(timespec="seconds"))

    def _update(self, task_id: str, **fields) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].update(fields)
                self._tasks[task_id]["revision"] += 1
                self._persist(self._tasks[task_id])
                self._changed.notify_all()

    @staticmethod
    def _public(task: dict[str, Any]) -> dict[str, Any]:
        return redact({k: v for k, v in task.items() if k != "dedupe_key"})

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return self._public(dict(task))
        if self._db_path:
            from src.database.db import get_db_session
            from src.database.models import TaskRun
            with get_db_session(self._db_path) as session:
                row = session.get(TaskRun, task_id)
                try:
                    return redact(json.loads(row.payload_json)) if row else None
                except (ValueError, TypeError):
                    return None
        return None

    def events(self, task_id: str, after_revision: int = 0):
        """发送当前快照和后续变化；有界等待便于断开与服务器关闭。"""
        while True:
            current = self.get(task_id)
            if current is None:
                return
            if current.get("revision", 0) > after_revision:
                after_revision = current["revision"]
                yield current
            if current["status"] in TERMINAL:
                return
            with self._changed:
                self._changed.wait_for(lambda: self._tasks.get(task_id, {}).get("revision", 0) > after_revision, timeout=1)
            yield None  # SSE heartbeat，消费者可以检测断开

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())[-limit:]
        return [self._public({**t, "result": None}) for t in reversed(tasks)]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
