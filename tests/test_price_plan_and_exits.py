from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from src.collectors.stock_info import StockInfoCollector
from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, TradeFill, TradeOrder, TradeSignal
from src.trading.broker.paper import PaperBrokerAdapter
from src.trading.constants import ORDER_STATUS_FILLED, ORDER_STATUS_PENDING_CONFIRM
from src.trading.execution_service import ExecutionService
from src.trading.models import PlaceOrderRequest
from src.trading.price_plan import PricePlan, daily_limit_pct, sanitize_price_plan, to_price

TODAY = date.today().strftime("%Y-%m-%d")
YESTERDAY = datetime.now() - timedelta(days=1)


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


# ---------- 价格计划校验 ----------

def test_daily_limit_and_price_parsing():
    assert daily_limit_pct("600519") == 0.10
    assert daily_limit_pct("sz300750") == 0.20
    assert daily_limit_pct("688001") == 0.20
    assert daily_limit_pct("920000") == 0.30
    assert daily_limit_pct("000001", "*ST样本") == 0.05
    assert [to_price(v) for v in (12.5, "12.50元", "约 9.8", None, "null", -1, True)] == [12.5, 12.5, 9.8, None, None, None, None]


def test_sanitize_keeps_reasonable_plan_and_drops_bad_prices():
    assert sanitize_price_plan("600519", "", 10.0, "10.2", 9.6, "11.5") == PricePlan(10.2, 9.6, 11.5)
    # 买入价超出主板涨停价 11.0 → 丢弃，止损/目标改以最新价为基准校验
    assert sanitize_price_plan("600519", "", 10.0, 11.5, 9.5, 12.0) == PricePlan(None, 9.5, 12.0)
    # 创业板 20% 涨跌幅：11.5 合理
    assert sanitize_price_plan("300750", "", 10.0, 11.5, None, None).entry_price == 11.5
    # 止损价不低于买入价、目标价高得离谱 → 丢弃
    assert sanitize_price_plan("600519", "", 10.0, 10.0, 10.5, 30.0) == PricePlan(10.0, None, None)
    # 没有参考价时不信任任何价格
    assert sanitize_price_plan("600519", "", None, 10.0, 9.5, 11.0) == PricePlan()


# ---------- 模拟盘 T+1 ----------

def test_paper_broker_same_day_buy_is_not_sellable():
    broker = PaperBrokerAdapter(initial_cash=100_000)
    broker.connect()
    assert broker.place_order(PlaceOrderRequest("o1", "600519", "buy", "limit", 10.0, 1000)).ok
    assert broker.get_positions()[0].available_quantity == 0
    sell = broker.place_order(PlaceOrderRequest("o2", "600519", "sell", "limit", 10.5, 1000))
    assert (sell.ok, sell.error_code) == (False, "INSUFFICIENT_POSITION")

    broker.restore_fills([("600519", "buy", 10.0, 1000, YESTERDAY), ("000001", "buy", 5.0, 200, datetime.now())])
    positions = {p.code: p for p in broker.get_positions()}
    assert positions["600519"].available_quantity == 1000
    assert positions["000001"].available_quantity == 0


# ---------- 下单价格与卖出闭环 ----------

def _setup(tmp_path, monkeypatch, **trading) -> dict:
    db_path = str(tmp_path / "exits.db")
    _reset_db_engine()
    init_db(db_path)
    monkeypatch.setattr(StockInfoCollector, "refresh_if_stale", lambda self, path, max_age_hours=24: 0)
    return {
        "database": {"sqlite_path": db_path},
        "risk": {"stop_loss_pct": -0.05, "take_profit_pct": 0.15},
        "strategy": {"weights": {}, "learning": {"enabled": False}},
        "trading": {"default_order_budget": 100_000, **trading},
    }


def _buy_and_settle(config: dict, code: str, close: float, **plan) -> None:
    """按信号买入并把成交时间改到昨天，模拟已过 T+1 的持仓。"""
    db_path = config["database"]["sqlite_path"]
    with get_db_session(db_path) as session:
        session.add(StockDaily(code=code, name="样本", trade_date=TODAY, close=close))
        session.add(TradeSignal(code=code, name="样本", signal_date=TODAY, signal_type="premarket", ai_verdict="买入",
                                signal_strength=1.0, composite_score=90, **plan))
    service = ExecutionService(config)
    order_ids = service.prepare_orders(TODAY)
    assert service.confirm_and_send(order_ids[0])["ok"]
    with get_db_session(db_path) as session:
        session.query(TradeFill).update({"filled_at": YESTERDAY})


def _set_price(config: dict, code: str, price: float) -> None:
    with get_db_session(config["database"]["sqlite_path"]) as session:
        session.query(StockDaily).filter(StockDaily.code == code).update({"close": price})


def test_order_uses_ai_entry_price(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    _buy_and_settle(config, "600519", close=50.0, entry_price=49.0)
    with get_db_session(config["database"]["sqlite_path"]) as session:
        order = session.query(TradeOrder).one()
        assert (order.price, order.quantity) == (49.0, 2000)  # 预算 10 万 / 49 → 2000 股
    _reset_db_engine()


def test_stop_loss_exit_order_is_created_once(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    _buy_and_settle(config, "600519", close=50.0)  # 无价格计划 → 按风控 -5% 止损 = 47.5

    service = ExecutionService(config)
    _set_price(config, "600519", 48.0)
    assert service.generate_exit_orders() == {"exit_orders": 0, "confirmed": 0}

    _set_price(config, "600519", 47.0)
    assert service.generate_exit_orders() == {"exit_orders": 1, "confirmed": 0}
    assert service.generate_exit_orders() == {"exit_orders": 0, "confirmed": 0}  # 当天不重复生成

    with get_db_session(config["database"]["sqlite_path"]) as session:
        sell = session.query(TradeOrder).filter(TradeOrder.side == "sell").one()
        assert (sell.status, sell.strategy_tag, sell.quantity, sell.price) == (ORDER_STATUS_PENDING_CONFIRM, "stop_loss", 2000, 47.0)
        assert "触发止损" in sell.risk_note

    snapshot = service.get_trading_snapshot()
    assert (snapshot["positions"][0]["stop_loss"], snapshot["positions"][0]["target_price"]) == (47.5, 57.5)
    _reset_db_engine()


def test_take_profit_uses_signal_plan_and_auto_confirm_sells(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch, auto_confirm=True)
    _buy_and_settle(config, "600519", close=50.0, stop_loss_price=46.0, target_price=53.0)

    service = ExecutionService(config)
    _set_price(config, "600519", 52.9)
    assert service.generate_exit_orders()["exit_orders"] == 0
    _set_price(config, "600519", 53.5)
    assert service.generate_exit_orders() == {"exit_orders": 1, "confirmed": 1}

    with get_db_session(config["database"]["sqlite_path"]) as session:
        sell = session.query(TradeOrder).filter(TradeOrder.side == "sell").one()
        assert (sell.status, sell.strategy_tag) == (ORDER_STATUS_FILLED, "take_profit")
    snapshot = ExecutionService(config).get_trading_snapshot()
    assert snapshot["positions"] == []
    assert snapshot["account"]["cash"] == pytest.approx(1_000_000 + (53.5 - 50.0) * 2000)
    _reset_db_engine()


def test_no_exit_before_t_plus_1(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    db_path = config["database"]["sqlite_path"]
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="600519", name="样本", trade_date=TODAY, close=50.0))
        session.add(TradeSignal(code="600519", name="样本", signal_date=TODAY, signal_type="premarket",
                                ai_verdict="买入", signal_strength=1.0, composite_score=90))
    service = ExecutionService(config)
    service.confirm_and_send(service.prepare_orders(TODAY)[0])
    _set_price(config, "600519", 40.0)
    assert service.generate_exit_orders()["exit_orders"] == 0  # 当日买入不可卖
    _reset_db_engine()


# ---------- 预测结果保存价格计划 ----------

def test_predictor_saves_only_valid_price_plan(tmp_path, monkeypatch):
    config = _setup(tmp_path, monkeypatch)
    config["llm"] = {"cache_enabled": False}
    db_path = config["database"]["sqlite_path"]
    with get_db_session(db_path) as session:
        session.add(StockDaily(code="sh600519", name="样本", trade_date=TODAY, close=10.0))

    from src.services.premarket_predictor import LimitUpPredictor

    predictor = LimitUpPredictor(config)
    predictor._save_predictions(TODAY, [
        {"code": "600519", "name": "样本", "confidence": 8, "buy_price": "10.2", "stop_loss": 9.6, "target_price": 30},
        {"code": "000002", "name": "无价格", "confidence": 7, "buy_price": 5, "stop_loss": 4.8, "target_price": 5.5},
    ], "震荡", "算力")

    with get_db_session(db_path) as session:
        signals = {s.code: s for s in session.query(TradeSignal).all()}
        assert (signals["600519"].entry_price, signals["600519"].stop_loss_price, signals["600519"].target_price) == (10.2, 9.6, None)
        assert "计划:买入10.20 止损9.60" in signals["600519"].ai_advice
        assert (signals["000002"].entry_price, signals["000002"].stop_loss_price) == (None, None)  # 库里没有价格，不信任
    _reset_db_engine()
