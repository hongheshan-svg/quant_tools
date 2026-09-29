"""首次配置向导：setup_status 与 /system/setup 接口。"""

from __future__ import annotations

from datetime import date

import pytest

from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo, TradeCalendar, Watchlist
from src.services.setup_status import setup_status


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "setup.db")
    _reset_db_engine()
    init_db(path)
    empty = tmp_path / "pw_empty"
    empty.mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))
    yield path
    _reset_db_engine()


def _cfg(db_path, **extra):
    cfg = {"database": {"sqlite_path": db_path}, "web": {"host": "127.0.0.1"},
           "llm": {"primary": {"provider": "deepseek", "api_key": "sk-real-1234", "model": "deepseek-chat"}},
           "notifier": {}}
    cfg.update(extra)
    return cfg


def _items(status):
    return {i["key"]: i for i in status["items"]}


def test_structure_and_counts(db_path):
    st = setup_status(_cfg(db_path))
    assert set(st) >= {"items", "done", "total", "required_missing"}
    for it in st["items"]:
        assert set(it) >= {"key", "label", "done", "required", "hint", "link"}
    assert st["total"] == len(st["items"])
    assert st["done"] == sum(1 for i in st["items"] if i["done"])
    assert st["required_missing"] == sum(1 for i in st["items"] if i["required"] and not i["done"])


def test_llm_item(db_path):
    assert _items(setup_status(_cfg(db_path)))["llm"]["done"] is True
    assert _items(setup_status(_cfg(db_path)))["llm"]["required"] is True
    for key in ("", "your-api-key"):
        cfg = _cfg(db_path, llm={"primary": {"provider": "deepseek", "api_key": key, "model": "m"}})
        assert _items(setup_status(cfg))["llm"]["done"] is False


def test_data_item(db_path):
    assert _items(setup_status(_cfg(db_path)))["data"]["done"] is False
    assert _items(setup_status(_cfg(db_path)))["data"]["required"] is True
    with get_db_session(db_path) as s:
        s.add(StockDaily(code="600519", name="x", trade_date=date.today().isoformat(), close=1.0))
    assert _items(setup_status(_cfg(db_path)))["data"]["done"] is True


def test_data_item_by_stock_info(db_path):
    with get_db_session(db_path) as s:
        for i in range(1000):
            s.add(StockInfo(code=f"{i:06d}", name=f"n{i}"))
    assert _items(setup_status(_cfg(db_path)))["data"]["done"] is True


def test_calendar_item(db_path):
    assert _items(setup_status(_cfg(db_path)))["calendar"]["done"] is False
    with get_db_session(db_path) as s:
        s.add(TradeCalendar(trade_date=date.today().isoformat()))
    it = _items(setup_status(_cfg(db_path)))["calendar"]
    assert it["done"] is True and it["required"] is False


def test_notifier_item(db_path):
    assert _items(setup_status(_cfg(db_path)))["notifier"]["done"] is False
    placeholder = {"wechat": {"enabled": True, "webhook_url": "https://x/your-key"}}
    assert _items(setup_status(_cfg(db_path, notifier=placeholder)))["notifier"]["done"] is False
    ok = {"wechat": {"enabled": True, "webhook_url": "https://qyapi.example.com/send?key=abc"}}
    it = _items(setup_status(_cfg(db_path, notifier=ok)))["notifier"]
    assert it["done"] is True and it["required"] is False


def test_watchlist_item(db_path):
    assert _items(setup_status(_cfg(db_path)))["watchlist"]["done"] is False
    with get_db_session(db_path) as s:
        s.add(Watchlist(code="600519", name="茅台"))
    assert _items(setup_status(_cfg(db_path)))["watchlist"]["done"] is True


def test_browser_item(db_path, tmp_path, monkeypatch):
    it = _items(setup_status(_cfg(db_path)))["browser"]
    assert it["done"] is False and it["required"] is False
    good = tmp_path / "pw_good"
    (good / "chromium-123").mkdir(parents=True)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(good))
    assert _items(setup_status(_cfg(db_path)))["browser"]["done"] is True


def test_web_auth_item(db_path):
    assert "web_auth" not in _items(setup_status(_cfg(db_path)))
    cfg = _cfg(db_path, web={"host": "0.0.0.0", "auth_enabled": False})
    it = _items(setup_status(cfg))["web_auth"]
    assert it["required"] is True and it["done"] is False
    cfg = _cfg(db_path, web={"host": "0.0.0.0", "auth_enabled": True})
    assert _items(setup_status(cfg))["web_auth"]["done"] is True


def test_required_missing_counts(db_path):
    cfg = _cfg(db_path, llm={"primary": {"api_key": ""}}, web={"host": "0.0.0.0"})
    st = setup_status(cfg)
    missing = {i["key"] for i in st["items"] if i["required"] and not i["done"]}
    assert {"llm", "data", "web_auth"} <= missing
    assert st["required_missing"] == len(missing)


def test_errors_do_not_raise(tmp_path):
    _reset_db_engine()
    bad = str(tmp_path / "no_such_dir" / "x" / "bad.db")
    cfg = {"database": {"sqlite_path": bad}, "web": {}, "llm": {}, "notifier": {}}
    try:
        st = setup_status(cfg)
    finally:
        _reset_db_engine()
    assert st["total"] == len(st["items"])


def test_api_setup(tmp_path, monkeypatch):
    from tests.test_api import env  # noqa: F401
