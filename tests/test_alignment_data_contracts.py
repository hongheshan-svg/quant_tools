"""日线时效、证据双时间和超时供应商隔离的行为回归。"""

import threading
from datetime import datetime

import pytest

from src.collectors.request_budget import bounded_call
from src.database.db import get_db_session
from src.database.models import FundDaily
from src.services.data_freshness import daily_quality
from src.services.research_artifact import build_context_pack, build_research_artifact
from src.services.stock_profile import StockProfileService
from src.strategy.screener import StrategyScreener
from tests.test_screener import config, TRADE_DATE  # noqa: F401


def phase(expected="2026-09-22", now="2026-09-22 16:00", partial=False):
    return {"phase": "intraday" if partial else "postmarket", "now": now,
            "effective_daily_bar_date": expected, "is_partial_bar": partial}


@pytest.mark.parametrize("day,stamp,ctx,status", [
    ("2025-11-07", None, phase(), "stale"),
    ("2026-09-22", "2026-09-22T11:00:00+08:00", phase(), "stale"),
    ("2026-09-22", "2026-09-22T08:00:00Z", phase(), "available"),
    ("2026-09-23", None, phase(), "stale"),
    ("2026-09-22", None, phase("2026-09-21", "2026-09-22 11:00", True), "stale"),
    (None, None, phase(), "missing"),
])
def test_daily_quality_rejects_old_future_and_incomplete_bars(monkeypatch, day, stamp, ctx, status):
    monkeypatch.setattr("src.trading_calendar.is_trade_day", lambda d: True)
    assert daily_quality(day, stamp, phase=ctx)["status"] == status


def test_live_old_database_does_not_overwrite_success_or_invoke_models(config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("src.services.market_phase.current_phase", lambda *a: phase())
    screener = StrategyScreener(config)
    result = screener.run(save=True)
    assert result.status == "partial"
    assert result.pipeline["daily_quality"]["status"] == "stale"
    assert not screener.latest()
    historical = screener.run(TRADE_DATE, save=False)
    assert historical.pipeline["mode"] == "point_in_time"
    assert historical.pipeline["daily_quality"]["mode"] == "historical"


def test_whole_stock_database_and_etf_are_not_fresh(config, monkeypatch):  # noqa: F811
    monkeypatch.setattr("src.services.market_phase.current_phase", lambda *a: phase())
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(FundDaily(code="510300", name="测试ETF", close=4, trade_date="2025-11-07"))
    profile = object.__new__(StockProfileService)
    profile.db_path = config["database"]["sqlite_path"]
    assert profile._quote("600001", "stock")["status"] == "partial"
    assert profile._quote("510300", "etf")["limitations"] == ["stale_quote"]


def test_observation_and_fetch_times_are_preserved_without_inventing_observation():
    quote = {"close": 10, "trade_date": "2026-09-21", "provider_timestamp": "2026-09-21T15:00:00+08:00",
             "updated_at": "2026-09-22T01:00:00+08:00"}
    pack = build_context_pack({"code": "600001", "quote": quote})
    block = pack["blocks"]["quote"]
    assert block["provider_timestamp"] == quote["provider_timestamp"]
    assert block["fetched_at"] == quote["updated_at"]
    artifact = build_research_artifact({"code": "600001", "context_pack": pack})
    evidence = next(e for e in artifact["evidence"] if e["source_type"] == "quote")
    assert evidence["provider_timestamp"] == quote["provider_timestamp"]
    quote.pop("provider_timestamp")
    assert build_context_pack({"code": "600001", "quote": quote})["blocks"]["quote"]["provider_timestamp"] is None


def test_timed_out_operation_is_quarantined_without_blocking_alternative():
    release, ended = threading.Event(), threading.Event()
    calls = []
    key = ("audit", datetime.now().isoformat())
    def blocked():
        calls.append(1)
        release.wait(2)
        ended.set()
    try:
        with pytest.raises(TimeoutError):
            bounded_call(blocked, .02, quarantine_key=key)
        with pytest.raises(TimeoutError, match="上次超时"):
            bounded_call(blocked, .02, quarantine_key=key)
        assert len(calls) == 1
        assert bounded_call(lambda: 7, .1, quarantine_key=(key, "fallback")) == 7
    finally:
        release.set()
        assert ended.wait(1)
    assert bounded_call(lambda: 9, .1, quarantine_key=key) == 9
