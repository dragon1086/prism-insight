"""Dedicated SQLite persistence for the Kakao bot."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kakao_bot.domain.errors import RoomNotApprovedError, RoomNotFoundError
from kakao_bot.domain.models import (
    ApprovalStatus,
    BatchCampaign,
    ClaimedOutboundDelivery,
    Market,
    OutboundDelivery,
    Room,
    RoomLifecycleType,
    RoomSubscription,
    Session,
)
from kakao_bot.ports.gateway_state import GatewayState, GatewayStatePort


_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE kakao_rooms (
            room_id TEXT PRIMARY KEY,
            approval_status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (approval_status IN ('PENDING', 'APPROVED', 'REJECTED')),
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE kakao_room_subscriptions (
            room_id TEXT PRIMARY KEY
                REFERENCES kakao_rooms(room_id) ON DELETE CASCADE,
            kr_morning INTEGER NOT NULL DEFAULT 0 CHECK (kr_morning IN (0, 1)),
            kr_afternoon INTEGER NOT NULL DEFAULT 0 CHECK (kr_afternoon IN (0, 1)),
            us_morning INTEGER NOT NULL DEFAULT 0 CHECK (us_morning IN (0, 1)),
            us_afternoon INTEGER NOT NULL DEFAULT 0 CHECK (us_afternoon IN (0, 1)),
            rest_notices INTEGER NOT NULL DEFAULT 0 CHECK (rest_notices IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE kakao_inbound_events (
            event_id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE kakao_signal_campaigns (
            campaign_id TEXT PRIMARY KEY,
            market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
            session TEXT NOT NULL CHECK (session IN ('MORNING', 'AFTERNOON')),
            trade_date TEXT NOT NULL,
            regime TEXT NOT NULL
                CHECK (
                    regime IN (
                        'UNKNOWN',
                        'UPTREND',
                        'UNDER_PRESSURE',
                        'CORRECTION'
                    )
                ),
            status TEXT NOT NULL
                CHECK (status IN ('COLLECTING', 'COMPLETED', 'SKIPPED')),
            candidates_json TEXT NOT NULL,
            skip_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE kakao_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_key TEXT NOT NULL UNIQUE,
            room_id TEXT NOT NULL
                REFERENCES kakao_rooms(room_id) ON DELETE CASCADE,
            message_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'DEAD')),
            created_at TEXT NOT NULL,
            expires_at TEXT
        );

        CREATE INDEX idx_kakao_rooms_approval
            ON kakao_rooms(approval_status);
        CREATE INDEX idx_kakao_campaign_slot
            ON kakao_signal_campaigns(trade_date, market, session, status);
        CREATE INDEX idx_kakao_outbox_status
            ON kakao_outbox(status, created_at);
        """,
    ),
    (
        2,
        """
        ALTER TABLE kakao_outbox
            ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK (attempt_count >= 0);
        ALTER TABLE kakao_outbox ADD COLUMN next_attempt_at TEXT;
        ALTER TABLE kakao_outbox ADD COLUMN lease_owner TEXT;
        ALTER TABLE kakao_outbox ADD COLUMN lease_expires_at TEXT;
        ALTER TABLE kakao_outbox ADD COLUMN last_error TEXT;
        ALTER TABLE kakao_outbox ADD COLUMN sent_at TEXT;

        CREATE INDEX idx_kakao_outbox_due
            ON kakao_outbox(status, next_attempt_at, lease_expires_at, created_at);
        """,
    ),
    (
        3,
        """
        CREATE TABLE kakao_gateway_state (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            session_id TEXT,
            sequence INTEGER CHECK (sequence IS NULL OR sequence >= 0),
            updated_at TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        """
        CREATE TABLE kakao_analysis_jobs (
            job_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL
                REFERENCES kakao_rooms(room_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            market TEXT NOT NULL CHECK (market IN ('kr', 'us')),
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
            summary TEXT,
            artifact_token TEXT,
            error_code TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0
                CHECK (attempt_count >= 0),
            lease_expires_at TEXT,
            requested_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );

        -- Worker claim scan: oldest pending first, skipping live leases.
        CREATE INDEX idx_kakao_analysis_jobs_claimable
            ON kakao_analysis_jobs(status, lease_expires_at, requested_at);

        -- Daily quota counting is done against these, so keep the lookup cheap.
        CREATE INDEX idx_kakao_analysis_jobs_room_requested
            ON kakao_analysis_jobs(room_id, requested_at);
        CREATE INDEX idx_kakao_analysis_jobs_user_requested
            ON kakao_analysis_jobs(user_id, requested_at);

        -- Opaque, expiring handles for PDF artifacts. The token is the only
        -- thing that ever appears in a URL; the real path stays here.
        CREATE TABLE kakao_report_links (
            token TEXT PRIMARY KEY,
            artifact_path TEXT NOT NULL,
            room_id TEXT NOT NULL
                REFERENCES kakao_rooms(room_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        CREATE INDEX idx_kakao_report_links_expiry
            ON kakao_report_links(expires_at);
        """,
    ),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class SQLiteKakaoRepository:
    """SQLite adapter implementing the Kakao application repository port."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

        self.database_path = str(database_path)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
        self._migrate()

    def __enter__(self) -> "SQLiteKakaoRepository":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        if self.database_path == ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._migrate_locked()
            return

        lock_path = f"{self.database_path}.migrate.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._migrate_locked()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _migrate_locked(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["version"]
            for row in self._connection.execute("SELECT version FROM schema_migrations")
        }

        for version, sql in _MIGRATIONS:
            if version in applied:
                continue
            with self._connection:
                self._connection.executescript(sql)
                self._connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (?, ?)
                    """,
                    (version, _utc_iso(_utc_now())),
                )

    def discover_room(
        self,
        room_id: str,
        *,
        discovered_at: datetime | None = None,
    ) -> Room:
        if not room_id.strip():
            raise ValueError("room_id must not be empty")

        timestamp = _utc_iso(discovered_at or _utc_now())
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_rooms(
                    room_id, approval_status, discovered_at, updated_at
                )
                VALUES (?, 'PENDING', ?, ?)
                """,
                (room_id, timestamp, timestamp),
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_room_subscriptions(
                    room_id,
                    kr_morning,
                    kr_afternoon,
                    us_morning,
                    us_afternoon,
                    rest_notices,
                    updated_at
                )
                VALUES (?, 0, 0, 0, 0, 0, ?)
                """,
                (room_id, timestamp),
            )

        room = self._fetch_room(room_id)
        if room is None:
            raise RuntimeError(f"failed to discover room: {room_id}")
        return room

    def set_room_approval(
        self,
        room_id: str,
        approval_status: ApprovalStatus,
    ) -> None:
        room = self._fetch_room(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)

        apply_default_profile = (
            room.approval_status is not ApprovalStatus.APPROVED
            and approval_status is ApprovalStatus.APPROVED
        )
        timestamp = _utc_iso(_utc_now())
        with self._connection:
            self._connection.execute(
                """
                UPDATE kakao_rooms
                SET approval_status = ?, updated_at = ?
                WHERE room_id = ?
                """,
                (approval_status.value, timestamp, room_id),
            )
            if apply_default_profile:
                self._connection.execute(
                    """
                    UPDATE kakao_room_subscriptions
                    SET
                        kr_morning = 0,
                        kr_afternoon = 1,
                        us_morning = 0,
                        us_afternoon = 0,
                        rest_notices = 0,
                        updated_at = ?
                    WHERE room_id = ?
                    """,
                    (timestamp, room_id),
                )
            if approval_status is not ApprovalStatus.APPROVED:
                self._connection.execute(
                    """
                    UPDATE kakao_outbox
                    SET
                        status = 'DEAD',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error = 'room approval revoked'
                    WHERE room_id = ?
                      AND status IN ('PENDING', 'SENDING')
                    """,
                    (room_id,),
                )

    def configure_subscription(self, subscription: RoomSubscription) -> None:
        room = self._fetch_room(subscription.room_id)
        if room is None:
            raise RoomNotFoundError(subscription.room_id)
        if room.approval_status is not ApprovalStatus.APPROVED:
            raise RoomNotApprovedError(subscription.room_id)

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO kakao_room_subscriptions(
                    room_id,
                    kr_morning,
                    kr_afternoon,
                    us_morning,
                    us_afternoon,
                    rest_notices,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(room_id) DO UPDATE SET
                    kr_morning = excluded.kr_morning,
                    kr_afternoon = excluded.kr_afternoon,
                    us_morning = excluded.us_morning,
                    us_afternoon = excluded.us_afternoon,
                    rest_notices = excluded.rest_notices,
                    updated_at = excluded.updated_at
                """,
                (
                    subscription.room_id,
                    int(subscription.kr_morning),
                    int(subscription.kr_afternoon),
                    int(subscription.us_morning),
                    int(subscription.us_afternoon),
                    int(subscription.rest_notices),
                    _utc_iso(_utc_now()),
                ),
            )

    def get_subscription(self, room_id: str) -> RoomSubscription:
        row = self._connection.execute(
            """
            SELECT
                room_id,
                kr_morning,
                kr_afternoon,
                us_morning,
                us_afternoon,
                rest_notices
            FROM kakao_room_subscriptions
            WHERE room_id = ?
            """,
            (room_id,),
        ).fetchone()
        if row is None:
            raise RoomNotFoundError(room_id)
        return RoomSubscription(
            room_id=row["room_id"],
            kr_morning=bool(row["kr_morning"]),
            kr_afternoon=bool(row["kr_afternoon"]),
            us_morning=bool(row["us_morning"]),
            us_afternoon=bool(row["us_afternoon"]),
            rest_notices=bool(row["rest_notices"]),
        )

    def get_room(self, room_id: str) -> Room:
        room = self._fetch_room(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    def list_rooms(self) -> tuple[Room, ...]:
        rows = self._connection.execute(
            """
            SELECT room_id, approval_status
            FROM kakao_rooms
            ORDER BY discovered_at, room_id
            """
        ).fetchall()
        return tuple(
            Room(
                room_id=row["room_id"],
                approval_status=ApprovalStatus(row["approval_status"]),
            )
            for row in rows
        )

    def record_inbound_event(
        self,
        event_id: str,
        *,
        occurred_at: datetime | None = None,
    ) -> bool:
        if not event_id.strip():
            raise ValueError("event_id must not be empty")

        now = _utc_now()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_inbound_events(
                    event_id, occurred_at, recorded_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    event_id,
                    _utc_iso(occurred_at or now),
                    _utc_iso(now),
                ),
            )
        return cursor.rowcount == 1

    def apply_gateway_event(
        self,
        event_id: str,
        *,
        room_id: str,
        event_type: RoomLifecycleType | None,
        occurred_at: datetime,
    ) -> bool:
        """Deduplicate and apply room lifecycle state in one transaction."""

        if not event_id.strip():
            raise ValueError("event_id must not be empty")
        if not room_id.strip():
            raise ValueError("room_id must not be empty")

        occurred_iso = _utc_iso(occurred_at)
        recorded_iso = _utc_iso(_utc_now())
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_inbound_events(
                    event_id, occurred_at, recorded_at
                )
                VALUES (?, ?, ?)
                """,
                (event_id, occurred_iso, recorded_iso),
            )
            if cursor.rowcount != 1:
                self._connection.commit()
                return False

            if event_type is RoomLifecycleType.ENTRANCE:
                self._connection.execute(
                    """
                    INSERT INTO kakao_rooms(
                        room_id, approval_status, discovered_at, updated_at
                    )
                    VALUES (?, 'PENDING', ?, ?)
                    ON CONFLICT(room_id) DO UPDATE SET
                        approval_status = 'PENDING',
                        updated_at = excluded.updated_at
                    """,
                    (room_id, occurred_iso, recorded_iso),
                )
                self._connection.execute(
                    """
                    INSERT INTO kakao_room_subscriptions(
                        room_id,
                        kr_morning,
                        kr_afternoon,
                        us_morning,
                        us_afternoon,
                        rest_notices,
                        updated_at
                    )
                    VALUES (?, 0, 0, 0, 0, 0, ?)
                    ON CONFLICT(room_id) DO UPDATE SET
                        kr_morning = 0,
                        kr_afternoon = 0,
                        us_morning = 0,
                        us_afternoon = 0,
                        rest_notices = 0,
                        updated_at = excluded.updated_at
                    """,
                    (room_id, recorded_iso),
                )
            elif event_type is RoomLifecycleType.LEAVE:
                self._connection.execute(
                    """
                    INSERT INTO kakao_rooms(
                        room_id, approval_status, discovered_at, updated_at
                    )
                    VALUES (?, 'REJECTED', ?, ?)
                    ON CONFLICT(room_id) DO UPDATE SET
                        approval_status = 'REJECTED',
                        updated_at = excluded.updated_at
                    """,
                    (room_id, occurred_iso, recorded_iso),
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO kakao_room_subscriptions(
                        room_id,
                        kr_morning,
                        kr_afternoon,
                        us_morning,
                        us_afternoon,
                        rest_notices,
                        updated_at
                    )
                    VALUES (?, 0, 0, 0, 0, 0, ?)
                    """,
                    (room_id, recorded_iso),
                )
                self._connection.execute(
                    """
                    UPDATE kakao_outbox
                    SET
                        status = 'DEAD',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error = 'room approval revoked'
                    WHERE room_id = ?
                      AND status IN ('PENDING', 'SENDING')
                    """,
                    (room_id,),
                )
            else:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO kakao_rooms(
                        room_id, approval_status, discovered_at, updated_at
                    )
                    VALUES (?, 'PENDING', ?, ?)
                    """,
                    (room_id, occurred_iso, recorded_iso),
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO kakao_room_subscriptions(
                        room_id,
                        kr_morning,
                        kr_afternoon,
                        us_morning,
                        us_afternoon,
                        rest_notices,
                        updated_at
                    )
                    VALUES (?, 0, 0, 0, 0, 0, ?)
                    """,
                    (room_id, recorded_iso),
                )

            self._connection.commit()
            return True
        except BaseException:
            self._connection.rollback()
            raise

    def save_campaign(self, campaign: BatchCampaign) -> bool:
        candidates_json = json.dumps(
            [candidate.as_payload() for candidate in campaign.candidates],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_signal_campaigns(
                    campaign_id,
                    market,
                    session,
                    trade_date,
                    regime,
                    status,
                    candidates_json,
                    skip_reason,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign.campaign_id,
                    campaign.market.value,
                    campaign.session.value,
                    campaign.trade_date.isoformat(),
                    campaign.regime.value,
                    campaign.status.value,
                    candidates_json,
                    campaign.skip_reason,
                    _utc_iso(campaign.created_at),
                ),
            )
        return cursor.rowcount == 1

    def list_delivery_targets(
        self,
        market: Market,
        session: Session,
        *,
        require_rest_notices: bool = False,
    ) -> tuple[str, ...]:
        slot_column = {
            (Market.KR, Session.MORNING): "kr_morning",
            (Market.KR, Session.AFTERNOON): "kr_afternoon",
            (Market.US, Session.MORNING): "us_morning",
            (Market.US, Session.AFTERNOON): "us_afternoon",
        }[(market, session)]
        rest_clause = "AND s.rest_notices = 1" if require_rest_notices else ""

        rows = self._connection.execute(
            f"""
            SELECT r.room_id
            FROM kakao_rooms AS r
            JOIN kakao_room_subscriptions AS s ON s.room_id = r.room_id
            WHERE r.approval_status = 'APPROVED'
              AND s.{slot_column} = 1
              {rest_clause}
            ORDER BY r.room_id
            """
        ).fetchall()
        return tuple(row["room_id"] for row in rows)

    def enqueue_outbound(self, delivery: OutboundDelivery) -> bool:
        if not delivery.delivery_key.strip():
            raise ValueError("delivery_key must not be empty")

        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO kakao_outbox(
                    delivery_key,
                    room_id,
                    message_type,
                    payload_json,
                    status,
                    created_at,
                    expires_at
                )
                SELECT ?, ?, ?, ?, 'PENDING', ?, ?
                FROM kakao_rooms
                WHERE room_id = ?
                  AND approval_status = 'APPROVED'
                """,
                (
                    delivery.delivery_key,
                    delivery.room_id,
                    delivery.message_type,
                    json.dumps(
                        dict(delivery.payload),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    _utc_iso(delivery.created_at),
                    (
                        _utc_iso(delivery.expires_at)
                        if delivery.expires_at is not None
                        else None
                    ),
                    delivery.room_id,
                ),
            )
        return cursor.rowcount == 1

    def claim_outbound(
        self,
        *,
        lease_owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> tuple[ClaimedOutboundDelivery, ...]:
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
            self._connection.execute(
                """
                UPDATE kakao_outbox
                SET
                    status = 'DEAD',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = 'delivery expired'
                WHERE status IN ('PENDING', 'SENDING')
                  AND expires_at IS NOT NULL
                  AND expires_at <= ?
                """,
                (now_iso,),
            )
            rows = self._connection.execute(
                """
                SELECT id
                FROM kakao_outbox AS outbox
                WHERE EXISTS (
                        SELECT 1
                        FROM kakao_rooms AS room
                        WHERE room.room_id = outbox.room_id
                          AND room.approval_status = 'APPROVED'
                    )
                  AND (
                    (
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
                  )
                ORDER BY id
                LIMIT ?
                """,
                (now_iso, now_iso, limit),
            ).fetchall()
            ids = tuple(row["id"] for row in rows)
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self._connection.execute(
                    f"""
                    UPDATE kakao_outbox
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
                claimed_rows = self._connection.execute(
                    f"""
                    SELECT
                        delivery_key,
                        room_id,
                        message_type,
                        payload_json,
                        attempt_count,
                        lease_owner,
                        lease_expires_at,
                        created_at,
                        expires_at
                    FROM kakao_outbox
                    WHERE id IN ({placeholders})
                    ORDER BY id
                    """,
                    ids,
                ).fetchall()
            else:
                claimed_rows = ()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

        return tuple(
            ClaimedOutboundDelivery(
                delivery_key=row["delivery_key"],
                room_id=row["room_id"],
                message_type=row["message_type"],
                payload=json.loads(row["payload_json"]),
                attempt_count=row["attempt_count"],
                lease_owner=row["lease_owner"],
                lease_expires_at=_parse_utc(row["lease_expires_at"]),
                created_at=_parse_utc(row["created_at"]),
                expires_at=(
                    _parse_utc(row["expires_at"])
                    if row["expires_at"] is not None
                    else None
                ),
            )
            for row in claimed_rows
        )

    def mark_outbound_sent(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        sent_at: datetime,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE kakao_outbox
                SET
                    status = 'SENT',
                    sent_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = NULL
                WHERE delivery_key = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (_utc_iso(sent_at), delivery_key, lease_owner),
            )
        return cursor.rowcount == 1

    def release_outbound(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        next_attempt_at: datetime,
        error: str,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE kakao_outbox
                SET
                    status = 'PENDING',
                    next_attempt_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?
                WHERE delivery_key = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (
                    _utc_iso(next_attempt_at),
                    error,
                    delivery_key,
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1

    def mark_outbound_dead(
        self,
        delivery_key: str,
        *,
        lease_owner: str,
        error: str,
    ) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE kakao_outbox
                SET
                    status = 'DEAD',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error = ?
                WHERE delivery_key = ?
                  AND status = 'SENDING'
                  AND lease_owner = ?
                """,
                (error, delivery_key, lease_owner),
            )
        return cursor.rowcount == 1

    def list_outbox(self) -> tuple[dict[str, object], ...]:
        """Return decoded outbox rows for sender adapters and focused tests."""

        rows = self._connection.execute(
            """
            SELECT
                delivery_key,
                room_id,
                message_type,
                payload_json,
                status,
                attempt_count,
                next_attempt_at,
                lease_owner,
                lease_expires_at,
                last_error,
                sent_at
            FROM kakao_outbox
            ORDER BY id
            """
        ).fetchall()
        return tuple(
            {
                "delivery_key": row["delivery_key"],
                "room_id": row["room_id"],
                "message_type": row["message_type"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
                "attempt_count": row["attempt_count"],
                "next_attempt_at": row["next_attempt_at"],
                "lease_owner": row["lease_owner"],
                "lease_expires_at": row["lease_expires_at"],
                "last_error": row["last_error"],
                "sent_at": row["sent_at"],
            }
            for row in rows
        )

    def database_settings(self) -> dict[str, object]:
        """Expose connection safety settings for diagnostics."""

        return {
            "journal_mode": self._connection.execute("PRAGMA journal_mode").fetchone()[
                0
            ],
            "foreign_keys": bool(
                self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            ),
            "busy_timeout": self._connection.execute("PRAGMA busy_timeout").fetchone()[
                0
            ],
        }

    def load_gateway_state(self) -> GatewayState:
        row = self._connection.execute(
            """
            SELECT session_id, sequence
            FROM kakao_gateway_state
            WHERE singleton_id = 1
            """
        ).fetchone()
        if row is None:
            return GatewayState()
        return GatewayState(
            session_id=row["session_id"],
            sequence=row["sequence"],
        )

    def save_gateway_state(self, state: GatewayState) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO kakao_gateway_state(
                    singleton_id, session_id, sequence, updated_at
                )
                VALUES (1, ?, ?, ?)
                ON CONFLICT(singleton_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    sequence = excluded.sequence,
                    updated_at = excluded.updated_at
                """,
                (state.session_id, state.sequence, _utc_iso(_utc_now())),
            )

    def clear_gateway_state(self) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM kakao_gateway_state WHERE singleton_id = 1"
            )

    def _fetch_room(self, room_id: str) -> Room | None:
        row = self._connection.execute(
            """
            SELECT room_id, approval_status
            FROM kakao_rooms
            WHERE room_id = ?
            """,
            (room_id,),
        ).fetchone()
        if row is None:
            return None
        return Room(
            room_id=row["room_id"],
            approval_status=ApprovalStatus(row["approval_status"]),
        )


class SQLiteGatewayStateStore(GatewayStatePort):
    """Async Gateway state port backed by the bot's dedicated SQLite DB."""

    def __init__(self, repository: SQLiteKakaoRepository) -> None:
        self._repository = repository

    async def load(self) -> GatewayState:
        return self._repository.load_gateway_state()

    async def save(self, state: GatewayState) -> None:
        self._repository.save_gateway_state(state)

    async def clear(self) -> None:
        self._repository.clear_gateway_state()
