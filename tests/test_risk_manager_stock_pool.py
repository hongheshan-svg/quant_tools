from __future__ import annotations

import types
from datetime import date, datetime, timedelta

import pandas as pd

from src.collectors import stock_info as stock_info_mod
from src.collectors.stock_info import StockInfoCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import LimitUpStock, StockInfo
from src.strategy.risk_manager import RiskManager


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _days_ago(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")


def _make_risk_manager(tmp_path, pool_cfg: dict, monkeypatch) -> tuple[RiskManager, list]:
    db_path = str(tmp_path / "stock_pool.db")
    _reset_db_engine()
    init_db(db_path)
    refresh_calls = []
    monkeypatch.setattr(
        StockInfoCollector,
        "refresh_if_stale",
        lambda self, path, max_age_hours=24: refresh_calls.append(path) or 0,
    )
    rm = RiskManager({"database": {"sqlite_path": db_path}, "risk": {}})
    rm.pool_cfg = pool_cfg
    return rm, refresh_calls


def test_blacklist_codes_ignore_exchange_prefix(tmp_path, monkeypatch):
    rm, _ = _make_risk_manager(tmp_path, {"blacklist": {"codes": ["600000"]}}, monkeypatch)

    assert rm._check_stock_pool("sh600000") is False
    assert rm._check_stock_pool("600000") is False
    assert rm._check_stock_pool("600001") is True
    _reset_db_engine()


def test_name_keywords_use_passed_name_or_db_name(tmp_path, monkeypatch):
    rm, _ = _make_risk_manager(tmp_path, {"blacklist": {"name_keywords": ["ST", "退"]}}, monkeypatch)
    with get_db_session(rm.db_path) as session:
        session.add(StockInfo(code="000002", name="*ST样本", exchange="sz", list_date="2000-01-01"))

    assert rm._check_stock_pool("000001", "退市样本") is False
    assert rm._check_stock_pool("000002") is False  # 未传名称时从 stock_info 补全
    assert rm._check_stock_pool("000003", "正常样本") is True
    _reset_db_engine()


def test_min_listing_days_filters_new_stocks_and_refreshes_once(tmp_path, monkeypatch):
    rm, refresh_calls = _make_risk_manager(tmp_path, {"blacklist": {"min_listing_days": 60}}, monkeypatch)
    with get_db_session(rm.db_path) as session:
        session.add(StockInfo(code="301001", name="次新样本", exchange="sz", list_date=_days_ago(10)))
        session.add(StockInfo(code="600001", name="老股样本", exchange="sh", list_date=_days_ago(365)))

    assert rm._check_stock_pool("301001") is False
    assert rm._check_stock_pool("sh600001") is True
    assert rm._check_stock_pool("600999") is True  # 上市日期未知不过滤
    assert len(refresh_calls) == 1
    _reset_db_engine()


def test_min_listing_days_zero_skips_refresh(tmp_path, monkeypatch):
    rm, refresh_calls = _make_risk_manager(tmp_path, {"blacklist": {"min_listing_days": 0}}, monkeypatch)

    assert rm._check_stock_pool("000001") is True
    assert refresh_calls == []
    _reset_db_engine()


def test_focus_sectors_whitelist(tmp_path, monkeypatch):
    rm, _ = _make_risk_manager(tmp_path, {"focus_sectors": ["半导体", "通信设备"]}, monkeypatch)
    with get_db_session(rm.db_path) as session:
        session.add(LimitUpStock(code="000001", name="芯片样本", trade_date="2026-02-24", sector="半导体"))
        session.add(LimitUpStock(code="000002", name="银行样本", trade_date="2026-02-24", sector="银行"))
        # 最新一条板块为空时取最近一条有板块的记录
        session.add(LimitUpStock(code="000003", name="通信样本", trade_date="2026-02-20", sector="通信设备"))
        session.add(LimitUpStock(code="000003", name="通信样本", trade_date="2026-02-24", sector=""))

    assert rm._check_stock_pool("000001") is True
    assert rm._check_stock_pool("000002") is False
    assert rm._check_stock_pool("000003") is True
    assert rm._check_stock_pool("000004") is False  # 板块未知视为不在白名单

    rm.pool_cfg = {"focus_sectors": []}
    assert rm._check_stock_pool("000002") is True
    _reset_db_engine()


def test_validate_order_intent_reports_stock_pool_rejection(tmp_path, monkeypatch):
    rm, _ = _make_risk_manager(tmp_path, {"blacklist": {"name_keywords": ["退"]}}, monkeypatch)

    result = rm.validate_order_intent(
        {"code": "000001", "name": "退市样本", "side": "sell", "price": 10.0, "quantity": 100}
    )
    assert result["passed"] is False
    assert "stock not in stock_pool" in result["reasons"]
    _reset_db_engine()


def test_stock_info_collector_merges_exchanges_and_upserts(tmp_path, monkeypatch):
    db_path = str(tmp_path / "stock_info.db")
    _reset_db_engine()
    init_db(db_path)

    names = {"600000": "浦发银行"}

    def _sh(symbol):
        if symbol == "科创板":
            raise ConnectionError("sse timeout")
        return pd.DataFrame(
            [{"证券代码": "600000", "证券简称": names["600000"], "上市日期": date(1999, 11, 10)}]
        )

    fake_ak = types.SimpleNamespace(
        stock_info_sh_name_code=_sh,
        stock_info_sz_name_code=lambda symbol: pd.DataFrame(
            [{"A股代码": "000001", "A股简称": "平安银行", "A股上市日期": "1991-04-03"}]
        ),
        stock_info_bj_name_code=lambda: pd.DataFrame(
            [{"证券代码": "920000", "证券简称": "安徽凤凰", "上市日期": "20201223"}]
        ),
    )
    monkeypatch.setattr(stock_info_mod, "ak", fake_ak)

    with StockInfoCollector() as collector:
        assert collector.refresh_if_stale(db_path) == 3
        assert collector.refresh_if_stale(db_path) == 0  # 一天内不重复采集

        names["600000"] = "浦发银行(更名)"
        assert collector.refresh(db_path) == 3

    with get_db_session(db_path) as session:
        rows = {r.code: (r.name, r.exchange, r.list_date) for r in session.query(StockInfo).all()}
        latest = max(r.updated_at for r in session.query(StockInfo).all())

    assert rows == {
        "600000": ("浦发银行(更名)", "sh", "1999-11-10"),
        "000001": ("平安银行", "sz", "1991-04-03"),
        "920000": ("安徽凤凰", "bj", "2020-12-23"),
    }
    assert datetime.now() - latest < timedelta(minutes=1)
    _reset_db_engine()
