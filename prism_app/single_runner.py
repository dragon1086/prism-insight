"""Explicit inert single-runner composition for Phase 1 application invocations."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from prism_app.daily_pipeline import DailyRunRequest
from prism_core.ops.job_runs import JobRunStore


class RunnableDailyPipeline(Protocol):
    async def run(self, request: DailyRunRequest) -> Any: ...


class SingleRunnerUnavailable(RuntimeError):
    """Fail-closed signal that another process owns the exact invocation."""


class LeasedDailyPipeline:
    """Acquire the existing ops lease before entering a daily pipeline.

    This wrapper is composition only. It neither schedules nor activates a job.
    """

    def __init__(
        self,
        *,
        pipeline: RunnableDailyPipeline,
        store: JobRunStore,
        owner_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lease_duration: timedelta = timedelta(minutes=10),
    ) -> None:
        if not isinstance(store, JobRunStore):
            raise TypeError("store must be JobRunStore")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be non-empty")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        self._pipeline = pipeline
        self._store = store
        self._owner_id = owner_id
        self._clock = clock
        self._lease_duration = lease_duration

    async def run(self, request: DailyRunRequest) -> Any:
        if not isinstance(request, DailyRunRequest):
            raise TypeError("request must be DailyRunRequest")
        # One market/date/run-type lease prevents provenance drift from creating
        # concurrent runners. The full invocation remains in the immutable payload.
        job_key = f"pipeline:{request.base_job_key}"
        started_at = self._clock()
        lease = self._store.acquire_lease(
            job_key=job_key,
            owner_id=self._owner_id,
            now=started_at,
            lease_duration=self._lease_duration,
        )
        if not lease.acquired:
            raise SingleRunnerUnavailable(
                "another runner already owns this daily Phase 1 run"
            )
        run = self._store.start_run(
            run_id=str(uuid4()),
            lease=lease,
            now=started_at,
            payload={
                "base_job_key": request.base_job_key,
                "invocation_id": request.invocation_id,
                "market": request.market.value,
                "run_type": request.run_type,
            },
        )
        lease_holder = [lease]
        runner_task = asyncio.create_task(self._pipeline.run(request))
        renewal_task = asyncio.create_task(
            self._renew_lease(
                run_id=run.run_id,
                lease_holder=lease_holder,
            )
        )
        try:
            done, _ = await asyncio.wait(
                {runner_task, renewal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if renewal_task in done:
                renewal_error = renewal_task.exception()
                runner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runner_task
                raise RuntimeError("Phase 1 invocation lease renewal failed") from renewal_error
            result = runner_task.result()
        except BaseException:
            if not runner_task.done():
                runner_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runner_task
            if not renewal_task.done():
                renewal_task.cancel()
                with suppress(asyncio.CancelledError):
                    await renewal_task
            else:
                # The renewal exception was already captured as the cause above.
                with suppress(Exception):
                    await renewal_task
            try:
                self._store.finish_run(
                    run_id=run.run_id,
                    lease=lease_holder[0],
                    finished_at=max(started_at, self._clock()),
                    succeeded=False,
                )
            except RuntimeError:
                pass  # preserve the original failure; expired ownership is fail-closed
            raise
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
        self._store.finish_run(
            run_id=run.run_id,
            lease=lease_holder[0],
            finished_at=max(started_at, self._clock()),
            succeeded=True,
        )
        return result

    async def _renew_lease(
        self,
        *,
        run_id: str,
        lease_holder: list,
    ) -> None:
        interval = self._lease_duration.total_seconds() / 3
        while True:
            await asyncio.sleep(interval)
            lease_holder[0] = self._store.heartbeat(
                run_id=run_id,
                lease=lease_holder[0],
                observed_at=self._clock(),
                lease_duration=self._lease_duration,
                payload={"source": "phase1-product-composition"},
            )
