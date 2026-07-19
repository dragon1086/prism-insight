"""Additive shadow position ledger for issue #412 Phase 4-a.

Every operation uses a caller-supplied SQLite connection or cursor.  This module
never commits: the legacy write and its shadow write therefore remain under the
caller's transaction boundary.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


_POSITION_STATUSES = (
    "PENDING_ENTRY",
    "OPEN",
    "ENTRY_FAILED",
    "PENDING_EXIT",
    "CLOSED",
    "EXIT_UNKNOWN",
)

_ALLOWED_TRANSITIONS = {
    "PENDING_ENTRY": frozenset({"OPEN", "ENTRY_FAILED"}),
    "OPEN": frozenset({"PENDING_EXIT", "CLOSED"}),
    "ENTRY_FAILED": frozenset(),
    "PENDING_EXIT": frozenset({"OPEN", "CLOSED", "EXIT_UNKNOWN"}),
    "CLOSED": frozenset(),
    "EXIT_UNKNOWN": frozenset({"OPEN", "CLOSED"}),
}

_POSITIONS_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    legacy_holding_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    account_name TEXT,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN {repr(_POSITION_STATUSES)}),
    execution_mode TEXT NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    entry_intent_id TEXT,
    exit_intent_id TEXT,
    entry_price REAL,
    exit_price REAL,
    realized_pnl_pct REAL,
    exit_kind TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (market, legacy_holding_id)
)
"""

_MIRROR_ERRORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_mirror_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    legacy_holding_id TEXT,
    account_ref TEXT,
    operation TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    created_at TEXT NOT NULL,
    resolved_at TEXT
)
"""

_LEGACY_TABLES = {"KR": "stock_holdings", "US": "us_stock_holdings"}
_POSITION_LINK_BUSY_TIMEOUT_MS = 50
_SQLITE_LOCK_PRIMARY_CODES = frozenset({5, 6})  # SQLITE_BUSY, SQLITE_LOCKED


class InvalidPositionTransition(ValueError):
    """Raised when a position lifecycle transition is not allowed."""


@dataclass(frozen=True)
class LegacyPositionWriteResult:
    """Internal result boundary preserving the public simulator bool contract."""

    success: bool
    legacy_holding_id: int | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _market(value: str) -> str:
    market = str(value).upper()
    if market not in _LEGACY_TABLES:
        raise ValueError(f"unsupported market: {market}")
    return market


def account_fingerprint(account_id: Any) -> str:
    """Return a stable, non-reversible account reference for safe output."""

    value = str(account_id or "")
    if not value:
        raise ValueError("account_id is required")
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def legacy_position_id(market: str, legacy_holding_id: Any) -> str:
    """Return the deterministic id for one legacy holding row."""

    normalized_market = _market(market)
    if legacy_holding_id is None or str(legacy_holding_id) == "":
        raise ValueError("legacy_holding_id is required")
    return f"legacy:{normalized_market}:{legacy_holding_id}"


def _entry_fingerprint(entry_price: Any, opened_at: Any) -> str:
    value = f"{entry_price}|{opened_at}"
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]


def _age_seconds(value: Any) -> int | None:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - timestamp).total_seconds()))
    except (TypeError, ValueError):
        return None


def _redact_error_text(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?i)(api[_-]?key|app[_-]?key|app[_-]?secret|token|password)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )


def _is_sqlite_lock_error(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    error_code = getattr(error, "sqlite_errorcode", None)
    if (
        isinstance(error_code, int)
        and error_code & 0xFF in _SQLITE_LOCK_PRIMARY_CODES
    ):
        return True
    message = str(error).strip().lower()
    return any(
        lock_text in message
        for lock_text in (
            "database is locked",
            "database table is locked",
            "database schema is locked",
        )
    )


class PositionStore:
    """Transaction-neutral operations for the shadow position ledger.

    ``connection_or_cursor`` is owned by the caller.  None of these methods call
    ``commit`` or ``rollback``, including schema creation and backfill.
    """

    def __init__(
        self, connection_or_cursor: sqlite3.Connection | sqlite3.Cursor
    ) -> None:
        if not isinstance(connection_or_cursor, (sqlite3.Connection, sqlite3.Cursor)):
            raise TypeError("PositionStore requires a sqlite3 Connection or Cursor")
        self._db = connection_or_cursor

    def _execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, parameters)

    def _require_active_transaction(self) -> None:
        connection = (
            self._db
            if isinstance(self._db, sqlite3.Connection)
            else self._db.connection
        )
        if not connection.in_transaction:
            raise RuntimeError(
                "position lifecycle requires an active caller-owned transaction"
            )

    def _fetchall(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        cursor = self._execute(sql, parameters)
        columns = [item[0] for item in cursor.description or ()]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def ensure_schema(self) -> None:
        """Create additive ledger tables without committing the transaction."""

        self._execute(_POSITIONS_SCHEMA)
        self._execute(_MIRROR_ERRORS_SCHEMA)
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_state "
            "ON positions(market, execution_mode, status)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_position_mirror_errors_open "
            "ON position_mirror_errors(market, resolved, created_at)"
        )

    def open_legacy_position(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        account_name: str | None,
        symbol: Any,
        entry_price: Any = None,
        opened_at: str | None = None,
    ) -> bool:
        """Insert one OPEN legacy mirror, returning false when it already exists."""

        market = _market(market)
        position_id = legacy_position_id(market, legacy_holding_id)
        account_id = str(account_id or "")
        if not account_id:
            raise ValueError("account_id is required")
        symbol = str(symbol or "").upper()
        if not symbol:
            raise ValueError("symbol is required")
        now = _utc_now()
        changed = self._execute(
            """
            INSERT INTO positions (
                id, market, legacy_holding_id, account_id, account_name, symbol,
                status, execution_mode, opened_at, entry_price, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 'legacy', ?, ?, ?, ?)
            ON CONFLICT(market, legacy_holding_id) DO NOTHING
            """,
            (
                position_id,
                market,
                str(legacy_holding_id),
                account_id,
                account_name,
                symbol,
                opened_at,
                entry_price,
                now,
                now,
            ),
        ).rowcount
        return changed == 1

    def assert_entry_attempt_allowed(
        self,
        *,
        market: str,
        account_id: Any,
        symbol: Any,
        allow_existing_open: bool = False,
        expected_open_count: int | None = None,
    ) -> bool:
        """Reject duplicate or stale entry attempts for the same identity."""

        self._require_active_transaction()
        market = _market(str(market).strip())
        account_id = str(account_id or "").strip()
        symbol = str(symbol or "").strip().upper()
        if not account_id or not symbol:
            raise ValueError("account_id and symbol are required")
        row = self._execute(
            "SELECT status FROM positions "
            "WHERE market=? AND account_id=? AND symbol=? "
            "AND status IN ('PENDING_ENTRY', 'ENTRY_FAILED', "
            "'PENDING_EXIT', 'EXIT_UNKNOWN') LIMIT 1",
            (market, account_id, symbol),
        ).fetchone()
        if row is not None:
            raise InvalidPositionTransition(
                f"entry attempt blocked by unresolved position status: {row[0]}"
            )
        open_count = int(
            self._execute(
                "SELECT COUNT(*) FROM positions "
                "WHERE market=? AND account_id=? AND symbol=? AND status='OPEN'",
                (market, account_id, symbol),
            ).fetchone()[0]
        )
        if not allow_existing_open and open_count:
            raise InvalidPositionTransition(
                "entry attempt blocked by existing OPEN position"
            )
        if allow_existing_open:
            if not isinstance(expected_open_count, int) or expected_open_count < 0:
                raise ValueError(
                    "expected_open_count is required for an additional entry"
                )
            if open_count != expected_open_count:
                raise InvalidPositionTransition(
                    "entry attempt blocked by changed OPEN position count: "
                    f"expected {expected_open_count}, found {open_count}"
                )
        return True

    def assert_exit_attempt_allowed(
        self,
        *,
        market: str,
        account_id: Any,
        symbol: Any,
    ) -> bool:
        """Reject exits while the same position identity has unresolved state."""

        self._require_active_transaction()
        market = _market(str(market).strip())
        account_id = str(account_id or "").strip()
        symbol = str(symbol or "").strip().upper()
        if not account_id or not symbol:
            raise ValueError("account_id and symbol are required")
        row = self._execute(
            "SELECT status, exit_intent_id FROM positions "
            "WHERE market=? AND account_id=? AND symbol=? "
            "AND (status IN ('PENDING_ENTRY', 'ENTRY_FAILED', "
            "'PENDING_EXIT', 'EXIT_UNKNOWN') "
            "OR (status='OPEN' AND exit_intent_id IS NOT NULL)) LIMIT 1",
            (market, account_id, symbol),
        ).fetchone()
        if row is not None:
            status, exit_intent_id = row
            detail = status
            if status == "OPEN" and exit_intent_id:
                detail = f"OPEN position linked to exit intent {exit_intent_id}"
            raise InvalidPositionTransition(
                f"exit attempt blocked by unresolved position state: {detail}"
            )
        return True

    @staticmethod
    def _source_position_ids(value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        items = tuple(item.strip() for item in str(value).split(","))
        if not items or any(not item for item in items) or len(set(items)) != len(items):
            raise ValueError("intent source_position_id is invalid")
        return items

    def _validated_intent(
        self,
        *,
        intent_id: Any,
        market: str,
        account_id: str,
        symbol: str,
        side: str,
        position_ids: Iterable[str],
    ) -> dict[str, Any]:
        intent_id = str(intent_id or "")
        if not intent_id:
            raise ValueError("intent_id is required")
        cursor = self._execute(
            "SELECT id, market, account_id, symbol, side, source_position_id, status "
            "FROM order_intents WHERE id=?",
            (intent_id,),
        )
        columns = [item[0] for item in cursor.description or ()]
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"intent not found: {intent_id}")
        intent = dict(zip(columns, row))
        expected_ids = tuple(position_ids)
        if (
            str(intent["market"]).upper() != market
            or str(intent["account_id"]) != account_id
            or str(intent["symbol"]).upper() != symbol
            or str(intent["side"]).upper() != side
            or set(self._source_position_ids(intent["source_position_id"]))
            != set(expected_ids)
        ):
            raise ValueError("intent identity does not match source positions")
        if str(intent["status"]).upper() not in {
            "CREATED",
            "SUBMITTING",
            "SUBMITTED",
            "QUEUED",
            "FAILED",
            "UNKNOWN",
        }:
            raise ValueError("intent status is not valid for position lifecycle")
        return intent

    def prepare_entry(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        account_name: str | None,
        symbol: Any,
        intent_id: Any,
        entry_price: Any = None,
        opened_at: str | None = None,
    ) -> bool:
        """Create one PENDING_ENTRY linked to a persisted matching BUY intent."""

        self._require_active_transaction()
        market = _market(market)
        position_id = legacy_position_id(market, legacy_holding_id)
        account_id = str(account_id or "")
        symbol = str(symbol or "").upper()
        if not account_id or not symbol:
            raise ValueError("account_id and symbol are required")
        intent_id = str(intent_id or "")
        intent = self._validated_intent(
            intent_id=intent_id,
            market=market,
            account_id=account_id,
            symbol=symbol,
            side="BUY",
            position_ids=(position_id,),
        )
        if str(intent["status"]).upper() != "CREATED":
            raise InvalidPositionTransition("entry prepare requires CREATED intent")
        row = self._execute(
            "SELECT market, legacy_holding_id, account_id, symbol, status, "
            "entry_intent_id FROM positions WHERE id=?",
            (position_id,),
        ).fetchone()
        if row is not None:
            identity = (market, str(legacy_holding_id), account_id, symbol)
            if tuple(str(value).upper() if index in {0, 3} else str(value)
                     for index, value in enumerate(row[:4])) != identity:
                raise ValueError("entry position identity mismatch")
            if row[5] != intent_id:
                raise ValueError("entry intent overwrite is not allowed")
            if str(row[4]) in {"PENDING_ENTRY", "OPEN", "ENTRY_FAILED"}:
                return True
            raise InvalidPositionTransition(
                f"entry prepare requires PENDING_ENTRY, found {row[4]}"
            )
        now = _utc_now()
        self._execute(
            """
            INSERT INTO positions (
                id, market, legacy_holding_id, account_id, account_name, symbol,
                status, execution_mode, opened_at, entry_intent_id, entry_price,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_ENTRY', 'legacy', ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                market,
                str(legacy_holding_id),
                account_id,
                account_name,
                symbol,
                opened_at,
                intent_id,
                entry_price,
                now,
                now,
            ),
        )
        return True

    def _finish_entry(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        symbol: Any,
        intent_id: Any,
        to_status: str,
        required_intent_status: str,
    ) -> bool:
        self._require_active_transaction()
        market = _market(market)
        position_id = legacy_position_id(market, legacy_holding_id)
        account_id = str(account_id or "")
        symbol = str(symbol or "").upper()
        intent_id = str(intent_id or "")
        if not account_id or not symbol:
            raise ValueError("account_id and symbol are required")
        intent = self._validated_intent(
            intent_id=intent_id,
            market=market,
            account_id=account_id,
            symbol=symbol,
            side="BUY",
            position_ids=(position_id,),
        )
        if str(intent["status"]).upper() != required_intent_status:
            raise InvalidPositionTransition(
                f"entry {to_status} requires {required_intent_status} intent"
            )
        row = self._execute(
            "SELECT status, entry_intent_id, symbol FROM positions "
            "WHERE id=? AND market=? AND account_id=?",
            (position_id, market, account_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"entry position not found: {position_id}")
        if row[1] != intent_id:
            raise ValueError("entry intent does not match position")
        if str(row[2]).upper() != symbol:
            raise ValueError("entry symbol does not match position")
        if str(row[0]) == to_status:
            return True
        if str(row[0]) != "PENDING_ENTRY":
            raise InvalidPositionTransition(
                f"entry finalize requires PENDING_ENTRY, found {row[0]}"
            )
        changed = self._execute(
            "UPDATE positions SET status=?, updated_at=? "
            "WHERE id=? AND market=? AND account_id=? AND status='PENDING_ENTRY' "
            "AND entry_intent_id=?",
            (to_status, _utc_now(), position_id, market, account_id, intent_id),
        ).rowcount
        if changed != 1:
            raise RuntimeError(f"position {position_id} changed concurrently")
        return True

    def complete_entry(self, **identity: Any) -> bool:
        """Transition one claimed entry to OPEN without committing."""

        return self._finish_entry(
            to_status="OPEN", required_intent_status="SUBMITTED", **identity
        )

    def fail_entry(self, **identity: Any) -> bool:
        """Transition one claimed entry to ENTRY_FAILED without committing."""

        return self._finish_entry(
            to_status="ENTRY_FAILED", required_intent_status="FAILED", **identity
        )

    def _validate_exit_positions(
        self,
        *,
        market: str,
        account_id: str,
        symbol: str,
        position_ids: tuple[str, ...],
        intent_id: str,
        allowed_statuses: frozenset[str],
        allow_unlinked: bool,
    ) -> list[dict[str, Any]]:
        if not position_ids or len(set(position_ids)) != len(position_ids):
            raise ValueError("position_ids must be non-empty and unique")
        if any(not item.startswith(f"legacy:{market}:") for item in position_ids):
            raise ValueError("position_ids must be canonical for market")
        if market != "US" and len(position_ids) != 1:
            raise ValueError("multiple exit positions are only supported for US")
        intent = self._validated_intent(
            intent_id=intent_id,
            market=market,
            account_id=account_id,
            symbol=symbol,
            side="SELL",
            position_ids=position_ids,
        )
        if allow_unlinked and str(intent["status"]).upper() != "CREATED":
            raise InvalidPositionTransition("exit prepare requires CREATED intent")
        rows = [
            row
            for row in self._fetchall(
                "SELECT id, legacy_holding_id, account_id, symbol, status, "
                "exit_intent_id FROM positions WHERE market=? AND account_id=?",
                (market, account_id),
            )
            if str(row["id"]) in position_ids
        ]
        if {str(row["id"]) for row in rows} != set(position_ids):
            raise LookupError("exit source positions not found")
        for row in rows:
            if (
                str(row["id"])
                != legacy_position_id(market, row["legacy_holding_id"])
                or str(row["account_id"]) != account_id
                or str(row["symbol"]).upper() != symbol
            ):
                raise ValueError("exit position identity mismatch")
            if str(row["status"]) not in allowed_statuses:
                raise InvalidPositionTransition(
                    f"exit lifecycle does not allow status {row['status']}"
                )
            linked = row["exit_intent_id"]
            if linked != intent_id and not (allow_unlinked and linked is None):
                raise ValueError("exit intent overwrite is not allowed")
        return rows

    def _update_exit_many(
        self,
        *,
        market: str,
        account_id: str,
        position_ids: tuple[str, ...],
        intent_id: str,
        from_status: str,
        to_status: str,
        exit_price: Any = None,
        realized_pnl_pct: Any = None,
        exit_kind: str | None = None,
        closed_at: str | None = None,
    ) -> None:
        self._require_active_transaction()
        now = _utc_now()
        if to_status == "CLOSED" and closed_at is None:
            closed_at = now
        self._execute("SAVEPOINT position_exit_many")
        try:
            changed = sum(
                self._execute(
                    """
                    UPDATE positions SET status=?, exit_intent_id=?,
                        exit_price=COALESCE(?, exit_price),
                        realized_pnl_pct=COALESCE(?, realized_pnl_pct),
                        exit_kind=COALESCE(?, exit_kind),
                        closed_at=CASE WHEN ?='CLOSED' THEN ? ELSE closed_at END,
                        updated_at=?
                    WHERE id=? AND market=? AND account_id=? AND status=?
                      AND (exit_intent_id IS NULL OR exit_intent_id=?)
                    """,
                    (
                        to_status,
                        intent_id,
                        exit_price,
                        realized_pnl_pct,
                        exit_kind,
                        to_status,
                        closed_at,
                        now,
                        position_id,
                        market,
                        account_id,
                        from_status,
                        intent_id,
                    ),
                ).rowcount
                for position_id in sorted(position_ids)
            )
            if changed != len(position_ids):
                raise RuntimeError("exit source positions changed concurrently")
        except Exception:
            self._execute("ROLLBACK TO position_exit_many")
            self._execute("RELEASE position_exit_many")
            raise
        self._execute("RELEASE position_exit_many")

    def prepare_exit_many(
        self,
        *,
        market: str,
        account_id: Any,
        symbol: Any,
        position_ids: Iterable[str],
        intent_id: Any,
    ) -> bool:
        """Atomically claim OPEN source positions for one persisted SELL intent."""

        market = _market(market)
        account_id = str(account_id or "")
        symbol = str(symbol or "").upper()
        intent_id = str(intent_id or "")
        source_ids = tuple(str(item) for item in position_ids)
        rows = self._validate_exit_positions(
            market=market,
            account_id=account_id,
            symbol=symbol,
            position_ids=source_ids,
            intent_id=intent_id,
            allowed_statuses=frozenset({"OPEN", "PENDING_EXIT"}),
            allow_unlinked=True,
        )
        statuses = {str(row["status"]) for row in rows}
        links = {row["exit_intent_id"] for row in rows}
        if statuses == {"PENDING_EXIT"} and links == {intent_id}:
            return True
        if statuses != {"OPEN"} or links != {None}:
            raise InvalidPositionTransition("exit positions are not claimable together")
        self._update_exit_many(
            market=market,
            account_id=account_id,
            position_ids=source_ids,
            intent_id=intent_id,
            from_status="OPEN",
            to_status="PENDING_EXIT",
        )
        return True

    def _finish_exit_many(
        self,
        *,
        market: str,
        account_id: Any,
        symbol: Any,
        position_ids: Iterable[str],
        intent_id: Any,
        to_status: str,
        required_intent_status: str | frozenset[str],
        exit_price: Any = None,
        realized_pnl_pct: Any = None,
        exit_kind: str | None = None,
        closed_at: str | None = None,
    ) -> bool:
        market = _market(market)
        account_id = str(account_id or "")
        symbol = str(symbol or "").upper()
        intent_id = str(intent_id or "")
        source_ids = tuple(str(item) for item in position_ids)
        intent = self._validated_intent(
            intent_id=intent_id,
            market=market,
            account_id=account_id,
            symbol=symbol,
            side="SELL",
            position_ids=source_ids,
        )
        required_statuses = (
            frozenset({required_intent_status})
            if isinstance(required_intent_status, str)
            else required_intent_status
        )
        if str(intent["status"]).upper() not in required_statuses:
            expected = ", ".join(sorted(required_statuses))
            raise InvalidPositionTransition(
                f"exit {to_status} requires one of {expected} intent statuses"
            )
        rows = self._validate_exit_positions(
            market=market,
            account_id=account_id,
            symbol=symbol,
            position_ids=source_ids,
            intent_id=intent_id,
            allowed_statuses=frozenset({"PENDING_EXIT", to_status}),
            allow_unlinked=False,
        )
        statuses = {str(row["status"]) for row in rows}
        if statuses == {to_status}:
            return True
        if statuses != {"PENDING_EXIT"}:
            raise InvalidPositionTransition("exit positions are not finalizable together")
        self._update_exit_many(
            market=market,
            account_id=account_id,
            position_ids=source_ids,
            intent_id=intent_id,
            from_status="PENDING_EXIT",
            to_status=to_status,
            exit_price=exit_price,
            realized_pnl_pct=realized_pnl_pct,
            exit_kind=exit_kind,
            closed_at=closed_at,
        )
        return True

    def complete_exit_many(self, **values: Any) -> bool:
        """Atomically finalize claimed exits as CLOSED."""

        return self._finish_exit_many(
            to_status="CLOSED", required_intent_status="SUBMITTED", **values
        )

    def fail_exit_many(self, **values: Any) -> bool:
        """Atomically return explicitly failed exits to OPEN."""

        return self._finish_exit_many(
            to_status="OPEN", required_intent_status="FAILED", **values
        )

    def quarantine_pending_exit_many(self, **values: Any) -> bool:
        """Quarantine claimed exits that cannot be finalized with certainty."""

        return self._finish_exit_many(
            to_status="EXIT_UNKNOWN",
            required_intent_status=frozenset(
                {"SUBMITTING", "SUBMITTED", "UNKNOWN", "QUEUED"}
            ),
            **values,
        )

    def mark_exit_unknown_many(self, **values: Any) -> bool:
        """Compatibility wrapper for exit quarantine."""

        return self.quarantine_pending_exit_many(**values)

    def transition(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        to_status: str,
        entry_intent_id: str | None = None,
        exit_intent_id: str | None = None,
        exit_price: Any = None,
        realized_pnl_pct: Any = None,
        exit_kind: str | None = None,
        closed_at: str | None = None,
    ) -> bool:
        """Apply one validated transition matched by market, legacy id, and account."""

        market = _market(market)
        to_status = str(to_status).upper()
        if to_status not in _POSITION_STATUSES:
            raise InvalidPositionTransition(f"unknown position status: {to_status}")
        legacy_holding_id = str(legacy_holding_id)
        account_id = str(account_id or "")
        if not account_id:
            raise ValueError("account_id is required")
        position_id = legacy_position_id(market, legacy_holding_id)
        row = self._execute(
            "SELECT status FROM positions WHERE id=? AND market=? "
            "AND legacy_holding_id=? AND account_id=?",
            (position_id, market, legacy_holding_id, account_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                f"position not found for {market}/{legacy_holding_id}/account"
            )
        current_status = str(row[0])
        if to_status not in _ALLOWED_TRANSITIONS[current_status]:
            raise InvalidPositionTransition(
                f"transition {current_status} -> {to_status} is not allowed"
            )

        now = _utc_now()
        if to_status == "CLOSED" and closed_at is None:
            closed_at = now
        changed = self._execute(
            """
            UPDATE positions
            SET status=?, entry_intent_id=COALESCE(?, entry_intent_id),
                exit_intent_id=COALESCE(?, exit_intent_id),
                exit_price=COALESCE(?, exit_price),
                realized_pnl_pct=COALESCE(?, realized_pnl_pct),
                exit_kind=COALESCE(?, exit_kind),
                closed_at=CASE WHEN ?='CLOSED' THEN ? ELSE closed_at END,
                updated_at=?
            WHERE id=? AND market=? AND legacy_holding_id=? AND account_id=?
              AND status=?
            """,
            (
                to_status,
                entry_intent_id,
                exit_intent_id,
                exit_price,
                realized_pnl_pct,
                exit_kind,
                to_status,
                closed_at,
                now,
                position_id,
                market,
                legacy_holding_id,
                account_id,
                current_status,
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError(f"position {position_id} changed concurrently")
        return True

    def close_legacy_position(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        exit_intent_id: str | None = None,
        exit_price: Any = None,
        realized_pnl_pct: Any = None,
        exit_kind: str | None = None,
        closed_at: str | None = None,
    ) -> bool:
        """Close exactly one legacy mirror without committing."""

        return self.transition(
            market=market,
            legacy_holding_id=legacy_holding_id,
            account_id=account_id,
            to_status="CLOSED",
            exit_intent_id=exit_intent_id,
            exit_price=exit_price,
            realized_pnl_pct=realized_pnl_pct,
            exit_kind=exit_kind,
            closed_at=closed_at,
        )

    def _link_intent(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        intent_id: Any,
        link_kind: str,
        expected_position_ids: Iterable[str] | None = None,
    ) -> bool:
        """Validate and persist one intent link without owning the transaction."""

        market = _market(market)
        legacy_holding_id = str(legacy_holding_id)
        account_id = str(account_id or "")
        if not account_id:
            raise ValueError("account_id is required")
        intent_id = str(intent_id or "")
        if not intent_id:
            raise ValueError("intent_id is required")

        if link_kind == "entry":
            expected_side = "BUY"
            required_status = "OPEN"
            position_query = (
                "SELECT id, legacy_holding_id, account_id, symbol, status, "
                "entry_intent_id AS current_intent_id FROM positions "
                "WHERE market=? AND account_id=?"
            )
            update_query = (
                "UPDATE positions SET entry_intent_id=?, updated_at=? "
                "WHERE market=? AND account_id=? AND id=? AND status=? "
                "AND entry_intent_id IS NULL"
            )
        elif link_kind == "exit":
            expected_side = "SELL"
            required_status = "CLOSED"
            position_query = (
                "SELECT id, legacy_holding_id, account_id, symbol, status, "
                "exit_intent_id AS current_intent_id FROM positions "
                "WHERE market=? AND account_id=?"
            )
            update_query = (
                "UPDATE positions SET exit_intent_id=?, updated_at=? "
                "WHERE market=? AND account_id=? AND id=? AND status=? "
                "AND exit_intent_id IS NULL"
            )
        else:
            raise ValueError(f"unsupported intent link kind: {link_kind}")

        position_id = legacy_position_id(market, legacy_holding_id)
        if expected_position_ids is None:
            expected_sources = {position_id}
        else:
            source_items = tuple(str(item) for item in expected_position_ids)
            expected_sources = set(source_items)
            if len(expected_sources) != len(source_items):
                raise ValueError("expected_position_ids must not contain duplicates")
            if any(
                not item.startswith(f"legacy:{market}:") for item in expected_sources
            ):
                raise ValueError(
                    "expected_position_ids must contain canonical position ids"
                )
            if market != "US" and expected_sources != {position_id}:
                raise ValueError("multiple source positions are only supported for US")
        if position_id not in expected_sources:
            raise ValueError("source_position_id does not include target position")

        intent = self._execute(
            "SELECT market, account_id, symbol, side, source_position_id "
            "FROM order_intents WHERE id=?",
            (intent_id,),
        ).fetchone()
        if intent is None:
            raise LookupError(f"intent not found: {intent_id}")

        intent_identity = (
            str(intent[0]).upper(),
            str(intent[1]),
            str(intent[2]).upper(),
            str(intent[3]).upper(),
        )
        source_position_id = intent[4]
        source_items = (
            ()
            if source_position_id is None
            else tuple(item.strip() for item in str(source_position_id).split(","))
        )
        if (
            not source_items
            or any(not item for item in source_items)
            or len(set(source_items)) != len(source_items)
            or set(source_items) != expected_sources
        ):
            raise ValueError(
                f"{link_kind} intent source_position_id does not match position"
            )

        source_ids = tuple(sorted(expected_sources))
        positions = [
            row
            for row in self._fetchall(position_query, (market, account_id))
            if str(row["id"]) in expected_sources
        ]
        if {str(row["id"]) for row in positions} != expected_sources:
            raise LookupError(f"{link_kind} source positions not found")
        if any(
            str(row["id"])
            != legacy_position_id(market, row["legacy_holding_id"])
            for row in positions
        ):
            raise ValueError(f"{link_kind} source position identity is invalid")
        if any(
            intent_identity
            != (
                market,
                str(row["account_id"]),
                str(row["symbol"]).upper(),
                expected_side,
            )
            for row in positions
        ):
            raise ValueError(f"{link_kind} intent does not match position")

        current_intent_ids = {row["current_intent_id"] for row in positions}
        if current_intent_ids == {intent_id}:
            return True
        if any(
            current_intent_id not in {None, intent_id}
            for current_intent_id in current_intent_ids
        ):
            raise ValueError(f"{link_kind} intent already linked")
        invalid_statuses = {
            str(row["status"])
            for row in positions
            if str(row["status"]) != required_status
        }
        if invalid_statuses:
            raise InvalidPositionTransition(
                f"{link_kind} intent linkage requires {required_status} position, "
                f"found {','.join(sorted(invalid_statuses))}"
            )

        unlinked_count = sum(
            row["current_intent_id"] is None for row in positions
        )
        now = _utc_now()
        changed = sum(
            self._execute(
                update_query,
                (
                    intent_id,
                    now,
                    market,
                    account_id,
                    source_id,
                    required_status,
                ),
            ).rowcount
            for source_id in source_ids
        )
        if changed != unlinked_count:
            raise RuntimeError("source positions changed concurrently")
        return True

    def link_entry_intent(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        intent_id: Any,
    ) -> bool:
        """Link one persisted BUY intent to an OPEN legacy position."""

        return self._link_intent(
            market=market,
            legacy_holding_id=legacy_holding_id,
            account_id=account_id,
            intent_id=intent_id,
            link_kind="entry",
        )

    def link_exit_intent(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        intent_id: Any,
        expected_position_ids: Iterable[str] | None = None,
    ) -> bool:
        """Link one persisted SELL intent to a CLOSED legacy position."""

        return self._link_intent(
            market=market,
            legacy_holding_id=legacy_holding_id,
            account_id=account_id,
            intent_id=intent_id,
            link_kind="exit",
            expected_position_ids=expected_position_ids,
        )

    def _legacy_rows(self, market: str) -> list[dict[str, Any]]:
        if market == "KR":
            table = "stock_holdings"
            table_info = self._execute("PRAGMA table_info(stock_holdings)").fetchall()
            cursor = self._execute("SELECT * FROM stock_holdings") if table_info else None
        else:
            table = "us_stock_holdings"
            table_info = self._execute("PRAGMA table_info(us_stock_holdings)").fetchall()
            cursor = self._execute("SELECT * FROM us_stock_holdings") if table_info else None
        if cursor is None:
            raise RuntimeError(f"legacy table does not exist: {table}")

        column_indexes = {
            description[0]: index
            for index, description in enumerate(cursor.description or ())
        }
        aliases = (
            ("id", "legacy_holding_id"),
            ("account_key", "account_id"),
            ("account_name", "account_name"),
            ("ticker", "symbol"),
            ("buy_price", "entry_price"),
            ("buy_date", "opened_at"),
        )
        return [
            {
                alias: row[column_indexes[name]] if name in column_indexes else None
                for name, alias in aliases
            }
            for row in cursor.fetchall()
        ]

    @staticmethod
    def _valid_legacy_row(row: dict[str, Any]) -> bool:
        return (
            row["legacy_holding_id"] is not None
            and bool(str(row["account_id"] or ""))
            and bool(str(row["symbol"] or ""))
        )

    def backfill_legacy_positions(self, market: str) -> dict[str, Any]:
        """Idempotently mirror current legacy holdings; never commit or overwrite."""

        market = _market(market)
        inserted = existing = skipped = 0
        for row in self._legacy_rows(market):
            if not self._valid_legacy_row(row):
                skipped += 1
                continue
            created = self.open_legacy_position(
                market=market,
                legacy_holding_id=row["legacy_holding_id"],
                account_id=row["account_id"],
                account_name=row["account_name"],
                symbol=row["symbol"],
                entry_price=row["entry_price"],
                opened_at=row["opened_at"],
            )
            if created:
                inserted += 1
            else:
                existing += 1
        return {
            "market": market,
            "inserted": inserted,
            "existing": existing,
            "skipped": skipped,
        }

    def record_mirror_error(
        self,
        *,
        market: str,
        legacy_holding_id: Any,
        account_id: Any,
        operation: str,
        error: BaseException,
    ) -> int:
        """Persist a sanitized mirror failure without committing."""

        market = _market(market)
        raw_account = str(account_id or "")
        account_ref = account_fingerprint(raw_account) if raw_account else None
        message = _redact_error_text(str(error))
        if raw_account:
            message = message.replace(raw_account, "[REDACTED]")
        cursor = self._execute(
            """
            INSERT INTO position_mirror_errors (
                market, legacy_holding_id, account_ref, operation,
                error_type, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                market,
                None if legacy_holding_id is None else str(legacy_holding_id),
                account_ref,
                str(operation),
                type(error).__name__,
                message,
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)

    def resolve_mirror_error(self, error_id: int) -> bool:
        """Mark one audit error resolved without committing."""

        changed = self._execute(
            "UPDATE position_mirror_errors SET resolved=1, resolved_at=? "
            "WHERE id=? AND resolved=0",
            (_utc_now(), error_id),
        ).rowcount
        return changed == 1

    @staticmethod
    def _identity(
        legacy_holding_id: Any, account_ref: str, symbol: Any
    ) -> dict[str, str]:
        return {
            "legacy_holding_id": str(legacy_holding_id),
            "account_ref": account_ref,
            "symbol": str(symbol).upper(),
        }

    def compare_legacy_positions(
        self,
        market: str,
        *,
        pending_stale_after_seconds: int = 300,
    ) -> dict[str, Any]:
        """Read-only comparison of legacy holdings and OPEN legacy positions."""

        market = _market(market)
        if pending_stale_after_seconds < 0:
            raise ValueError("pending_stale_after_seconds must be non-negative")
        legacy: dict[tuple[str, str, str], dict[str, str]] = {}
        legacy_entry_fingerprints: dict[tuple[str, str, str], str] = {}
        invalid_legacy_rows: list[dict[str, str | None]] = []
        for row in self._legacy_rows(market):
            if not self._valid_legacy_row(row):
                invalid_legacy_rows.append(
                    {
                        "legacy_holding_id": (
                            None
                            if row["legacy_holding_id"] is None
                            else str(row["legacy_holding_id"])
                        )
                    }
                )
                continue
            identity = self._identity(
                row["legacy_holding_id"],
                account_fingerprint(row["account_id"]),
                row["symbol"],
            )
            key = tuple(identity.values())
            legacy[key] = identity
            legacy_entry_fingerprints[key] = _entry_fingerprint(
                row["entry_price"], row["opened_at"]
            )

        position_rows = self._fetchall(
            "SELECT id, legacy_holding_id, account_id, symbol, status, "
            "entry_intent_id, exit_intent_id, entry_price, opened_at, updated_at "
            "FROM positions WHERE market=? AND execution_mode='legacy'",
            (market,),
        )
        positions_by_id = {str(row["id"]): row for row in position_rows}
        positions: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        position_entry_fingerprints: dict[tuple[str, str, str], list[str]] = {}
        for row in position_rows:
            identity = self._identity(
                row["legacy_holding_id"],
                account_fingerprint(row["account_id"]),
                row["symbol"],
            )
            key = tuple(identity.values())
            positions.setdefault(key, []).append({**identity, "status": row["status"]})
            position_entry_fingerprints.setdefault(key, []).append(
                _entry_fingerprint(row["entry_price"], row["opened_at"])
            )

        missing_positions = [
            identity for key, identity in legacy.items() if key not in positions
        ]
        extra_open_positions = [
            row
            for key, rows in positions.items()
            if key not in legacy
            for row in rows
            if row["status"] == "OPEN"
        ]
        non_open_positions = [
            row
            for key, rows in positions.items()
            if key in legacy
            for row in rows
            if row["status"] not in {"OPEN", "PENDING_ENTRY", "PENDING_EXIT"}
        ]
        duplicate_positions = [
            {"identity": dict(zip(("legacy_holding_id", "account_ref", "symbol"), key)),
             "count": len(rows)}
            for key, rows in positions.items()
            if len(rows) > 1
        ]
        entry_mismatches = [
            {
                **legacy[key],
                "legacy_entry_fingerprint": legacy_entry_fingerprints[key],
                "position_entry_fingerprints": position_entry_fingerprints[key],
            }
            for key, rows in positions.items()
            if key in legacy
            and any(row["status"] == "OPEN" for row in rows)
            and legacy_entry_fingerprints[key]
            not in position_entry_fingerprints[key]
        ]
        pending_positions: list[dict[str, Any]] = []
        stale_pending_positions: list[dict[str, Any]] = []
        exit_unknown_positions: list[dict[str, Any]] = []
        for row in position_rows:
            status = str(row["status"])
            if status not in {"PENDING_ENTRY", "PENDING_EXIT", "EXIT_UNKNOWN"}:
                continue
            age_seconds = _age_seconds(row["updated_at"])
            item = {
                "position_id": str(row["id"]),
                "legacy_holding_id": str(row["legacy_holding_id"]),
                "account_ref": account_fingerprint(row["account_id"]),
                "symbol": str(row["symbol"]).upper(),
                "status": status,
                "intent_id": (
                    row["entry_intent_id"]
                    if status == "PENDING_ENTRY"
                    else row["exit_intent_id"]
                ),
                "age_seconds": age_seconds,
            }
            if status == "EXIT_UNKNOWN":
                exit_unknown_positions.append(item)
            elif (
                age_seconds is None
                or age_seconds >= pending_stale_after_seconds
            ):
                stale_pending_positions.append(item)
            else:
                pending_positions.append(item)
        unresolved_mirror_errors = self._fetchall(
            "SELECT id, legacy_holding_id, account_ref, operation, error_type, "
            "error_message, created_at FROM position_mirror_errors "
            "WHERE market=? AND resolved=0 ORDER BY id",
            (market,),
        )
        intent_link_mismatches: list[dict[str, Any]] = []
        failed_exit_linked_open_positions: list[dict[str, Any]] = []
        has_intent_table = self._execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='order_intents'"
        ).fetchone()
        if has_intent_table:
            intent_columns = {
                str(row[1])
                for row in self._execute("PRAGMA table_info(order_intents)").fetchall()
            }
            if "status" in intent_columns:
                intent_rows = self._fetchall(
                    "SELECT id, account_id, symbol, side, source_position_id, status "
                    "FROM order_intents "
                    "WHERE market=? AND source_position_id IS NOT NULL",
                    (market,),
                )
            else:
                intent_rows = self._fetchall(
                    "SELECT id, account_id, symbol, side, source_position_id, "
                    "NULL AS status FROM order_intents "
                    "WHERE market=? AND source_position_id IS NOT NULL",
                    (market,),
                )
            intents_by_id = {str(row["id"]): row for row in intent_rows}
            for position in position_rows:
                exit_intent_id = position["exit_intent_id"]
                intent = (
                    intents_by_id.get(str(exit_intent_id))
                    if exit_intent_id is not None
                    else None
                )
                if (
                    str(position["status"]) == "OPEN"
                    and intent is not None
                    and str(intent["side"]).upper() == "SELL"
                    and str(intent["status"]).upper() == "FAILED"
                ):
                    failed_exit_linked_open_positions.append(
                        {
                            "position_id": str(position["id"]),
                            "legacy_holding_id": str(position["legacy_holding_id"]),
                            "account_ref": account_fingerprint(
                                position["account_id"]
                            ),
                            "symbol": str(position["symbol"]).upper(),
                            "intent_id": str(exit_intent_id),
                            "intent_status": "FAILED",
                        }
                    )
            canonical_prefix = f"legacy:{market}:"
            for intent in intent_rows:
                source_ids = tuple(
                    item.strip()
                    for item in str(intent["source_position_id"]).split(",")
                )
                if (
                    not source_ids
                    or any(not item.startswith(canonical_prefix) for item in source_ids)
                    or len(set(source_ids)) != len(source_ids)
                ):
                    continue
                side = str(intent["side"]).upper()
                if side not in {"BUY", "SELL"}:
                    continue
                intent_column = (
                    "entry_intent_id" if side == "BUY" else "exit_intent_id"
                )
                for source_id in source_ids:
                    position = positions_by_id.get(source_id)
                    reasons = []
                    if position is None:
                        reasons.append("missing_position")
                    else:
                        if str(position["account_id"]) != str(intent["account_id"]):
                            reasons.append("account_mismatch")
                        if str(position["symbol"]).upper() != str(
                            intent["symbol"]
                        ).upper():
                            reasons.append("symbol_mismatch")
                        if position[intent_column] != intent["id"]:
                            reasons.append("missing_or_wrong_link")
                    if reasons:
                        intent_link_mismatches.append(
                            {
                                "intent_id": str(intent["id"]),
                                "position_id": source_id,
                                "account_ref": account_fingerprint(
                                    intent["account_id"]
                                ),
                                "symbol": str(intent["symbol"]).upper(),
                                "side": side,
                                "reasons": reasons,
                            }
                        )
        mismatches = (
            missing_positions,
            extra_open_positions,
            non_open_positions,
            duplicate_positions,
            entry_mismatches,
            invalid_legacy_rows,
            unresolved_mirror_errors,
            intent_link_mismatches,
            pending_positions,
            stale_pending_positions,
            exit_unknown_positions,
            failed_exit_linked_open_positions,
        )
        return {
            "market": market,
            "matches": not any(mismatches),
            "counts": {
                "legacy": len(legacy),
                "positions": len(position_rows),
                "open_positions": sum(
                    row["status"] == "OPEN" for row in position_rows
                ),
                "pending_positions": len(pending_positions),
                "stale_pending_positions": len(stale_pending_positions),
                "exit_unknown_positions": len(exit_unknown_positions),
                "failed_exit_linked_open_positions": len(
                    failed_exit_linked_open_positions
                ),
            },
            "missing_positions": missing_positions,
            "extra_open_positions": extra_open_positions,
            "non_open_positions": non_open_positions,
            "duplicate_positions": duplicate_positions,
            "entry_mismatches": entry_mismatches,
            "invalid_legacy_rows": invalid_legacy_rows,
            "unresolved_mirror_errors": unresolved_mirror_errors,
            "intent_link_mismatches": intent_link_mismatches,
            "pending_positions": pending_positions,
            "stale_pending_positions": stale_pending_positions,
            "exit_unknown_positions": exit_unknown_positions,
            "failed_exit_linked_open_positions": (
                failed_exit_linked_open_positions
            ),
        }


def mirror_write_fail_open(
    connection_or_cursor: sqlite3.Connection | sqlite3.Cursor,
    *,
    logger: Any,
    market: str,
    legacy_holding_id: Any,
    account_id: Any,
    operation: str,
    write: Callable[[PositionStore], Any],
) -> bool:
    """Run one mirror write behind a savepoint and audit any failure.

    The caller owns the outer transaction. A failed shadow write is rolled back
    to the savepoint, then a sanitized unresolved audit row is inserted in that
    same transaction so the legacy path can continue and commit normally.
    """

    store = PositionStore(connection_or_cursor)
    connection_or_cursor.execute("SAVEPOINT position_shadow_write")
    try:
        write(store)
    except Exception as error:
        connection_or_cursor.execute("ROLLBACK TO position_shadow_write")
        connection_or_cursor.execute("RELEASE position_shadow_write")
        logger.critical(
            "[POSITION-SHADOW][%s] %s failed for legacy_id=%s (%s)",
            _market(market),
            operation,
            legacy_holding_id,
            type(error).__name__,
        )
        if _is_sqlite_lock_error(error):
            logger.critical(
                "[POSITION-SHADOW][%s] %s audit deferred to comparator "
                "because sqlite is locked for legacy_id=%s",
                _market(market),
                operation,
                legacy_holding_id,
            )
            return False
        try:
            store.record_mirror_error(
                market=market,
                legacy_holding_id=legacy_holding_id,
                account_id=account_id,
                operation=operation,
                error=error,
            )
        except Exception as audit_error:
            logger.critical(
                "[POSITION-SHADOW][%s] audit write failed for legacy_id=%s (%s)",
                _market(market),
                legacy_holding_id,
                type(audit_error).__name__,
            )
        return False
    connection_or_cursor.execute("RELEASE position_shadow_write")
    return True


def bounded_link_write_fail_open(
    db_path: str | Path,
    *,
    logger: Any,
    market: str,
    legacy_holding_id: Any,
    account_id: Any,
    operation: str,
    write: Callable[[PositionStore], Any],
) -> bool:
    """Run post-broker linkage with a bounded SQLite lock wait.

    Validation failures still use the durable mirror-error table. SQLITE_BUSY /
    SQLITE_LOCKED cannot write to that same database, so those failures emit a
    CRITICAL runtime log and are detected later by the intent-link comparator.
    A dedicated short-lived connection isolates the 50ms busy timeout from the
    agent's long-lived legacy connection. The outermost savepoint release commits
    this linkage/audit transaction before the connection is closed.
    """

    connection = sqlite3.connect(
        str(db_path), timeout=_POSITION_LINK_BUSY_TIMEOUT_MS / 1000
    )
    try:
        try:
            return mirror_write_fail_open(
                connection,
                logger=logger,
                market=market,
                legacy_holding_id=legacy_holding_id,
                account_id=account_id,
                operation=operation,
                write=write,
            )
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()
            if not _is_sqlite_lock_error(error):
                raise
            logger.critical(
                "[POSITION-LINK][%s] %s commit skipped because sqlite is "
                "locked for legacy_id=%s; comparator will detect the gap",
                _market(market),
                operation,
                legacy_holding_id,
            )
            return False
    finally:
        connection.close()
