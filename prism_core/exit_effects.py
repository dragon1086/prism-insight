"""Durable effect candidates created atomically with a completed exit."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping


EXIT_EFFECT_TYPES = ("JOURNAL", "TELEGRAM", "REDIS", "GCP")

_EXIT_EFFECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS exit_effect_outbox (
    id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    market TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    effect_type TEXT NOT NULL
        CHECK (effect_type IN ('JOURNAL', 'TELEGRAM', 'REDIS', 'GCP')),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IN_PROGRESS', 'DELIVERED', 'DEAD')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    remote_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(intent_id, effect_type)
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class ExitEffectStore:
    """Transaction-neutral storage for post-CLOSED effect candidates."""

    def __init__(
        self, connection_or_cursor: sqlite3.Connection | sqlite3.Cursor
    ) -> None:
        if not isinstance(connection_or_cursor, (sqlite3.Connection, sqlite3.Cursor)):
            raise TypeError("ExitEffectStore requires a sqlite3 Connection or Cursor")
        self._db = connection_or_cursor

    @property
    def _connection(self) -> sqlite3.Connection:
        if isinstance(self._db, sqlite3.Connection):
            return self._db
        return self._db.connection

    def _execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, parameters)

    def _require_active_transaction(self) -> None:
        if not self._connection.in_transaction:
            raise RuntimeError(
                "exit effect enqueue requires an active caller-owned transaction"
            )

    def ensure_schema(self) -> None:
        """Create the additive outbox schema without committing caller work."""

        self._execute(_EXIT_EFFECT_SCHEMA)
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_exit_effect_outbox_pending "
            "ON exit_effect_outbox(status, next_attempt_at, created_at)"
        )
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_exit_effect_outbox_intent "
            "ON exit_effect_outbox(intent_id, effect_type)"
        )

    def enqueue_exit_effects(
        self,
        *,
        intent_id: str,
        market: str,
        account_id: str,
        symbol: str,
        source: str,
        payload: Mapping[str, Any],
    ) -> int:
        """Insert four deterministic effect candidates in the caller transaction."""

        self._require_active_transaction()
        identity = {
            "intent_id": str(intent_id or "").strip(),
            "market": str(market or "").strip().upper(),
            "account_id": str(account_id or "").strip(),
            "symbol": str(symbol or "").strip().upper(),
            "source": str(source or "").strip(),
        }
        if not all(identity.values()):
            raise ValueError("exit effect identity fields are required")
        if payload.get("event_id") != identity["intent_id"]:
            raise ValueError("exit effect payload event_id must match intent_id")

        payload_json = _canonical_json(payload)
        now = _utc_now()
        inserted = 0
        for effect_type in EXIT_EFFECT_TYPES:
            effect_id = f"{identity['intent_id']}:{effect_type.lower()}"
            changed = self._execute(
                """
                INSERT INTO exit_effect_outbox (
                    id, intent_id, market, account_id, symbol, source,
                    effect_type, payload_json, status, attempt_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)
                ON CONFLICT(intent_id, effect_type) DO NOTHING
                """,
                (
                    effect_id,
                    identity["intent_id"],
                    identity["market"],
                    identity["account_id"],
                    identity["symbol"],
                    identity["source"],
                    effect_type,
                    payload_json,
                    now,
                    now,
                ),
            ).rowcount
            if changed == 1:
                inserted += 1
                continue

            existing = self._execute(
                """
                SELECT id, market, account_id, symbol, source, payload_json
                FROM exit_effect_outbox
                WHERE intent_id=? AND effect_type=?
                """,
                (identity["intent_id"], effect_type),
            ).fetchone()
            expected = (
                effect_id,
                identity["market"],
                identity["account_id"],
                identity["symbol"],
                identity["source"],
                payload_json,
            )
            if existing is None or tuple(existing) != expected:
                raise ValueError(
                    "exit effect payload conflict for existing intent/effect identity"
                )
        return inserted

    def list_for_intent(self, intent_id: str) -> list[dict[str, Any]]:
        """Return decoded effect rows for audit and tests without mutation."""

        cursor = self._execute(
            "SELECT * FROM exit_effect_outbox WHERE intent_id=?",
            (str(intent_id or ""),),
        )
        columns = [column[0] for column in cursor.description or ()]
        order = {
            effect_type: index for index, effect_type in enumerate(EXIT_EFFECT_TYPES)
        }
        rows = []
        for value in cursor.fetchall():
            row = dict(zip(columns, value))
            row["payload"] = json.loads(row.pop("payload_json"))
            rows.append(row)
        rows.sort(key=lambda row: order[str(row["effect_type"])])
        return rows
