"""Trade execution orchestration service."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from loguru import logger
from sqlalchemy import func

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    ExecutionEvent,
    PositionSnapshot,
    StockDaily,
    TradeFill,
    TradeOrder,
    TradeSignal,
)
from src.strategy.risk_manager import RiskManager
from src.trading.broker.base import BrokerAdapter
from src.trading.broker.paper import PaperBrokerAdapter
from src.trading.constants import (
    ORDER_ACTIVE_STATUSES,
    ORDER_FINAL_STATUSES,
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_PENDING_CONFIRM,
    ORDER_STATUS_REJECTED,
    ORDER_STATUS_SUBMITTED,
    SIDE_BUY,
)
from src.trading.models import CancelOrderRequest, OrderIntent, PlaceOrderRequest
from src.trading.order_mapper import signal_to_order_intent


class ExecutionService:
    """统一交易执行服务。"""

    STOCK_CODE_LENGTH = 6

    def __init__(self, config: dict | None = None, broker: BrokerAdapter | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        trading_cfg = self.config.get("trading", {})
        self.default_budget = float(trading_cfg.get("default_order_budget", 100_000))
        self.max_orders_per_run = int(trading_cfg.get("max_orders_per_run", 10))
        initial_cash = float(trading_cfg.get("paper_initial_cash", 1_000_000))
        self.broker = broker or PaperBrokerAdapter(initial_cash=initial_cash)
        self.risk = RiskManager(self.config)
        self.broker.connect()

    def prepare_orders(self, signal_date: str | None = None) -> list[str]:
        """生成待确认订单。"""
        target_date = signal_date or self._resolve_latest_signal_date()
        if not target_date:
            return []

        created_ids: list[str] = []
        account = self.broker.get_account()
        logger.info(f"prepare_orders: signal_date={target_date}, account_total={account.total_assets:.2f}")

        with get_db_session(self.db_path) as session:
            signals = (
                session.query(TradeSignal)
                .filter(TradeSignal.signal_date == target_date)
                .order_by(TradeSignal.composite_score.desc().nullslast())
                .limit(max(self.max_orders_per_run * 3, 30))
                .all()
            )
            if not signals:
                return []

            for sig in signals:
                if len(created_ids) >= self.max_orders_per_run:
                    break
                if not self._is_buy_signal(sig):
                    continue

                code = (sig.code or "").strip()
                if not code:
                    continue

                close_price = self._get_latest_close(session, code)
                intent = signal_to_order_intent(
                    signal_id=sig.id,
                    signal_date=sig.signal_date,
                    code=code,
                    name=sig.name or "",
                    side=SIDE_BUY,
                    signal_type=sig.signal_type or "",
                    signal_strength=float(sig.signal_strength or 0.5),
                    close_price=close_price,
                    recommendation=sig.ai_verdict or "",
                    reason=sig.reason or "",
                    budget=self.default_budget,
                )
                if intent is None:
                    continue

                # 幂等: 活跃/终态中的已成交单都不重复创建
                existing = (
                    session.query(TradeOrder)
                    .filter(TradeOrder.idempotency_key == intent.idempotency_key)
                    .order_by(TradeOrder.created_at.desc())
                    .first()
                )
                if existing:
                    # idempotency_key 在表上唯一，任一状态存在都不能重复创建
                    if existing.status in (ORDER_ACTIVE_STATUSES | {ORDER_STATUS_FILLED}):
                        logger.debug(
                            "prepare_orders skip existing active/filled order: key={} status={}",
                            intent.idempotency_key,
                            existing.status,
                        )
                    else:
                        logger.debug(
                            "prepare_orders skip duplicate idempotency key: key={} status={}",
                            intent.idempotency_key,
                            existing.status,
                        )
                    continue

                risk_result = self.risk.validate_order_intent(
                    {
                        "code": intent.code,
                        "name": intent.name,
                        "side": intent.side,
                        "price": intent.price,
                        "quantity": intent.quantity,
                    }
                )
                if not risk_result.get("passed", False):
                    self._create_rejected_order(session, intent, risk_result)
                    continue

                order_id = uuid4().hex
                order = TradeOrder(
                    id=order_id,
                    signal_id=intent.signal_id,
                    signal_date=intent.signal_date,
                    code=intent.code,
                    name=intent.name,
                    side=intent.side,
                    order_type=intent.order_type,
                    price=intent.price,
                    quantity=intent.quantity,
                    amount=round(intent.price * intent.quantity, 2),
                    status=ORDER_STATUS_PENDING_CONFIRM,
                    broker=self.broker.name,
                    strategy_tag=intent.strategy_tag,
                    idempotency_key=intent.idempotency_key,
                    risk_note="; ".join(risk_result.get("reasons", [])),
                )
                session.add(order)
                self._add_event(
                    session,
                    order_id=order.id,
                    event_type="ORDER_PREPARED",
                    payload={
                        "signal_id": intent.signal_id,
                        "signal_date": intent.signal_date,
                        "code": intent.code,
                        "price": intent.price,
                        "quantity": intent.quantity,
                        "idempotency_key": intent.idempotency_key,
                    },
                )
                created_ids.append(order.id)

        return created_ids

    def list_pending_orders(self) -> list[dict]:
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(TradeOrder)
                .filter(TradeOrder.status == ORDER_STATUS_PENDING_CONFIRM)
                .order_by(TradeOrder.created_at.desc())
                .all()
            )
            return [self._order_to_dict(r) for r in rows]

    def list_orders(self, limit: int = 300) -> list[dict]:
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(TradeOrder)
                .order_by(TradeOrder.created_at.desc())
                .limit(max(1, limit))
                .all()
            )
            return [self._order_to_dict(r) for r in rows]

    def confirm_and_send(self, order_id: str, operator: str = "manual") -> dict:
        with get_db_session(self.db_path) as session:
            order = session.query(TradeOrder).filter(TradeOrder.id == order_id).first()
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND"}
            if order.status != ORDER_STATUS_PENDING_CONFIRM:
                return {"ok": False, "error": f"ORDER_STATUS_{order.status}"}

            req = PlaceOrderRequest(
                client_order_id=order.id,
                code=order.code,
                side=order.side,
                order_type=order.order_type,
                price=float(order.price or 0),
                quantity=int(order.quantity or 0),
                strategy_tag=order.strategy_tag or "",
            )
            result = self.broker.place_order(req)
            now = datetime.now()
            order.confirmed_by = operator
            order.confirmed_at = now
            order.submitted_at = now
            order.updated_at = now
            order.broker_order_id = result.broker_order_id or order.broker_order_id

            if result.ok:
                order.status = result.status or ORDER_STATUS_SUBMITTED
                order.error_code = ""
                order.error_msg = ""
                order.filled_quantity = int(result.filled_quantity or 0)
                order.avg_fill_price = float(result.avg_fill_price or 0)
                if order.status in ORDER_FINAL_STATUSES:
                    order.finished_at = now
                self._save_fills(session, order, result.fills)
                self._add_event(
                    session,
                    order_id=order.id,
                    event_type="ORDER_SUBMITTED",
                    payload={
                        "broker_order_id": result.broker_order_id,
                        "status": order.status,
                        "filled_quantity": order.filled_quantity,
                    },
                )
                self._sync_positions_snapshot(session)
                return {"ok": True, "order_id": order.id, "status": order.status}

            order.status = result.status or ORDER_STATUS_FAILED
            order.error_code = result.error_code or "PLACE_ORDER_FAILED"
            order.error_msg = result.error_msg or ""
            if order.status in ORDER_FINAL_STATUSES:
                order.finished_at = now
            self._add_event(
                session,
                order_id=order.id,
                event_type="ORDER_REJECTED",
                payload={
                    "broker_order_id": result.broker_order_id,
                    "error_code": order.error_code,
                    "error_msg": order.error_msg,
                },
            )
            return {"ok": False, "error": order.error_msg or order.error_code}

    def refresh_order_status(self, order_id: str) -> dict:
        with get_db_session(self.db_path) as session:
            order = session.query(TradeOrder).filter(TradeOrder.id == order_id).first()
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND"}
            if not order.broker_order_id:
                return {"ok": False, "error": "BROKER_ORDER_ID_EMPTY"}
            if order.status in ORDER_FINAL_STATUSES:
                return {"ok": True, "status": order.status}

            status_result = self.broker.query_order(order.broker_order_id)
            if not status_result.ok:
                order.error_code = status_result.error_code or "QUERY_FAILED"
                order.error_msg = status_result.error_msg or ""
                order.updated_at = datetime.now()
                self._add_event(
                    session,
                    order_id=order.id,
                    event_type="ORDER_QUERY_FAILED",
                    payload={"error_code": order.error_code, "error_msg": order.error_msg},
                )
                return {"ok": False, "error": order.error_msg or order.error_code}

            order.status = status_result.status or order.status
            order.filled_quantity = int(status_result.filled_quantity or 0)
            order.avg_fill_price = float(status_result.avg_fill_price or 0)
            order.updated_at = datetime.now()
            if order.status in ORDER_FINAL_STATUSES:
                order.finished_at = datetime.now()
            self._save_fills(session, order, status_result.fills)
            self._add_event(
                session,
                order_id=order.id,
                event_type="ORDER_STATUS_SYNCED",
                payload={
                    "status": order.status,
                    "filled_quantity": order.filled_quantity,
                    "avg_fill_price": order.avg_fill_price,
                },
            )
            self._sync_positions_snapshot(session)
            return {"ok": True, "status": order.status}

    def cancel_order(self, order_id: str, operator: str = "manual") -> dict:
        with get_db_session(self.db_path) as session:
            order = session.query(TradeOrder).filter(TradeOrder.id == order_id).first()
            if not order:
                return {"ok": False, "error": "ORDER_NOT_FOUND"}
            if order.status in ORDER_FINAL_STATUSES:
                return {"ok": False, "error": f"ORDER_STATUS_{order.status}"}
            if not order.broker_order_id:
                order.status = ORDER_STATUS_CANCELED
                order.finished_at = datetime.now()
                self._add_event(
                    session,
                    order_id=order.id,
                    event_type="ORDER_CANCELED_LOCAL",
                    payload={"operator": operator},
                )
                return {"ok": True, "status": order.status}

            result = self.broker.cancel_order(CancelOrderRequest(broker_order_id=order.broker_order_id, code=order.code))
            if result.ok and result.canceled:
                order.status = ORDER_STATUS_CANCELED
                order.finished_at = datetime.now()
                order.updated_at = datetime.now()
                self._add_event(
                    session,
                    order_id=order.id,
                    event_type="ORDER_CANCELED",
                    payload={"operator": operator, "broker_order_id": order.broker_order_id},
                )
                return {"ok": True, "status": order.status}

            order.error_code = result.error_code or "CANCEL_FAILED"
            order.error_msg = result.error_msg or ""
            order.updated_at = datetime.now()
            self._add_event(
                session,
                order_id=order.id,
                event_type="ORDER_CANCEL_FAILED",
                payload={"error_code": order.error_code, "error_msg": order.error_msg},
            )
            return {"ok": False, "error": order.error_msg or order.error_code}

    def list_execution_events(self, order_id: str, limit: int = 200) -> list[dict]:
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(ExecutionEvent)
                .filter(ExecutionEvent.order_id == order_id)
                .order_by(ExecutionEvent.created_at.desc())
                .limit(max(1, limit))
                .all()
            )
            result = []
            for row in rows:
                payload = {}
                try:
                    payload = json.loads(row.payload_json or "{}")
                except Exception:
                    payload = {"raw": row.payload_json or ""}
                result.append(
                    {
                        "id": row.id,
                        "order_id": row.order_id,
                        "event_type": row.event_type,
                        "payload": payload,
                        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
                    }
                )
            return result

    def _is_buy_signal(self, sig: TradeSignal) -> bool:
        st = (sig.signal_type or "").lower()
        if st not in {"buy", "premarket"}:
            return False
        verdict = (sig.ai_verdict or "").strip()
        verdict_lower = verdict.lower()
        return not (
            verdict
            and any(k in verdict_lower for k in ("卖", "避", "观望", "sell", "avoid", "hold", "watch"))
        )

    def _resolve_latest_signal_date(self) -> str | None:
        with get_db_session(self.db_path) as session:
            return session.query(func.max(TradeSignal.signal_date)).scalar()

    def _get_latest_close(self, session, code: str) -> float:
        cands = [code]
        raw = (code or "").strip()
        if len(raw) == self.STOCK_CODE_LENGTH and raw.isdigit():
            cands.extend([f"sh{raw}", f"sz{raw}", f"bj{raw}"])
        row = (
            session.query(StockDaily)
            .filter(StockDaily.code.in_(list(dict.fromkeys(cands))))
            .order_by(StockDaily.trade_date.desc())
            .first()
        )
        return float(row.close or 0.0) if row else 0.0

    def _create_rejected_order(self, session, intent: OrderIntent, risk_result: dict) -> None:
        order_id = uuid4().hex
        reasons = risk_result.get("reasons", []) or ["risk check failed"]
        order = TradeOrder(
            id=order_id,
            signal_id=intent.signal_id,
            signal_date=intent.signal_date,
            code=intent.code,
            name=intent.name,
            side=intent.side,
            order_type=intent.order_type,
            price=intent.price,
            quantity=intent.quantity,
            amount=round(intent.price * intent.quantity, 2),
            status=ORDER_STATUS_REJECTED,
            broker=self.broker.name,
            strategy_tag=intent.strategy_tag,
            idempotency_key=intent.idempotency_key,
            risk_note="; ".join(reasons),
            error_code="RISK_REJECTED",
            error_msg="; ".join(reasons),
            finished_at=datetime.now(),
        )
        session.add(order)
        self._add_event(
            session,
            order_id=order.id,
            event_type="ORDER_REJECTED_BY_RISK",
            payload={"reasons": reasons},
        )

    def _save_fills(self, session, order: TradeOrder, fills: list[dict]) -> None:
        for fill in fills or []:
            broker_fill_id = str(fill.get("broker_fill_id") or "").strip()
            if broker_fill_id:
                exists = (
                    session.query(TradeFill)
                    .filter(TradeFill.order_id == order.id, TradeFill.broker_fill_id == broker_fill_id)
                    .first()
                )
                if exists:
                    continue
            filled_at = fill.get("filled_at")
            if not isinstance(filled_at, datetime):
                filled_at = datetime.now()
            f = TradeFill(
                order_id=order.id,
                broker_fill_id=broker_fill_id,
                code=order.code,
                side=order.side,
                price=float(fill.get("price") or 0),
                quantity=int(fill.get("quantity") or 0),
                amount=float(fill.get("amount") or 0),
                filled_at=filled_at,
            )
            session.add(f)

    def _sync_positions_snapshot(self, session) -> None:
        positions = self.broker.get_positions()
        now = datetime.now()
        for pos in positions:
            row = PositionSnapshot(
                code=pos.code,
                name=pos.name or "",
                quantity=int(pos.quantity or 0),
                available_quantity=int(pos.available_quantity or 0),
                avg_cost=float(pos.avg_cost or 0),
                market_price=float(pos.market_price or 0),
                market_value=float(pos.market_value or 0),
                unrealized_pnl=float(pos.unrealized_pnl or 0),
                source=self.broker.name,
                snapshot_at=now,
            )
            session.add(row)

    def _add_event(self, session, *, order_id: str, event_type: str, payload: dict) -> None:
        evt = ExecutionEvent(
            order_id=order_id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        session.add(evt)

    @staticmethod
    def _order_to_dict(order: TradeOrder) -> dict:
        return {
            "id": order.id,
            "signal_id": order.signal_id,
            "signal_date": order.signal_date,
            "code": order.code,
            "name": order.name,
            "side": order.side,
            "order_type": order.order_type,
            "price": order.price,
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
            "avg_fill_price": order.avg_fill_price,
            "amount": order.amount,
            "status": order.status,
            "broker": order.broker,
            "broker_order_id": order.broker_order_id or "",
            "strategy_tag": order.strategy_tag or "",
            "idempotency_key": order.idempotency_key,
            "risk_note": order.risk_note or "",
            "error_code": order.error_code or "",
            "error_msg": order.error_msg or "",
            "confirmed_by": order.confirmed_by or "",
            "confirmed_at": order.confirmed_at.strftime("%Y-%m-%d %H:%M:%S") if order.confirmed_at else "",
            "submitted_at": order.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if order.submitted_at else "",
            "finished_at": order.finished_at.strftime("%Y-%m-%d %H:%M:%S") if order.finished_at else "",
            "updated_at": order.updated_at.strftime("%Y-%m-%d %H:%M:%S") if order.updated_at else "",
            "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
        }
