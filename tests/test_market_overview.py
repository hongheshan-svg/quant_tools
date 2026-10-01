"""市场概况：今天没有行情（节假日、开盘前）时按最近一个交易日统计。离线运行。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src import trading_calendar
from src.collectors.stock_data import StockDataCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def collector(tmp_path, monkeypatch):
    path = str(tmp_path / "overview.db")
    _reset_db_engine()
    init_db(path)
    monkeypatch.setattr(StockDataCollector, "_collect_index_and_sectors", lambda self, overview: None)
    monkeypatch.setattr(StockDataCollector, "_market_overview_cache", {})
    saved = (trading_calendar._trade_days, trading_calendar._first_day, trading_calendar._last_day)
    yield StockDataCollector({"database": {"sqlite_path": path}}), path
    trading_calendar._trade_days, trading_calendar._first_day, trading_calendar._last_day = saved
    _reset_db_engine()


def _rows(trade_date: str, up: int, down: int) -> list[StockDaily]:
    changes = [1.5] * up + [-1.0] * down
    return [StockDaily(code=f"600{i:03d}", name=f"股票{i}", trade_date=trade_date, close=10.0, change_pct=c, amount=1e8)
            for i, c in enumerate(changes)]


def test_overview_falls_back_to_latest_trade_day(collector):
    service, path = collector
    today = date.today()
    trade_day = (today - timedelta(days=3)).isoformat()
    holiday = (today - timedelta(days=1)).isoformat()      # 旧版本在节假日写入的重复数据，不能拿来统计
    trading_calendar._set_days({trade_day, (today - timedelta(days=10)).isoformat(), (today + timedelta(days=10)).isoformat()})
    with get_db_session(path) as s:
        s.add_all(_rows(trade_day, up=120, down=40))
        s.add_all(_rows(holiday, up=10, down=150))

    overview = service.collect_market_overview()
    assert overview["trade_date"] == trade_day
    assert (overview["up_count"], overview["down_count"]) == (120, 40)
    assert overview["total_amount_yi"] == 160
    assert overview["market_emotion"] != "待开盘"


def test_overview_uses_today_when_available(collector):
    service, path = collector
    today = date.today().isoformat()
    trading_calendar._set_days({today})
    with get_db_session(path) as s:
        s.add_all(_rows(today, up=50, down=110))
    overview = service.collect_market_overview()
    assert overview["trade_date"] == today and overview["down_count"] == 110
