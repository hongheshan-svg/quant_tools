from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import AlertRecord, StockDaily, TradeSignal
from src.notifier.noise import NoiseFilter, in_quiet_hours
from src.services import alert_service as alert_mod
from src.services.alert_service import AlertService

TODAY = date.today().strftime("%Y-%m-%d")


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


# ---------- 降噪 ----------

def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 9, 28, 23, 0), ["22:00", "08:00"]) is True   # 跨午夜
    assert in_quiet_hours(datetime(2026, 9, 28, 7, 59), ["22:00", "08:00"]) is True
    assert in_quiet_hours(datetime(2026, 9, 28, 10, 0), ["22:00", "08:00"]) is False
    assert in_quiet_hours(datetime(2026, 9, 28, 12, 0), ["11:30", "13:00"]) is True
    assert in_quiet_hours(datetime(2026, 9, 28, 12, 0), []) is False


def test_noise_filter_cooldown_and_quiet_hours():
    noise = NoiseFilter(cooldown_minutes=30, quiet_hours=["11:30", "13:00"])
    t = datetime(2026, 9, 28, 10, 0)
    assert noise.check("a", "info", t) == ""
    assert noise.check("a", "info", t + timedelta(minutes=10)) == "冷却中"
    assert noise.check("a", "info", t + timedelta(minutes=31)) == ""
    lunch = datetime(2026, 9, 28, 12, 0)
    assert noise.check("b", "warning", lunch) == "免打扰时段"
    assert noise.check("b", "warning", lunch + timedelta(minutes=5)) == "冷却中"   # 免打扰期间也不重复记录
    assert noise.check("c", "critical", lunch) == ""                               # 紧急提醒不受免打扰限制


# ---------- 盘中提醒 ----------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "alerts.db")
    _reset_db_engine()
    init_db(path)
    alert_mod.reset_state()
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: True)
    with get_db_session(path) as session:
        session.add(TradeSignal(code="600001", name="涨停股", signal_date=TODAY, signal_type="premarket"))
        session.add(TradeSignal(code="000002", name="大跌股", signal_date=TODAY, signal_type="buy"))
        session.add(StockDaily(code="sh600001", name="涨停股", trade_date=TODAY, close=11.0, change_pct=10.0))
        session.add(StockDaily(code="000002", name="大跌股", trade_date=TODAY, close=9.2, change_pct=-8.0))
        session.add(StockDaily(code="300003", name="持仓股", trade_date=TODAY, close=9.4, change_pct=-2.0, volume=5000))
        for i in range(1, 21):
            day = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
            session.add(StockDaily(code="300003", name="持仓股", trade_date=day, close=9.6, change_pct=0.0, volume=1000))
    monkeypatch.setattr(AlertService, "_positions", lambda self: [
        {"code": "300003", "name": "持仓股", "stop_loss": 9.5, "target_price": 12.0},
    ])
    yield path
    alert_mod.reset_state()
    _reset_db_engine()


def _service(db_path: str, **alerts) -> AlertService:
    return AlertService({"database": {"sqlite_path": db_path}, "alerts": alerts, "notifier": {}})


def test_builtin_alerts(db_path):
    events = {(e.code, e.alert_type): e for e in _service(db_path).evaluate()}
    assert set(events) == {("600001", "limit_up"), ("000002", "big_drop"), ("300003", "stop_loss")}
    assert events[("300003", "stop_loss")].severity == "critical"
    assert "跌破止损价 9.50" in events[("300003", "stop_loss")].message

    # 第二次检查：仍在涨停价不再提醒；打开涨停提醒炸板
    with get_db_session(db_path) as session:
        session.query(StockDaily).filter(StockDaily.code == "sh600001").update({"change_pct": 7.5, "close": 10.75})
    types = {(e.code, e.alert_type) for e in _service(db_path).evaluate()}
    assert ("600001", "limit_open") in types and ("600001", "limit_up") not in types


def test_custom_rules(db_path):
    service = _service(db_path, rules=[
        {"code": "300003", "type": "price_cross", "direction": "below", "price": 9.5},
        {"code": "300003", "type": "volume_spike", "multiplier": 3},
        {"code": "000002", "type": "change_pct", "direction": "down", "change_pct": 5},
        {"code": "000002", "type": "change_pct", "direction": "up", "change_pct": 5},  # 不触发
    ])
    rule_events = [e for e in service.evaluate() if e.rule_id]
    assert [(e.code, e.alert_type) for e in rule_events] == [
        ("300003", "price_cross"), ("300003", "volume_spike"), ("000002", "change_pct"),
    ]
    assert "成交量为近 20 日均量的 5.0 倍" in rule_events[1].message


def test_run_dedupes_pushes_merged_and_records(db_path, monkeypatch):
    pushed = []
    monkeypatch.setattr(alert_mod, "enabled_channels", lambda config, kind=None: ["wechat"] if kind == "alert" else [])
    monkeypatch.setattr(alert_mod, "broadcast", lambda config, title, content, kind=None: pushed.append((title, content)) or {"wechat": True})

    service = _service(db_path)
    assert service.run(datetime(2026, 9, 28, 10, 0)) == {"alerts": 3, "pushed": 3}
    assert len(pushed) == 1 and pushed[0][1].count("\n") == 2  # 三条合并为一条消息
    assert "🔴 持仓 持仓股(300003) 跌破止损价" in pushed[0][1]

    assert service.run(datetime(2026, 9, 28, 10, 2)) == {"alerts": 0, "pushed": 0}  # 冷却期内不重复
    with get_db_session(db_path) as session:
        assert session.query(AlertRecord).count() == 3
        assert all(r.notified for r in session.query(AlertRecord).all())
    assert [r["type"] for r in service.recent()][:1] and service.recent()[0]["notified"] is True


def test_quiet_hours_record_without_push(db_path, monkeypatch):
    pushed = []
    monkeypatch.setattr(alert_mod, "enabled_channels", lambda config, kind=None: ["wechat"] if kind == "alert" else [])
    monkeypatch.setattr(alert_mod, "broadcast", lambda config, title, content, kind=None: pushed.append(content) or {"wechat": True})
    service = AlertService({"database": {"sqlite_path": db_path}, "alerts": {}, "notifier": {"quiet_hours": ["09:00", "11:00"]}})

    result = service.run(datetime(2026, 9, 28, 10, 0))
    assert result == {"alerts": 3, "pushed": 1}
    assert "跌破止损" in pushed[0] and "封涨停" not in pushed[0]  # 只推送紧急提醒
    reasons = {r["type"]: (r["notified"], r["reason"]) for r in service.recent()}
    assert reasons["封涨停"] == (False, "免打扰时段") and reasons["跌破止损"] == (True, "")


def test_run_skipped_outside_session_or_disabled(db_path, monkeypatch):
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: False)
    assert _service(db_path).run() == {"alerts": 0, "skipped": "非交易时段"}
    assert _service(db_path, enabled=False).run() == {"alerts": 0, "skipped": "未启用"}


def test_no_channel_still_records(db_path):
    assert _service(db_path).run(datetime(2026, 9, 28, 10, 0)) == {"alerts": 3, "pushed": 0}
    assert {r["reason"] for r in _service(db_path).recent()} == {"未启用推送"}
