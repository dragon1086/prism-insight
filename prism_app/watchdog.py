"""Fail-closed local job catch-up and health monitoring primitives."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Protocol
from uuid import uuid4

from prism_core.ops.job_runs import JobRunStore, LeaseClaim
from prism_core.telegram.publisher import PublicationStatus, TelegramPublisher


class CatchUpDecision(str, Enum):
    NOT_YET_DUE = "not_yet_due"
    ALREADY_SUCCEEDED = "already_succeeded"
    CATCH_UP = "catch_up"
    MISSED_WINDOW = "missed_window"


class CatchUpStatus(str, Enum):
    NOT_YET_DUE = "not_yet_due"
    ALREADY_SUCCEEDED = "already_succeeded"
    MISSED_WINDOW = "missed_window"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class JobExecution:
    run_id: str
    job_key: str
    scheduled_for: datetime


@dataclass(frozen=True)
class CatchUpJob:
    """One scheduled occurrence; its runner must not share the ops connection."""

    job_key: str
    owner_id: str
    scheduled_for: datetime
    catch_up_window: timedelta
    lease_duration: timedelta
    runner: Callable[[JobExecution], Coroutine[Any, Any, None]]

    def __post_init__(self) -> None:
        for field_name in ("job_key", "owner_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        _utc(self.scheduled_for, "scheduled_for")
        if (
            not isinstance(self.catch_up_window, timedelta)
            or self.catch_up_window < timedelta(0)
        ):
            raise ValueError("catch_up_window must be a non-negative timedelta")
        if (
            not isinstance(self.lease_duration, timedelta)
            or self.lease_duration <= timedelta(0)
        ):
            raise ValueError("lease_duration must be a positive timedelta")
        if not callable(self.runner):
            raise ValueError("runner must be callable")


@dataclass(frozen=True)
class CatchUpResult:
    job_key: str
    status: CatchUpStatus
    run_id: str | None


@dataclass(frozen=True)
class JobHealthCheck:
    """Heartbeat policy for a persistent worker, not a completed batch job."""

    job_key: str
    heartbeat_timeout: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.job_key, str) or not self.job_key.strip():
            raise ValueError("job_key must be a non-empty string")
        if (
            not isinstance(self.heartbeat_timeout, timedelta)
            or self.heartbeat_timeout <= timedelta(0)
        ):
            raise ValueError("heartbeat_timeout must be a positive timedelta")


@dataclass(frozen=True)
class MacOSNotification:
    title: str
    text: str


class MacOSNotifier(Protocol):
    async def notify(self, *, title: str, text: str) -> None: ...


class InMemoryMacOSNotifier:
    """Fixture default with no operating-system side effect."""

    def __init__(self) -> None:
        self.notifications: list[MacOSNotification] = []

    async def notify(self, *, title: str, text: str) -> None:
        self.notifications.append(MacOSNotification(title=title, text=text))


class OsascriptMacOSNotifier:
    """Opt-in local notification transport; constructing it does not activate it."""

    _SCRIPT = (
        "on run argv\n"
        "display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run"
    )

    def __init__(self, *, enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self._enabled = enabled

    async def notify(self, *, title: str, text: str) -> None:
        if not self._enabled:
            raise RuntimeError("macOS notifications require explicit activation")
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/osascript",
            "-e",
            self._SCRIPT,
            title[:128],
            text[:512],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await process.wait() != 0:
            raise RuntimeError("macOS notification delivery failed")


@dataclass(frozen=True)
class HealthCheckResult:
    job_key: str
    state: str
    alerted: bool
    telegram_succeeded: bool | None
    fallback_succeeded: bool | None


class Watchdog:
    """Persist health transitions before attempting bounded external alerts."""

    def __init__(
        self,
        *,
        store: JobRunStore,
        telegram_publisher: TelegramPublisher,
        macos_notifier: MacOSNotifier,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._telegram_publisher = telegram_publisher
        self._macos_notifier = macos_notifier
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def run_catch_up(
        self, job: CatchUpJob, *, now: datetime
    ) -> CatchUpResult:
        current = _utc(now, "now")
        decision = evaluate_catch_up(
            scheduled_for=job.scheduled_for,
            last_success=self._store.last_success(job.job_key),
            now=current,
            catch_up_window=job.catch_up_window,
        )
        if decision is not CatchUpDecision.CATCH_UP:
            return CatchUpResult(
                job_key=job.job_key,
                status=CatchUpStatus(decision.value),
                run_id=None,
            )

        lease = self._store.acquire_lease(
            job_key=job.job_key,
            owner_id=job.owner_id,
            now=current,
            lease_duration=job.lease_duration,
        )
        if not lease.acquired:
            return CatchUpResult(job.job_key, CatchUpStatus.LEASED, None)
        run_id = str(uuid4())
        run = self._store.start_run(
            run_id=run_id,
            lease=lease,
            now=current,
            payload={"scheduled_for": _utc(job.scheduled_for, "scheduled_for").isoformat()},
        )
        lease_holder = [lease]
        runner_task = asyncio.create_task(
            job.runner(
                JobExecution(
                    run_id=run.run_id,
                    job_key=job.job_key,
                    scheduled_for=_utc(job.scheduled_for, "scheduled_for"),
                )
            )
        )
        renewal_task = asyncio.create_task(
            self._renew_lease_until_cancelled(
                run_id=run.run_id,
                lease_holder=lease_holder,
                lease_duration=job.lease_duration,
                started_at=current,
            )
        )
        runner_failed = False
        try:
            done, _ = await asyncio.wait(
                {runner_task, renewal_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if renewal_task in done:
                renewal_error = renewal_task.exception()
                runner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runner_task
                raise RuntimeError("job lease renewal failed") from renewal_error
            runner_error = runner_task.exception()
            runner_failed = runner_error is not None
        except BaseException:
            runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await runner_task
            raise
        finally:
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task

        finished_at = max(current, _utc(self._clock(), "clock"))
        if runner_failed:
            self._store.finish_run(
                run_id=run.run_id,
                lease=lease_holder[0],
                finished_at=finished_at,
                succeeded=False,
            )
            return CatchUpResult(job.job_key, CatchUpStatus.FAILED, run.run_id)
        self._store.finish_run(
            run_id=run.run_id,
            lease=lease_holder[0],
            finished_at=finished_at,
            succeeded=True,
        )
        return CatchUpResult(job.job_key, CatchUpStatus.SUCCEEDED, run.run_id)

    async def _renew_lease_until_cancelled(
        self,
        *,
        run_id: str,
        lease_holder: list[LeaseClaim],
        lease_duration: timedelta,
        started_at: datetime,
    ) -> None:
        interval_seconds = lease_duration.total_seconds() / 3
        while True:
            await asyncio.sleep(interval_seconds)
            observed_at = max(started_at, _utc(self._clock(), "clock"))
            lease_holder[0] = self._store.heartbeat(
                run_id=run_id,
                lease=lease_holder[0],
                observed_at=observed_at,
                lease_duration=lease_duration,
                payload={"source": "watchdog"},
            )

    async def check_health(
        self, check: JobHealthCheck, *, now: datetime
    ) -> HealthCheckResult:
        current = _utc(now, "now")
        last_heartbeat = self._store.latest_running_activity(check.job_key)
        is_healthy = bool(
            last_heartbeat is not None
            and current - last_heartbeat <= check.heartbeat_timeout
            and last_heartbeat <= current
        )
        previous = self._store.latest_health_state(check.job_key)
        state = "RECOVERY" if is_healthy else "ERROR"
        should_alert = (not is_healthy and previous != "ERROR") or (
            is_healthy and previous == "ERROR"
        )
        if not should_alert:
            return HealthCheckResult(
                job_key=check.job_key,
                state="HEALTHY" if is_healthy else "ERROR",
                alerted=False,
                telegram_succeeded=None,
                fallback_succeeded=None,
            )

        message = (
            f"[{state}] {check.job_key} heartbeat "
            + ("recovered" if is_healthy else "is stale or missing")
        )
        alert = self._store.record_health_alert(
            job_key=check.job_key,
            state=state,
            message=message,
            created_at=current,
        )
        try:
            publication = await self._telegram_publisher.publish_report(
                report_job_id=f"ops-alert:{alert.alert_id}",
                request_id=f"ops-alert:{alert.alert_id}",
                text=message,
            )
            if publication.status not in {
                PublicationStatus.SENT,
                PublicationStatus.DUPLICATE,
            }:
                raise RuntimeError("Telegram alert was not delivered")
        except Exception:
            try:
                await self._macos_notifier.notify(title="PRISM watchdog", text=message)
            except Exception:
                fallback_succeeded = False
            else:
                fallback_succeeded = True
                self._store.record_health_delivery(
                    alert=alert,
                    channel="MACOS",
                    created_at=current,
                )
            return HealthCheckResult(
                job_key=check.job_key,
                state=state,
                alerted=True,
                telegram_succeeded=False,
                fallback_succeeded=fallback_succeeded,
            )
        self._store.record_health_delivery(
            alert=alert,
            channel="TELEGRAM",
            created_at=current,
        )
        return HealthCheckResult(
            job_key=check.job_key,
            state=state,
            alerted=True,
            telegram_succeeded=True,
            fallback_succeeded=None,
        )


def evaluate_catch_up(
    *,
    scheduled_for: datetime,
    last_success: datetime | None,
    now: datetime,
    catch_up_window: timedelta,
) -> CatchUpDecision:
    """Classify one scheduled occurrence after sleep or process restart."""

    scheduled = _utc(scheduled_for, "scheduled_for")
    current = _utc(now, "now")
    previous = None if last_success is None else _utc(last_success, "last_success")
    if not isinstance(catch_up_window, timedelta) or catch_up_window < timedelta(0):
        raise ValueError("catch_up_window must be a non-negative timedelta")
    if current < scheduled:
        return CatchUpDecision.NOT_YET_DUE
    if previous is not None and previous >= scheduled:
        return CatchUpDecision.ALREADY_SUCCEEDED
    if current - scheduled <= catch_up_window:
        return CatchUpDecision.CATCH_UP
    return CatchUpDecision.MISSED_WINDOW


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)
