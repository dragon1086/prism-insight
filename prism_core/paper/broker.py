"""Internal deterministic simulated broker; no external broker capabilities."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from prism_core.paper.ledger import PaperLedger
from prism_core.paper.models import OrderEvent, OrderState, PaperOrder


class SimulatedBroker:
    def __init__(self, ledger: PaperLedger) -> None:
        self._ledger = ledger

    def create(self, order: PaperOrder) -> OrderEvent:
        return self._ledger.create_order(order)

    def accept(self, order_id: str, *, occurred_at: datetime) -> OrderEvent:
        return self._ledger.transition(order_id, OrderState.ACCEPTED, occurred_at)

    def cancel(self, order_id: str, *, occurred_at: datetime) -> OrderEvent:
        return self._ledger.transition(order_id, OrderState.CANCELED, occurred_at)

    def reject(self, order_id: str, *, occurred_at: datetime) -> OrderEvent:
        return self._ledger.transition(order_id, OrderState.REJECTED, occurred_at)

    def mark_unknown(self, order_id: str, *, occurred_at: datetime) -> OrderEvent:
        return self._ledger.transition(order_id, OrderState.UNKNOWN, occurred_at)

    def fill(
        self,
        order_id: str,
        *,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        occurred_at: datetime,
    ) -> OrderEvent:
        return self._ledger.record_fill(
            order_id,
            fill_id=fill_id,
            quantity=quantity,
            price=price,
            fee=fee,
            occurred_at=occurred_at,
        )
