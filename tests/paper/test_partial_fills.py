from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

import pytest

from prism_core.paper import (
    InsufficientCash,
    InsufficientPosition,
    OrderState,
    PaperLedger,
    PaperOrder,
    SimulatedBroker,
    Side,
)


NOW = datetime(2026, 7, 26, 7, 0, tzinfo=timezone.utc)


def _ready_broker(tmp_path: Path, *, cash: Decimal = Decimal("2000")):
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
        amount=cash,
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
            limit_price=Decimal("105"),
            occurred_at=NOW,
        )
    )
    broker.accept("order-1", occurred_at=NOW)
    return ledger, broker


def test_partial_fills_update_cash_position_and_average_cost_atomically(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)

    first = broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("4"),
        price=Decimal("100"),
        fee=Decimal("1"),
        occurred_at=NOW,
    )
    second = broker.fill(
        "order-1",
        fill_id="fill-2",
        quantity=Decimal("6"),
        price=Decimal("102"),
        fee=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=1),
    )

    assert first.state is OrderState.PARTIALLY_FILLED
    assert second.state is OrderState.FILLED
    assert ledger.cash_balance("swing-us", "USD") == Decimal("986")
    position = ledger.position("swing-us", "security-1")
    assert position.quantity == Decimal("10")
    assert position.average_cost == Decimal("101.4")


def test_duplicate_fill_is_idempotent_and_does_not_double_account(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    kwargs = {
        "fill_id": "fill-1",
        "quantity": Decimal("4"),
        "price": Decimal("100"),
        "fee": Decimal("1"),
        "occurred_at": NOW,
    }

    first = broker.fill("order-1", **kwargs)
    retry_kwargs = dict(kwargs)
    retry_kwargs["quantity"] = Decimal("4.0")
    retry_kwargs["price"] = Decimal("100.00")
    retry_kwargs["fee"] = Decimal("1.00")
    duplicate = broker.fill("order-1", **retry_kwargs)

    assert duplicate == first
    assert ledger.cash_balance("swing-us", "USD") == Decimal("1599")
    assert ledger.position("swing-us", "security-1").quantity == Decimal("4")
    assert ledger.fill_count("order-1") == 1


def test_decimal_accounting_is_independent_of_global_context(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        broker.fill(
            "order-1",
            fill_id="fill-1",
            quantity=Decimal("3"),
            price=Decimal("10"),
            fee=Decimal("1"),
            occurred_at=NOW,
        )
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        expected = Decimal("31") / Decimal("3")

    assert ledger.position("swing-us", "security-1").average_cost == expected


def test_nav_accounting_is_independent_of_global_context(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("3"),
        price=Decimal("10"),
        fee=Decimal("1"),
        occurred_at=NOW,
    )

    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        snapshot = ledger.snapshot_nav(
            snapshot_id="nav-low-context",
            book_id="swing-us",
            mark_prices={"security-1": Decimal("3.3333333333333")},
            as_of_at=NOW + timedelta(seconds=1),
        )
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        expected = Decimal("1969") + Decimal("3") * Decimal("3.3333333333333")

    assert snapshot.cash == Decimal("1969")
    assert snapshot.nav == expected


def test_filled_quantity_is_independent_of_global_context(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    broker.create(
        PaperOrder(
            order_id="precise-order",
            book_id="swing-us",
            proposal_id="precise-proposal",
            security_id="security-2",
            side=Side.BUY,
            quantity=Decimal("1.0000001"),
            limit_price=Decimal("1"),
            occurred_at=NOW,
        )
    )
    broker.accept("precise-order", occurred_at=NOW)
    broker.fill(
        "precise-order",
        fill_id="precise-fill-1",
        quantity=Decimal("0.0000001"),
        price=Decimal("1"),
        fee=Decimal("0"),
        occurred_at=NOW,
    )

    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        completed = broker.fill(
            "precise-order",
            fill_id="precise-fill-2",
            quantity=Decimal("1"),
            price=Decimal("1"),
            fee=Decimal("0"),
            occurred_at=NOW + timedelta(seconds=1),
        )

    assert completed.state is OrderState.FILLED
    assert completed.filled_quantity == Decimal("1.0000001")


def test_next_partial_fill_requires_later_event_time(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("4"),
        price=Decimal("100"),
        fee=Decimal("0"),
        occurred_at=NOW,
    )

    with pytest.raises(ValueError, match="later than prior partial fill"):
        broker.fill(
            "order-1",
            fill_id="fill-2",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    assert ledger.fill_count("order-1") == 1


def test_insufficient_cash_rolls_back_fill_and_state_event(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path, cash=Decimal("100"))

    with pytest.raises(InsufficientCash):
        broker.fill(
            "order-1",
            fill_id="fill-1",
            quantity=Decimal("4"),
            price=Decimal("100"),
            fee=Decimal("1"),
            occurred_at=NOW,
        )

    assert ledger.fill_count("order-1") == 0
    assert ledger.cash_balance("swing-us", "USD") == Decimal("100")
    assert ledger.position("swing-us", "security-1").quantity == Decimal("0")
    assert ledger.current_order("order-1").state is OrderState.ACCEPTED


def test_fill_price_must_respect_limit_without_ledger_effects(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)

    with pytest.raises(ValueError, match="buy fill price exceeds limit"):
        broker.fill(
            "order-1",
            fill_id="fill-1",
            quantity=Decimal("1"),
            price=Decimal("106"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    assert ledger.fill_count("order-1") == 0
    assert ledger.cash_balance("swing-us", "USD") == Decimal("2000")


def test_sell_fill_preserves_average_cost_and_updates_nav(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    broker.fill(
        "order-1",
        fill_id="buy-fill",
        quantity=Decimal("10"),
        price=Decimal("100"),
        fee=Decimal("1"),
        occurred_at=NOW,
    )
    broker.create(
        PaperOrder(
            order_id="sell-1",
            book_id="swing-us",
            proposal_id="proposal-sell",
            security_id="security-1",
            side=Side.SELL,
            quantity=Decimal("4"),
            limit_price=Decimal("110"),
            occurred_at=NOW + timedelta(seconds=1),
        )
    )
    broker.accept("sell-1", occurred_at=NOW + timedelta(seconds=1))
    broker.fill(
        "sell-1",
        fill_id="sell-fill",
        quantity=Decimal("4"),
        price=Decimal("110"),
        fee=Decimal("1"),
        occurred_at=NOW + timedelta(seconds=1),
    )

    position = ledger.position("swing-us", "security-1")
    nav = ledger.snapshot_nav(
        snapshot_id="nav-1",
        book_id="swing-us",
        mark_prices={"security-1": Decimal("110")},
        as_of_at=NOW + timedelta(seconds=2),
    )
    assert position.quantity == Decimal("6")
    assert position.average_cost == Decimal("100.1")
    assert ledger.cash_balance("swing-us", "USD") == Decimal("1438")
    assert nav.cash == Decimal("1438")
    assert nav.nav == Decimal("2098")


def test_oversell_rolls_back_all_accounting(tmp_path: Path):
    ledger, broker = _ready_broker(tmp_path)
    broker.create(
        PaperOrder(
            order_id="sell-1",
            book_id="swing-us",
            proposal_id="proposal-sell",
            security_id="security-1",
            side=Side.SELL,
            quantity=Decimal("1"),
            limit_price=Decimal("100"),
            occurred_at=NOW,
        )
    )
    broker.accept("sell-1", occurred_at=NOW)

    with pytest.raises(InsufficientPosition):
        broker.fill(
            "sell-1",
            fill_id="sell-fill",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    assert ledger.fill_count("sell-1") == 0
    assert ledger.current_order("sell-1").state is OrderState.ACCEPTED
