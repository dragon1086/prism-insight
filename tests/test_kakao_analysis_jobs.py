from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import sqlite3

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import AnalysisJob, ApprovalStatus

NOW = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)


def make_job(
    *,
    job_id: str = "job-1",
    room_id: str = "room-1",
    user_id: str = "user-1",
    ticker: str = "005930",
    company_name: str = "Samsung Electronics",
    market: str = "kr",
) -> AnalysisJob:
    return AnalysisJob(
        job_id=job_id,
        room_id=room_id,
        user_id=user_id,
        ticker=ticker,
        company_name=company_name,
        market=market,
    )


def prepare_room(repository: SQLiteKakaoRepository, room_id: str = "room-1") -> None:
    repository.discover_room(room_id)
    repository.set_room_approval(room_id, ApprovalStatus.APPROVED)


def test_enqueue_is_idempotent_on_duplicate_job_id(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)

        assert repository.enqueue_analysis_job(make_job(), now=NOW) is True
        assert (
            repository.enqueue_analysis_job(
                make_job(company_name="Different Name"), now=NOW
            )
            is False
        )

        rows = repository.list_analysis_jobs()
        assert len(rows) == 1
        assert rows[0]["company_name"] == "Samsung Electronics"


def test_enqueue_with_missing_room_raises_integrity_error(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        with pytest.raises(sqlite3.IntegrityError):
            repository.enqueue_analysis_job(
                make_job(room_id="no-such-room"), now=NOW
            )


def test_claim_moves_pending_to_running_and_increments_attempt_count(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)

        [claimed] = repository.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)

        assert claimed.job_id == "job-1"
        assert claimed.attempt_count == 1
        [row] = repository.list_analysis_jobs()
        assert row["status"] == "RUNNING"
        assert row["attempt_count"] == 1
        assert row["lease_expires_at"] == (NOW + timedelta(seconds=30)).isoformat()


def test_active_lease_is_not_reclaimed_by_another_worker(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    with (
        SQLiteKakaoRepository(database_path) as first,
        SQLiteKakaoRepository(database_path) as second,
    ):
        prepare_room(first)
        first.enqueue_analysis_job(make_job(), now=NOW)

        [claimed] = first.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)
        assert claimed.job_id == "job-1"

        assert second.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10) == ()


def test_expired_lease_is_reclaimed(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    with (
        SQLiteKakaoRepository(database_path) as first,
        SQLiteKakaoRepository(database_path) as second,
    ):
        prepare_room(first)
        first.enqueue_analysis_job(make_job(), now=NOW)
        first.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)

        [reclaimed] = second.claim_analysis_jobs(
            now=NOW + timedelta(seconds=31), lease_seconds=30, limit=10
        )

        assert reclaimed.job_id == "job-1"
        assert reclaimed.attempt_count == 2


def test_claim_respects_limit_and_orders_by_requested_at(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        for index, offset in enumerate([2, 0, 1]):
            repository.enqueue_analysis_job(
                make_job(job_id=f"job-{index}"),
                now=NOW + timedelta(seconds=offset),
            )

        claimed = repository.claim_analysis_jobs(now=NOW + timedelta(seconds=10), lease_seconds=30, limit=2)

        assert [job.job_id for job in claimed] == ["job-1", "job-2"]


def test_complete_analysis_job_records_status_and_result(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)
        repository.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)

        completed_at = NOW + timedelta(seconds=5)
        repository.complete_analysis_job(
            "job-1",
            summary="Buy signal detected.",
            artifact_token="token-123",
            now=completed_at,
        )

        [row] = repository.list_analysis_jobs()
        assert row["status"] == "COMPLETED"
        assert row["summary"] == "Buy signal detected."
        assert row["artifact_token"] == "token-123"
        assert row["lease_expires_at"] is None
        assert row["completed_at"] == completed_at.isoformat()


def test_fail_analysis_job_records_status_and_error_code(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)
        repository.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)

        failed_at = NOW + timedelta(seconds=5)
        repository.fail_analysis_job("job-1", error_code="upstream_timeout", now=failed_at)

        [row] = repository.list_analysis_jobs()
        assert row["status"] == "FAILED"
        assert row["error_code"] == "upstream_timeout"
        assert row["lease_expires_at"] is None
        assert row["completed_at"] == failed_at.isoformat()


def test_release_makes_job_claimable_again(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        prepare_room(repository)
        repository.enqueue_analysis_job(make_job(), now=NOW)
        repository.claim_analysis_jobs(now=NOW, lease_seconds=30, limit=10)

        repository.release_analysis_job("job-1", now=NOW + timedelta(seconds=1))

        [row] = repository.list_analysis_jobs()
        assert row["status"] == "PENDING"
        assert row["lease_expires_at"] is None

        [reclaimed] = repository.claim_analysis_jobs(
            now=NOW + timedelta(seconds=2), lease_seconds=30, limit=10
        )
        assert reclaimed.job_id == "job-1"
        assert reclaimed.attempt_count == 2


def test_count_analysis_jobs_since_filters_by_room_and_user(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repository:
        repository.discover_room("room-1")
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
        repository.discover_room("room-2")
        repository.set_room_approval("room-2", ApprovalStatus.APPROVED)

        repository.enqueue_analysis_job(
            make_job(job_id="job-a", room_id="room-1", user_id="user-1"), now=NOW
        )
        repository.enqueue_analysis_job(
            make_job(job_id="job-b", room_id="room-1", user_id="user-2"), now=NOW
        )
        repository.enqueue_analysis_job(
            make_job(job_id="job-c", room_id="room-2", user_id="user-1"),
            now=NOW - timedelta(days=1),
        )

        since = NOW - timedelta(hours=1)
        assert (
            repository.count_analysis_jobs_since(
                room_id="room-1", user_id=None, since=since
            )
            == 2
        )
        assert (
            repository.count_analysis_jobs_since(
                room_id=None, user_id="user-1", since=since
            )
            == 1
        )
        assert (
            repository.count_analysis_jobs_since(
                room_id="room-1", user_id="user-1", since=since
            )
            == 1
        )
        assert (
            repository.count_analysis_jobs_since(
                room_id=None, user_id=None, since=since
            )
            == 2
        )


def test_concurrent_claim_never_double_claims_a_job(tmp_path):
    database_path = tmp_path / "kakao.sqlite"
    job_count = 20
    worker_count = 5

    with SQLiteKakaoRepository(database_path) as setup:
        prepare_room(setup)
        for index in range(job_count):
            assert setup.enqueue_analysis_job(
                make_job(job_id=f"job-{index}"), now=NOW
            )

    def claim_all_available() -> list[str]:
        claimed: list[str] = []
        with SQLiteKakaoRepository(database_path) as repository:
            while True:
                jobs = repository.claim_analysis_jobs(
                    now=NOW, lease_seconds=30, limit=1
                )
                if not jobs:
                    break
                claimed.append(jobs[0].job_id)
        return claimed

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(
            executor.map(lambda _: claim_all_available(), range(worker_count))
        )

    all_claimed = [job_id for worker_result in results for job_id in worker_result]
    assert len(all_claimed) == job_count
    assert len(set(all_claimed)) == job_count
