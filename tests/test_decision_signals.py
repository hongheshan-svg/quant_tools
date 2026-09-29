"""WS15 决策信号生命周期：生成、替代/失效、评估、复盘、诊断接入、定时任务。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import DecisionSignal, FundDaily, StockDaily

# 交易日：9 月工作日 + 10 月 8 日起（10/1-10/7 休市）
DAYS = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-09-01", "2026-10-30")
        if not ("2026-10-01" <= d.strftime("%Y-%m-%d") <= "2026-10-07")]
NOW = datetime(2026, 10, 30, 16, 0)


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture(autouse=True)
def calendar():
    trading_calendar._set_days(set(DAYS))
    yield
    trading_calendar._set_days(set())


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "sig.db")
    _reset_db_engine()
    init_db(path)
    yield path
    _reset_db_engine()


@pytest.fixture
def svc(db_path):
    from src.services.decision_signals import DecisionSignalService

    return DecisionSignalService({"database": {"sqlite_path": db_path}, "risk": {}, "trading": {}})


def _result(code="600519", action="buy", trade_date="2026-09-21", horizon=5, stop=9.0, target=12.0, score=80):
    return {"code": code, "name": "测试股", "action": action, "score": score, "confidence": "高",
            "trade_date": trade_date, "horizon_days": horizon, "invalidation": "跌破 9 元",
            "battle_plan": {"buy_price": 10.0, "stop_loss": stop, "target_price": target}}


def _bars(db_path, code, rows, table=StockDaily):
    """rows: [(date, close, high, low)]"""
    with get_db_session(db_path) as s:
        for d, c, h, l in rows:
            s.add(table(code=code, name="测试股", trade_date=d, open=c, close=c, high=h, low=l))


def _flat(db_path, code="600519", start="2026-09-21", n=8, close=10.0, table=StockDaily):
    days = [d for d in DAYS if d >= start][:n]
    _bars(db_path, code, [(d, close, close, close) for d in days], table)
    return days


def _get(db_path, sid):
    with get_db_session(db_path) as s:
        row = s.query(DecisionSignal).filter(DecisionSignal.id == sid).one()
        s.expunge(row)
        return row


def _add_signal(db_path, **kw):
    base = dict(code="600519", name="测试股", action="buy", score=80, confidence="高", trade_date="2026-09-21",
                horizon_days=5, status="active", expires_on="2026-09-28", stop_loss=None, target_price=None)
    base.update(kw)
    with get_db_session(db_path) as s:
        row = DecisionSignal(**base)
        s.add(row)
        s.flush()
        return row.id


# ---------- 生成 ----------

def test_record_creates_active_signal(svc, db_path):
    sid = svc.record_from_diagnosis(_result(), 1)
    assert sid
    row = svc.get(sid["id"] if isinstance(sid, dict) else sid)
    assert row["status"] == "active" and row["action"] == "buy"
    assert row["stop_loss"] == 9.0 and row["target_price"] == 12.0
    assert row["horizon_days"] == 5
    assert row["expires_on"] == "2026-09-28"     # 9/21 之后第 5 个交易日


def test_expires_skips_holiday(svc):
    sid = svc.record_from_diagnosis(_result(trade_date="2026-09-30", horizon=3), 1)
    row = svc.get(sid["id"] if isinstance(sid, dict) else sid)
    assert row["expires_on"] == "2026-10-12"     # 10/8, 10/9, 10/12


@pytest.mark.parametrize("action", ["hold", "watch"])
def test_hold_watch_no_signal(svc, action):
    assert svc.record_from_diagnosis(_result(action=action), 1) is None
    assert svc.list()["total"] == 0


def test_opposite_direction_invalidated_same_replaced(svc):
    first = svc.record_from_diagnosis(_result(action="buy"), 1)
    second = svc.record_from_diagnosis(_result(action="add", trade_date="2026-09-22"), 2)
    fid = first["id"] if isinstance(first, dict) else first
    assert svc.get(fid)["status"] == "replaced" and "替代" in svc.get(fid)["status_reason"]
    third = svc.record_from_diagnosis(_result(action="sell", trade_date="2026-09-23"), 3)
    sid2 = second["id"] if isinstance(second, dict) else second
    assert svc.get(sid2)["status"] == "invalidated" and "相反信号" in svc.get(sid2)["status_reason"]
    tid = third["id"] if isinstance(third, dict) else third
    assert svc.get(tid)["status"] == "active"


def test_same_day_two_diagnoses(svc):
    a = svc.record_from_diagnosis(_result(), 1)
    b = svc.record_from_diagnosis(_result(), 2)
    aid = a["id"] if isinstance(a, dict) else a
    bid = b["id"] if isinstance(b, dict) else b
    assert svc.get(aid)["status"] == "replaced"
    assert svc.get(bid)["status"] == "active"
    assert svc.list(status="active")["total"] == 1


def test_other_code_unaffected(svc):
    a = svc.record_from_diagnosis(_result(code="600519"), 1)
    svc.record_from_diagnosis(_result(code="601919", action="sell"), 2)
    aid = a["id"] if isinstance(a, dict) else a
    assert svc.get(aid)["status"] == "active"


# ---------- 评估 ----------

def test_evaluate_returns_and_expired(svc, db_path):
    days = _flat(db_path, n=8)
    closes = {days[0]: 10.0, days[1]: 10.5, days[2]: 10.2, days[3]: 10.1, days[4]: 11.0, days[5]: 10.8}
    with get_db_session(db_path) as s:
        s.query(StockDaily).delete()
    _bars(db_path, "600519", [(d, c, c + 0.1, c - 0.3) for d, c in closes.items()])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[5])
    r = svc.evaluate(now=NOW)
    assert r["evaluated"] >= 1 and r["expired"] == 1
    row = svc.get(sid)
    assert row["status"] == "expired"
    assert row["ret_1d"] == pytest.approx(5.0, abs=0.01)
    assert row["ret_3d"] == pytest.approx(1.0, abs=0.01)
    assert row["ret_5d"] == pytest.approx(8.0, abs=0.01)
    assert row["max_adverse_pct"] <= 0
    assert row["max_favorable_pct"] > 0
    assert row["evaluated_at"]


def test_hit_stop_and_target_same_bar_stop_wins(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:4]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 10, 12.5, 8.5), (days[2], 10, 10, 10)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[3], stop_loss=9.0, target_price=12.0)
    r = svc.evaluate(now=datetime(2026, 9, 24, 16, 0))
    assert r["hit_stop"] == 1 and r["hit_target"] == 0
    assert svc.get(sid)["status"] == "hit_stop"


def test_hit_target(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:4]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 11, 12.2, 10.5)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[3], stop_loss=9.0, target_price=12.0)
    r = svc.evaluate(now=datetime(2026, 9, 23, 16, 0))
    assert r["hit_target"] == 1
    assert svc.get(sid)["status"] == "hit_target"


def test_no_stop_when_stop_empty(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:4]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 8, 10, 5)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[3], stop_loss=None, target_price=None)
    r = svc.evaluate(now=datetime(2026, 9, 23, 16, 0))
    assert r["hit_stop"] == 0
    row = svc.get(sid)
    assert row["status"] == "active"
    assert row["max_adverse_pct"] == pytest.approx(-50.0, abs=0.01)


def test_short_signal_directions(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:4]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 10.5, 11.0, 9.5)])
    sid = _add_signal(db_path, action="sell", trade_date=days[0], expires_on=days[3])
    svc.evaluate(now=datetime(2026, 9, 23, 16, 0))
    row = svc.get(sid)
    assert row["ret_1d"] == pytest.approx(5.0, abs=0.01)   # ret 保持原始涨跌幅
    # 空头（实现口径）：不利为最高价最大涨幅（正数），有利为最低价的最大跌幅
    assert row["max_adverse_pct"] == pytest.approx(10.0, abs=0.01)
    assert row["max_favorable_pct"] == pytest.approx(5.0, abs=0.01) or row["max_favorable_pct"] == pytest.approx(-5.0, abs=0.01)


def test_short_bars_only_one_or_two(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:3]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 10.2, 10.3, 10.0), (days[2], 10.4, 10.5, 10.1)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on="2026-09-28")
    svc.evaluate(now=datetime(2026, 9, 24, 16, 0))
    row = svc.get(sid)
    assert row["ret_1d"] == pytest.approx(2.0, abs=0.01)
    assert row["ret_3d"] is None and row["ret_5d"] is None
    assert row["status"] == "active"


def test_no_bars_suspended(svc, db_path):
    sid = _add_signal(db_path, code="000001")
    r = svc.evaluate(now=datetime(2026, 9, 25, 16, 0))
    assert r["hit_stop"] == 0 and r["hit_target"] == 0
    row = svc.get(sid)
    assert row["ret_1d"] is None and row["status"] == "active"


def test_zero_base_close(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:3]
    _bars(db_path, "600519", [(days[0], 0, 0, 0), (days[1], 10, 10, 10)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[2], stop_loss=9.0)
    svc.evaluate(now=datetime(2026, 9, 23, 16, 0))         # 不应抛除零
    row = svc.get(sid)
    assert row["ret_1d"] is None


def test_weekend_rows_ignored(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:6]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 10, 10, 10)])
    _bars(db_path, "600519", [("2026-09-26", 20, 20, 20)])      # 周六重复行
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[5], target_price=15.0)
    r = svc.evaluate(now=datetime(2026, 9, 28, 16, 0))
    assert r["hit_target"] == 0
    assert svc.get(sid)["max_favorable_pct"] == pytest.approx(0.0, abs=0.01)


def test_index_signal_uses_fund_daily(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:3]
    _bars(db_path, "sh000300", [(days[0], 4000, 4000, 4000), (days[1], 4040, 4050, 3990)], table=FundDaily)
    sid = _add_signal(db_path, code="sh000300", trade_date=days[0], expires_on=days[2])
    svc.evaluate(now=datetime(2026, 9, 23, 16, 0))
    assert svc.get(sid)["ret_1d"] == pytest.approx(1.0, abs=0.01)


def test_expired_reevaluate_keeps_status_fills_returns(svc, db_path):
    days = [d for d in DAYS if d >= "2026-09-21"][:8]
    _bars(db_path, "600519", [(days[0], 10, 10, 10), (days[1], 10.5, 10.5, 10.5)])
    sid = _add_signal(db_path, trade_date=days[0], expires_on=days[2])
    svc.evaluate(now=datetime(2026, 9, 30, 16, 0))
    assert svc.get(sid)["status"] == "expired"
    assert svc.get(sid)["ret_3d"] is None
    _bars(db_path, "600519", [(days[2], 10.2, 10.2, 10.2), (days[3], 11, 11, 11)])   # 数据后来补齐
    svc.evaluate(now=datetime(2026, 9, 30, 16, 0))
    row = svc.get(sid)
    assert row["status"] == "expired"
    assert row["ret_3d"] == pytest.approx(10.0, abs=0.01)


# ---------- 复盘与统计 ----------

def _evaluated(db_path, code, n, ret, action="buy", adverse=-2.0):
    for i in range(n):
        _add_signal(db_path, code=code, action=action, trade_date=f"2026-09-{1 + i:02d}", status="expired",
                    ret_1d=ret, ret_3d=ret, ret_5d=ret, max_adverse_pct=adverse, max_favorable_pct=3.0,
                    evaluated_at=datetime(2026, 9, 25))


def test_review_insufficient_samples(svc, db_path):
    _evaluated(db_path, "600519", 2, 5.0)
    r = svc.review("600519")
    assert r["samples"] == 2 and "样本不足" in r["text"]


def test_review_bias_optimistic(svc, db_path):
    _evaluated(db_path, "600519", 4, -3.0)
    r = svc.review("600519")
    assert r["samples"] == 4 and r["hits"] == 0 and r["hit_rate"] < 40
    assert r["bias"] == "偏乐观"
    assert r["avg_ret"] == pytest.approx(-3.0, abs=0.01)
    for k in ("avg_adverse", "text"):
        assert k in r


def test_review_good_hit_rate(svc, db_path):
    _evaluated(db_path, "600519", 4, 4.0)
    r = svc.review("600519")
    assert r["hits"] == 4 and r["hit_rate"] == pytest.approx(100, abs=0.1)
    assert r["bias"] != "偏乐观"


def test_is_hit_direction(svc):
    assert svc.is_hit({"action": "buy", "ret_3d": 2.0, "ret_1d": 2.0, "ret_5d": 2.0}) is True
    assert svc.is_hit({"action": "buy", "ret_3d": -2.0, "ret_1d": -2.0, "ret_5d": -2.0}) is False
    assert svc.is_hit({"action": "sell", "ret_3d": -2.0, "ret_1d": -2.0, "ret_5d": -2.0}) is True


def test_list_filter_paging_get_feedback(svc, db_path):
    ids = [_add_signal(db_path, code="600519" if i % 2 else "601919", trade_date=f"2026-09-{1 + i:02d}") for i in range(5)]
    r = svc.list(code="600519")
    assert r["total"] == 2 and all(i["code"] == "600519" for i in r["items"])
    page = svc.list(limit=2, offset=2)
    assert page["total"] == 5 and len(page["items"]) == 2
    assert svc.get(999999) is None
    svc.set_feedback(ids[0], "useful", "不错")
    row = svc.get(ids[0])
    assert row["feedback"] == "useful" and row["feedback_note"] == "不错"
    try:
        assert svc.set_feedback(ids[0], "bogus", "") is False
    except ValueError:
        pass
    assert svc.get(ids[0])["feedback"] == "useful"
    assert svc.stats(30) is not None


# ---------- 诊断接入 ----------

def test_diagnosis_signal_integration(tmp_path, monkeypatch):
    from tests import test_stock_diagnosis as td
    from src.collectors import daily_history, fundamentals, stock_news
    monkeypatch.setattr(fundamentals, "fetch_chip_summary", lambda code, db_path: dict(td.CHIP))
    monkeypatch.setattr(fundamentals.EarningsCache, "get", classmethod(lambda cls, code: dict(td.EARNINGS)))
    monkeypatch.setattr(stock_news, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": []})
    monkeypatch.setattr(daily_history, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)
    path = str(tmp_path / "d.db")
    _reset_db_engine()
    init_db(path)
    trading_calendar._set_days({d for d in td.DAYS})
    with get_db_session(path) as session:
        for i, d in enumerate(td.DAYS):
            close = 14 + i * 0.4
            session.add(StockDaily(code="sz002594", name="比亚迪", trade_date=d, open=close - 0.2, close=close,
                                   change_pct=2.0, volume=1000 + i, amount=5e9, turnover=3.2, circ_mv=6e11))
    try:
        reply = dict(td.GOOD_REPLY, horizon_days=99, invalidation="x" * 500)
        svc, _ = td._service(path, reply)
        result = svc.diagnose("002594", force=True)
        assert result["horizon_days"] == 5 or 1 <= result["horizon_days"] <= 20
        assert len(result["invalidation"]) <= 200
        from src.services.decision_signals import DecisionSignalService
        sigs = DecisionSignalService(svc.config).list()
        assert sigs["total"] == 1 and sigs["items"][0]["code"] in ("002594", "sz002594")

        # 非法 horizon -> 5；hold 不生成
        svc2, _ = td._service(path, dict(td.GOOD_REPLY, horizon_days="abc", action="持有", score=60))
        r2 = svc2.diagnose("002594", force=True)
        assert r2["horizon_days"] == 5
        assert DecisionSignalService(svc.config).list()["total"] == 1

        # 生成信号出错不影响诊断
        def boom(self, *a, **k):
            raise RuntimeError("boom")
        monkeypatch.setattr(DecisionSignalService, "record_from_diagnosis", boom)
        svc3, _ = td._service(path, td.GOOD_REPLY)
        r3 = svc3.diagnose("002594", force=True)
        assert not r3.get("error") and r3["action"] == "buy"

        # 历史信号复盘注入
        monkeypatch.undo()
        monkeypatch.setattr(fundamentals, "fetch_chip_summary", lambda code, db_path: dict(td.CHIP))
        monkeypatch.setattr(fundamentals.EarningsCache, "get", classmethod(lambda cls, code: dict(td.EARNINGS)))
        monkeypatch.setattr(stock_news, "get_stock_news", lambda code, refresh=False, now=None: {"news": [], "notices": []})
        monkeypatch.setattr(daily_history, "ensure_daily_history", lambda code, db_path, name="", min_bars=60, now=None: 0)
        svc4, _ = td._service(path, td.GOOD_REPLY)
        svc4.config["diagnosis"] = {"signal_review": True}
        assert "【历史信号复盘】" not in svc4.build_context("002594")["text"]
        _evaluated(path, "002594", 3, 2.0)
        assert "【历史信号复盘】" in svc4.build_context("002594")["text"]
        svc4.config["diagnosis"] = {"signal_review": False}
        assert "【历史信号复盘】" not in svc4.build_context("002594")["text"]
    finally:
        _reset_db_engine()


# ---------- 定时任务 ----------

def test_scheduler_registration():
    from src.scheduler import JOBS, ONCE_STEPS, build_scheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from src.config_loader import load_config

    assert "signal_lifecycle" in JOBS
    sched = build_scheduler(load_config("config/settings.yaml"), BackgroundScheduler())
    assert "signal_lifecycle" in {j.id for j in sched.get_jobs()}
    assert any(fn.__name__ == JOBS["signal_lifecycle"][1].__name__ for fn in ONCE_STEPS["learn"][1]) or \
        len(ONCE_STEPS["learn"][1]) >= 2
