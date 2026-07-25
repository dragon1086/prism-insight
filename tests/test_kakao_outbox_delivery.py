from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from kakao_bot.adapters.kakao.skill_response import simple_text
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.delivery_service import OutboxDeliveryService
from kakao_bot.domain.models import (
    ApprovalStatus,
    MessageSendResult,
    OutboundDelivery,
)

NOW = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)


class FakeSender:
    def __init__(self, *results: MessageSendResult):
        self.results = list(results)
        self.calls = []

    async def send_message(self, room_id, skill_response):
        self.calls.append((room_id, skill_response))
        return self.results.pop(0)


def enqueue(
    repository: SQLiteKakaoRepository,
    *,
    delivery_key: str = "delivery-1",
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> None:
    repository.discover_room("room-1")
    repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
    assert repository.enqueue_outbound(
        OutboundDelivery(
            delivery_key=delivery_key,
            room_id="room-1",
            message_type="test",
            payload=simple_text("안녕하세요."),
            created_at=created_at,
            expires_at=expires_at,
        )
    )


def render_stored_skill_response(delivery):
    return dict(delivery.payload)


def test_claim_is_exclusive_and_expired_lease_can_be_reclaimed(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    with (
        SQLiteKakaoRepository(database_path) as first,
        SQLiteKakaoRepository(database_path) as second,
    ):
        enqueue(first)

        [first_claim] = first.claim_outbound(
            lease_owner="worker-1",
            now=NOW,
            lease_seconds=30,
            limit=1,
        )
        assert (
            second.claim_outbound(
                lease_owner="worker-2",
                now=NOW,
                lease_seconds=30,
                limit=1,
            )
            == ()
        )

        [reclaimed] = second.claim_outbound(
            lease_owner="worker-2",
            now=NOW + timedelta(seconds=31),
            lease_seconds=30,
            limit=1,
        )

        assert first_claim.attempt_count == 1
        assert reclaimed.attempt_count == 2
        assert reclaimed.lease_owner == "worker-2"
        assert not first.mark_outbound_sent(
            "delivery-1",
            lease_owner="worker-1",
            sent_at=NOW + timedelta(seconds=31),
        )
        assert second.mark_outbound_sent(
            "delivery-1",
            lease_owner="worker-2",
            sent_at=NOW + timedelta(seconds=31),
        )


def test_v1_database_is_migrated_with_delivery_state_columns(tmp_path):
    database_path = tmp_path / "kakao-v1.sqlite"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version, applied_at)
        VALUES (1, '2026-07-23T00:00:00+00:00');

        CREATE TABLE kakao_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_key TEXT NOT NULL UNIQUE,
            room_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING', 'SENDING', 'SENT', 'DEAD')),
            created_at TEXT NOT NULL,
            expires_at TEXT
        );
        """
    )
    connection.close()

    with SQLiteKakaoRepository(database_path) as repository:
        assert repository.list_outbox() == ()

    connection = sqlite3.connect(database_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(kakao_outbox)")}
    versions = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    connection.close()

    assert {
        "attempt_count",
        "next_attempt_at",
        "lease_owner",
        "lease_expires_at",
        "last_error",
        "sent_at",
    } <= columns
    assert versions == {1, 2, 3}


def test_concurrent_initialization_serializes_migrations(tmp_path):
    database_path = tmp_path / "concurrent.sqlite"

    def initialize():
        with SQLiteKakaoRepository(database_path) as repository:
            return repository.database_settings()["journal_mode"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: initialize(), range(8)))

    assert results == ["wal"] * 8
    connection = sqlite3.connect(database_path)
    versions = {
        row[0] for row in connection.execute("SELECT version FROM schema_migrations")
    }
    connection.close()
    assert versions == {1, 2, 3}


def test_expired_pending_delivery_is_marked_dead_without_claim(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository, expires_at=NOW)

        assert (
            repository.claim_outbound(
                lease_owner="worker-1",
                now=NOW,
                lease_seconds=30,
                limit=1,
            )
            == ()
        )

        [row] = repository.list_outbox()
        assert row["status"] == "DEAD"
        assert row["last_error"] == "delivery expired"


def test_revoked_room_cannot_enqueue_or_claim_delivery(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)

        repository.set_room_approval("room-1", ApprovalStatus.REJECTED)

        assert (
            repository.enqueue_outbound(
                OutboundDelivery(
                    delivery_key="delivery-after-revocation",
                    room_id="room-1",
                    message_type="test",
                    payload=simple_text("발송되면 안 됩니다."),
                    created_at=NOW,
                )
            )
            is False
        )
        assert (
            repository.claim_outbound(
                lease_owner="worker-1",
                now=NOW,
                lease_seconds=30,
                limit=10,
            )
            == ()
        )
        [revoked] = repository.list_outbox()
        assert revoked["status"] == "DEAD"
        assert revoked["last_error"] == "room approval revoked"


@pytest.mark.asyncio
async def test_success_is_marked_sent_and_not_claimed_again(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)
        sender = FakeSender(MessageSendResult(success=True, status_code=200))
        service = OutboxDeliveryService(
            repository,
            sender,
            render_stored_skill_response,
            lease_owner="worker-1",
        )

        first = await service.run_once(now=NOW)
        second = await service.run_once(now=NOW)

        assert first.sent == 1
        assert second.claimed == 0
        assert len(sender.calls) == 1
        [row] = repository.list_outbox()
        assert row["status"] == "SENT"
        assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_retryable_failure_is_released_with_backoff(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)
        sender = FakeSender(
            MessageSendResult(
                success=False,
                status_code=503,
                error_code="server_error",
                retryable=True,
            ),
            MessageSendResult(success=True, status_code=200),
        )
        service = OutboxDeliveryService(
            repository,
            sender,
            render_stored_skill_response,
            lease_owner="worker-1",
            retry_base_seconds=30,
        )

        failed = await service.run_once(now=NOW)
        too_early = await service.run_once(now=NOW + timedelta(seconds=29))
        retried = await service.run_once(now=NOW + timedelta(seconds=30))

        assert failed.retry_scheduled == 1
        assert too_early.claimed == 0
        assert retried.sent == 1
        assert len(sender.calls) == 2
        [row] = repository.list_outbox()
        assert row["status"] == "SENT"
        assert row["attempt_count"] == 2


@pytest.mark.asyncio
async def test_nonretryable_or_exhausted_delivery_is_dead(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)
        sender = FakeSender(
            MessageSendResult(
                success=False,
                status_code=400,
                error_code="http_400",
                error_message="bad request",
                retryable=False,
            )
        )
        service = OutboxDeliveryService(
            repository,
            sender,
            render_stored_skill_response,
            lease_owner="worker-1",
        )

        result = await service.run_once(now=NOW)

        assert result.dead == 1
        [row] = repository.list_outbox()
        assert row["status"] == "DEAD"
        assert "bad request" in row["last_error"]


@pytest.mark.asyncio
async def test_retryable_delivery_becomes_dead_at_bounded_attempt_limit(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)
        failure = MessageSendResult(
            success=False,
            status_code=503,
            error_code="server_error",
            retryable=True,
        )
        sender = FakeSender(failure, failure)
        service = OutboxDeliveryService(
            repository,
            sender,
            render_stored_skill_response,
            lease_owner="worker-1",
            max_attempts=2,
            retry_base_seconds=1,
        )

        first = await service.run_once(now=NOW)
        second = await service.run_once(now=NOW + timedelta(seconds=1))

        assert first.retry_scheduled == 1
        assert second.dead == 1
        assert len(sender.calls) == 2
        [row] = repository.list_outbox()
        assert row["status"] == "DEAD"
        assert row["attempt_count"] == 2


@pytest.mark.asyncio
async def test_ambiguous_post_is_not_duplicated_in_same_run_or_immediately(
    tmp_path,
):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        enqueue(repository)
        sender = FakeSender(
            MessageSendResult(
                success=False,
                error_code="ambiguous_network_error",
                retryable=True,
                ambiguous=True,
            )
        )
        service = OutboxDeliveryService(
            repository,
            sender,
            render_stored_skill_response,
            lease_owner="worker-1",
            ambiguous_retry_seconds=300,
        )

        failed = await service.run_once(now=NOW)
        immediate = await service.run_once(now=NOW)

        assert failed.retry_scheduled == 1
        assert immediate.claimed == 0
        assert len(sender.calls) == 1
        [row] = repository.list_outbox()
        assert row["status"] == "PENDING"
        assert row["next_attempt_at"] == (NOW + timedelta(seconds=300)).isoformat()
