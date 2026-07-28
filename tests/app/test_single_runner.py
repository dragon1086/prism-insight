from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from prism_app.daily_pipeline import DailyRunRequest
from prism_app.single_runner import LeasedDailyPipeline, SingleRunnerUnavailable
from prism_core.ops.job_runs import JobRunStore
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


class RecordingPipeline:
    def __init__(self, result: object = "complete") -> None:
        self.calls: list[DailyRunRequest] = []
        self.result = result

    async def run(self, request: DailyRunRequest) -> object:
        self.calls.append(request)
        return self.result


def _request() -> DailyRunRequest:
    return DailyRunRequest(
        market=Market.KR,
        as_of_date=date(2026, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
        invocation_id="a" * 64,
    )


@pytest.mark.asyncio
async def test_leased_pipeline_owns_single_runner_and_records_success(tmp_path: Path) -> None:
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        inner = RecordingPipeline()
        wrapper = LeasedDailyPipeline(
            pipeline=inner,
            store=JobRunStore(connection),
            owner_id="uat-runner-1",
            clock=lambda: NOW,
            lease_duration=timedelta(minutes=5),
        )

        result = await wrapper.run(_request())

        row = connection.execute(
            "SELECT job_key, status, payload_json FROM job_runs "
            "WHERE job_key = ?",
            ("pipeline:" + _request().base_job_key,),
        ).fetchone()
        lease_count = connection.execute("SELECT count(*) FROM leases").fetchone()[0]

    assert result == "complete"
    assert inner.calls == [_request()]
    assert row[0] == "pipeline:" + _request().base_job_key
    assert row[1] == "SUCCESS"
    assert "invocation_id" in row[2]
    assert lease_count == 0


@pytest.mark.asyncio
async def test_leased_pipeline_fails_closed_when_another_runner_owns_invocation(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        request = _request()
        store.acquire_lease(
            job_key="pipeline:" + request.base_job_key,
            owner_id="other-runner",
            now=NOW,
            lease_duration=timedelta(minutes=5),
        )
        inner = RecordingPipeline()
        wrapper = LeasedDailyPipeline(
            pipeline=inner,
            store=store,
            owner_id="uat-runner-2",
            clock=lambda: NOW + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )

        with pytest.raises(SingleRunnerUnavailable, match="already owns"):
            await wrapper.run(request)

    assert inner.calls == []


@pytest.mark.asyncio
async def test_leased_pipeline_serializes_all_invocations_for_same_daily_run(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        request = _request()
        store.acquire_lease(
            job_key="pipeline:" + request.base_job_key,
            owner_id="other-runner",
            now=NOW,
            lease_duration=timedelta(minutes=5),
        )
        drifted = DailyRunRequest(
            market=request.market,
            as_of_date=request.as_of_date,
            run_type=request.run_type,
            evaluated_at=request.evaluated_at,
            invocation_id="b" * 64,
        )
        inner = RecordingPipeline()
        wrapper = LeasedDailyPipeline(
            pipeline=inner,
            store=store,
            owner_id="uat-runner-drifted",
            clock=lambda: NOW + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )

        with pytest.raises(SingleRunnerUnavailable, match="daily Phase 1 run"):
            await wrapper.run(drifted)

    assert inner.calls == []


@pytest.mark.asyncio
async def test_leased_pipeline_records_error_and_releases_lease(tmp_path: Path) -> None:
    class FailingPipeline:
        async def run(self, request: DailyRunRequest) -> object:
            raise ValueError("provider failed")

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        wrapper = LeasedDailyPipeline(
            pipeline=FailingPipeline(),
            store=JobRunStore(connection),
            owner_id="uat-runner-1",
            clock=lambda: NOW,
            lease_duration=timedelta(minutes=5),
        )
        with pytest.raises(ValueError, match="provider failed"):
            await wrapper.run(_request())
        status = connection.execute(
            "SELECT status FROM job_runs WHERE job_key = ?",
            ("pipeline:" + _request().base_job_key,),
        ).fetchone()[0]
        lease_count = connection.execute("SELECT count(*) FROM leases").fetchone()[0]

    assert status == "ERROR"
    assert lease_count == 0


@pytest.mark.asyncio
async def test_leased_pipeline_renews_lease_during_slow_run(tmp_path: Path) -> None:
    class SlowPipeline:
        async def run(self, request: DailyRunRequest) -> object:
            await asyncio.sleep(0.12)
            return "slow-complete"

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        wrapper = LeasedDailyPipeline(
            pipeline=SlowPipeline(),
            store=JobRunStore(connection),
            owner_id="uat-runner-slow",
            clock=lambda: datetime.now(timezone.utc),
            lease_duration=timedelta(seconds=0.06),
        )

        assert await wrapper.run(_request()) == "slow-complete"
        heartbeat_count = connection.execute(
            "SELECT count(*) FROM heartbeats"
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status FROM job_runs WHERE job_key = ?",
            ("pipeline:" + _request().base_job_key,),
        ).fetchone()[0]

    assert heartbeat_count >= 2
    assert status == "SUCCESS"


@pytest.mark.asyncio
async def test_lease_renewal_failure_preserves_wrapped_error_and_records_failure(
    tmp_path: Path,
) -> None:
    class SlowPipeline:
        async def run(self, request: DailyRunRequest) -> object:
            await asyncio.sleep(0.1)
            return "must-not-complete"

    class FailingHeartbeatStore(JobRunStore):
        def heartbeat(self, **kwargs):
            raise RuntimeError("injected heartbeat failure")

    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        wrapper = LeasedDailyPipeline(
            pipeline=SlowPipeline(),
            store=FailingHeartbeatStore(connection),
            owner_id="uat-runner-renewal-failure",
            clock=lambda: datetime.now(timezone.utc),
            lease_duration=timedelta(seconds=0.3),
        )

        with pytest.raises(
            RuntimeError, match="Phase 1 invocation lease renewal failed"
        ):
            await wrapper.run(_request())
        status = connection.execute(
            "SELECT status FROM job_runs WHERE job_key = ?",
            ("pipeline:" + _request().base_job_key,),
        ).fetchone()[0]

    assert status == "ERROR"
