from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.paper import (
    InvalidOrderTransition,
    OrderReconciler,
    OrderState,
    PaperLedger,
    PaperOrder,
    ReconciliationMismatch,
    SimulatedBroker,
    Side,
)


NOW = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)


def _unknown_order(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "paper.sqlite")
    ledger.create_book(
        book_id="swing-us",
        strategy_id="SWING_V1",
        market="US",
        currency="USD",
        created_at=NOW,
    )
    ledger.deposit(
        entry_id="deposit-1",
        book_id="swing-us",
        currency="USD",
        amount=Decimal("2000"),
        effective_at=NOW,
    )
    broker = SimulatedBroker(ledger)
    broker.create(
        PaperOrder(
            order_id="order-1",
            book_id="swing-us",
            proposal_id="proposal-1",
            security_id="security-1",
            side=Side.BUY,
            quantity=Decimal("10"),
            limit_price=Decimal("100"),
            occurred_at=NOW,
        )
    )
    broker.accept("order-1", occurred_at=NOW)
    broker.mark_unknown("order-1", occurred_at=NOW)
    return ledger, broker


def test_unknown_blocks_retry_until_reconciled_and_resolution_is_idempotent(
    tmp_path: Path,
):
    ledger, broker = _unknown_order(tmp_path)
    reconciler = OrderReconciler(ledger)

    with pytest.raises(InvalidOrderTransition, match="UNKNOWN -> FILLED"):
        broker.fill(
            "order-1",
            fill_id="fill-1",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    resolved = reconciler.resolve(
        "order-1", observed_state=OrderState.ACCEPTED, occurred_at=NOW
    )
    duplicate = reconciler.resolve(
        "order-1", observed_state=OrderState.ACCEPTED, occurred_at=NOW
    )
    filled = broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("10"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=NOW,
    )

    assert resolved.state is OrderState.ACCEPTED
    assert duplicate == resolved
    assert filled.state is OrderState.FILLED
    assert [event.state for event in ledger.order_history("order-1")] == [
        OrderState.CREATED,
        OrderState.ACCEPTED,
        OrderState.UNKNOWN,
        OrderState.ACCEPTED,
        OrderState.FILLED,
    ]


def test_reconciliation_rejects_state_inconsistent_with_durable_fills(tmp_path: Path):
    ledger, _ = _unknown_order(tmp_path)

    with pytest.raises(ReconciliationMismatch, match="FILLED"):
        OrderReconciler(ledger).resolve(
            "order-1", observed_state=OrderState.FILLED, occurred_at=NOW
        )

    assert ledger.current_order("order-1").state is OrderState.UNKNOWN
    assert ledger.fill_count("order-1") == 0


def test_reconciliation_cannot_backdate_resolution(tmp_path: Path):
    ledger, broker = _unknown_order(tmp_path)
    resolved = OrderReconciler(ledger).resolve(
        "order-1", observed_state=OrderState.ACCEPTED, occurred_at=NOW
    )
    broker.mark_unknown("order-1", occurred_at=NOW + timedelta(hours=1))

    with pytest.raises(ValueError, match="before current order state"):
        OrderReconciler(ledger).resolve(
            "order-1", observed_state=OrderState.ACCEPTED, occurred_at=NOW
        )

    assert resolved.state is OrderState.ACCEPTED
    assert ledger.current_order("order-1").state is OrderState.UNKNOWN
