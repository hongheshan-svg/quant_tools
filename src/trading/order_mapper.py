"""Map trade signals to order intents."""

from __future__ import annotations

from src.trading.constants import ORDER_TYPE_LIMIT, SIDE_BUY
from src.trading.models import OrderIntent


def build_idempotency_key(signal_date: str, code: str, side: str, strategy_tag: str) -> str:
    return f"{signal_date}|{code}|{side}|{strategy_tag or 'default'}"


def signal_to_order_intent(
    *,
    signal_id: int | None,
    signal_date: str,
    code: str,
    name: str,
    side: str,
    signal_type: str,
    signal_strength: float,
    close_price: float,
    recommendation: str = "",
    reason: str = "",
    min_lot: int = 100,
    budget: float = 100_000.0,
) -> OrderIntent | None:
    if not code:
        return None

    norm_side = (side or "").lower().strip()
    if norm_side not in {"buy", "sell"}:
        norm_side = SIDE_BUY

    price = float(close_price or 0.0)
    if price <= 0:
        return None

    strength = max(0.1, min(1.0, float(signal_strength or 0.5)))
    raw_qty = int((budget * strength) / price)
    lot_qty = (raw_qty // min_lot) * min_lot
    if lot_qty <= 0:
        lot_qty = min_lot

    strategy_tag = (signal_type or recommendation or "signal").lower()[:40]
    idem = build_idempotency_key(signal_date, code, norm_side, strategy_tag)
    return OrderIntent(
        signal_id=signal_id,
        signal_date=signal_date,
        code=code,
        name=name or "",
        side=norm_side,
        order_type=ORDER_TYPE_LIMIT,
        price=round(price, 3),
        quantity=lot_qty,
        strategy_tag=strategy_tag,
        idempotency_key=idem,
        source_strength=strength,
        raw_reason=reason or "",
    )

