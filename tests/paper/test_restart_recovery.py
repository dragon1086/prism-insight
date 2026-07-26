from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from prism_core.paper import OrderState, PaperLedger, PaperOrder, SimulatedBroker, Side


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def test_restart_recovers_partial_order_and_continues_without_duplicate_effects(
    tmp_path: Path,
):
    path = tmp_path / "paper.sqlite"
    first_ledger = PaperLedger(path)
    first_ledger.create_book(
        book_id="trend-us",
        strategy_id="TREND_V1",
        market="US",
        currency="USD",
        created_at=NOW,
    )
    first_ledger.deposit(
        entry_id="deposit-1",
        book_id="trend-us",
        currency="USD",
        amount=Decimal("5000"),
        effective_at=NOW,
    )
    first_broker = SimulatedBroker(first_ledger)
    first_broker.create(
        PaperOrder(
            order_id="order-1",
            book_id="trend-us",
            proposal_id="proposal-1",
            security_id="security-1",
            side=Side.BUY,
            quantity=Decimal("10"),
            limit_price=Decimal("100"),
            occurred_at=NOW,
        )
    )
    first_broker.accept("order-1", occurred_at=NOW)
    first_broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("3"),
        price=Decimal("99"),
        fee=Decimal("0.30"),
        occurred_at=NOW,
    )

    recovered_ledger = PaperLedger(path)
    recovered_broker = SimulatedBroker(recovered_ledger)
    recovered = recovered_ledger.current_order("order-1")
    completed = recovered_broker.fill(
        "order-1",
        fill_id="fill-2",
        quantity=Decimal("7"),
        price=Decimal("100"),
        fee=Decimal("0.70"),
        occurred_at=NOW + timedelta(seconds=1),
    )

    assert recovered.state is OrderState.PARTIALLY_FILLED
    assert recovered.filled_quantity == Decimal("3")
    assert completed.state is OrderState.FILLED
    assert recovered_ledger.fill_count("order-1") == 2
    assert recovered_ledger.position("trend-us", "security-1").quantity == Decimal("10")
    assert recovered_ledger.cash_balance("trend-us", "USD") == Decimal("4002")


def test_strategy_books_keep_same_security_positions_separate(tmp_path: Path):
    ledger = PaperLedger(tmp_path / "paper.sqlite")
    broker = SimulatedBroker(ledger)
    for book_id, strategy_id in (("swing-us", "SWING_V1"), ("trend-us", "TREND_V1")):
        ledger.create_book(
            book_id=book_id,
            strategy_id=strategy_id,
            market="US",
            currency="USD",
            created_at=NOW,
        )
        ledger.deposit(
            entry_id=f"{book_id}:deposit",
            book_id=book_id,
            currency="USD",
            amount=Decimal("1000"),
            effective_at=NOW,
        )
        broker.create(
            PaperOrder(
                order_id=f"{book_id}:order",
                book_id=book_id,
                proposal_id=f"{book_id}:proposal",
                security_id="security-1",
                side=Side.BUY,
                quantity=Decimal("2"),
                limit_price=Decimal("100"),
                occurred_at=NOW,
            )
        )
        broker.accept(f"{book_id}:order", occurred_at=NOW)
        broker.fill(
            f"{book_id}:order",
            fill_id=f"{book_id}:fill",
            quantity=Decimal("2"),
            price=Decimal("100"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    assert ledger.position("swing-us", "security-1").quantity == Decimal("2")
    assert ledger.position("trend-us", "security-1").quantity == Decimal("2")
    assert ledger.cash_balance("swing-us", "USD") == Decimal("800")
    assert ledger.cash_balance("trend-us", "USD") == Decimal("800")


def test_same_book_security_accepts_two_order_fills_at_same_timestamp(tmp_path: Path):
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
        amount=Decimal("1000"),
        effective_at=NOW,
    )
    broker = SimulatedBroker(ledger)
    for sequence in (1, 2):
        order_id = f"order-{sequence}"
        broker.create(
            PaperOrder(
                order_id=order_id,
                book_id="swing-us",
                proposal_id=f"proposal-{sequence}",
                security_id="security-1",
                side=Side.BUY,
                quantity=Decimal("2"),
                limit_price=Decimal("100"),
                occurred_at=NOW,
            )
        )
        broker.accept(order_id, occurred_at=NOW)
        broker.fill(
            order_id,
            fill_id=f"fill-{sequence}",
            quantity=Decimal("2"),
            price=Decimal("100"),
            fee=Decimal("0"),
            occurred_at=NOW,
        )

    assert ledger.position("swing-us", "security-1").quantity == Decimal("4")
    assert ledger.fill_count("order-1") == 1
    assert ledger.fill_count("order-2") == 1
