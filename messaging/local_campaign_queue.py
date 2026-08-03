"""Channel-neutral durable queue for local batch campaign handoff."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class QueuedBatchCampaign:
    queue_id: int
    campaign_id: str
    payload: Mapping[str, object]
    attempt_count: int
    lease_owner: str
    lease_expires_at: datetime


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SQLiteBatchCampaignQueue:
    """SQLite queue shared by the Prism producer and local channel consumers."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.database_path = str(database_path)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._initialize()

    def _initialize(self) -> None:
        if self.database_path == ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_locked()
            return

        lock_fd = os.open(
            f"{self.database_path}.init.lock",
            os.O_CREAT | os.O_RDWR,
            0o600,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_locked()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _initialize_locked(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prism_batch_campaign_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (
                            status IN (
                                'PENDING',
                                'SENDING',
                                'CONSUMED',
                                'DEAD'
                            )
                        ),
                    attempt_count INTEGER NOT NULL DEFAULT 0
                        CHECK (attempt_count >= 0),
                    next_attempt_at TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_prism_campaign_queue_due
                ON prism_batch_campaign_queue(
                    status,
                    next_attempt_at,
                    lease_expires_at,
                    id
                )
                """
            )

    def __enter__(self) -> "SQLiteBatchCampaignQueue":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def enqueue(self, payload: Mapping[str, object]) -> str | None:
        campaign_id = payload.get("campaign_id")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            raise ValueError("campaign payload requires campaign_id")
        created_at = payload.get("occurred_at")
        timestamp = (
            created_at
            if isinstance(created_at, str) and created_at.strip()
            else _utc_iso(datetime.now(timezone.utc))
        )
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO prism_batch_campaign_queue(
                    campaign_id,
                    payload_json,
                    status,
                    created_at
                )
                VALUES (?, ?, 'PENDING', ?)
                """,
                (
                    campaign_id.strip(),
                    json.dumps(
                        dict(payload),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    timestamp,
                ),
            )
        return campaign_id.strip() if cursor.rowcount == 1 else None

    def claim(
        self,
        *,
        lease_owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[QueuedBatchCampaign, ...]:
        if not lease_owner.strip():
            raise ValueError("lease_owner must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if limit <= 0:
            raise ValueError("limit must be positive")

        now_iso = _utc_iso(now)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        lease_expires_iso = _utc_iso(lease_expires_at)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._connection.execute(
                """
                SELECT id
                FROM prism_batch_campaign_queue
                WHERE (
                    status = 'PENDING'
                    AND (
                        next_attempt_at IS NULL
                        OR next_attempt_at <= ?
                    )
                )
                OR (
                    status = 'SENDING'
                    AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                )
                ORDER BY id
                LIMIT ?
                """,
                (now_iso, now_iso, limit),
            ).fetchall()
            ids = tuple(row["id"] for row in rows)
            if not ids:
                self._connection.commit()
                return ()

            placeholders = ",".join("?" for _ in ids)
            self._connection.execute(
                f"""
                UPDATE prism_batch_campaign_queue
                SET
                    status = 'SENDING',
                    attempt_count = attempt_count + 1,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    next_attempt_at = NULL
                WHERE id IN ({placeholders})
                """,
                (lease_owner, lease_expires_iso, *ids),
            )
            claimed = self._connection.execute(
                f"""
                SELECT
                    id,
                    campaign_id,
                    payload_json,
                    attempt_count,
                    lease_owner,
                    lease_expires_at
                FROM prism_batch_campaign_queue
                WHERE id IN ({placeholders})
                ORDER BY id
                """,
                ids,
            ).fetchall()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

        return tuple(
            QueuedBatchCampaign(
                queue_id=row["id"],
                campaign_id=row["campaign_id"],
                payload=json.loads(row["payload_json"]),
                attempt_count=row["attempt_count"],
                lease_owner=row["lease_owner"],
                lease_expires_at=_parse_utc(row["lease_expires_at"]),
            )
            for row in claimed
        )

    def acknowledge(
        self,
        queue_id: int,
        *,
        lease_owner: str,
        consumed_at: datetime,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE prism_batch_campaign_queue
                SET
                    status = 'CONSUMED',
                    consumed_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = NULL
                WHERE id = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (_utc_iso(consumed_at), queue_id, lease_owner),
            )
        return cursor.rowcount == 1

    def release(
        self,
        queue_id: int,
        *,
        lease_owner: str,
        next_attempt_at: datetime,
        error: str,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE prism_batch_campaign_queue
                SET
                    status = 'PENDING',
                    next_attempt_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?
                WHERE id = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (
                    _utc_iso(next_attempt_at),
                    error[:1_000],
                    queue_id,
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1

    def mark_dead(
        self,
        queue_id: int,
        *,
        lease_owner: str,
        error: str,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE prism_batch_campaign_queue
                SET
                    status = 'DEAD',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?
                WHERE id = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (error[:1_000], queue_id, lease_owner),
            )
        return cursor.rowcount == 1

    def list_entries(self) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            """
            SELECT
                campaign_id,
                status,
                attempt_count,
                next_attempt_at,
                last_error,
                consumed_at
            FROM prism_batch_campaign_queue
            ORDER BY id
            """
        ).fetchall()
        return tuple(dict(row) for row in rows)
