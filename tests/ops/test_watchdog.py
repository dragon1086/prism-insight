from __future__ import annotations

import asyncio
import plistlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prism_app.watchdog import (
    CatchUpDecision,
    CatchUpJob,
    CatchUpStatus,
    InMemoryMacOSNotifier,
    JobHealthCheck,
    Watchdog,
    evaluate_catch_up,
)
from prism_core.ops.job_runs import JobRunStore
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.telegram.config import TelegramConfig
from prism_core.telegram.publisher import TelegramPublisher


UTC = timezone.utc


def test_missed_run_inside_window_is_selected_for_catch_up() -> None:
    scheduled_for = datetime(2026, 7, 26, 10, 0, tzinfo=UTC)

    decision = evaluate_catch_up(
        scheduled_for=scheduled_for,
        last_success=scheduled_for - timedelta(days=1),
        now=scheduled_for + timedelta(hours=2),
        catch_up_window=timedelta(hours=4),
    )

    assert decision is CatchUpDecision.CATCH_UP


class FailingTelegramTransport:
    def __init__(self) -> None:
        self.chat_ids: list[str] = []

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.chat_ids.append(chat_id)
        raise OSError("offline")


class DenyRateLimiter:
    def allow(self, scope: str) -> bool:
        return False


class FailingMacOSNotifier:
    async def notify(self, *, title: str, text: str) -> None:
        raise OSError("notifications unavailable")


@pytest.mark.asyncio
async def test_rate_limited_telegram_alert_falls_back_to_macos(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        notifier = InMemoryMacOSNotifier()
        watchdog = Watchdog(
            store=JobRunStore(connection),
            telegram_publisher=TelegramPublisher(
                TelegramConfig(
                    enabled=True,
                    bot_token="test-token",
                    allowed_chat_id="chat-1",
                    allowed_user_id="user-1",
                ),
                rate_limiter=DenyRateLimiter(),
            ),
            macos_notifier=notifier,
        )

        result = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram", heartbeat_timeout=timedelta(minutes=5)
            ),
            now=now,
        )

        delivery_count = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_kind = 'JOB_HEALTH_DELIVERY'"
        ).fetchone()[0]

    assert result.telegram_succeeded is False
    assert result.fallback_succeeded is True
    assert len(notifier.notifications) == 1
    assert delivery_count == 1


@pytest.mark.asyncio
async def test_undelivered_error_is_retried_on_next_watchdog_tick(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        watchdog = Watchdog(
            store=store,
            telegram_publisher=TelegramPublisher(
                TelegramConfig(
                    enabled=True,
                    bot_token="test-token",
                    allowed_chat_id="chat-1",
                    allowed_user_id="user-1",
                ),
                transport=FailingTelegramTransport(),
            ),
            macos_notifier=FailingMacOSNotifier(),
        )

        first = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram", heartbeat_timeout=timedelta(minutes=5)
            ),
            now=now,
        )
        second = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram", heartbeat_timeout=timedelta(minutes=5)
            ),
            now=now + timedelta(minutes=1),
        )

        transition_count = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_kind = 'JOB_HEALTH'"
        ).fetchone()[0]
        delivery_count = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_kind = 'JOB_HEALTH_DELIVERY'"
        ).fetchone()[0]

    assert first.alerted is True
    assert first.fallback_succeeded is False
    assert second.alerted is True
    assert second.fallback_succeeded is False
    assert transition_count == 2
    assert delivery_count == 0


@pytest.mark.asyncio
async def test_new_running_job_is_healthy_before_first_heartbeat(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="telegram",
            owner_id="telegram-worker",
            now=now - timedelta(minutes=1),
            lease_duration=timedelta(minutes=10),
        )
        store.start_run(
            run_id="telegram-worker-1",
            lease=lease,
            now=now - timedelta(minutes=1),
            payload={},
        )
        watchdog = Watchdog(
            store=store,
            telegram_publisher=TelegramPublisher(
                TelegramConfig(
                    enabled=True,
                    bot_token="test-token",
                    allowed_chat_id="chat-1",
                    allowed_user_id="user-1",
                )
            ),
            macos_notifier=InMemoryMacOSNotifier(),
        )

        result = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram", heartbeat_timeout=timedelta(minutes=5)
            ),
            now=now,
        )

        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0

    assert result.state == "HEALTHY"
    assert result.alerted is False


@pytest.mark.asyncio
async def test_stale_heartbeat_persists_error_and_falls_back_to_macos(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="telegram",
            owner_id="telegram-worker",
            now=now - timedelta(minutes=20),
            lease_duration=timedelta(minutes=30),
        )
        run = store.start_run(
            run_id="telegram-worker-1",
            lease=lease,
            now=now - timedelta(minutes=20),
            payload={},
        )
        store.heartbeat(
            run_id=run.run_id,
            lease=lease,
            observed_at=now - timedelta(minutes=10),
            lease_duration=timedelta(minutes=30),
            payload={},
        )
        telegram = FailingTelegramTransport()
        publisher = TelegramPublisher(
            TelegramConfig(
                enabled=True,
                bot_token="test-token",
                allowed_chat_id="chat-1",
                allowed_user_id="user-1",
            ),
            transport=telegram,
        )
        notifier = InMemoryMacOSNotifier()
        watchdog = Watchdog(
            store=store,
            telegram_publisher=publisher,
            macos_notifier=notifier,
        )

        result = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram",
                heartbeat_timeout=timedelta(minutes=5),
            ),
            now=now,
        )
        repeated = await watchdog.check_health(
            JobHealthCheck(
                job_key="telegram",
                heartbeat_timeout=timedelta(minutes=5),
            ),
            now=now + timedelta(minutes=1),
        )

        assert store.latest_health_state("telegram") == "ERROR"
        alert_count = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_kind = 'JOB_HEALTH'"
        ).fetchone()[0]
        delivery_count = connection.execute(
            "SELECT COUNT(*) FROM alerts WHERE alert_kind = 'JOB_HEALTH_DELIVERY'"
        ).fetchone()[0]

    assert result.state == "ERROR"
    assert result.alerted is True
    assert repeated.alerted is False
    assert telegram.chat_ids == ["chat-1"]
    assert len(notifier.notifications) == 1
    assert notifier.notifications[0].text.startswith("[ERROR]")
    assert alert_count == 1
    assert delivery_count == 1


@pytest.mark.asyncio
async def test_fresh_heartbeat_after_error_sends_recovery_to_same_chat(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="telegram",
            owner_id="telegram-worker",
            now=now - timedelta(minutes=20),
            lease_duration=timedelta(hours=1),
        )
        run = store.start_run(
            run_id="telegram-worker-1",
            lease=lease,
            now=now - timedelta(minutes=20),
            payload={},
        )
        renewed = store.heartbeat(
            run_id=run.run_id,
            lease=lease,
            observed_at=now - timedelta(minutes=10),
            lease_duration=timedelta(hours=1),
            payload={},
        )
        publisher = TelegramPublisher(
            TelegramConfig(
                enabled=True,
                bot_token="test-token",
                allowed_chat_id="chat-1",
                allowed_user_id="user-1",
            )
        )
        watchdog = Watchdog(
            store=store,
            telegram_publisher=publisher,
            macos_notifier=InMemoryMacOSNotifier(),
        )
        check = JobHealthCheck(
            job_key="telegram", heartbeat_timeout=timedelta(minutes=5)
        )
        await watchdog.check_health(check, now=now)

        store.heartbeat(
            run_id=run.run_id,
            lease=renewed,
            observed_at=now + timedelta(minutes=1),
            lease_duration=timedelta(hours=1),
            payload={},
        )
        recovery = await watchdog.check_health(
            check, now=now + timedelta(minutes=1)
        )

        assert store.latest_health_state("telegram") == "RECOVERY"

    assert recovery.state == "RECOVERY"
    assert recovery.alerted is True
    assert [message.chat_id for message in publisher.fake_transport.sent] == [
        "chat-1",
        "chat-1",
    ]
    assert publisher.fake_transport.sent[1].text.startswith("[RECOVERY]")


@pytest.mark.asyncio
async def test_missed_run_is_caught_up_once_after_wake(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    calls: list[str] = []

    async def runner(execution) -> None:
        calls.append(execution.job_key)

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        publisher = TelegramPublisher(
            TelegramConfig(
                enabled=True,
                bot_token="test-token",
                allowed_chat_id="chat-1",
                allowed_user_id="user-1",
            )
        )
        watchdog = Watchdog(
            store=store,
            telegram_publisher=publisher,
            macos_notifier=InMemoryMacOSNotifier(),
            clock=lambda: now,
        )
        job = CatchUpJob(
            job_key="daily",
            owner_id="watchdog-1",
            scheduled_for=now - timedelta(hours=2),
            catch_up_window=timedelta(hours=4),
            lease_duration=timedelta(minutes=30),
            runner=runner,
        )

        first = await watchdog.run_catch_up(job, now=now)
        duplicate = await watchdog.run_catch_up(job, now=now + timedelta(minutes=1))

        assert store.last_success("daily") == now

    assert first.status is CatchUpStatus.SUCCEEDED
    assert duplicate.status is CatchUpStatus.ALREADY_SUCCEEDED
    assert calls == ["daily"]


@pytest.mark.asyncio
async def test_failed_catch_up_releases_lease_for_a_later_retry(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    attempts = 0

    async def runner(execution) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fixture failure")

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        watchdog = Watchdog(
            store=store,
            telegram_publisher=TelegramPublisher(
                TelegramConfig(
                    enabled=True,
                    bot_token="test-token",
                    allowed_chat_id="chat-1",
                    allowed_user_id="user-1",
                )
            ),
            macos_notifier=InMemoryMacOSNotifier(),
        )
        job = CatchUpJob(
            job_key="daily",
            owner_id="watchdog-1",
            scheduled_for=now - timedelta(hours=1),
            catch_up_window=timedelta(hours=4),
            lease_duration=timedelta(minutes=5),
            runner=runner,
        )

        failed = await watchdog.run_catch_up(job, now=now)
        retried = await watchdog.run_catch_up(job, now=now + timedelta(minutes=1))
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM job_runs ORDER BY created_at, rowid"
            )
        ]

    assert failed.status is CatchUpStatus.FAILED
    assert retried.status is CatchUpStatus.SUCCEEDED
    assert statuses == ["ERROR", "SUCCESS"]


@pytest.mark.asyncio
async def test_long_catch_up_renews_lease_and_blocks_second_runner(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    calls: list[str] = []

    async def runner(execution) -> None:
        calls.append(execution.run_id)
        await asyncio.sleep(0.12)

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        publisher = TelegramPublisher(
            TelegramConfig(
                enabled=True,
                bot_token="test-token",
                allowed_chat_id="chat-1",
                allowed_user_id="user-1",
            )
        )
        first_watchdog = Watchdog(
            store=store,
            telegram_publisher=publisher,
            macos_notifier=InMemoryMacOSNotifier(),
        )
        second_watchdog = Watchdog(
            store=store,
            telegram_publisher=publisher,
            macos_notifier=InMemoryMacOSNotifier(),
        )
        first_job = CatchUpJob(
            job_key="daily",
            owner_id="watchdog-1",
            scheduled_for=now - timedelta(seconds=1),
            catch_up_window=timedelta(minutes=5),
            lease_duration=timedelta(milliseconds=50),
            runner=runner,
        )
        second_job = CatchUpJob(
            job_key="daily",
            owner_id="watchdog-2",
            scheduled_for=first_job.scheduled_for,
            catch_up_window=first_job.catch_up_window,
            lease_duration=first_job.lease_duration,
            runner=runner,
        )

        first_task = asyncio.create_task(first_watchdog.run_catch_up(first_job, now=now))
        await asyncio.sleep(0.07)
        second = await second_watchdog.run_catch_up(
            second_job, now=datetime.now(UTC)
        )
        first = await first_task
        heartbeat_count = connection.execute(
            "SELECT COUNT(*) FROM heartbeats"
        ).fetchone()[0]

    assert first.status is CatchUpStatus.SUCCEEDED
    assert second.status is CatchUpStatus.LEASED
    assert len(calls) == 1
    assert heartbeat_count >= 1


def test_launchd_templates_define_bounded_supervision_without_secrets() -> None:
    root = Path(__file__).parents[2] / "ops" / "launchd"
    expected = {
        "com.prism.daily.plist.template": "com.prism.daily",
        "com.prism.telegram.plist.template": "com.prism.telegram",
        "com.prism.watchdog.plist.template": "com.prism.watchdog",
    }

    loaded = {}
    for filename, label in expected.items():
        raw = (root / filename).read_bytes()
        text = raw.decode("utf-8")
        assert "INERT TEMPLATE" in text
        assert "TELEGRAM_BOT_TOKEN" not in text
        assert "TELEGRAM_ALLOWED_CHAT_ID" not in text
        assert "trading" not in text.lower()
        loaded[label] = plistlib.loads(raw)
        assert loaded[label]["Label"] == label
        assert loaded[label]["ProcessType"] == "Background"

    assert "StartCalendarInterval" in loaded["com.prism.daily"]
    assert loaded["com.prism.telegram"]["KeepAlive"] is True
    assert loaded["com.prism.telegram"]["RunAtLoad"] is True
    assert loaded["com.prism.watchdog"]["RunAtLoad"] is True
    assert loaded["com.prism.watchdog"]["StartInterval"] == 300


def test_ops_suite_is_explicitly_enforced_by_ci() -> None:
    workflow = (Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "python -m pytest tests/ops -q" in workflow
