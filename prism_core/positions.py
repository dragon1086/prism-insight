"""Additive shadow position ledger for issue #412 Phase 4-a.

Every operation uses a caller-supplied SQLite connection or cursor.  This module
never commits: the legacy write and its shadow write therefore remain under the
caller's transaction boundary.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
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


class InvalidPositionTransition(ValueError):
    """Raised when a position lifecycle transition is not allowed."""


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


def _redact_error_text(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?i)(api[_-]?key|app[_-]?key|app[_-]?secret|token|password)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
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

    def compare_legacy_positions(self, market: str) -> dict[str, Any]:
        """Read-only comparison of legacy holdings and OPEN legacy positions."""

        market = _market(market)
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
            "SELECT legacy_holding_id, account_id, symbol, status, "
            "entry_price, opened_at "
            "FROM positions WHERE market=? AND execution_mode='legacy'",
            (market,),
        )
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
            if row["status"] != "OPEN"
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
        unresolved_mirror_errors = self._fetchall(
            "SELECT id, legacy_holding_id, account_ref, operation, error_type, "
            "error_message, created_at FROM position_mirror_errors "
            "WHERE market=? AND resolved=0 ORDER BY id",
            (market,),
        )
        mismatches = (
            missing_positions,
            extra_open_positions,
            non_open_positions,
            duplicate_positions,
            entry_mismatches,
            invalid_legacy_rows,
            unresolved_mirror_errors,
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
            },
            "missing_positions": missing_positions,
            "extra_open_positions": extra_open_positions,
            "non_open_positions": non_open_positions,
            "duplicate_positions": duplicate_positions,
            "entry_mismatches": entry_mismatches,
            "invalid_legacy_rows": invalid_legacy_rows,
            "unresolved_mirror_errors": unresolved_mirror_errors,
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
