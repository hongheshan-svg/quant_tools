"""数据请求预算：阻塞接口使用有上限的守护工作槽，结果过期后不进入调用方。"""

from contextvars import ContextVar, copy_context
from dataclasses import dataclass
import threading
import time
from typing import Callable, Any


@dataclass(frozen=True)
class RequestPolicy:
    request_seconds: float = 15
    stage_seconds: float = 60


POLICY = ContextVar("source_request_policy", default=RequestPolicy())
_slots = threading.BoundedSemaphore(8)
_quarantine_lock = threading.Lock()
_quarantined: dict[object, int] = {}


def configure_policy(config: dict) -> None:
    cfg = config.get("data_sources") or {}
    POLICY.set(RequestPolicy(float(cfg.get("request_timeout_seconds", 15)),
                             float(cfg.get("stage_timeout_seconds", 60))))


def bounded_call(fn: Callable[[], Any], seconds: float, *, quarantine_key=None) -> Any:
    """超时不会释放仍运行的槽，避免持续失败时无限堆积线程。

    回调仅用于只读取数；写库在调用方拿到有效结果后执行。外层隔离进程负责硬终止。
    """
    if seconds <= 0:
        raise TimeoutError("数据阶段预算已耗尽")
    with _quarantine_lock:
        if quarantine_key is not None and _quarantined.get(quarantine_key):
            raise TimeoutError("该供应商接口的上次超时请求仍在执行，跳过重复请求")
        if not _slots.acquire(blocking=False):
            raise TimeoutError("数据请求工作槽已满，等待现有请求结束后重试")
    done, output = threading.Event(), []
    context = copy_context()
    state = {"timed_out": False}

    def work():
        try:
            output.append((True, context.run(fn)))
        except BaseException as error:
            output.append((False, error))
        finally:
            with _quarantine_lock:
                if state["timed_out"] and quarantine_key is not None:
                    _quarantined[quarantine_key] -= 1
                    if not _quarantined[quarantine_key]:
                        del _quarantined[quarantine_key]
                _slots.release()
                done.set()

    try:
        threading.Thread(target=work, daemon=True, name="source-request").start()
    except BaseException:
        _slots.release()
        raise
    if not done.wait(seconds):
        with _quarantine_lock:
            if not done.is_set() and quarantine_key is not None:
                state["timed_out"] = True
                _quarantined[quarantine_key] = _quarantined.get(quarantine_key, 0) + 1
        raise TimeoutError(f"数据请求超过 {seconds:g} 秒")
    ok, value = output[0]
    if not ok:
        raise value
    return value


def deadline(seconds: float | None = None) -> float:
    return time.monotonic() + (POLICY.get().stage_seconds if seconds is None else seconds)
