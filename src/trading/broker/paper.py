"""Paper broker adapter for simulation (A股 T+1：当日买入次日可卖)."""

from __future__ import annotations

from datetime import date, datetime
from threading import RLock
from uuid import uuid4

from src.trading.broker.base import BrokerAdapter
from src.trading.constants import (
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_REJECTED,
)
from src.trading.models import (
    AccountInfo,
    CancelOrderRequest,
    CancelOrderResult,
    OrderStatusResult,
    PlaceOrderRequest,
    PlaceOrderResult,
    Position,
    Quote,
)


class PaperBrokerAdapter(BrokerAdapter):
    """简单仿真交易：限价单默认立即全成。"""

    def __init__(self, initial_cash: float = 1_000_000.0):
        self._initial_cash = float(initial_cash)
        self._cash = float(initial_cash)
        self._connected = False
        self._lock = RLock()
        self._orders: dict[str, dict] = {}
        self._positions: dict[str, Position] = {}

    @property
    def name(self) -> str:
        return "paper"

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def get_account(self) -> AccountInfo:
        with self._lock:
            market_value = sum((pos.market_price or pos.avg_cost) * pos.quantity for pos in self._positions.values())
            return AccountInfo(
                account_id="PAPER-001",
                total_assets=self._cash + market_value,
                cash=self._cash,
                frozen_cash=0.0,
                source=self.name,
            )

    def get_positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions.values())

    def get_quotes(self, codes: list[str]) -> dict[str, Quote]:
        with self._lock:
            quotes: dict[str, Quote] = {}
            for code in codes:
                pos = self._positions.get(code)
                base = pos.market_price if (pos and pos.market_price > 0) else pos.avg_cost if pos else 0.0
                if base <= 0:
                    base = 10.0
                quotes[code] = Quote(code=code, last_price=base, bid_price=base * 0.999, ask_price=base * 1.001)
            return quotes

    def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult:
        with self._lock:
            if not self._connected:
                return PlaceOrderResult(ok=False, status=ORDER_STATUS_REJECTED, error_code="NOT_CONNECTED", error_msg="broker not connected")
            if req.quantity <= 0:
                return PlaceOrderResult(ok=False, status=ORDER_STATUS_REJECTED, error_code="INVALID_QTY", error_msg="quantity must > 0")
            if req.price <= 0:
                return PlaceOrderResult(ok=False, status=ORDER_STATUS_REJECTED, error_code="INVALID_PRICE", error_msg="price must > 0")

            broker_order_id = f"PAPER-{uuid4().hex[:12].upper()}"
            amount = req.price * req.quantity

            if req.side == "buy" and self._cash + 1e-8 < amount:
                status = ORDER_STATUS_REJECTED
                self._orders[broker_order_id] = {
                    "status": status,
                    "filled_quantity": 0,
                    "avg_fill_price": 0.0,
                    "fills": [],
                    "code": req.code,
                    "side": req.side,
                    "price": req.price,
                    "quantity": req.quantity,
                    "error_code": "INSUFFICIENT_CASH",
                    "error_msg": "insufficient cash",
                }
                return PlaceOrderResult(
                    ok=False,
                    broker_order_id=broker_order_id,
                    status=status,
                    error_code="INSUFFICIENT_CASH",
                    error_msg="insufficient cash",
                )
            if req.side == "sell":
                pos = self._positions.get(req.code)
                if pos is None or pos.available_quantity < req.quantity:
                    status = ORDER_STATUS_REJECTED
                    self._orders[broker_order_id] = {
                        "status": status,
                        "filled_quantity": 0,
                        "avg_fill_price": 0.0,
                        "fills": [],
                        "code": req.code,
                        "side": req.side,
                        "price": req.price,
                        "quantity": req.quantity,
                        "error_code": "INSUFFICIENT_POSITION",
                        "error_msg": "insufficient position",
                    }
                    return PlaceOrderResult(
                        ok=False,
                        broker_order_id=broker_order_id,
                        status=status,
                        error_code="INSUFFICIENT_POSITION",
                        error_msg="insufficient position",
                    )

            status = ORDER_STATUS_FILLED
            fill = {
                "broker_fill_id": f"PF-{uuid4().hex[:10].upper()}",
                "price": req.price,
                "quantity": req.quantity,
                "amount": amount,
                "filled_at": datetime.now(),
            }

            self._orders[broker_order_id] = {
                "status": status,
                "filled_quantity": req.quantity,
                "avg_fill_price": req.price,
                "fills": [fill],
                "code": req.code,
                "side": req.side,
                "price": req.price,
                "quantity": req.quantity,
                "error_code": "",
                "error_msg": "",
            }

            self._apply_fill(req.code, req.side, req.price, req.quantity)

            return PlaceOrderResult(
                ok=True,
                broker_order_id=broker_order_id,
                status=status,
                filled_quantity=req.quantity,
                avg_fill_price=req.price,
                fills=[fill],
            )

    def cancel_order(self, req: CancelOrderRequest) -> CancelOrderResult:
        with self._lock:
            if req.broker_order_id not in self._orders:
                return CancelOrderResult(
                    ok=False,
                    broker_order_id=req.broker_order_id,
                    canceled=False,
                    error_code="ORDER_NOT_FOUND",
                    error_msg="order not found",
                )
            od = self._orders[req.broker_order_id]
            if od["status"] in {ORDER_STATUS_FILLED, ORDER_STATUS_CANCELED, ORDER_STATUS_REJECTED}:
                return CancelOrderResult(
                    ok=False,
                    broker_order_id=req.broker_order_id,
                    canceled=False,
                    error_code="ORDER_FINAL",
                    error_msg="order already final",
                )
            od["status"] = ORDER_STATUS_CANCELED
            return CancelOrderResult(ok=True, broker_order_id=req.broker_order_id, canceled=True)

    def query_order(self, broker_order_id: str) -> OrderStatusResult:
        with self._lock:
            od = self._orders.get(broker_order_id)
            if not od:
                return OrderStatusResult(
                    ok=False,
                    broker_order_id=broker_order_id,
                    status=ORDER_STATUS_REJECTED,
                    error_code="ORDER_NOT_FOUND",
                    error_msg="order not found",
                )
            return OrderStatusResult(
                ok=True,
                broker_order_id=broker_order_id,
                status=od["status"],
                filled_quantity=od.get("filled_quantity", 0),
                avg_fill_price=od.get("avg_fill_price", 0.0),
                error_code=od.get("error_code", ""),
                error_msg=od.get("error_msg", ""),
                fills=list(od.get("fills", [])),
            )

    def restore_fills(self, fills: list[tuple[str, str, float, int, datetime | None]]) -> None:
        """从初始资金开始按时间顺序重放历史成交 (code, side, price, quantity, filled_at)，恢复现金和持仓。
        今天之前的买入计入可卖数量，今天的买入按 T+1 不可卖。"""
        today = date.today()
        with self._lock:
            self._cash = self._initial_cash
            self._positions = {}
            for code, side, price, quantity, filled_at in fills:
                settled = filled_at is None or filled_at.date() < today
                self._apply_fill(code, side, float(price or 0), int(quantity or 0), settled=settled)

    def _apply_fill(self, code: str, side: str, price: float, quantity: int, settled: bool = False) -> None:
        """settled=False 表示当日买入，按 T+1 不增加可卖数量。"""
        amount = price * quantity
        pos = self._positions.get(code)
        if side == "buy":
            self._cash -= amount
            if pos is None:
                self._positions[code] = Position(
                    code=code,
                    quantity=quantity,
                    available_quantity=quantity if settled else 0,
                    avg_cost=price,
                    market_price=price,
                    market_value=amount,
                    unrealized_pnl=0.0,
                )
            else:
                total_cost = pos.avg_cost * pos.quantity + amount
                total_qty = pos.quantity + quantity
                pos.quantity = total_qty
                if settled:
                    pos.available_quantity += quantity
                pos.avg_cost = total_cost / total_qty if total_qty > 0 else 0.0
                pos.market_price = price
                pos.market_value = pos.market_price * pos.quantity
                pos.unrealized_pnl = (pos.market_price - pos.avg_cost) * pos.quantity
        else:
            if pos is None or pos.available_quantity < quantity:
                return
            self._cash += amount
            pos.quantity -= quantity
            pos.available_quantity -= quantity
            pos.market_price = price
            if pos.quantity <= 0:
                del self._positions[code]
            else:
                pos.market_value = pos.market_price * pos.quantity
                pos.unrealized_pnl = (pos.market_price - pos.avg_cost) * pos.quantity
