from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import TradeCalendar

# 2026 年国庆：10-01 ~ 10-07 休市，10-08 开市
NATIONAL_DAY_WEEKS = {
    "2026-09-28", "2026-09-29", "2026-09-30",
    "2026-10-08", "2026-10-09", "2026-10-12",
}


@pytest.fixture(autouse=True)
def _isolated_calendar():
    """交易日历是进程级状态，每个测试前后清空。"""
    trading_calendar._set_days(set())
    trading_calendar._last_refresh_attempt = None
    yield
    trading_calendar._set_days(set())
    trading_calendar._last_refresh_attempt = None


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _init(tmp_path) -> str:
    db_path = str(tmp_path / "calendar.db")
    _reset_db_engine()
    init_db(db_path)
    return db_path


def _future_days() -> set[str]:
    """覆盖今天之后 60 天的工作日，让缓存被视为新鲜。"""
    today = date.today()
    return {
        (today + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(-5, 60)
        if (today + timedelta(days=i)).weekday() < 5
    }


def test_holidays_and_next_trade_day():
    trading_calendar._set_days(set(NATIONAL_DAY_WEEKS))

    assert trading_calendar.is_trade_day("2026-09-30") is True
    assert trading_calendar.is_trade_day("2026-10-05") is False  # 周一，但在国庆假期内
    assert trading_calendar.next_trade_day("2026-09-30") == date(2026, 10, 8)
    assert trading_calendar.next_trade_day("2026-10-08", include_self=True) == date(2026, 10, 8)
    assert trading_calendar.next_trade_day(datetime(2026, 10, 3, 10, 0)) == date(2026, 10, 8)


def test_falls_back_to_weekdays_without_calendar_or_out_of_range():
    assert trading_calendar.is_trade_day("2026-10-05") is True   # 无日历：周一视为交易日
    assert trading_calendar.is_trade_day("2026-10-04") is False  # 周日

    trading_calendar._set_days(set(NATIONAL_DAY_WEEKS))
    assert trading_calendar.is_trade_day("2027-03-01") is True   # 超出日历范围按工作日判断


def test_load_fetches_once_and_then_uses_db_cache(tmp_path, monkeypatch):
    db_path = _init(tmp_path)
    remote = _future_days() | {"2026-09-30"}
    calls = []
    monkeypatch.setattr(trading_calendar, "_fetch_remote", lambda: calls.append(1) or set(remote))

    assert trading_calendar.load(db_path) is True
    assert len(calls) == 1
    with get_db_session(db_path) as session:
        assert session.query(TradeCalendar).count() == len(remote)

    # 缓存新鲜：再次加载不联网，且进程重启（清空内存）后能从数据库恢复
    trading_calendar._set_days(set())
    trading_calendar._last_refresh_attempt = None
    assert trading_calendar.load(db_path) is True
    assert len(calls) == 1
    assert trading_calendar.is_trade_day("2026-09-30") is True
    _reset_db_engine()


def test_refresh_false_never_touches_network(tmp_path, monkeypatch):
    db_path = _init(tmp_path)
    monkeypatch.setattr(trading_calendar, "_fetch_remote", lambda: pytest.fail("should not fetch"))

    assert trading_calendar.load(db_path, refresh=False) is False
    _reset_db_engine()


def test_fetch_failure_keeps_stale_cache_and_throttles_retries(tmp_path, monkeypatch):
    db_path = _init(tmp_path)
    stale = datetime.now() - timedelta(days=30)
    with get_db_session(db_path) as session:
        session.add(TradeCalendar(trade_date="2026-09-30", updated_at=stale))
        session.add(TradeCalendar(trade_date="2026-10-08", updated_at=stale))

    calls = []

    def _boom():
        calls.append(1)
        raise ConnectionError("sina down")

    monkeypatch.setattr(trading_calendar, "_fetch_remote", _boom)

    assert trading_calendar.load(db_path) is True
    assert trading_calendar.is_trade_day("2026-10-01") is False  # 沿用缓存：两个交易日之间的日期都是休市
    trading_calendar.load(db_path)
    assert len(calls) == 1  # 失败后 6 小时内不重复联网
    _reset_db_engine()


def test_market_data_ready_and_trade_days_only():
    trading_calendar._set_days({"2026-09-24", "2026-09-28"})
    assert trading_calendar.market_data_ready(datetime(2026, 9, 25, 10, 0)) is False  # 中秋休市
    assert trading_calendar.market_data_ready(datetime(2026, 9, 28, 9, 0)) is False   # 开盘前
    assert trading_calendar.market_data_ready(datetime(2026, 9, 28, 9, 25)) is True
    assert trading_calendar.trade_days_only(["2026-09-24", "2026-09-25", "2026-09-28"]) == ["2026-09-24", "2026-09-28"]
