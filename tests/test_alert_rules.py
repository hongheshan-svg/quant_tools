"""盘中提醒规则：校验、试算、Web API、自选股指定代码。"""

from __future__ import annotations

from datetime import date

import pytest

from src import settings_store, trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import AlertRecord, StockDaily
from src.services import alert_service as alert_mod
from src.services.alert_service import AlertService, RULE_TYPES, validate_rule
from tests.test_api import _wait, env  # noqa: F401  (env 是 fixture)

TODAY = date.today().strftime("%Y-%m-%d")
ALL_TYPES = {"price_cross", "change_pct", "volume_spike", "ma_cross", "macd_cross", "kdj_cross", "rsi"}


# ---------- RULE_TYPES / validate_rule ----------

def test_rule_types_metadata():
    assert ALL_TYPES <= set(RULE_TYPES)
    for meta in RULE_TYPES.values():
        assert meta["label"]
        for f in meta["fields"]:
            assert f["key"] and f["label"] and f["type"]


VALID = [
    ({"code": "600519", "type": "price_cross", "direction": "above", "price": 1800}, {"price": 1800}),
    ({"code": "600519", "type": "price_cross", "direction": "below", "price": "1700.5"}, {"price": 1700.5}),
    ({"code": "600519", "type": "change_pct", "direction": "up", "change_pct": 5}, {"change_pct": 5}),
    ({"code": "600519", "type": "change_pct", "direction": "down", "change_pct": "3.5"}, {"change_pct": 3.5}),
    ({"code": "600519", "type": "volume_spike", "multiplier": 3}, {"multiplier": 3}),
    ({"code": "600519", "type": "ma_cross", "period": 20, "direction": "above"}, {"period": 20}),
    ({"code": "600519", "type": "ma_cross", "period": "10", "direction": "below"}, {"period": 10}),
    ({"code": "600519", "type": "macd_cross", "direction": "golden"}, {}),
    ({"code": "600519", "type": "macd_cross", "direction": "dead"}, {}),
    ({"code": "600519", "type": "kdj_cross", "direction": "golden"}, {}),
    ({"code": "600519", "type": "kdj_cross", "direction": "dead"}, {}),
    ({"code": "600519", "type": "rsi", "period": 6, "direction": "above", "value": 80}, {"value": 80}),
    ({"code": "600519", "type": "rsi", "period": 14, "direction": "below", "value": 20}, {"value": 20}),
]


@pytest.mark.parametrize("rule,expected", VALID)
def test_validate_rule_valid(rule, expected):
    out = validate_rule(rule)
    assert out["code"] == "600519" and out["type"] == rule["type"]
    assert out["enabled"] is True
    for k, v in expected.items():
        assert out[k] == v and isinstance(out[k], (int, float))


@pytest.mark.parametrize("raw", ["600519", "sh600519", "SH600519", "600519.SH", "600519.sh", " 600519 "])
def test_validate_rule_code_formats(raw):
    rule = {"code": raw, "type": "macd_cross", "direction": "golden"}
    assert validate_rule(rule)["code"] == "600519"


def test_validate_rule_enabled_and_note_and_unknown_fields():
    base = {"code": "600519", "type": "macd_cross", "direction": "golden"}
    assert validate_rule({**base, "enabled": "false"})["enabled"] is False
    assert validate_rule({**base, "enabled": False})["enabled"] is False
    assert validate_rule({**base, "enabled": "true"})["enabled"] is True
    assert validate_rule({**base, "enabled": True})["enabled"] is True
    assert validate_rule({**base, "note": "  金叉  "})["note"] == "金叉"
    out = validate_rule({**base, "foo": 1, "price": 3})
    assert "foo" not in out and "price" not in out


INVALID = [
    {"type": "macd_cross", "direction": "golden"},                                          # 缺 code
    {"code": "", "type": "macd_cross", "direction": "golden"},
    {"code": "600519", "type": "nope"},                                                     # 未知类型
    {"code": "600519"},                                                                     # 缺类型
    {"code": "abc", "type": "macd_cross", "direction": "golden"},                           # 代码非法
    {"code": "600519", "type": "price_cross", "direction": "above", "price": 0},
    {"code": "600519", "type": "price_cross", "direction": "above", "price": -3},
    {"code": "600519", "type": "price_cross", "direction": "above", "price": "abc"},
    {"code": "600519", "type": "price_cross", "direction": "above"},                        # 缺价格
    {"code": "600519", "type": "price_cross", "direction": "up", "price": 10},              # 方向非法
    {"code": "600519", "type": "change_pct", "direction": "above", "change_pct": 3},
    {"code": "600519", "type": "change_pct", "direction": "up", "change_pct": 0},
    {"code": "600519", "type": "volume_spike", "multiplier": 0},
    {"code": "600519", "type": "volume_spike", "multiplier": -1},
    {"code": "600519", "type": "ma_cross", "period": 0, "direction": "above"},
    {"code": "600519", "type": "ma_cross", "period": 20, "direction": "golden"},
    {"code": "600519", "type": "macd_cross", "direction": "above"},
    {"code": "600519", "type": "kdj_cross", "direction": "up"},
    {"code": "600519", "type": "rsi", "period": 6, "direction": "above", "value": 0},
    {"code": "600519", "type": "rsi", "period": 6, "direction": "golden", "value": 80},
]


@pytest.mark.parametrize("rule", INVALID)
def test_validate_rule_invalid(rule):
    with pytest.raises(ValueError):
        validate_rule(rule)


def test_validate_rule_does_not_mutate_input():
    rule = {"code": "sh600519", "type": "macd_cross", "direction": "golden", "enabled": "false"}
    validate_rule(rule)
    assert rule["code"] == "sh600519" and rule["enabled"] == "false"


# ---------- AlertService：disabled 与 test_rule ----------

@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "rules.db")
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    init_db(path)
    alert_mod.reset_state()
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: True)
    monkeypatch.setattr(AlertService, "_positions", lambda self: [])
    with get_db_session(path) as session:
        session.add(StockDaily(code="600519", name="贵州茅台", trade_date=TODAY, close=1800.0, change_pct=2.0, volume=1000))
    yield path
    alert_mod.reset_state()
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _svc(path, **alerts):
    return AlertService({"database": {"sqlite_path": path}, "alerts": alerts, "notifier": {}})


def test_disabled_rule_skipped(db_path):
    on = {"code": "600519", "type": "price_cross", "direction": "above", "price": 1700}
    events = [e for e in _svc(db_path, rules=[on]).evaluate() if e.rule_id]
    assert len(events) == 1
    events = [e for e in _svc(db_path, rules=[{**on, "enabled": False}]).evaluate() if e.rule_id]
    assert events == []


def test_test_rule_triggered_and_not_triggered(db_path, monkeypatch):
    calls = []
    monkeypatch.setattr(alert_mod, "broadcast", lambda *a, **k: calls.append(1) or {})
    svc = _svc(db_path)
    hit = svc.test_rule({"code": "600519", "type": "price_cross", "direction": "above", "price": 1700})
    assert hit["triggered"] is True and hit["message"] and hit["quote"]
    miss = svc.test_rule({"code": "600519", "type": "price_cross", "direction": "above", "price": 1900})
    assert miss["triggered"] is False and miss["message"] and miss["quote"]
    assert hit["message"] != miss["message"]
    # 连续试算不受冷却影响
    assert svc.test_rule({"code": "600519", "type": "price_cross", "direction": "above", "price": 1700})["triggered"] is True
    assert calls == []
    with get_db_session(db_path) as session:
        assert session.query(AlertRecord).count() == 0


def test_test_rule_ignores_trade_session_and_enabled(db_path, monkeypatch):
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: False)
    r = _svc(db_path).test_rule({"code": "sh600519", "type": "change_pct", "direction": "up", "change_pct": 1, "enabled": False})
    assert r["triggered"] is True


def test_test_rule_no_quote(db_path):
    r = _svc(db_path).test_rule({"code": "000001", "type": "price_cross", "direction": "above", "price": 10})
    assert r == {"triggered": False, "message": "没有取到行情", "quote": None}


# ---------- API ----------

def _rule(**kw):
    return {"code": "600519", "type": "macd_cross", "direction": "golden", **kw}


def test_rules_get_empty(env):  # noqa: F811
    client, _, _ = env
    body = client.get("/api/v1/alerts/rules").json()
    assert body["rules"] == [] and ALL_TYPES <= set(body["types"])


def test_rules_put_normalizes_and_persists(env):  # noqa: F811
    client, _, _ = env
    rules = [_rule(code="sh600519", enabled="false", note=" 备注 ", junk=1),
             {"code": "601919.SH", "type": "price_cross", "direction": "below", "price": "9.5"}]
    res = client.put("/api/v1/alerts/rules", json={"rules": rules})
    assert res.status_code == 200
    saved = res.json()["rules"]
    assert saved[0]["code"] == "600519" and saved[0]["enabled"] is False and saved[0]["note"] == "备注"
    assert "junk" not in saved[0] and saved[1]["code"] == "601919" and saved[1]["price"] == 9.5
    assert settings_store.read_settings()["alerts"]["rules"] == saved
    assert client.get("/api/v1/alerts/rules").json()["rules"] == saved


def test_rules_put_empty_list_ok(env):  # noqa: F811
    client, _, _ = env
    client.put("/api/v1/alerts/rules", json={"rules": [_rule()]})
    res = client.put("/api/v1/alerts/rules", json={"rules": []})
    assert res.status_code == 200 and res.json() == {"rules": []}
    assert settings_store.read_settings()["alerts"]["rules"] == []


def test_rules_put_invalid_422_with_index(env):  # noqa: F811
    client, _, _ = env
    res = client.put("/api/v1/alerts/rules", json={"rules": [_rule(), {"code": "600519", "type": "price_cross", "direction": "above", "price": -1}]})
    assert res.status_code == 422 and "第 2 条" in str(res.json()["detail"])
    assert "rules" not in (settings_store.read_settings().get("alerts") or {})   # 整体不写入


def test_rules_put_keeps_other_alert_keys(env):  # noqa: F811
    client, _, _ = env
    assert client.put("/api/v1/alerts/settings", json={"cooldown_minutes": 45}).status_code == 200
    client.put("/api/v1/alerts/rules", json={"rules": [_rule()]})
    saved = settings_store.read_settings()["alerts"]
    assert saved["cooldown_minutes"] == 45 and len(saved["rules"]) == 1


def test_rules_test_endpoint(env, monkeypatch):  # noqa: F811
    client, _, _ = env
    seen = []
    monkeypatch.setattr(AlertService, "test_rule",
                        lambda self, rule: seen.append(rule) or {"triggered": True, "message": "命中", "quote": {"price": 1}})
    res = client.post("/api/v1/alerts/rules/test", json={"rule": _rule(code="sh600519")})
    assert res.status_code == 200 and res.json() == {"triggered": True, "message": "命中", "quote": {"price": 1}}
    assert seen and bare(seen[0]["code"]) == "600519"
    bad = client.post("/api/v1/alerts/rules/test", json={"rule": {"code": "600519", "type": "nope"}})
    assert bad.status_code == 422


def bare(code: str) -> str:
    return code[-6:] if len(code) > 6 and code[-6:].isdigit() else code[:6]


def test_alert_settings_endpoints(env):  # noqa: F811
    client, _, _ = env
    body = client.get("/api/v1/alerts/settings").json()
    for key in ("enabled", "cooldown_minutes", "big_drop_pct", "near_stop_pct", "market_regime", "regime_score_drop", "watchlist"):
        assert key in body
    client.put("/api/v1/alerts/rules", json={"rules": [_rule()]})
    res = client.put("/api/v1/alerts/settings", json={"enabled": False, "watchlist": ["sh600519", "601919.SH"]})
    assert res.status_code == 200
    got = client.get("/api/v1/alerts/settings").json()
    assert got["enabled"] is False and got["watchlist"] == ["600519", "601919"]
    assert got["cooldown_minutes"] == body["cooldown_minutes"]                     # 部分字段不影响其他
    assert len(settings_store.read_settings()["alerts"]["rules"]) == 1             # 不覆盖 rules
    full = {"enabled": True, "cooldown_minutes": 10, "big_drop_pct": -5, "near_stop_pct": 3,
            "market_regime": False, "regime_score_drop": 20, "watchlist": []}
    assert client.put("/api/v1/alerts/settings", json=full).status_code == 200
    got = client.get("/api/v1/alerts/settings").json()
    assert {k: got[k] for k in full} == full


@pytest.mark.parametrize("payload", [{"cooldown_minutes": "abc"}, {"enabled": "maybe"}, {"big_drop_pct": "x"}, {"watchlist": "600519"}])
def test_alert_settings_invalid_422(env, payload):  # noqa: F811
    client, _, _ = env
    assert client.put("/api/v1/alerts/settings", json=payload).status_code == 422


# ---------- WatchlistReportService codes ----------

def test_watchlist_report_run_with_codes(tmp_path, monkeypatch):
    from src.services import watchlist_report as wr
    from src.services.watchlist import WatchlistService

    path = str(tmp_path / "wl.db")
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None
    init_db(path)
    try:
        cfg = {"database": {"sqlite_path": path}, "notifier": {}}
        svc = wr.WatchlistReportService(cfg, diagnosis=object())
        seen = []
        monkeypatch.setattr(svc, "_diagnose_one", lambda stock, threshold: seen.append(stock["code"]) or {"error": "跳过"})
        monkeypatch.setattr(svc, "_save", lambda *a, **k: None)
        monkeypatch.setattr(WatchlistService, "list", lambda self: [{"code": "000001", "name": "平安银行"}])

        svc.run(push=False, codes=["600519", "601919"])
        assert sorted(seen) == ["600519", "601919"]
        for empty in ([], None):
            seen.clear()
            svc.run(push=False, codes=empty)
            assert seen == ["000001"]
    finally:
        db_module._engine.dispose()
        db_module._engine = None
        db_module._SessionFactory = None
