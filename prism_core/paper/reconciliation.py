"""Fail-closed reconciliation for simulated UNKNOWN outcomes."""

from __future__ import annotations

from datetime import datetime

from prism_core.paper.ledger import PaperLedger
from prism_core.paper.models import OrderEvent, OrderState


class OrderReconciler:
    def __init__(self, ledger: PaperLedger) -> None:
        self._ledger = ledger

    def resolve(
        self,
        order_id: str,
        *,
        observed_state: OrderState,
        occurred_at: datetime,
    ) -> OrderEvent:
        return self._ledger.reconcile_unknown(order_id, observed_state, occurred_at)