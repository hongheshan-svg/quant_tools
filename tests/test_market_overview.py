"""市场概况：今天没有行情（节假日、开盘前）时按最近一个交易日统计；补齐最近交易日的行情。离线运行。"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from src import trading_calendar
from src.collectors import stock_data as stock_data_mod
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
    monkeypatch.setattr(stock_data_mod, "FALLBACK_OVERVIEW_SAMPLE_SIZE", 150)
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


def test_overview_skips_partial_fallback_day(collector):
    """个股页按需补齐只写了少数股票的日线，不能拿来当全市场的涨跌统计"""
    service, path = collector
    today = date.today()
    full_day = (today - timedelta(days=5)).isoformat()
    partial_day = (today - timedelta(days=2)).isoformat()
    trading_calendar._set_days({full_day, partial_day, (today + timedelta(days=10)).isoformat()})
    with get_db_session(path) as s:
        s.add_all(_rows(full_day, up=100, down=60))
        s.add_all(_rows(partial_day, up=110, down=0))
    overview = service.collect_market_overview()
    assert overview["trade_date"] == full_day and overview["up_count"] == 100


def test_overview_uses_today_when_available(collector):
    service, path = collector
    today = date.today().isoformat()
    trading_calendar._set_days({today})
    with get_db_session(path) as s:
        s.add_all(_rows(today, up=50, down=110))
    overview = service.collect_market_overview()
    assert overview["trade_date"] == today and overview["down_count"] == 110


def _quotes(rows: list[tuple[str, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"代码": code, "名称": f"股票{code}", "最新价": 10.0, "涨跌幅": 1.0, "今开": 9.9, "最高": 10.1,
                          "最低": 9.8, "成交量": 1000.0, "成交额": 1e7, "行情日期": day} for code, day in rows])


def test_fill_last_session_on_holiday(collector, monkeypatch):
    """节假日：最近交易日缺行情时，用行情时间属于该交易日的行补齐，按该交易日入库，并补该日涨停池"""
    service, path = collector
    monkeypatch.setattr(stock_data_mod, "FALLBACK_OVERVIEW_SAMPLE_SIZE", 3)
    trading_calendar._set_days({"2026-09-29", "2026-09-30", "2026-10-08"})
    pools = []
    monkeypatch.setattr(StockDataCollector, "_collect_limit_up_pool", lambda self, d, db: pools.append(d))
    quotes = _quotes([("600001", "2026-09-30"), ("600002", "2026-09-30"), ("600003", "2026-09-30"), ("600004", "2026-09-12")])
    monkeypatch.setattr(StockDataCollector, "_fetch_tencent_quotes", lambda self: quotes)

    holiday = datetime(2026, 10, 2, 10, 0)
    assert service.fill_last_session(path, now=holiday) == "filled 2026-09-30"
    with get_db_session(path) as s:
        rows = s.query(StockDaily).all()
        assert sorted(r.code for r in rows) == ["600001", "600002", "600003"]       # 停牌股（行情时间更早）不写
        assert {r.trade_date for r in rows} == {"2026-09-30"}
    assert pools == ["2026-09-30"]

    monkeypatch.setattr(StockDataCollector, "_fetch_tencent_quotes", lambda self: pytest.fail("已有行情不应再请求"))
    assert service.fill_last_session(path, now=holiday) == "exists 2026-09-30"


def test_fill_last_session_guards(collector, monkeypatch):
    service, path = collector
    monkeypatch.setattr(stock_data_mod, "FALLBACK_OVERVIEW_SAMPLE_SIZE", 3)
    trading_calendar._set_days({"2026-09-29", "2026-09-30", "2026-10-08"})   # 10-01 ~ 10-07 国庆休市
    monkeypatch.setattr(StockDataCollector, "_collect_limit_up_pool", lambda self, d, db: pytest.fail("不应补涨停池"))
    # 交易日 9:15 集合竞价开始后，行情已经不是上一交易日的收盘
    monkeypatch.setattr(StockDataCollector, "_fetch_tencent_quotes", lambda self: pytest.fail("不应请求"))
    assert service.fill_last_session(path, now=datetime(2026, 9, 30, 9, 20)) == "skipped"
    # 行情时间对不上（比如接口返回的是更早的数据）时不写
    monkeypatch.setattr(StockDataCollector, "_fetch_tencent_quotes", lambda self: _quotes([("600001", "2026-09-29")] * 3))
    assert service.fill_last_session(path, now=datetime(2026, 10, 2, 10, 0)) == "mismatch 2026-09-30"
    with get_db_session(path) as s:
        assert s.query(StockDaily).count() == 0


def test_tencent_quote_date():
    assert stock_data_mod._tencent_quote_date("20260930161458") == "2026-09-30"
    assert stock_data_mod._tencent_quote_date("") == "" and stock_data_mod._tencent_quote_date("abc") == ""
