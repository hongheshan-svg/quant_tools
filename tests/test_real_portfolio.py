"""实盘记账：交割单导入、持仓与盈亏、可用资金、组合风险和提醒接入。"""

from __future__ import annotations

from datetime import datetime

import pytest

from src import trading_calendar
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, StockInfo
from src.services import alert_service as alert_mod
from src.services.alert_service import AlertService
from src.services.portfolio_risk import PortfolioRiskService
from src.services.real_portfolio import RealPortfolioService, parse_trade_rows, read_rows
from src.services.stock_search import StockSearch

EXPORT = """资金账号：123456,,,,,,,,,,,
成交日期,成交时间,证券代码,证券名称,操作,成交数量,成交均价,成交金额,佣金,印花税,过户费,成交编号
20260921,09:31:05,600519,贵州茅台,证券买入,100,1500.00,150000.00,5.00,0.00,1.50,A001
20260922,10:02:00,600519,贵州茅台,证券买入,100,1400.00,140000.00,5.00,0.00,1.40,A002
20260923,14:30:00,600519,贵州茅台,证券卖出,100,1600.00,160000.00,5.00,80.00,1.60,A003
20260923,15:00:00,600519,贵州茅台,红利入账,0,0,300.00,0,0,0,
20260924,9:45,="000001",平安银行,买入,1000,10.50,10500.00,5.00,0,0.10,
"""


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = str(tmp_path / "real.db")
    _reset_db_engine()
    init_db(path)
    StockSearch.reset()
    monkeypatch.setattr(trading_calendar, "_trade_days", set())
    with get_db_session(path) as session:
        session.add(StockInfo(code="600519", name="贵州茅台"))
        session.add(StockInfo(code="000001", name="平安银行"))
        for day, close in (("2026-09-21", 1500.0), ("2026-09-22", 1400.0), ("2026-09-23", 1600.0), ("2026-09-24", 1550.0)):
            session.add(StockDaily(code="sh600519", name="贵州茅台", trade_date=day, close=close, change_pct=0.0))
        session.add(StockDaily(code="000001", name="平安银行", trade_date="2026-09-24", close=10.0, change_pct=-2.0))
    yield {"database": {"sqlite_path": path}, "risk": {"stop_loss_pct": -0.05, "take_profit_pct": 0.15}}
    StockSearch.reset()
    _reset_db_engine()


def _write(tmp_path, text: str, encoding: str = "gbk"):
    path = tmp_path / "export.csv"
    path.write_bytes(text.encode(encoding))
    return str(path)


def test_parse_export(tmp_path):
    trades, skipped, error = parse_trade_rows(read_rows(_write(tmp_path, EXPORT)))
    assert error == "" and skipped == 1                                  # 红利入账跳过
    assert [(t["trade_date"], t["trade_time"], t["code"], t["side"], t["quantity"], t["fee"]) for t in trades] == [
        ("2026-09-21", "09:31:05", "600519", "buy", 100, 6.5),
        ("2026-09-22", "10:02:00", "600519", "buy", 100, 6.4),
        ("2026-09-23", "14:30:00", "600519", "sell", 100, 86.6),
        ("2026-09-24", "09:45:00", "000001", "buy", 1000, 5.1),
    ]
    assert trades[0]["import_key"] == "A001#1" and trades[3]["import_key"].startswith("2026-09-24|09:45:00|000001|buy|10.5|1000#")
    assert parse_trade_rows([["日期", "摘要"], ["20260901", "转账"]]) == ([], 0, "没有找到表头（需要证券代码、买卖标志、成交价格、成交数量几列）")


def test_import_positions_and_pnl(config, tmp_path):
    service = RealPortfolioService(config)
    assert service.import_file(_write(tmp_path, EXPORT)) == {"added": 4, "duplicate": 0, "skipped": 1, "error": ""}
    assert service.import_file(_write(tmp_path, EXPORT))["duplicate"] == 4       # 重复导入不会记两次

    positions = {p["code"]: p for p in service.positions()}
    moutai = positions["600519"]
    # 成本 = (150000 + 6.5 + 140000 + 6.4) / 200 = 1450.0645；卖出 100 股后剩 100 股，均价不变
    assert moutai["quantity"] == 100 and moutai["avg_cost"] == pytest.approx(1450.0645)
    assert moutai["market_price"] == 1550.0 and moutai["unrealized_pnl"] == pytest.approx((1550 - 1450.0645) * 100)
    assert moutai["stop_loss"] == pytest.approx(1377.56) and moutai["target_price"] == pytest.approx(1667.57)
    snapshot = service.snapshot()
    assert snapshot["account"]["realized_pnl"] == pytest.approx((1600 - 1450.0645) * 100 - 86.6, abs=0.01)
    assert snapshot["account"]["cash_known"] is False and "还没有设置可用资金" in snapshot["warnings"][0]

    service.set_plan("600519", 1480.0, None)
    assert {p["code"]: p for p in service.positions()}["600519"]["stop_loss"] == 1480.0


def test_manual_trades_cash_anchor_and_oversell(config):
    service = RealPortfolioService(config)
    assert service.add_trade("2026-09-21", "gzmt", "buy", 1500.0, 100, fee=5.0, trade_time="10:00") == {"ok": True}
    assert service.add_trade("2026-09-22", "600519", "sell", 1600.0, 200)["error"] == "卖出 200 股超过当前持仓 100 股"
    assert service.add_trade("2026-09-22", "不存在", "buy", 10.0, 100)["error"] == "找不到股票「不存在」"
    assert service.add_trade("bad", "600519", "buy", 10.0, 100)["ok"] is False

    service.set_cash(50_000.0, as_of=datetime(2026, 9, 21, 12, 0))          # 设置后才发生的成交会增减资金
    service.add_trade("2026-09-22", "600519", "sell", 1600.0, 50, fee=10.0, trade_time="14:00")
    assert service.cash() == pytest.approx(50_000 + 80_000 - 10)
    trades = service.trades()
    assert [t["side"] for t in trades] == ["sell", "buy"] and trades[0]["trade_time"] == "14:00:00"
    assert service.delete_trade(trades[0]["id"]) and service.cash() == 50_000.0

    # 导入的卖出超过持仓（缺少更早的买入记录）：只减到 0 并提示
    service.add_trade("2026-09-23", "600519", "sell", 1600.0, 100)
    with get_db_session(config["database"]["sqlite_path"]) as session:
        from src.database.models import RealTrade

        session.add(RealTrade(trade_date="2026-09-24", code="000001", name="平安银行", side="sell", price=10.0, quantity=500))
    snapshot = service.snapshot()
    assert snapshot["positions"] == [] and any("缺少更早的买入记录" in w for w in snapshot["warnings"])


def test_real_account_risk_and_alerts(config, tmp_path, monkeypatch):
    service = RealPortfolioService(config)
    service.import_file(_write(tmp_path, EXPORT))
    monkeypatch.setattr(PortfolioRiskService, "_regime_limit", lambda self: ("防守", 30.0))

    report = PortfolioRiskService(config, account="real").report()
    assert report["account"] == "real" and report["cash_known"] is False
    assert not any("总仓位" in w for w in report["warnings"])               # 没设置可用资金时不比较总仓位
    assert report["drawdown"]["days"] == 4

    service.set_cash(100_000.0, as_of=datetime(2026, 9, 25, 9, 0))
    report = PortfolioRiskService(config, account="real").report()
    assert report["cash_known"] is True and report["exposure"] == pytest.approx(
        (155_000 + 10_000) / (100_000 + 155_000 + 10_000) * 100, abs=0.1)
    assert "总仓位 62% 高于大盘「防守」环境建议的 30%" in report["warnings"]

    # 盘中提醒覆盖实盘持仓
    alert_mod.reset_state()
    monkeypatch.setattr(trading_calendar, "in_trade_session", lambda now=None: True)
    today = datetime.now().strftime("%Y-%m-%d")
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.add(StockDaily(code="000001", name="平安银行", trade_date=today, close=9.8, change_pct=-2.0))
    from src.trading import execution_service as exec_mod

    monkeypatch.setattr(exec_mod.ExecutionService, "get_trading_snapshot", lambda self, order_limit=1: {"positions": []})
    events = AlertService({**config, "alerts": {"market_regime": False}, "notifier": {}}).evaluate()
    stop = next(e for e in events if e.alert_type == "stop_loss")
    assert stop.message.startswith("实盘持仓 平安银行(000001) 跌破止损价") and stop.message.endswith("请按计划止损")
    alert_mod.reset_state()


def test_chat_position_tool_includes_real(config, tmp_path, monkeypatch):
    from src.services.chat_tools import ChatTools
    from src.trading import execution_service as exec_mod

    RealPortfolioService(config).import_file(_write(tmp_path, EXPORT))
    monkeypatch.setattr(exec_mod.ExecutionService, "get_trading_snapshot", lambda self, order_limit=1: {"positions": []})
    text = ChatTools(config).call("position", {})
    assert text.startswith("模拟盘持仓：无\n实盘持仓：贵州茅台(600519) 100股 成本1450.06")
