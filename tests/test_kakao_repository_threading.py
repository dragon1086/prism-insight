"""The repository must survive being used from a thread it was not born in.

Runtimes hand blocking work to `asyncio.to_thread`, and consecutive calls can
land on different pool threads. A connection pinned to its creating thread
raises `sqlite3.ProgrammingError` at runtime while every unit test passes,
because tests call the repository directly on the test thread.

That is exactly what happened on 2026-07-28: `/report` was silently dropped in
a live chatroom while `도움말`, which returns before touching the database,
worked fine.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import AnalysisJob, ApprovalStatus

NOW = datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc)
ROOM = "room-1"


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao.sqlite") as repo:
        repo.discover_room(ROOM, discovered_at=NOW)
        repo.set_room_approval(ROOM, ApprovalStatus.APPROVED)
        yield repo


def job(job_id: str) -> AnalysisJob:
    return AnalysisJob(
        job_id=job_id,
        room_id=ROOM,
        user_id="user-1",
        ticker="005930",
        company_name="삼성전자",
        market="kr",
    )


def test_reads_work_from_another_thread(repository):
    result: dict[str, object] = {}

    def read() -> None:
        result["thread"] = threading.get_ident()
        result["room"] = repository.get_room(ROOM)

    worker = threading.Thread(target=read)
    worker.start()
    worker.join()

    assert result["thread"] != threading.get_ident()
    assert result["room"].approval_status is ApprovalStatus.APPROVED


def test_writes_work_from_another_thread(repository):
    worker = threading.Thread(
        target=lambda: repository.enqueue_analysis_job(job("job-1"), now=NOW)
    )
    worker.start()
    worker.join()

    assert len(repository.list_analysis_jobs()) == 1


@pytest.mark.asyncio
async def test_asyncio_to_thread_matches_the_runtime_call_shape(repository):
    """Reproduces how the analysis worker actually drives the repository."""

    await asyncio.to_thread(repository.enqueue_analysis_job, job("job-1"), now=NOW)
    claimed = await asyncio.to_thread(
        repository.claim_analysis_jobs, now=NOW, lease_seconds=900, limit=1
    )

    assert [c.job_id for c in claimed] == ["job-1"]


@pytest.mark.asyncio
async def test_consecutive_to_thread_calls_may_use_different_threads(repository):
    """The failure mode was a *different* pool thread, not merely a non-main one."""

    seen: set[int] = set()

    def touch(job_id: str) -> None:
        seen.add(threading.get_ident())
        repository.enqueue_analysis_job(job(job_id), now=NOW)

    for index in range(6):
        await asyncio.to_thread(touch, f"job-{index}")

    assert len(repository.list_analysis_jobs()) == 6
    # Not asserting len(seen) > 1: the pool may reuse one thread. What matters
    # is that none of the calls raised.
    assert seen


def test_a_thread_pinned_connection_would_have_failed(tmp_path):
    """Guard the guard: prove this test file can detect the original bug."""

    path = tmp_path / "pinned.sqlite"
    SQLiteKakaoRepository(path).close()
    pinned = sqlite3.connect(path)  # check_same_thread defaults to True
    error: list[BaseException] = []

    def read() -> None:
        try:
            pinned.execute("SELECT 1").fetchone()
        except BaseException as exc:  # noqa: BLE001
            error.append(exc)

    worker = threading.Thread(target=read)
    worker.start()
    worker.join()
    pinned.close()

    assert error and isinstance(error[0], sqlite3.ProgrammingError)
