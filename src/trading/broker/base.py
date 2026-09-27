"""Broker adapter abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod

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


class BrokerAdapter(ABC):
    """统一交易网关接口。"""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> AccountInfo:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def get_quotes(self, codes: list[str]) -> dict[str, Quote]:
        raise NotImplementedError

    @abstractmethod
    def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, req: CancelOrderRequest) -> CancelOrderResult:
        raise NotImplementedError

    @abstractmethod
    def query_order(self, broker_order_id: str) -> OrderStatusResult:
        raise NotImplementedError

