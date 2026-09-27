from __future__ import annotations

from src.collectors import circuit_breaker as cb_mod
from src.collectors.circuit_breaker import CircuitBreaker


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _breaker(monkeypatch) -> tuple[CircuitBreaker, _Clock]:
    clock = _Clock()
    monkeypatch.setattr(cb_mod.time, "monotonic", clock)
    return CircuitBreaker("测试", failure_threshold=3, cooldown_seconds=300), clock


def test_opens_after_consecutive_failures_and_success_resets(monkeypatch):
    breaker, _ = _breaker(monkeypatch)

    breaker.record_failure("tencent")
    breaker.record_failure("tencent")
    breaker.record_success("tencent")  # 中间成功一次，计数清零
    breaker.record_failure("tencent")
    breaker.record_failure("tencent")
    assert breaker.is_available("tencent") is True

    breaker.record_failure("tencent")
    assert breaker.is_available("tencent") is False
    assert breaker.is_available("sina") is True  # 各数据源独立计数


def test_half_open_probe_after_cooldown(monkeypatch):
    breaker, clock = _breaker(monkeypatch)
    for _ in range(3):
        breaker.record_failure("em")

    clock.now += 299
    assert breaker.is_available("em") is False

    clock.now += 2
    assert breaker.is_available("em") is True    # 放行一次探测
    assert breaker.is_available("em") is False   # 探测期间其他请求仍跳过

    breaker.record_failure("em")                 # 探测失败：重新冷却
    clock.now += 100
    assert breaker.is_available("em") is False

    clock.now += 201
    assert breaker.is_available("em") is True
    breaker.record_success("em")                 # 探测成功：完全恢复
    assert breaker.is_available("em") is True
    assert breaker.is_available("em") is True


def test_available_sources_keeps_order_and_never_empty(monkeypatch):
    breaker, _ = _breaker(monkeypatch)
    for _ in range(3):
        breaker.record_failure("tencent")

    assert breaker.available_sources(["tencent", "em", "sina"]) == ["em", "sina"]

    for source in ("em", "sina"):
        for _ in range(3):
            breaker.record_failure(source)
    assert breaker.available_sources(["tencent", "em", "sina"]) == ["tencent"]
