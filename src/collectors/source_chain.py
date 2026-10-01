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

ERROR_MAX_CHARS = 200


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

    @property
    def ok(self) -> bool:
        return has_data(self.data)


class SourceHealthRegistry:
    """进程级数据源健康记录（线程安全）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, dataset: str, source: str, ok: bool, error: str = "", elapsed: float | None = None) -> None:
        now = datetime.now()
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
            else:
                rec["last_failure"] = now
                rec["consecutive_failures"] += 1
                rec["total_failure"] += 1
                rec["last_error"] = (error or "无数据")[:ERROR_MAX_CHARS]

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


source_health = SourceHealthRegistry()
_breakers: dict[str, CircuitBreaker] = {}
_last_good: dict[str, tuple[Any, str, datetime]] = {}
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
) -> FetchResult:
    """按顺序尝试 sources=[(名称, 取数函数)]，返回第一个有效结果。"""
    breaker = get_breaker(dataset)
    result = FetchResult()
    for name in breaker.available_sources([n for n, _ in sources]):
        fetch = dict(sources)[name]
        for attempt in range(1, max(attempts, 1) + 1):
            started = time.monotonic()
            try:
                data = fetch()
                if is_valid(data):
                    elapsed = time.monotonic() - started
                    breaker.record_success(name)
                    source_health.record(dataset, name, True, elapsed=elapsed)
                    _last_good[dataset] = (data, name, datetime.now())
                    return FetchResult(data=data, source=name, fetched_at=datetime.now(), errors=result.errors)
                result.errors[name] = NO_DATA
            except Exception as e:
                result.errors[name] = str(e)[:ERROR_MAX_CHARS] or type(e).__name__
                logger.warning(f"[{dataset}] {name} 第{attempt}次失败: {e}")
            if attempt < attempts and retry_wait:
                time.sleep(retry_wait)
        breaker.record_failure(name, result.errors.get(name, ""))
        source_health.record(dataset, name, False, result.errors.get(name, ""), time.monotonic() - started)

    if allow_stale and dataset in _last_good:
        data, source, fetched_at = _last_good[dataset]
        logger.warning(f"[{dataset}] 所有数据源失败，使用 {fetched_at:%H:%M:%S} 的缓存数据（来源 {source}）")
        return FetchResult(data=data, source=source, stale=True, fetched_at=fetched_at, errors=result.errors)
    logger.error(f"[{dataset}] 所有数据源均失败: {result.errors}")
    return result
