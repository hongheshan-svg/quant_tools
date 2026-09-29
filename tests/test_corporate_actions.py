"""实盘记账：分红送转（公司行为）的记录、按方案计算、重放规则与现金/成交接入。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo
from src.services.real_portfolio import RealPortfolioService
from src.services.stock_search import StockSearch

CODE = "600519"


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = str(tmp_path / "corp.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "_trade_days", set())
    with get_db_session(path) as session:
        session.add(StockInfo(code=CODE, name="贵州茅台"))
        session.add(StockDaily(code=CODE, name="贵州茅台", trade_date="2026-09-25", close=8.0, change_pct=0.0))
    yield {"database": {"sqlite_path": path}, "risk": {"stop_loss_pct": -0.05, "take_profit_pct": 0.15}}
    StockSearch.reset()
    _reset_db_engine()


@pytest.fixture
def svc(config):
    return RealPortfolioService(config)


def _buy(svc, date, qty, price=10.0, time="10:00", code=CODE):
    assert svc.add_trade(date, code, "buy", price, qty, trade_time=time)["ok"]


def _sell(svc, date, qty, price, time="10:00"):
    result = svc.add_trade(date, CODE, "sell", price, qty, trade_time=time)
    assert result["ok"], result


def _pos(svc):
    return {p["code"]: p for p in svc.positions()}.get(CODE)


def _base(svc):
    _buy(svc, "2026-09-01", 1000)


# ---------- 直接金额录入与校验 ----------

def test_add_action_validation(svc):
    _base(svc)
    ok = svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)
    assert ok["ok"] is True and "id" in ok
    bad = [
        dict(ex_date="2026-09-10", code=CODE, action="split", cash=1.0),          # 非法 action
        dict(ex_date="2026-09-10", code=CODE, action="dividend", cash=0),
        dict(ex_date="2026-09-10", code=CODE, action="dividend", cash=-1),
        dict(ex_date="2026-09-10", code=CODE, action="tax", cash=0),
        dict(ex_date="2026-09-10", code=CODE, action="bonus", shares=0),
        dict(ex_date="2026-09-10", code=CODE, action="bonus", shares=-5),
        dict(ex_date="", code=CODE, action="dividend", cash=1.0),        # 日期格式错误
        dict(ex_date="bad", code=CODE, action="dividend", cash=1.0),
        dict(ex_date="2026-09-10", code="不存在的股票", action="dividend", cash=1.0),
    ]
    for kwargs in bad:
        result = svc.add_corporate_action(**kwargs)
        assert result["ok"] is False and result.get("error"), kwargs
    assert len(svc.corporate_actions()) == 1


def test_corporate_actions_list_and_delete(svc):
    _base(svc)
    a = svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)["id"]
    b = svc.add_corporate_action("2026-09-20", CODE, "bonus", shares=100)["id"]
    rows = svc.corporate_actions()
    assert [r["id"] for r in rows] == [b, a]                        # ex_date 倒序
    assert all(r.get("action_label") for r in rows)
    assert rows[0]["code"] == CODE and rows[0]["action"] == "bonus" and rows[0]["shares"] == 100
    assert svc.delete_corporate_action(a) is True
    assert svc.delete_corporate_action(a) is False
    assert [r["id"] for r in svc.corporate_actions()] == [b]


def test_sh_prefix_code(svc):
    _base(svc)
    result = svc.add_corporate_action("2026-09-10", "sh600519", "bonus", shares=500)
    assert result["ok"] is True
    assert svc.corporate_actions()[0]["code"] == CODE
    assert _pos(svc)["quantity"] == 1500


# ---------- 重放规则 ----------

def test_contract_numbers_9700(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)
    svc.add_corporate_action("2026-09-10", CODE, "bonus", shares=500)
    pos = _pos(svc)
    assert pos["quantity"] == 1500 and pos["avg_cost"] == pytest.approx(9700 / 1500, abs=1e-3)
    _sell(svc, "2026-09-15", 1500, 7.0)
    realized = svc.snapshot()["account"]["realized_pnl"]
    assert realized == pytest.approx((7 - 9700 / 1500) * 1500, abs=0.01)
    assert _pos(svc) is None


def test_dividend_not_realized_pnl_and_no_position_after_sell(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)
    assert svc.snapshot()["account"]["realized_pnl"] == 0          # 分红不计已实现盈亏
    assert _pos(svc)["quantity"] == 1000


def test_tax_increases_cost(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "tax", cash=50.0)
    pos = _pos(svc)
    assert pos["quantity"] == 1000 and pos["avg_cost"] == pytest.approx((10000 + 50) / 1000)


def test_tax_greater_than_dividend(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=100.0)
    svc.add_corporate_action("2026-09-10", CODE, "tax", cash=150.0)
    assert _pos(svc)["avg_cost"] == pytest.approx((10000 - 100 + 150) / 1000)


def test_same_day_buy_does_not_enjoy_dividend_order(svc):
    """同一天先公司行为后成交：当天买入的数量不参与按方案计算。"""
    _base(svc)
    _buy(svc, "2026-09-10", 1000, price=12.0, time="09:31")
    result = svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=3)
    assert result["ok"] is True
    rows = svc.corporate_actions()
    assert rows[0]["cash"] == pytest.approx(300.0)                  # 仅 1000 股，而非 2000 股
    # 重放：先分红（成本 10000-300），再买入（+12000）
    pos = _pos(svc)
    assert pos["quantity"] == 2000 and pos["avg_cost"] == pytest.approx((10000 - 300 + 12000) / 2000)


def test_sell_all_then_rebuy_ignores_old_action_and_warns(svc):
    _base(svc)
    _sell(svc, "2026-09-05", 1000, 11.0)
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)     # 无持仓 -> 忽略
    _buy(svc, "2026-09-12", 500, price=9.0)
    pos = _pos(svc)
    assert pos["quantity"] == 500 and pos["avg_cost"] == pytest.approx(9.0)
    snap = svc.snapshot()
    assert snap["warnings"], "无持仓时的公司行为应产生 warning"
    assert any(("分红" in w or "公司行为" in w or "红" in w or "送" in w) for w in snap["warnings"])


def test_multiple_bonus_stack(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "bonus", shares=500)      # 1500
    svc.add_corporate_action("2026-09-20", CODE, "bonus", shares=300)      # 1800
    pos = _pos(svc)
    assert pos["quantity"] == 1800 and pos["avg_cost"] == pytest.approx(10000 / 1800, abs=1e-3)


def test_sell_more_than_holding_after_bonus_warns(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "bonus", shares=500)      # 1500
    from src.database.models import RealTrade

    with get_db_session(svc.db_path) as session:                            # 绕过 add_trade 的持仓校验
        session.add(RealTrade(trade_date="2026-09-15", trade_time="10:00:00", code=CODE, name="贵州茅台",
                              side="sell", price=8.0, quantity=1800))
    snap = svc.snapshot()
    assert snap["positions"] == []
    assert any("超过持仓" in w for w in snap["warnings"])


def test_delete_action_restores_position(svc):
    _base(svc)
    before = _pos(svc)
    aid = svc.add_corporate_action("2026-09-10", CODE, "bonus", shares=500)["id"]
    assert _pos(svc)["quantity"] == 1500
    assert svc.delete_corporate_action(aid)
    after = _pos(svc)
    assert after["quantity"] == before["quantity"] and after["avg_cost"] == pytest.approx(before["avg_cost"])


# ---------- 按方案计算 ----------

def test_plan_dividend_and_bonus(svc):
    _base(svc)
    result = svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=3, bonus_per_10=2, transfer_per_10=3)
    assert result["ok"] is True
    rows = {r["action"]: r for r in svc.corporate_actions()}
    assert set(rows) == {"dividend", "bonus"}
    assert rows["dividend"]["cash"] == pytest.approx(300.0)
    assert rows["bonus"]["shares"] == 500


def test_plan_tax_rate_and_floor(svc):
    _buy(svc, "2026-09-01", 1234, price=10.0)
    svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=1.5, bonus_per_10=1, tax_rate=0.1)
    rows = {r["action"]: r for r in svc.corporate_actions()}
    assert rows["dividend"]["cash"] == pytest.approx(round(1234 * 1.5 / 10 * 0.9, 2))
    assert rows["bonus"]["shares"] == 123                                   # floor(123.4)


def test_plan_zero_items_not_generated(svc):
    _base(svc)
    assert svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=2)["ok"] is True
    assert [r["action"] for r in svc.corporate_actions()] == ["dividend"]
    assert svc.add_corporate_action_by_plan("2026-09-11", CODE, transfer_per_10=10)["ok"] is True
    assert sorted(r["action"] for r in svc.corporate_actions()) == ["bonus", "dividend"]


def test_plan_no_holding_before_ex_date(svc):
    result = svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=3)
    assert result["ok"] is False and result.get("error")
    _buy(svc, "2026-09-10", 1000)                                           # 除权日当天买入，不算 ex_date 之前
    assert svc.add_corporate_action_by_plan("2026-09-10", CODE, cash_per_10=3)["ok"] is False
    assert svc.corporate_actions() == []


# ---------- 现金与成交接入 ----------

def test_cash_includes_actions_after_anchor(svc):
    _base(svc)
    svc.set_cash(50_000.0, as_of=datetime(2026, 9, 5, 12, 0))
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)
    svc.add_corporate_action("2026-09-11", CODE, "tax", cash=60.0)
    svc.add_corporate_action("2026-09-12", CODE, "bonus", shares=500)      # 送股不影响现金
    assert svc.cash() == pytest.approx(50_000 + 300 - 60)


def test_cash_ignores_actions_before_anchor(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-03", CODE, "dividend", cash=300.0)
    svc.set_cash(50_000.0, as_of=datetime(2026, 9, 5, 12, 0))
    assert svc.cash() == 50_000.0
    # 锚点当天 09:00 之前 -> 不计；之后 -> 计
    svc.add_corporate_action("2026-09-05", CODE, "dividend", cash=100.0)    # 09:00 < 12:00
    assert svc.cash() == 50_000.0


def test_fills_include_bonus_only(svc):
    _base(svc)
    svc.add_corporate_action("2026-09-10", CODE, "dividend", cash=300.0)
    svc.add_corporate_action("2026-09-10", CODE, "tax", cash=30.0)
    svc.add_corporate_action("2026-09-10", CODE, "bonus", shares=500)
    fills = svc.fills()
    assert (CODE, "buy", 0.0, 500, datetime(2026, 9, 10, 9, 0)) in fills
    assert len(fills) == 2                                                  # 1 笔买入 + 1 笔送股
