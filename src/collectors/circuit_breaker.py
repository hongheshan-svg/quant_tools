"""
数据源熔断器
连续失败达到阈值后短期跳过该数据源；冷却结束后放行一次探测请求，成功则恢复，失败则继续熔断。
避免已经故障的数据源在每轮采集中反复重试超时，拖慢整批采集。
"""

from __future__ import annotations

import threading
import time

from loguru import logger

DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 300.0


class CircuitBreaker:
    """按数据源名称记录连续失败次数的熔断器（线程安全）。"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_available(self, source: str) -> bool:
        """是否可以请求该数据源。熔断冷却结束后放行一次探测，探测期间其他请求继续跳过。"""
        with self._lock:
            if self._failures.get(source, 0) < self.failure_threshold:
                return True
            if time.monotonic() - self._opened_at.get(source, 0.0) < self.cooldown_seconds:
                return False
            self._opened_at[source] = time.monotonic()
            logger.info(f"[{self.name}] {source} 熔断冷却结束，放行一次探测")
            return True

    def is_open(self, source: str) -> bool:
        """是否处于熔断冷却中（只读，不会放行探测）。"""
        with self._lock:
            if self._failures.get(source, 0) < self.failure_threshold:
                return False
            return time.monotonic() - self._opened_at.get(source, 0.0) < self.cooldown_seconds

    def record_success(self, source: str) -> None:
        with self._lock:
            if self._failures.get(source, 0) >= self.failure_threshold:
                logger.info(f"[{self.name}] {source} 探测成功，恢复正常")
            self._failures.pop(source, None)
            self._opened_at.pop(source, None)

    def record_failure(self, source: str, error: str = "") -> None:
        with self._lock:
            failures = self._failures.get(source, 0) + 1
            self._failures[source] = failures
            if failures >= self.failure_threshold:
                self._opened_at[source] = time.monotonic()
                if failures == self.failure_threshold:
                    logger.warning(
                        f"[{self.name}] {source} 连续失败 {failures} 次，熔断 {self.cooldown_seconds:.0f} 秒: {error}"
                    )

    def available_sources(self, sources: list[str]) -> list[str]:
        """按原顺序返回可请求的源；全部冷却时返回空列表，不能绕过熔断。"""
        return [s for s in dict.fromkeys(sources) if self.is_available(s)]
