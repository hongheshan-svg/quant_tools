"""Trading domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AccountInfo:
    account_id: str
    total_assets: float
    cash: float
    frozen_cash: float = 0.0
    source: str = "paper"


@dataclass
class Position:
    code: str
    name: str = ""
    quantity: int = 0
    available_quantity: int = 0
    avg_cost: float = 0.0
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass
class Quote:
    code: str
    last_price: float
    bid_price: float = 0.0
    ask_price: float = 0.0
    upper_limit: float = 0.0
    lower_limit: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PlaceOrderRequest:
    client_order_id: str
    code: str
    side: str
    order_type: str
    price: float
    quantity: int
    strategy_tag: str = ""


@dataclass
class PlaceOrderResult:
    ok: bool
    broker_order_id: str = ""
    status: str = ""
    error_code: str = ""
    error_msg: str = ""
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    fills: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CancelOrderRequest:
    broker_order_id: str
    code: str = ""


@dataclass
class CancelOrderResult:
    ok: bool
    broker_order_id: str
    canceled: bool
    error_code: str = ""
    error_msg: str = ""


@dataclass
class OrderStatusResult:
    ok: bool
    broker_order_id: str
    status: str
    filled_quantity: int = 0
    avg_fill_price: float = 0.0
    error_code: str = ""
    error_msg: str = ""
    fills: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OrderIntent:
    signal_id: int | None
    signal_date: str
    code: str
    name: str
    side: str
    order_type: str
    price: float
    quantity: int
    strategy_tag: str = ""
    idempotency_key: str = ""
    risk_note: str = ""
    source_strength: float = 0.0
    raw_reason: str = ""

