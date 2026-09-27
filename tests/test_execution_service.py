from src.database import db as db_module
from src.database.db import get_db_session, init_db
from src.database.models import StockDaily, TradeOrder, TradeSignal
from src.trading.broker.paper import PaperBrokerAdapter
from src.trading.constants import (
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_PENDING_CONFIRM,
    ORDER_STATUS_REJECTED,
)
from src.trading.execution_service import ExecutionService
from src.trading.models import PlaceOrderRequest
from src.trading.order_mapper import build_idempotency_key


def _reset_db_engine():
    if db_module._engine:
        db_module._engine.dispose()
    db_module._engine = None
    db_module._SessionFactory = None


def _make_config(db_path: str) -> dict:
    return {
        "database": {"sqlite_path": db_path},
        "risk": {"blacklist_keywords": ["ST", "*ST"]},
        "strategy": {"weights": {}, "learning": {"enabled": False}},
        "trading": {"default_order_budget": 100_000, "max_orders_per_run": 10},
    }


def test_cancel_order_without_broker_id_marks_canceled(tmp_path):
    db_path = tmp_path / "execution_cancel.db"
    _reset_db_engine()
    init_db(str(db_path))

    with get_db_session(str(db_path)) as session:
        session.add(
            TradeOrder(
                id="order_local_cancel",
                signal_date="2026-02-25",
                code="000001",
                name="测试样本",
                side="buy",
                order_type="limit",
                price=10.0,
                quantity=100,
                amount=1000.0,
                status=ORDER_STATUS_PENDING_CONFIRM,
                broker="paper",
                idempotency_key="2026-02-25|000001|buy|test",
            )
        )

    service = ExecutionService(config=_make_config(str(db_path)))
    result = service.cancel_order("order_local_cancel", operator="unit_test")
    assert result["ok"] is True
    assert result["status"] == ORDER_STATUS_CANCELED

    with get_db_session(str(db_path)) as session:
        row = session.query(TradeOrder).filter(TradeOrder.id == "order_local_cancel").first()
        assert row is not None
        assert row.status == ORDER_STATUS_CANCELED

    _reset_db_engine()


def test_prepare_orders_skips_existing_idempotent_order(tmp_path):
    db_path = tmp_path / "execution_idempotent.db"
    _reset_db_engine()
    init_db(str(db_path))
    trade_date = "2026-02-25"
    idem = build_idempotency_key(trade_date, "000001", "buy", "buy")

    with get_db_session(str(db_path)) as session:
        session.add(
            StockDaily(
                code="000001",
                name="测试样本",
                trade_date=trade_date,
                open=10.0,
                high=10.2,
                low=9.8,
                close=10.0,
                change_pct=1.0,
            )
        )
        session.add(
            TradeSignal(
                code="000001",
                name="测试样本",
                signal_date=trade_date,
                signal_type="buy",
                signal_strength=0.8,
                composite_score=88.0,
                reason="测试信号",
                ai_verdict="买入",
            )
        )
        session.add(
            TradeOrder(
                id="existing_rejected_order",
                signal_date=trade_date,
                code="000001",
                name="测试样本",
                side="buy",
                order_type="limit",
                price=10.0,
                quantity=100,
                amount=1000.0,
                status=ORDER_STATUS_REJECTED,
                broker="paper",
                idempotency_key=idem,
            )
        )

    service = ExecutionService(config=_make_config(str(db_path)))
    created = service.prepare_orders(signal_date=trade_date)
    assert created == []

    with get_db_session(str(db_path)) as session:
        rows = session.query(TradeOrder).all()
        assert len(rows) == 1
        assert rows[0].id == "existing_rejected_order"

    _reset_db_engine()


def test_paper_broker_rejects_sell_without_position():
    broker = PaperBrokerAdapter(initial_cash=100_000)
    broker.connect()

    result = broker.place_order(
        PlaceOrderRequest(
            client_order_id="sell_without_pos",
            code="000001",
            side="sell",
            order_type="limit",
            price=10.0,
            quantity=100,
        )
    )

    assert result.ok is False
    assert result.error_code == "INSUFFICIENT_POSITION"
