"""
多数据源回退链与健康状态（参考 daily_stock_analysis 的 DataFetcherManager 与数据源健康度）

fetch_with_fallback() 按优先级依次尝试数据源：跳过熔断中的源，成功即返回并记录来源；
全部失败且允许时，返回最近一次成功的数据并标记 stale。
每次尝试都记入 source_health，供界面「数据源状态」页和日报展示。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from src.collectors.circuit_breaker import CircuitBreaker
from src.utils.redaction import redact_text
from src.collectors.request_budget import POLICY, bounded_call, deadline

ERROR_MAX_CHARS = 200
MAX_CACHED_RESULTS = 128


def has_data(data: Any) -> bool:
    """默认有效性判断：非 None 且非空（兼容 DataFrame / list / dict）。"""
    if data is None:
        return False
    empty = getattr(data, "empty", None)
    if isinstance(empty, bool):
        return not empty
    try:
        return len(data) > 0
    except TypeError:
        return True


NO_DATA = "无数据"  # 源正常返回但没有数据（区别于请求出错）


@dataclass
class FetchResult:
    data: Any = None
    source: str = ""                     # 提供数据的源；stale 时为缓存数据的原始来源
    stale: bool = False                  # True 表示所有源都失败，返回的是之前缓存的数据
    fetched_at: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return has_data(self.data)


class SourceHealthRegistry:
    """进程级数据源健康记录（线程安全）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._db_path = None

    def configure(self, db_path: str) -> None:
        if self._db_path == db_path:
            return
        self.reset()
        self._db_path = db_path
        from src.database.models import SourceAttemptRecord
        from src.database.db import get_db_session
        with get_db_session(db_path) as session:
            rows = session.query(SourceAttemptRecord).order_by(SourceAttemptRecord.id.desc()).limit(2000).all()
            for row in reversed(rows):
                self.record(row.dataset, row.source, row.success, row.error or "", row.elapsed, emit=False, persist=False, at=row.created_at)

    def record(self, dataset: str, source: str, ok: bool, error: str = "", elapsed: float | None = None, *, emit: bool = True, persist: bool = True, at: datetime | None = None, capture_trace: bool = True) -> None:
        now = at or datetime.now()
        with self._lock:
            rec = self._records.setdefault(
                (dataset, source),
                {"dataset": dataset, "source": source, "last_success": None, "last_failure": None,
                 "consecutive_failures": 0, "total_success": 0, "total_failure": 0, "last_error": "", "last_elapsed": None},
            )
            rec["last_elapsed"] = round(elapsed, 2) if elapsed is not None else rec["last_elapsed"]
            if ok:
                rec["last_success"] = now
                rec["consecutive_failures"] = 0
                rec["total_success"] += 1
                rec["last_error"] = ""
            else:
                rec["last_failure"] = now
                rec["consecutive_failures"] += 1
                rec["total_failure"] += 1
                rec["last_error"] = redact_text(error or "无数据", ERROR_MAX_CHARS)
        from src.services.screening_sources import source_attempt
        if emit and capture_trace:
            source_attempt(dataset, {"source": source, "ok": bool(ok), "error": redact_text(error, 200), "ms": int((elapsed or 0) * 1000)})
        from src.services.run_log import EVENT_SINK, TRACE_ID, emit_event
        if emit and EVENT_SINK.get():
            emit_event({"type": "source_health", "trace_id": TRACE_ID.get(), "dataset": dataset, "source": source,
                              "ok": bool(ok), "error": redact_text(error, 200), "elapsed": elapsed})
        if persist and self._db_path:
            from src.database.db import get_db_session
            from src.database.models import SourceAttemptRecord
            try:
                with get_db_session(self._db_path) as session:
                    session.add(SourceAttemptRecord(dataset=dataset, source=source, success=ok, elapsed=elapsed,
                                                   error=redact_text(error, 200), trace_id=TRACE_ID.get(), created_at=now))
                    cutoff = session.query(SourceAttemptRecord.id).order_by(SourceAttemptRecord.id.desc()).offset(20000).first()
                    if cutoff:
                        session.query(SourceAttemptRecord).filter(SourceAttemptRecord.id <= cutoff[0]).delete()
            except Exception as failure:
                logger.warning("数据源健康记录保存失败: {}", redact_text(failure, 160))

    def snapshot(self) -> list[dict[str, Any]]:
        """各数据源状态：ok 正常 / failing 最近一次失败 / circuit_open 熔断中。"""
        with self._lock:
            rows = [dict(r) for r in self._records.values()]
        for row in rows:
            breaker = _breakers.get(row["dataset"])
            if breaker and breaker.is_open(row["source"]):
                row["status"] = "circuit_open"
            elif row["consecutive_failures"]:
                row["status"] = "failing"
            else:
                row["status"] = "ok"
        return sorted(rows, key=lambda r: (r["dataset"], r["source"]))

    def failing(self) -> list[dict[str, Any]]:
        return [r for r in self.snapshot() if r["status"] != "ok"]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._db_path = None


source_health = SourceHealthRegistry()
_breakers: dict[str, CircuitBreaker] = {}
_last_good: dict[str | tuple[str, str], tuple[Any, str, datetime]] = {}
_state_lock = threading.Lock()


def get_breaker(dataset: str) -> CircuitBreaker:
    """每个数据集一个熔断器（数据源名称在数据集内唯一）。"""
    with _state_lock:
        if dataset not in _breakers:
            _breakers[dataset] = CircuitBreaker(dataset)
        return _breakers[dataset]


def fetch_with_fallback(
    dataset: str,
    sources: list[tuple[str, Callable[[], Any]]],
    *,
    attempts: int = 1,
    retry_wait: float = 0.0,
    is_valid: Callable[[Any], bool] = has_data,
    allow_stale: bool = False,
    cache_key: str | None = None,
    max_stale_seconds: float | None = None,
    count_empty_failures: bool = True,
    timeout_seconds: float | None = None,
    stage_timeout_seconds: float | None = None,
) -> FetchResult:
    """按顺序尝试 sources=[(名称, 取数函数)]，返回第一个有效结果。"""
    breaker = get_breaker(dataset)
    result = FetchResult()
    key = dataset if cache_key is None else (dataset, cache_key)
    seen = set()
    expires = deadline(stage_timeout_seconds)
    for name, fetch in sources:
        if name in seen:
            continue
        seen.add(name)
        # 直到真正准备请求该源才领取探测机会，避免前一个源成功后占用后备源的探测。
        if not breaker.is_available(name):
            result.errors[name] = "熔断冷却中"
            continue
        for attempt in range(1, max(attempts, 1) + 1):
            started = time.monotonic()
            try:
                data = bounded_call(fetch, min(timeout_seconds or POLICY.get().request_seconds, expires - started),
                                    quarantine_key=(dataset, name))
                if is_valid(data):
                    elapsed = time.monotonic() - started
                    breaker.record_success(name)
                    source_health.record(dataset, name, True, elapsed=elapsed, capture_trace=False)
                    result.errors.pop(name, None)
                    count = len(data) if hasattr(data, "__len__") else 1
                    result.attempts.append({"source": name, "attempt": attempt, "ok": True, "ms": int(elapsed * 1000), "record_count": count})
                    _trace_attempt(dataset, result.attempts[-1])
                    with _state_lock:
                        _last_good[key] = (data, name, datetime.now())
                        if len(_last_good) > MAX_CACHED_RESULTS:
                            del _last_good[next(iter(_last_good))]
                    return FetchResult(data=data, source=name, fetched_at=datetime.now(), errors=result.errors, attempts=result.attempts)
                result.errors[name] = NO_DATA
            except Exception as e:
                result.errors[name] = redact_text(e, ERROR_MAX_CHARS) or type(e).__name__
                logger.warning(f"[{dataset}] {name} 第{attempt}次失败: {result.errors[name]}")
            result.attempts.append({"source": name, "attempt": attempt, "ok": False, "ms": int((time.monotonic() - started) * 1000), "error": result.errors[name]})
            _trace_attempt(dataset, result.attempts[-1])
            if attempt < attempts and retry_wait:
                time.sleep(min(retry_wait, max(0, expires - time.monotonic())))
        if count_empty_failures or result.errors.get(name) != NO_DATA:
            breaker.record_failure(name, result.errors.get(name, ""))
        source_health.record(dataset, name, False, result.errors.get(name, ""), time.monotonic() - started, capture_trace=False)

    with _state_lock:
        cached = _last_good.get(key)
    if allow_stale and cached and (max_stale_seconds is None or (datetime.now() - cached[2]).total_seconds() <= max_stale_seconds):
        data, source, fetched_at = cached
        logger.warning(f"[{dataset}] 所有数据源失败，使用 {fetched_at:%H:%M:%S} 的缓存数据（来源 {source}）")
        _trace_attempt(dataset, {"source": source, "ok": True, "ms": 0, "cache_hit": True, "stale_seconds": (datetime.now() - fetched_at).total_seconds()})
        return FetchResult(data=data, source=source, stale=True, fetched_at=fetched_at, errors=result.errors, attempts=result.attempts)
    logger.error(f"[{dataset}] 所有数据源均失败: {result.errors}")
    return result


def _trace_attempt(dataset: str, attempt: dict) -> None:
    from src.services.screening_sources import source_attempt
    source_attempt(dataset, attempt)
    from src.services.run_log import ACTIVE_LOG
    log = ACTIVE_LOG.get()
    if log is not None:
        log.data_attempt(dataset, attempt)
