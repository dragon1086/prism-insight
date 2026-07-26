"""Transactional append-only accounting for the internal paper broker."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

from prism_core.paper.models import (
    DuplicateFill,
    DuplicateOrder,
    InsufficientCash,
    InsufficientPosition,
    InvalidOrderTransition,
    NavSnapshot,
    OrderEvent,
    OrderState,
    PaperOrder,
    PositionSnapshot,
    ReconciliationMismatch,
    Side,
    require_decimal,
    require_utc,
)
from prism_core.storage.database import open_database, transaction
from prism_core.storage.migrations import DatabaseKind, migrate_database


_ALLOWED = {
    OrderState.CREATED: {
        OrderState.ACCEPTED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.UNKNOWN,
    },
    OrderState.FILLED: set(),
    OrderState.CANCELED: set(),
    OrderState.REJECTED: set(),
    OrderState.UNKNOWN: set(),
}


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _same_decimal(stored: str, value: Decimal) -> bool:
    return Decimal(stored) == value


def _payload(event: OrderEvent) -> str:
    return json.dumps(
        {
            "logical_order_id": event.order_id,
            "side": event.side.value,
            "quantity": _decimal_text(event.quantity),
            "limit_price": _decimal_text(event.limit_price),
            "filled_quantity": _decimal_text(event.filled_quantity),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _event_from_row(row: sqlite3.Row | tuple) -> OrderEvent:
    event_id, book_id, proposal_id, security_id, state, payload, occurred_at = row
    data = json.loads(payload)
    return OrderEvent(
        order_id=data["logical_order_id"],
        book_id=book_id,
        proposal_id=proposal_id,
        security_id=security_id,
        side=Side(data["side"]),
        quantity=Decimal(data["quantity"]),
        limit_price=Decimal(data["limit_price"]),
        state=OrderState(state),
        occurred_at=datetime.fromisoformat(occurred_at),
        event_id=event_id,
        filled_quantity=Decimal(data.get("filled_quantity", "0")),
    )


class PaperLedger:
    """Own one managed paper.sqlite path and reconstruct state from events."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        with open_database(self.path) as connection:
            migrate_database(connection, DatabaseKind.PAPER)

    def create_book(
        self,
        *,
        book_id: str,
        strategy_id: str,
        market: str,
        currency: str,
        created_at: datetime,
    ) -> None:
        timestamp = require_utc(created_at, "created_at").isoformat()
        with open_database(self.path) as connection, transaction(connection):
            existing = connection.execute(
                "SELECT strategy_id, market, currency, created_at "
                "FROM strategy_books WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            expected = (strategy_id, market, currency, timestamp)
            if existing is not None:
                if tuple(existing) != expected:
                    raise ValueError("book_id already exists with different data")
                return
            connection.execute(
                "INSERT INTO strategy_books "
                "(book_id, strategy_id, market, currency, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (book_id, strategy_id, market, currency, timestamp),
            )

    def create_order(self, order: PaperOrder) -> OrderEvent:
        if ":event:" in order.order_id:
            raise DuplicateOrder("reserved order_id namespace ':event:'")
        event = OrderEvent(
            order_id=order.order_id,
            book_id=order.book_id,
            proposal_id=order.proposal_id,
            security_id=order.security_id,
            side=order.side,
            quantity=order.quantity,
            limit_price=order.limit_price,
            state=OrderState.CREATED,
            occurred_at=require_utc(order.occurred_at),
            event_id=order.order_id,
        )
        with open_database(self.path) as connection, transaction(connection):
            history = self._history(connection, order.order_id)
            if history:
                if history[0] == event:
                    return history[0]
                raise DuplicateOrder(order.order_id)
            self._insert_event(connection, event)
        return event

    def deposit(
        self,
        *,
        entry_id: str,
        book_id: str,
        currency: str,
        amount: Decimal,
        effective_at: datetime,
    ) -> None:
        amount = require_decimal(amount, "amount", positive=True)
        timestamp = require_utc(effective_at, "effective_at").isoformat()
        values = (
            entry_id,
            book_id,
            currency,
            _decimal_text(amount),
            "DEPOSIT",
            timestamp,
            timestamp,
        )
        with open_database(self.path) as connection, transaction(connection):
            book = connection.execute(
                "SELECT currency FROM strategy_books WHERE book_id = ?", (book_id,)
            ).fetchone()
            if book is None:
                raise KeyError(book_id)
            if currency != book[0]:
                raise ValueError(
                    f"deposit currency {currency} does not match book currency {book[0]}"
                )
            existing = connection.execute(
                "SELECT cash_entry_id, book_id, currency, amount, entry_kind, "
                "effective_at, created_at FROM cash_ledger WHERE cash_entry_id = ?",
                (entry_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing[0] != entry_id
                    or existing[1] != book_id
                    or existing[2] != currency
                    or not _same_decimal(existing[3], amount)
                    or tuple(existing[4:]) != values[4:]
                ):
                    raise ValueError("cash entry identity reused with different data")
                return
            connection.execute(
                "INSERT INTO cash_ledger "
                "(cash_entry_id, book_id, currency, amount, entry_kind, "
                "effective_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def order_history(self, order_id: str) -> tuple[OrderEvent, ...]:
        with open_database(self.path) as connection:
            return self._history(connection, order_id)

    def current_order(self, order_id: str) -> OrderEvent:
        history = self.order_history(order_id)
        if not history:
            raise KeyError(order_id)
        return history[-1]

    def cash_balance(self, book_id: str, currency: str) -> Decimal:
        with open_database(self.path) as connection:
            rows = connection.execute(
                "SELECT amount FROM cash_ledger WHERE book_id = ? AND currency = ?",
                (book_id, currency),
            ).fetchall()
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            return sum((Decimal(row[0]) for row in rows), Decimal("0"))

    def position(self, book_id: str, security_id: str) -> PositionSnapshot:
        with open_database(self.path) as connection:
            row = connection.execute(
                "SELECT quantity, average_cost, as_of_at FROM positions "
                "WHERE book_id = ? AND security_id = ? ORDER BY rowid DESC LIMIT 1",
                (book_id, security_id),
            ).fetchone()
        if row is None:
            return PositionSnapshot(
                book_id, security_id, Decimal("0"), Decimal("0"), None
            )
        return PositionSnapshot(
            book_id,
            security_id,
            Decimal(row[0]),
            Decimal(row[1]),
            datetime.fromisoformat(row[2]),
        )

    def fill_count(self, order_id: str) -> int:
        with open_database(self.path) as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM fills WHERE order_id = ?", (order_id,)
            ).fetchone()
        return int(row[0])

    def snapshot_nav(
        self,
        *,
        snapshot_id: str,
        book_id: str,
        mark_prices: dict[str, Decimal],
        as_of_at: datetime,
    ) -> NavSnapshot:
        timestamp = require_utc(as_of_at, "as_of_at")
        with open_database(self.path) as connection, transaction(connection):
            book = connection.execute(
                "SELECT currency FROM strategy_books WHERE book_id = ?", (book_id,)
            ).fetchone()
            if book is None:
                raise KeyError(book_id)
            cash_rows = connection.execute(
                "SELECT amount FROM cash_ledger WHERE book_id = ? AND currency = ?",
                (book_id, book[0]),
            ).fetchall()
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                cash = sum((Decimal(row[0]) for row in cash_rows), Decimal("0"))
            position_rows = connection.execute(
                "SELECT p.security_id, p.quantity FROM positions p "
                "WHERE p.book_id = ? AND p.rowid = ("
                "SELECT MAX(latest.rowid) FROM positions latest "
                "WHERE latest.book_id = p.book_id "
                "AND latest.security_id = p.security_id)",
                (book_id,),
            ).fetchall()
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                market_value = Decimal("0")
                for security_id, quantity_text in position_rows:
                    quantity = Decimal(quantity_text)
                    if quantity == 0:
                        continue
                    if security_id not in mark_prices:
                        raise ValueError(f"missing mark price for {security_id}")
                    mark = require_decimal(
                        mark_prices[security_id],
                        f"mark_prices[{security_id}]",
                        positive=True,
                    )
                    market_value += quantity * mark
                snapshot = NavSnapshot(
                    snapshot_id=snapshot_id,
                    book_id=book_id,
                    nav=cash + market_value,
                    cash=cash,
                    as_of_at=timestamp,
                )
            values = (
                snapshot.snapshot_id,
                snapshot.book_id,
                _decimal_text(snapshot.nav),
                _decimal_text(snapshot.cash),
                timestamp.isoformat(),
                timestamp.isoformat(),
            )
            existing = connection.execute(
                "SELECT nav_snapshot_id, book_id, nav, cash, as_of_at, created_at "
                "FROM nav_snapshots WHERE nav_snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != values:
                    raise ValueError("NAV snapshot identity reused with different data")
                return snapshot
            connection.execute(
                "INSERT INTO nav_snapshots "
                "(nav_snapshot_id, book_id, nav, cash, as_of_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                values,
            )
            return snapshot

    def transition(
        self, order_id: str, state: OrderState, occurred_at: datetime
    ) -> OrderEvent:
        timestamp = require_utc(occurred_at, "occurred_at")
        with open_database(self.path) as connection, transaction(connection):
            history = self._history(connection, order_id)
            if not history:
                raise KeyError(order_id)
            current = history[-1]
            if timestamp < current.occurred_at:
                raise ValueError("occurred_at is before current order state")
            if state is current.state and timestamp == current.occurred_at:
                return current
            self._require_transition(current.state, state)
            event = OrderEvent(
                order_id=current.order_id,
                book_id=current.book_id,
                proposal_id=current.proposal_id,
                security_id=current.security_id,
                side=current.side,
                quantity=current.quantity,
                limit_price=current.limit_price,
                state=state,
                occurred_at=timestamp,
                event_id=f"{order_id}:event:{len(history)}",
                filled_quantity=current.filled_quantity,
            )
            self._insert_event(connection, event)
            return event

    def reconcile_unknown(
        self, order_id: str, observed_state: OrderState, occurred_at: datetime
    ) -> OrderEvent:
        timestamp = require_utc(occurred_at, "occurred_at")
        if observed_state is OrderState.UNKNOWN:
            raise ReconciliationMismatch("UNKNOWN is not a resolved state")
        with open_database(self.path) as connection, transaction(connection):
            history = self._history(connection, order_id)
            if not history:
                raise KeyError(order_id)
            current = history[-1]
            if timestamp < current.occurred_at:
                raise ValueError("occurred_at is before current order state")
            if current.state is not OrderState.UNKNOWN:
                if (
                    current.state is observed_state
                    and len(history) >= 2
                    and history[-2].state is OrderState.UNKNOWN
                ):
                    return current
                raise ReconciliationMismatch(
                    f"order {order_id} is not awaiting reconciliation"
                )
            filled = current.filled_quantity
            if observed_state is OrderState.ACCEPTED and filled != 0:
                raise ReconciliationMismatch("ACCEPTED contradicts durable fills")
            if observed_state is OrderState.PARTIALLY_FILLED and not (
                Decimal("0") < filled < current.quantity
            ):
                raise ReconciliationMismatch(
                    "PARTIALLY_FILLED contradicts durable fills"
                )
            if observed_state is OrderState.FILLED and filled != current.quantity:
                raise ReconciliationMismatch("FILLED contradicts durable fills")
            if observed_state is OrderState.REJECTED and filled != 0:
                raise ReconciliationMismatch("REJECTED contradicts durable fills")
            if observed_state is OrderState.CREATED:
                raise ReconciliationMismatch("CREATED is not a reconciliation result")
            event = OrderEvent(
                order_id=current.order_id,
                book_id=current.book_id,
                proposal_id=current.proposal_id,
                security_id=current.security_id,
                side=current.side,
                quantity=current.quantity,
                limit_price=current.limit_price,
                state=observed_state,
                occurred_at=timestamp,
                event_id=f"{order_id}:event:{len(history)}",
                filled_quantity=filled,
            )
            self._insert_event(connection, event)
            return event

    def record_fill(
        self,
        order_id: str,
        *,
        fill_id: str,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        occurred_at: datetime,
    ) -> OrderEvent:
        quantity = require_decimal(quantity, "quantity", positive=True)
        price = require_decimal(price, "price", positive=True)
        fee = require_decimal(fee, "fee")
        if fee < 0:
            raise ValueError("fee must be non-negative")
        timestamp = require_utc(occurred_at, "occurred_at")
        with open_database(self.path) as connection, transaction(connection):
            history = self._history(connection, order_id)
            if not history:
                raise KeyError(order_id)
            current = history[-1]
            duplicate = connection.execute(
                "SELECT order_id, quantity, price, fee, occurred_at, created_at "
                "FROM fills WHERE fill_id = ?",
                (fill_id,),
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate[0] != order_id
                    or not _same_decimal(duplicate[1], quantity)
                    or not _same_decimal(duplicate[2], price)
                    or not _same_decimal(duplicate[3], fee)
                    or duplicate[4] != timestamp.isoformat()
                    or duplicate[5] != timestamp.isoformat()
                ):
                    raise DuplicateFill(fill_id)
                return current
            attempted = OrderState.FILLED
            if current.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                raise InvalidOrderTransition(
                    f"invalid order transition: {current.state.value} -> {attempted.value}"
                )
            if timestamp < current.occurred_at:
                raise ValueError("occurred_at is before current order state")
            if (
                current.state is OrderState.PARTIALLY_FILLED
                and timestamp <= current.occurred_at
            ):
                raise ValueError("fill occurred_at must be later than prior partial fill")
            if current.side is Side.BUY and price > current.limit_price:
                raise ValueError("buy fill price exceeds limit")
            if current.side is Side.SELL and price < current.limit_price:
                raise ValueError("sell fill price is below limit")
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                new_filled = current.filled_quantity + quantity
            if new_filled > current.quantity:
                raise ValueError("fill quantity exceeds order quantity")
            target = (
                OrderState.FILLED
                if new_filled == current.quantity
                else OrderState.PARTIALLY_FILLED
            )
            self._require_transition(current.state, target)
            currency_row = connection.execute(
                "SELECT currency FROM strategy_books WHERE book_id = ?",
                (current.book_id,),
            ).fetchone()
            if currency_row is None:
                raise KeyError(current.book_id)
            currency = currency_row[0]
            cash_rows = connection.execute(
                "SELECT amount FROM cash_ledger WHERE book_id = ? AND currency = ?",
                (current.book_id, currency),
            ).fetchall()
            with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                cash = sum((Decimal(row[0]) for row in cash_rows), Decimal("0"))
            position_row = connection.execute(
                "SELECT quantity, average_cost FROM positions "
                "WHERE book_id = ? AND security_id = ? ORDER BY rowid DESC LIMIT 1",
                (current.book_id, current.security_id),
            ).fetchone()
            old_quantity = Decimal(position_row[0]) if position_row else Decimal("0")
            old_average = Decimal(position_row[1]) if position_row else Decimal("0")
            if current.side is Side.BUY:
                with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                    cash_change = -(quantity * price + fee)
                    new_quantity = old_quantity + quantity
                    new_average = (
                        old_quantity * old_average + quantity * price + fee
                    ) / new_quantity
                    if cash + cash_change < 0:
                        raise InsufficientCash(current.book_id)
                entry_kind = "BUY_FILL"
            else:
                if old_quantity < quantity:
                    raise InsufficientPosition(current.security_id)
                with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
                    cash_change = quantity * price - fee
                    new_quantity = old_quantity - quantity
                new_average = old_average if new_quantity else Decimal("0")
                entry_kind = "SELL_FILL"
            event = OrderEvent(
                order_id=current.order_id,
                book_id=current.book_id,
                proposal_id=current.proposal_id,
                security_id=current.security_id,
                side=current.side,
                quantity=current.quantity,
                limit_price=current.limit_price,
                state=target,
                occurred_at=timestamp,
                event_id=f"{order_id}:event:{len(history)}",
                filled_quantity=new_filled,
            )
            connection.execute(
                "INSERT INTO fills "
                "(fill_id, order_id, quantity, price, fee, occurred_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    fill_id,
                    order_id,
                    _decimal_text(quantity),
                    _decimal_text(price),
                    _decimal_text(fee),
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO cash_ledger "
                "(cash_entry_id, book_id, currency, amount, entry_kind, "
                "effective_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{fill_id}:cash",
                    current.book_id,
                    currency,
                    _decimal_text(cash_change),
                    entry_kind,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO positions "
                "(position_snapshot_id, book_id, security_id, quantity, "
                "average_cost, as_of_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{fill_id}:position",
                    current.book_id,
                    current.security_id,
                    _decimal_text(new_quantity),
                    _decimal_text(new_average),
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                ),
            )
            self._insert_event(connection, event)
            return event

    @staticmethod
    def _require_transition(current: OrderState, target: OrderState) -> None:
        if target not in _ALLOWED[current]:
            raise InvalidOrderTransition(
                f"invalid order transition: {current.value} -> {target.value}"
            )

    @staticmethod
    def _insert_event(connection: sqlite3.Connection, event: OrderEvent) -> None:
        connection.execute(
            "INSERT INTO paper_orders "
            "(order_id, book_id, proposal_id, security_id, order_state, "
            "payload_json, occurred_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.book_id,
                event.proposal_id,
                event.security_id,
                event.state.value,
                _payload(event),
                event.occurred_at.isoformat(),
                event.occurred_at.isoformat(),
            ),
        )

    @staticmethod
    def _history(
        connection: sqlite3.Connection, order_id: str
    ) -> tuple[OrderEvent, ...]:
        rows = connection.execute(
            "SELECT order_id, book_id, proposal_id, security_id, order_state, "
            "payload_json, occurred_at FROM paper_orders "
            "WHERE json_extract(payload_json, '$.logical_order_id') = ? "
            "ORDER BY rowid",
            (order_id,),
        ).fetchall()
        return tuple(_event_from_row(row) for row in rows)
