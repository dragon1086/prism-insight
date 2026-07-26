from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.paper import (
    DuplicateOrder,
    InvalidOrderTransition,
    OrderState,
    PaperLedger,
    PaperOrder,
    SimulatedBroker,
    Side,
)


NOW = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)


def _broker(tmp_path: Path) -> tuple[PaperLedger, SimulatedBroker]:
    ledger = PaperLedger(tmp_path / "paper.sqlite")
    ledger.create_book(
        book_id="swing-us",
        strategy_id="SWING_V1",
        market="US",
        currency="USD",
        created_at=NOW,
    )
    return ledger, SimulatedBroker(ledger)


def _order() -> PaperOrder:
    return PaperOrder(
        order_id="order-1",
        book_id="swing-us",
        proposal_id="proposal-1",
        security_id="security-1",
        side=Side.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("101.25"),
        occurred_at=NOW,
    )


def test_order_lifecycle_is_durable_and_append_only(tmp_path: Path):
    ledger, broker = _broker(tmp_path)

    created = broker.create(_order())
    accepted = broker.accept("order-1", occurred_at=NOW)

    assert created.state is OrderState.CREATED
    assert accepted.state is OrderState.ACCEPTED
    assert ledger.order_history("order-1") == (created, accepted)


def test_duplicate_transition_is_idempotent_and_time_cannot_move_backwards(
    tmp_path: Path,
):
    ledger, broker = _broker(tmp_path)
    broker.create(_order())
    accepted = broker.accept("order-1", occurred_at=NOW)

    assert broker.accept("order-1", occurred_at=NOW) == accepted
    with pytest.raises(ValueError, match="before current order state"):
        broker.cancel(
            "order-1",
            occurred_at=datetime(2026, 7, 26, 5, 59, tzinfo=timezone.utc),
        )
    assert len(ledger.order_history("order-1")) == 2


def test_invalid_transition_is_rejected_without_new_event(tmp_path: Path):
    ledger, broker = _broker(tmp_path)
    broker.create(_order())

    with pytest.raises(InvalidOrderTransition, match="CREATED -> FILLED"):
        broker.fill(
            "order-1",
            fill_id="fill-1",
            quantity=Decimal("10"),
            price=Decimal("100"),
            fee=Decimal("1"),
            occurred_at=NOW,
        )

    assert [event.state for event in ledger.order_history("order-1")] == [
        OrderState.CREATED
    ]


def test_rejection_and_partial_cancellation_are_terminal(tmp_path: Path):
    ledger, broker = _broker(tmp_path)
    broker.create(_order())
    rejected = broker.reject("order-1", occurred_at=NOW)

    assert rejected.state is OrderState.REJECTED
    with pytest.raises(InvalidOrderTransition, match="REJECTED -> ACCEPTED"):
        broker.accept("order-1", occurred_at=NOW)

    second = PaperOrder(
        order_id="order-2",
        book_id="swing-us",
        proposal_id="proposal-2",
        security_id="security-2",
        side=Side.BUY,
        quantity=Decimal("10"),
        limit_price=Decimal("100"),
        occurred_at=NOW,
    )
    ledger.deposit(
        entry_id="deposit-1",
        book_id="swing-us",
        currency="USD",
        amount=Decimal("1000"),
        effective_at=NOW,
    )
    broker.create(second)
    broker.accept("order-2", occurred_at=NOW)
    broker.fill(
        "order-2",
        fill_id="fill-2",
        quantity=Decimal("4"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=NOW,
    )
    canceled = broker.cancel("order-2", occurred_at=NOW)

    assert canceled.state is OrderState.CANCELED
    assert canceled.filled_quantity == Decimal("4")


def test_book_rejects_wrong_currency_and_order_ids_reserved_for_events(tmp_path: Path):
    ledger, broker = _broker(tmp_path)

    ledger.deposit(
        entry_id="deposit-usd",
        book_id="swing-us",
        currency="USD",
        amount=Decimal("100"),
        effective_at=NOW,
    )
    ledger.deposit(
        entry_id="deposit-usd",
        book_id="swing-us",
        currency="USD",
        amount=Decimal("100.00"),
        effective_at=NOW,
    )
    assert ledger.cash_balance("swing-us", "USD") == Decimal("100")

    with pytest.raises(ValueError, match="book currency"):
        ledger.deposit(
            entry_id="deposit-eur",
            book_id="swing-us",
            currency="EUR",
            amount=Decimal("100"),
            effective_at=NOW,
        )
    with pytest.raises(DuplicateOrder, match="reserved"):
        broker.create(
            PaperOrder(
                order_id="order:event:1",
                book_id="swing-us",
                proposal_id="proposal-2",
                security_id="security-2",
                side=Side.BUY,
                quantity=Decimal("1"),
                limit_price=Decimal("1"),
                occurred_at=NOW,
            )
        )
