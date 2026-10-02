from __future__ import annotations

import pandas as pd
import pytest

from src.collectors import circuit_breaker as cb_mod
from src.collectors import source_chain as sc
from src.collectors.source_chain import fetch_with_fallback, has_data, source_health


@pytest.fixture(autouse=True)
def _fresh_state():
    """回退链的熔断器、缓存和健康记录都是进程级状态，每个测试前后清空。"""
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()
    yield
    sc._breakers.clear()
    sc._last_good.clear()
    source_health.reset()


def _boom():
    raise ConnectionError("timeout")


def test_has_data():
    assert not has_data(None) and not has_data(pd.DataFrame()) and not has_data([])
    assert has_data(pd.DataFrame({"a": [1]})) and has_data([1]) and has_data(3)


def test_falls_back_in_order_and_records_health():
    result = fetch_with_fallback("行情", [("腾讯", _boom), ("东财", lambda: []), ("新浪", lambda: [1, 2])])
    assert (result.ok, result.source, result.stale) == (True, "新浪", False)
    assert result.errors == {"腾讯": "timeout", "东财": "无数据"}

    status = {r["source"]: r for r in source_health.snapshot()}
    assert status["腾讯"]["status"] == "failing" and status["腾讯"]["last_error"] == "timeout"
    assert status["新浪"]["status"] == "ok" and status["新浪"]["total_success"] == 1


def test_retries_before_moving_on(monkeypatch):
    monkeypatch.setattr(sc.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise ConnectionError("reset")
        return [1]

    result = fetch_with_fallback("行情", [("腾讯", flaky), ("新浪", lambda: [9])], attempts=2, retry_wait=1)
    assert result.source == "腾讯" and len(calls) == 2
    assert result.errors == {}


def test_all_open_sources_do_not_bypass_cooldown(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock[0])
    calls = []
    breaker = sc.get_breaker("行情")
    for name in ("一", "二"):
        for _ in range(3):
            breaker.record_failure(name)
    result = fetch_with_fallback("行情", [("一", lambda: calls.append(1)), ("二", lambda: calls.append(2))])
    assert not result.ok and calls == []
    assert result.errors == {"一": "熔断冷却中", "二": "熔断冷却中"}


def test_unused_backup_keeps_half_open_probe_for_next_request(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock[0])
    breaker = sc.get_breaker("行情")
    for _ in range(3):
        breaker.record_failure("后备")
    clock[0] += 301
    result = fetch_with_fallback("行情", [("主源", lambda: [1]), ("后备", lambda: [2])])
    assert result.source == "主源"
    result = fetch_with_fallback("行情", [("主源", _boom), ("后备", lambda: [2])])
    assert result.source == "后备"


def test_empty_symbol_history_does_not_open_provider_circuit_for_other_symbols():
    for i in range(4):
        result = fetch_with_fallback("历史", [("源", lambda: [])], cache_key=str(i), count_empty_failures=False)
        assert not result.ok
    result = fetch_with_fallback("历史", [("源", lambda: [1])], cache_key="正常股票", count_empty_failures=False)
    assert result.ok and result.errors == {}


def test_open_breaker_skips_source(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cb_mod.time, "monotonic", lambda: clock[0])
    calls = []

    def tencent():
        calls.append(1)
        raise ConnectionError("down")

    for _ in range(3):
        fetch_with_fallback("行情", [("腾讯", tencent), ("新浪", lambda: [1])])
    assert len(calls) == 3

    result = fetch_with_fallback("行情", [("腾讯", tencent), ("新浪", lambda: [1])])
    assert result.source == "新浪" and len(calls) == 3  # 熔断中，直接跳过腾讯
    assert {r["source"]: r["status"] for r in source_health.snapshot()}["腾讯"] == "circuit_open"


def test_stale_cache_only_when_allowed():
    assert fetch_with_fallback("板块", [("东财", lambda: ["AI"])]).source == "东财"

    fresh_only = fetch_with_fallback("板块", [("东财", _boom)])
    assert not fresh_only.ok

    stale = fetch_with_fallback("板块", [("东财", _boom)], allow_stale=True)
    assert (stale.data, stale.source, stale.stale) == (["AI"], "东财", True)


def test_strong_pool_fallback_keeps_only_limit_up_rows():
    from src.collectors.stock_data import StockDataCollector

    df = pd.DataFrame({
        "代码": ["600001", "600002", "300001", "300002", "000003"],
        "名称": ["主板涨停", "主板强势", "创业板涨停", "创业板强势", "*ST涨停"],
        "涨跌幅": [9.98, 7.0, 19.99, 12.0, 4.97],
    })
    kept = StockDataCollector._limit_up_rows_only(df)
    assert kept["代码"].tolist() == ["600001", "300001", "000003"]
