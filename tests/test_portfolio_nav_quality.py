"""出入金不能成为收益；费用、除息、送股计入实际账户净值。"""

from types import SimpleNamespace as Row

import pytest

from src.services.portfolio_nav import ledger_nav
from src.services.portfolio_risk import drawdowns, PortfolioRiskService
from src.services.real_portfolio import RealPortfolioService
from tests.test_real_portfolio import config  # noqa: F401


def flow(day, amount, direction="in"):
    return Row(flow_date=day, amount=amount, direction=direction, account="默认")


def trade(day, price=10, quantity=10, fee=0):
    return Row(trade_date=day, trade_time="10:00:00", code="600001", side="buy", price=price, quantity=quantity, fee=fee, account="默认")


def test_withdrawal_does_not_create_drawdown():
    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    nav, quality = ledger_nav([trade(days[1])], [], [flow(days[0], 200), flow(days[2], 100, "out")], [], {"600001": {days[1]: 10, days[2]: 5}}, days, ["默认"])
    assert quality["status"] == "available"
    assert quality["samples"][-1]["assets"] == 50
    assert drawdowns(nav)["max_drawdown"] == -25


def test_fees_dividend_tax_and_bonus_replay():
    days = ["2026-09-01", "2026-09-02", "2026-09-03"]
    actions = [Row(ex_date=days[2], action=kind, cash=cash, shares=shares, code="600001", account="默认") for kind, cash, shares in [("dividend", 10, 0), ("tax", 2, 0), ("bonus", 0, 10)]]
    nav, quality = ledger_nav([trade(days[1], fee=2)], actions, [flow(days[0], 200)], [], {"600001": {days[1]: 10, days[2]: 5}}, days, ["默认"])
    assert quality["samples"][-1]["assets"] == 206
    assert nav[-1][1] == pytest.approx(1.03)
    assert drawdowns(nav)["max_drawdown"] == -1


def test_missing_quotes_and_unknown_cash_are_not_zero_risk(config):
    real = RealPortfolioService(config)
    real.add_trade("2026-09-21", "600001", "买入", 10, 100)
    position = real.positions()[0]
    assert position["valuation_is_estimate"] and not position["price_available"]
    report = PortfolioRiskService(config, account="real").report()
    assert report["exposure"] is None
    assert report["positions"][0]["stop_gap"] is None
    assert report["drawdown"]["max_drawdown"] is None
    assert report["quality"]["priced"] == 0
