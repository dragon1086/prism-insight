from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from prism_core.ops.job_runs import JobRunStore
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database


UTC = timezone.utc


def test_only_one_owner_can_hold_a_live_job_lease(tmp_path: Path) -> None:
    database_path = tmp_path / "ops.sqlite"
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

    with open_database(database_path) as first_connection:
        migrate_database(first_connection, DatabaseKind.OPS)
        first = JobRunStore(first_connection).acquire_lease(
            job_key="daily:kr:2026-07-26",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=5),
        )

    with open_database(database_path) as second_connection:
        second = JobRunStore(second_connection).acquire_lease(
            job_key="daily:kr:2026-07-26",
            owner_id="owner-b",
            now=now + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )

    assert first.acquired is True
    assert first.owner_id == "owner-a"
    assert second.acquired is False
    assert second.owner_id == "owner-a"
    assert second.lease_id == first.lease_id


def test_expired_job_lease_can_be_taken_over(tmp_path: Path) -> None:
    database_path = tmp_path / "ops.sqlite"
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

    with open_database(database_path) as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        first = store.acquire_lease(
            job_key="daily:us:2026-07-26",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=5),
        )
        replacement = store.acquire_lease(
            job_key="daily:us:2026-07-26",
            owner_id="owner-b",
            now=now + timedelta(minutes=5),
            lease_duration=timedelta(minutes=10),
        )

    assert replacement.acquired is True
    assert replacement.owner_id == "owner-b"
    assert replacement.lease_id != first.lease_id


def test_starting_the_same_run_twice_is_idempotent(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="daily:kr:2026-07-26",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=5),
        )

        first = store.start_run(
            run_id="run-daily-kr-2026-07-26",
            lease=lease,
            now=now,
            payload={"scheduled_for": "2026-07-26T11:00:00+00:00"},
        )
        duplicate = store.start_run(
            run_id="run-daily-kr-2026-07-26",
            lease=lease,
            now=now + timedelta(seconds=1),
            payload={"scheduled_for": "2026-07-26T11:00:00+00:00"},
        )

        count = connection.execute(
            "SELECT COUNT(*) FROM job_runs WHERE run_id = ?",
            (first.run_id,),
        ).fetchone()[0]

    assert duplicate == first
    assert count == 1


def test_heartbeat_renews_owned_lease_and_records_observation(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="daily:kr:2026-07-26",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=5),
        )
        run = store.start_run(
            run_id="run-daily-kr-2026-07-26",
            lease=lease,
            now=now,
            payload={},
        )

        renewed = store.heartbeat(
            run_id=run.run_id,
            lease=lease,
            observed_at=now + timedelta(minutes=4),
            lease_duration=timedelta(minutes=5),
            payload={"phase": "reports"},
        )
        heartbeat = connection.execute(
            "SELECT run_id, observed_at, payload_json FROM heartbeats"
        ).fetchone()
        stored_expiry = connection.execute(
            "SELECT expires_at FROM leases WHERE job_key = ?", (lease.job_key,)
        ).fetchone()[0]
        latest_heartbeat = store.last_heartbeat(lease.job_key)

    assert renewed.expires_at == now + timedelta(minutes=9)
    assert heartbeat == (
        run.run_id,
        (now + timedelta(minutes=4)).isoformat(),
        '{"phase":"reports"}',
    )
    assert stored_expiry == renewed.expires_at.isoformat()
    assert latest_heartbeat == now + timedelta(minutes=4)


def test_success_completion_records_last_success_and_releases_lease(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        lease = store.acquire_lease(
            job_key="daily",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=5),
        )
        run = store.start_run(
            run_id="daily:2026-07-26",
            lease=lease,
            now=now,
            payload={"scheduled_for": now.isoformat()},
        )
        finished_at = now + timedelta(minutes=2)

        completed = store.finish_run(
            run_id=run.run_id,
            lease=lease,
            finished_at=finished_at,
            succeeded=True,
        )

        assert store.last_success("daily") == finished_at
        assert connection.execute(
            "SELECT COUNT(*) FROM leases WHERE job_key = 'daily'"
        ).fetchone()[0] == 0

    assert completed.status == "SUCCESS"
    assert completed.finished_at == finished_at


def test_concurrent_claimers_choose_exactly_one_owner(tmp_path: Path) -> None:
    database_path = tmp_path / "ops.sqlite"
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(database_path) as connection:
        migrate_database(connection, DatabaseKind.OPS)
    barrier = Barrier(4)

    def claim(owner_id: str):
        barrier.wait()
        with open_database(database_path) as connection:
            return JobRunStore(connection).acquire_lease(
                job_key="daily",
                owner_id=owner_id,
                now=now,
                lease_duration=timedelta(minutes=5),
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(claim, ("owner-a", "owner-b", "owner-c", "owner-d")))

    winners = [claim for claim in claims if claim.acquired]
    assert len(winners) == 1
    assert {claim.lease_id for claim in claims} == {winners[0].lease_id}
    assert {claim.owner_id for claim in claims} == {winners[0].owner_id}


def test_expired_owner_cannot_write_after_lease_takeover(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        stale = store.acquire_lease(
            job_key="daily",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=1),
        )
        run = store.start_run(
            run_id="daily-attempt-1", lease=stale, now=now, payload={}
        )
        current = store.acquire_lease(
            job_key="daily",
            owner_id="owner-b",
            now=now + timedelta(minutes=1),
            lease_duration=timedelta(minutes=5),
        )

        with pytest.raises(RuntimeError, match="active unexpired lease owner"):
            store.heartbeat(
                run_id=run.run_id,
                lease=stale,
                observed_at=now + timedelta(minutes=1),
                lease_duration=timedelta(minutes=5),
                payload={},
            )

        assert current.acquired is True
        assert connection.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == 0


def test_lease_takeover_closes_the_abandoned_running_attempt(tmp_path: Path) -> None:
    now = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
    takeover_at = now + timedelta(minutes=2)
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        store = JobRunStore(connection)
        stale = store.acquire_lease(
            job_key="daily",
            owner_id="owner-a",
            now=now,
            lease_duration=timedelta(minutes=1),
        )
        store.start_run(
            run_id="daily-attempt-1", lease=stale, now=now, payload={}
        )

        replacement = store.acquire_lease(
            job_key="daily",
            owner_id="owner-b",
            now=takeover_at,
            lease_duration=timedelta(minutes=5),
        )
        abandoned = connection.execute(
            "SELECT status, finished_at FROM job_runs WHERE run_id = 'daily-attempt-1'"
        ).fetchone()

        assert replacement.acquired is True
        assert abandoned == ("ABANDONED", takeover_at.isoformat())
        assert store.latest_running_activity("daily") is None


@pytest.mark.parametrize("unsafe_job_key", ["daily\nforged", "../daily", "daily job"])
def test_job_identifiers_reject_control_path_and_space_syntax(
    tmp_path: Path, unsafe_job_key: str
) -> None:
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        with pytest.raises(ValueError, match="identifier syntax"):
            JobRunStore(connection).acquire_lease(
                job_key=unsafe_job_key,
                owner_id="owner-a",
                now=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
                lease_duration=timedelta(minutes=5),
            )
