"""Durable M0.5/M1.1 entry intents; not a broker or runtime risk adapter.

Both lanes must use the SAME database and execution lock on a local filesystem.
Hold execution_mutex across fresh reconciliation, reservation and submission;
network I/O must never occur in the reserve eligibility callback. Persist
SUBMITTED_UNKNOWN before contacting the broker. A duplicate intent is NOT a
submission authorization. Never expire reservations by age, ACK or absence from
an open-order response. Only exact confirmed fills/cancellation reduce pending.
Filled exposure must simultaneously be represented in the caller's positions.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from math import isfinite
from pathlib import Path
import sqlite3
from typing import Callable, Iterator

from core.portfolio_risk import PendingEntry, ProposedEntry


STATES = ("RESERVED", "SUBMITTED_UNKNOWN", "ACK", "PARTIAL", "FILLED",
          "CANCELLED_CONFIRMED")
TERMINAL = ("FILLED", "CANCELLED_CONFIRMED")


class LockBusy(RuntimeError):
    """Another execution owns the shared same-host critical section."""


@contextmanager
def execution_mutex(path: str | Path) -> Iterator[None]:
    """Nonblocking OS lock, released on process death; never unlink this file.

    All writers must share this path. Not a distributed/network-filesystem lock.
    A busy lock skips this execution rather than queuing an obsolete snapshot.
    """
    with open(path, "a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy("lock_busy") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _number(value: float, name: str, *, zero: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not isfinite(value) or value < 0 or (value == 0 and not zero)):
        raise ValueError(name)


def _identity(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(name)


@dataclass(frozen=True)
class Reservation:
    intent_id: str
    order_link_id: str
    lane: str
    side: str
    requested_qty: float
    remaining_qty: float
    price_bound: float
    stop: float
    min_fill_price: float | None
    state: str
    confirmed_filled_qty: float
    order_id: str | None

    def pending_entry(self) -> PendingEntry:
        return PendingEntry(self.lane, self.side, self.remaining_qty,
                            self.price_bound, self.stop, self.min_fill_price)


@dataclass(frozen=True)
class ReserveResult:
    reservation: Reservation
    created: bool


class EntryReservationStore:
    """Short-lived, FULL-synchronous SQLite transactions with immutable intent IDs.

    Eligibility is a pure callback over all currently active pending entries;
    return literal True to authorize, False to reject, or raise to roll back.
    The callback must include coherent actual exposure and the proposed entry in
    its calculation (e.g. evaluate_portfolio_entry(...).allowed).
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("durable_database_required")
        with self._connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS entry_reservations (
                intent_id TEXT PRIMARY KEY, order_link_id TEXT NOT NULL UNIQUE,
                lane TEXT NOT NULL, side TEXT NOT NULL,
                requested_qty REAL NOT NULL, remaining_qty REAL NOT NULL,
                price_bound REAL NOT NULL, stop REAL NOT NULL,
                min_fill_price REAL, state TEXT NOT NULL,
                confirmed_filled_qty REAL NOT NULL, order_id TEXT UNIQUE
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS entry_reservation_evidence (
                evidence_ref TEXT PRIMARY KEY, intent_id TEXT NOT NULL,
                state TEXT NOT NULL, confirmed_filled_qty REAL NOT NULL,
                order_id TEXT
            )""")

    @contextmanager
    def _connection(self, external=None) -> Iterator[sqlite3.Connection]:
        if external is not None:
            if not external.in_transaction:
                raise ValueError("receipt_transaction_required")
            actual = next((row[2] for row in external.execute("PRAGMA database_list") if row[1] == "main"), "")
            if not actual or Path(actual).resolve() != Path(self.path).resolve():
                raise ValueError("receipt_database_mismatch")
            yield external
            return
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _get(conn: sqlite3.Connection, intent_id: str) -> Reservation | None:
        row = conn.execute("SELECT * FROM entry_reservations WHERE intent_id=?",
                           (intent_id,)).fetchone()
        return Reservation(**dict(row)) if row else None

    @staticmethod
    def _active(conn: sqlite3.Connection) -> tuple[Reservation, ...]:
        rows = conn.execute("SELECT * FROM entry_reservations WHERE state NOT IN "
                            "('FILLED','CANCELLED_CONFIRMED') ORDER BY intent_id")
        return tuple(Reservation(**dict(row)) for row in rows)

    def get(self, intent_id: str) -> Reservation | None:
        with self._connection() as conn:
            return self._get(conn, intent_id)

    def active(self) -> tuple[Reservation, ...]:
        with self._connection() as conn:
            return self._active(conn)

    def reserve(self, *, intent_id: str, order_link_id: str,
                proposed: ProposedEntry,
                eligibility: Callable[[tuple[PendingEntry, ...]], bool]) -> ReserveResult:
        _identity(intent_id, "intent_id")
        _identity(order_link_id, "order_link_id")
        if not isinstance(proposed, ProposedEntry):
            raise ValueError("proposed")
        if proposed.lane not in ("main", "swing") or proposed.side not in ("long", "short"):
            raise ValueError("lane_or_side")
        for name in ("qty", "price_bound", "stop"):
            _number(getattr(proposed, name), name)
        if proposed.min_fill_price is not None:
            _number(proposed.min_fill_price, "min_fill_price")
            if proposed.min_fill_price > proposed.price_bound:
                raise ValueError("price_bound_order")
        if proposed.side == "short":
            _number(proposed.min_fill_price, "short_min_fill_price")
            if proposed.stop < proposed.min_fill_price:
                raise ValueError("entry_stop_direction")
        elif proposed.stop > proposed.price_bound:
            raise ValueError("entry_stop_direction")
        new = Reservation(intent_id, order_link_id, proposed.lane, proposed.side,
                          proposed.qty, proposed.qty, proposed.price_bound,
                          proposed.stop, proposed.min_fill_price, "RESERVED", 0., None)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._get(conn, intent_id)
            if existing is not None:
                immutable = ("order_link_id", "lane", "side", "requested_qty",
                             "price_bound", "stop", "min_fill_price")
                if any(getattr(existing, key) != getattr(new, key) for key in immutable):
                    raise ValueError("conflicting_intent")
                return ReserveResult(existing, False)
            pending = tuple(row.pending_entry() for row in self._active(conn))
            if eligibility(pending) is not True:
                raise ValueError("entry_ineligible")
            conn.execute("INSERT INTO entry_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(vars(new).values()))
            return ReserveResult(new, True)

    def update(self, *, intent_id: str, order_link_id: str, state: str,
               confirmed_filled_qty: float, order_id: str | None = None,
               evidence_ref: str | None = None, connection=None) -> Reservation:
        """Apply an exact-ID, authoritative cumulative confirmation, never a guess.

        CANCELLED_CONFIRMED requires final cumulative fill evidence from the
        caller, not cancellation ACK. A timeout is SUBMITTED_UNKNOWN, not cancel.
        Terminal rows remain durable tombstones preventing intent reuse.
        """
        _identity(intent_id, "intent_id")
        _identity(order_link_id, "order_link_id")
        if order_id is not None:
            _identity(order_id, "order_id")
        if evidence_ref is not None:
            _identity(evidence_ref, "evidence_ref")
        _number(confirmed_filled_qty, "confirmed_filled_qty", zero=True)
        if state not in STATES:
            raise ValueError("state")
        with self._connection(connection) as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            old = self._get(conn, intent_id)
            if old is None or old.order_link_id != order_link_id:
                raise ValueError("unknown_order_identity")
            if old.order_id is not None and order_id not in (None, old.order_id):
                raise ValueError("conflicting_order_id")
            if (confirmed_filled_qty > old.confirmed_filled_qty or state in TERMINAL
                    ) and evidence_ref is None:
                raise ValueError("confirmation_evidence_required")
            if evidence_ref is not None:
                evidence = conn.execute(
                    "SELECT * FROM entry_reservation_evidence WHERE evidence_ref=?",
                    (evidence_ref,)).fetchone()
                expected = (evidence_ref, intent_id, state, confirmed_filled_qty,
                            order_id or old.order_id)
                if evidence is not None and tuple(evidence) != expected:
                    raise ValueError("conflicting_evidence")
            if not old.confirmed_filled_qty <= confirmed_filled_qty <= old.requested_qty:
                raise ValueError("nonmonotonic_or_overfill")
            if old.state in TERMINAL and (state != old.state
                                          or confirmed_filled_qty != old.confirmed_filled_qty):
                raise ValueError("conflicting_terminal_update")
            if old.state not in TERMINAL and STATES.index(state) < STATES.index(old.state):
                raise ValueError("out_of_order_state")
            if state in ("RESERVED", "SUBMITTED_UNKNOWN", "ACK") and confirmed_filled_qty != 0:
                raise ValueError("unconfirmed_state_with_fill")
            if state == "PARTIAL" and not 0 < confirmed_filled_qty < old.requested_qty:
                raise ValueError("invalid_partial")
            if state == "FILLED" and confirmed_filled_qty != old.requested_qty:
                raise ValueError("invalid_filled")
            remaining = 0. if state in TERMINAL else old.requested_qty - confirmed_filled_qty
            conn.execute("UPDATE entry_reservations SET state=?, confirmed_filled_qty=?, "
                         "remaining_qty=?, order_id=? WHERE intent_id=?",
                         (state, confirmed_filled_qty, remaining,
                          order_id or old.order_id, intent_id))
            if evidence_ref is not None:
                conn.execute("INSERT OR IGNORE INTO entry_reservation_evidence "
                             "VALUES (?,?,?,?,?)", expected)
            result = self._get(conn, intent_id)
            if result is None:
                raise RuntimeError("reservation_missing_after_update")
            return result
