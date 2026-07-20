"""Durable effect candidates created atomically with a completed exit."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping


EXIT_EFFECT_TYPES = ("JOURNAL", "TELEGRAM", "REDIS", "GCP")
REMOTE_ID_EFFECT_TYPES = frozenset({"REDIS", "GCP"})
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,119}$")

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


def _utc_datetime(value: datetime | None = None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        raise ValueError("effect timestamps must be timezone-aware")
    return resolved.astimezone(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    return _utc_datetime(value).isoformat(timespec="seconds")


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

    @staticmethod
    def _decode_rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        columns = [column[0] for column in cursor.description or ()]
        rows = []
        for value in cursor.fetchall():
            row = dict(zip(columns, value))
            row["payload"] = json.loads(row.pop("payload_json"))
            rows.append(row)
        return rows

    def _require_claimed(self, effect_id: str, owner: str) -> dict[str, Any]:
        cursor = self._execute(
            """
            SELECT id, effect_type, status, lease_owner, attempt_count
            FROM exit_effect_outbox
            WHERE id=?
            """,
            (effect_id,),
        )
        value = cursor.fetchone()
        if value is None:
            raise ValueError("exit effect does not exist")
        columns = [column[0] for column in cursor.description or ()]
        row = dict(zip(columns, value))
        if row["status"] != "IN_PROGRESS" or row["lease_owner"] != owner:
            raise RuntimeError("exit effect requires the active lease owner")
        return row

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

    def claim_ready_effects(
        self,
        *,
        owner: str,
        limit: int,
        effect_types: Iterable[str] | None = None,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        """Claim a bounded ready batch without holding a lease transaction open."""

        self._require_active_transaction()
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("lease owner is required")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("claim limit must be a positive integer")
        if not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")

        selected_types = tuple(effect_types or EXIT_EFFECT_TYPES)
        if not selected_types or any(
            effect_type not in EXIT_EFFECT_TYPES for effect_type in selected_types
        ):
            raise ValueError("unsupported exit effect type")
        selected_types = tuple(dict.fromkeys(selected_types))
        type_enabled = tuple(
            int(effect_type in selected_types) for effect_type in EXIT_EFFECT_TYPES
        )
        current = _utc_datetime(now)
        current_iso = _utc_iso(current)
        lease_expires_at = _utc_iso(current + timedelta(seconds=lease_seconds))
        candidates = self._execute(
            """
            SELECT id
            FROM exit_effect_outbox
            WHERE (
                    (? = 1 AND effect_type='JOURNAL')
                 OR (? = 1 AND effect_type='TELEGRAM')
                 OR (? = 1 AND effect_type='REDIS')
                 OR (? = 1 AND effect_type='GCP')
              )
              AND (
                  (status='PENDING' AND (
                      next_attempt_at IS NULL OR next_attempt_at <= ?
                  ))
                  OR
                  (status='IN_PROGRESS' AND lease_expires_at IS NOT NULL
                   AND lease_expires_at <= ?)
              )
            ORDER BY created_at,
                     CASE effect_type
                         WHEN 'JOURNAL' THEN 1
                         WHEN 'TELEGRAM' THEN 2
                         WHEN 'REDIS' THEN 3
                         WHEN 'GCP' THEN 4
                     END,
                     id
            LIMIT ?
            """,
            (*type_enabled, current_iso, current_iso, limit),
        ).fetchall()
        effect_ids = [str(row[0]) for row in candidates]
        claimed = []
        for effect_id in effect_ids:
            changed = self._execute(
                """
                UPDATE exit_effect_outbox
                SET status='IN_PROGRESS', attempt_count=attempt_count+1,
                    lease_owner=?, lease_expires_at=?, updated_at=?
                WHERE id=?
                """,
                (owner, lease_expires_at, current_iso, effect_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("exit effect claim changed unexpectedly")
            cursor = self._execute(
                "SELECT * FROM exit_effect_outbox WHERE id=?", (effect_id,)
            )
            claimed.extend(self._decode_rows(cursor))
        return claimed

    def get_effect(self, effect_id: str) -> dict[str, Any] | None:
        """Return one decoded effect without changing its lease or status."""

        cursor = self._execute(
            "SELECT * FROM exit_effect_outbox WHERE id=?",
            (str(effect_id or "").strip(),),
        )
        rows = self._decode_rows(cursor)
        return rows[0] if rows else None

    def claim_effect(
        self,
        *,
        effect_id: str,
        owner: str,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Claim one exact ready effect without selecting unrelated backlog."""

        self._require_active_transaction()
        effect_id = str(effect_id or "").strip()
        owner = str(owner or "").strip()
        if not effect_id:
            raise ValueError("effect id is required")
        if not owner:
            raise ValueError("lease owner is required")
        if not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")

        current = _utc_datetime(now)
        current_iso = _utc_iso(current)
        lease_expires_at = _utc_iso(current + timedelta(seconds=lease_seconds))
        changed = self._execute(
            """
            UPDATE exit_effect_outbox
            SET status='IN_PROGRESS', attempt_count=attempt_count+1,
                lease_owner=?, lease_expires_at=?, updated_at=?
            WHERE id=?
              AND (
                  (status='PENDING' AND (
                      next_attempt_at IS NULL OR next_attempt_at <= ?
                  ))
                  OR
                  (status='IN_PROGRESS' AND lease_expires_at IS NOT NULL
                   AND lease_expires_at <= ?)
              )
            """,
            (
                owner,
                lease_expires_at,
                current_iso,
                effect_id,
                current_iso,
                current_iso,
            ),
        ).rowcount
        if changed == 0:
            return None
        if changed != 1:
            raise RuntimeError("exit effect claim changed unexpectedly")
        return self.get_effect(effect_id)

    def mark_delivered(
        self,
        *,
        effect_id: str,
        owner: str,
        remote_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Complete one claimed effect; publisher effects require a message id."""

        self._require_active_transaction()
        owner = str(owner or "").strip()
        row = self._require_claimed(str(effect_id or "").strip(), owner)
        normalized_remote_id = str(remote_id).strip() if remote_id is not None else None
        if row["effect_type"] in REMOTE_ID_EFFECT_TYPES and not normalized_remote_id:
            raise ValueError("publisher delivery requires a remote message id")
        completed_at = _utc_iso(now)
        changed = self._execute(
            """
            UPDATE exit_effect_outbox
            SET status='DELIVERED', remote_id=?, last_error=NULL,
                next_attempt_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                updated_at=?, completed_at=?
            WHERE id=? AND status='IN_PROGRESS' AND lease_owner=?
            """,
            (
                normalized_remote_id,
                completed_at,
                completed_at,
                row["id"],
                owner,
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("exit effect delivery changed unexpectedly")
        return True

    def record_failure(
        self,
        *,
        effect_id: str,
        owner: str,
        error_type: str,
        next_attempt_at: datetime,
        max_attempts: int,
        now: datetime | None = None,
    ) -> str:
        """Release a failed claim to PENDING or isolate it as DEAD."""

        self._require_active_transaction()
        owner = str(owner or "").strip()
        error_type = str(error_type or "").strip()
        if not _ERROR_TYPE.fullmatch(error_type):
            raise ValueError("error_type must be a redacted exception type")
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        row = self._require_claimed(str(effect_id or "").strip(), owner)
        current_iso = _utc_iso(now)
        terminal = int(row["attempt_count"]) >= max_attempts
        status = "DEAD" if terminal else "PENDING"
        retry_at = None if terminal else _utc_iso(next_attempt_at)
        completed_at = current_iso if terminal else None
        changed = self._execute(
            """
            UPDATE exit_effect_outbox
            SET status=?, last_error=?, next_attempt_at=?,
                lease_owner=NULL, lease_expires_at=NULL,
                updated_at=?, completed_at=?
            WHERE id=? AND status='IN_PROGRESS' AND lease_owner=?
            """,
            (
                status,
                error_type,
                retry_at,
                current_iso,
                completed_at,
                row["id"],
                owner,
            ),
        ).rowcount
        if changed != 1:
            raise RuntimeError("exit effect failure changed unexpectedly")
        return status

    def list_for_intent(self, intent_id: str) -> list[dict[str, Any]]:
        """Return decoded effect rows for audit and tests without mutation."""

        cursor = self._execute(
            "SELECT * FROM exit_effect_outbox WHERE intent_id=?",
            (str(intent_id or ""),),
        )
        order = {
            effect_type: index for index, effect_type in enumerate(EXIT_EFFECT_TYPES)
        }
        rows = self._decode_rows(cursor)
        rows.sort(key=lambda row: order[str(row["effect_type"])])
        return rows
