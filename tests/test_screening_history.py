"""策略选股历史浏览：StrategyScreener.picks / history_dates 与 /screening/dates、/screening/picks 接口。"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.auth import AuthStore
from src import settings_store, trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StrategyPick
from src.strategy.screener import STRATEGIES, StrategyScreener

D1, D2, D3 = "2026-09-21", "2026-09-22", "2026-09-23"   # 周一至周三，均为过去的交易日


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _pick(d, strategy, code, name, score, **kw):
    return StrategyPick(trade_date=d, strategy=strategy, code=code, name=name, score=score,
                        reason=kw.get("reason", f"{strategy}理由"), close=10.0, change_pct=kw.get("change_pct", 4.0),
                        fits_regime=kw.get("fits", True))


def _seed(path: str) -> None:
    with get_db_session(path) as s:
        # D1：A 两个策略，B 一个策略
        s.add(_pick(D1, "volume_breakout", "600001", "甲股", 80))
        s.add(_pick(D1, "strong_close", "600001", "甲股", 70))
        s.add(_pick(D1, "trend_pullback", "600002", "乙股", 60))
        # D2：A 一个策略，C 一个策略
        s.add(_pick(D2, "volume_breakout", "600001", "甲股", 75))
        s.add(_pick(D2, "oversold_rebound", "000003", "丙股", 65))
        # D3：D 一只，次日行情缺失
        s.add(_pick(D3, "volume_breakout", "600004", "丁股", 90))
        # 次日行情：D1 的次日(D2)有 A +5、B -3；D2 的次日(D3)只有 A +2；D3 的次日(09-24)没有
        s.add(StockDaily(code="600001", name="甲股", trade_date=D2, close=10.5, change_pct=5.0))
        s.add(StockDaily(code="600002", name="乙股", trade_date=D2, close=9.7, change_pct=-3.0))
        s.add(StockDaily(code="600001", name="甲股", trade_date=D3, close=10.7, change_pct=2.0))


@pytest.fixture
def db_path(tmp_path):
    days = {(date(2026, 9, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(60)
            if (date(2026, 9, 1) + timedelta(days=i)).weekday() < 5}
    old = (trading_calendar._trade_days, trading_calendar._first_day, trading_calendar._last_day)
    trading_calendar._set_days(days)
    path = str(tmp_path / "hist.db")
    _reset_db_engine()
    init_db(path)
    _seed(path)
    yield path
    trading_calendar._trade_days, trading_calendar._first_day, trading_calendar._last_day = old
    _reset_db_engine()


@pytest.fixture
def screener(db_path):
    return StrategyScreener({"database": {"sqlite_path": db_path}})


# ---------- picks ----------

def test_picks_default_is_latest_day(screener):
    rows = screener.picks()
    assert [r["code"] for r in rows] == ["600004"]
    assert rows[0]["trade_date"] == D3 and rows[0]["next_change_pct"] is None


def test_picks_specific_date_merges_strategies(screener):
    rows = {r["code"]: r for r in screener.picks(D1)}
    assert set(rows) == {"600001", "600002"}
    a = rows["600001"]
    assert a["labels"] == ["放量突破", "强势未板"] and len(a["reasons"]) == 2
    assert a["next_change_pct"] == 5.0 and rows["600002"]["next_change_pct"] == -3.0
    # 字段与 latest() 一致
    assert set(a) == set(screener.latest()[0])


def test_picks_filter_by_strategy_keeps_all_labels(screener):
    rows = screener.picks(D1, "volume_breakout")
    assert [r["code"] for r in rows] == ["600001"]
    assert rows[0]["labels"] == ["放量突破", "强势未板"]           # 合并后仍保留当天所有策略
    assert [r["code"] for r in screener.picks(D1, "trend_pullback")] == ["600002"]
    assert screener.picks(D1, "oversold_rebound") == []
    assert screener.picks(D1, "no_such_strategy") == []


def test_picks_strategy_filter_with_default_date(screener):
    assert [r["code"] for r in screener.picks(strategy="volume_breakout")] == ["600004"]
    assert screener.picks(strategy="trend_pullback") == []          # 最近一天没有该策略


def test_picks_unknown_date_empty(screener):
    assert screener.picks("2020-01-01") == []


def test_picks_sorted_like_latest(screener):
    rows = screener.picks(D2)
    assert [r["code"] for r in rows] == ["600001", "000003"]         # 按得分降序（都适配大盘）
    assert rows[0]["score"] >= rows[1]["score"]


def test_latest_equals_picks(screener):
    assert screener.latest() == screener.picks()


def test_picks_empty_db(tmp_path):
    _reset_db_engine()
    path = str(tmp_path / "e.db")
    init_db(path)
    s = StrategyScreener({"database": {"sqlite_path": path}})
    assert s.picks() == [] and s.picks(D1, "volume_breakout") == [] and s.history_dates() == []
    _reset_db_engine()


# ---------- history_dates ----------

def test_history_dates_desc_and_counts(screener):
    rows = screener.history_dates()
    assert [r["trade_date"] for r in rows] == [D3, D2, D1]
    by = {r["trade_date"]: r for r in rows}
    assert by[D1]["picks"] == 2                                      # 甲股多策略只算一只
    assert by[D1]["strategies"] == {"volume_breakout": 1, "strong_close": 1, "trend_pullback": 1}
    assert by[D2]["picks"] == 2 and by[D2]["strategies"] == {"volume_breakout": 1, "oversold_rebound": 1}
    assert by[D3]["picks"] == 1 and by[D3]["strategies"] == {"volume_breakout": 1}


def test_history_dates_next_day_performance(screener):
    by = {r["trade_date"]: r for r in screener.history_dates()}
    assert by[D1]["evaluated"] == 2 and by[D1]["avg_next_pct"] == 1.0 and by[D1]["win_rate"] == 50.0
    assert by[D2]["evaluated"] == 1 and by[D2]["avg_next_pct"] == 2.0 and by[D2]["win_rate"] == 100.0
    assert by[D3]["evaluated"] == 0 and by[D3]["avg_next_pct"] is None and by[D3]["win_rate"] is None


def test_history_dates_limit(screener):
    assert [r["trade_date"] for r in screener.history_dates(limit=2)] == [D3, D2]
    assert [r["trade_date"] for r in screener.history_dates(limit=1)] == [D3]
    assert len(screener.history_dates(limit=250)) == 3


def test_history_dates_item_keys(screener):
    assert set(screener.history_dates()[0]) == {"trade_date", "picks", "strategies", "evaluated", "avg_next_pct", "win_rate"}


def test_history_dates_rounding(db_path):
    with get_db_session(db_path) as s:
        s.add(_pick(D1, "trend_pullback", "600009", "戊股", 50))
        s.add(StockDaily(code="600009", name="戊股", trade_date=D2, close=10.0, change_pct=1.111))
    row = {r["trade_date"]: r for r in StrategyScreener({"database": {"sqlite_path": db_path}}).history_dates()}[D1]
    # 5.0, -3.0, 1.111 -> 平均 1.037 -> 1.04；上涨 2/3 -> 66.7
    assert row["avg_next_pct"] == 1.04 and row["win_rate"] == 66.7 and row["evaluated"] == 3


# ---------- API ----------

@pytest.fixture
def client(db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(trading_calendar, "load", lambda db_path="", refresh=True: True)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.yaml")
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>app</html>", encoding="utf-8")
    config = {"database": {"sqlite_path": db_path}, "web": {}, "risk": {},
              "llm": {"primary": {"provider": "deepseek", "api_key": "sk-x-1234", "model": "m"},
                      "cache_path": str(tmp_path / "llm.sqlite3")}}
    app = create_app(config, start_scheduler=False, static_dir=static, auth=AuthStore(tmp_path / "auth.json"))
    with TestClient(app) as c:
        yield c


def test_api_dates(client):
    r = client.get("/api/v1/screening/dates")
    assert r.status_code == 200
    body = r.json()
    assert [d["trade_date"] for d in body["dates"]] == [D3, D2, D1]
    assert body["dates"][2]["avg_next_pct"] == 1.0
    assert {s["name"] for s in body["strategies"]} == {s.name for s in STRATEGIES}
    assert {"name": "volume_breakout", "label": "放量突破"} in body["strategies"]


def test_api_dates_limit_bounds(client):
    assert len(client.get("/api/v1/screening/dates?limit=2").json()["dates"]) == 2
    assert client.get("/api/v1/screening/dates?limit=1").status_code == 200
    assert client.get("/api/v1/screening/dates?limit=250").status_code == 200
    assert client.get("/api/v1/screening/dates?limit=0").status_code == 422
    assert client.get("/api/v1/screening/dates?limit=251").status_code == 422


def test_api_picks_default_and_date(client):
    body = client.get("/api/v1/screening/picks").json()
    assert body["trade_date"] == D3 and [p["code"] for p in body["picks"]] == ["600004"]
    body = client.get(f"/api/v1/screening/picks?trade_date={D1}").json()
    assert body["trade_date"] == D1 and {p["code"] for p in body["picks"]} == {"600001", "600002"}


def test_api_picks_strategy_filter(client):
    body = client.get(f"/api/v1/screening/picks?trade_date={D1}&strategy=trend_pullback").json()
    assert [p["code"] for p in body["picks"]] == ["600002"]


def test_api_picks_unknown_date(client):
    r = client.get("/api/v1/screening/picks?trade_date=2020-01-01")
    assert r.status_code == 200
    assert r.json()["picks"] == [] and r.json()["trade_date"] is None


@pytest.mark.parametrize("bad", ["20260921", "2026-9-21", "abc", "2026/09/21", "2026-13-40"])
def test_api_picks_bad_date_422(client, bad):
    assert client.get(f"/api/v1/screening/picks?trade_date={bad}").status_code == 422


def test_api_latest_unchanged(client):
    body = client.get("/api/v1/screening").json()
    assert [p["code"] for p in body["picks"]] == ["600004"]
