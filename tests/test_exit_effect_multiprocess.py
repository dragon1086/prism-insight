import multiprocessing
import sqlite3
from datetime import datetime, timedelta, timezone

from prism_core.exit_effects import ExitEffectStore


INTENT_ID = "intent-multiprocess-1"
NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _seed(path):
    with sqlite3.connect(path) as connection:
        store = ExitEffectStore(connection)
        store.ensure_schema()
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        store.enqueue_exit_effects(
            intent_id=INTENT_ID,
            market="KR",
            account_id="vps:kr-primary:01",
            symbol="005930",
            source="kr_batch",
            payload={"event_id": INTENT_ID, "message": "sold"},
        )
        connection.commit()


def _claim_in_process(
    db_path,
    owner,
    now_iso,
    start_event,
    result_queue,
    finalize,
):
    now = datetime.fromisoformat(now_iso)
    start_event.wait(timeout=10)
    connection = sqlite3.connect(db_path, timeout=10)
    store = ExitEffectStore(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        effect = store.claim_effect(
            effect_id=f"{INTENT_ID}:redis",
            owner=owner,
            now=now,
            lease_seconds=1,
        )
        connection.commit()
        result_queue.put((owner, effect is not None))
        if effect is not None and finalize:
            connection.execute("BEGIN IMMEDIATE")
            store.mark_delivered(
                effect_id=effect["id"],
                owner=owner,
                remote_id=f"remote-{owner}",
                now=now,
            )
            connection.commit()
    finally:
        connection.close()


def _run_process(context, path, owner, now, start_event, result_queue, finalize):
    process = context.Process(
        target=_claim_in_process,
        args=(
            str(path),
            owner,
            now.isoformat(),
            start_event,
            result_queue,
            finalize,
        ),
    )
    process.start()
    return process


def test_two_os_processes_claim_exact_effect_once(tmp_path):
    db_path = tmp_path / "process-race.sqlite"
    _seed(db_path)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        _run_process(
            context,
            db_path,
            owner,
            NOW,
            start_event,
            result_queue,
            True,
        )
        for owner in ("process-a", "process-b")
    ]

    start_event.set()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    results = sorted(result_queue.get(timeout=2)[1] for _ in processes)

    with sqlite3.connect(db_path) as connection:
        row = ExitEffectStore(connection).get_effect(f"{INTENT_ID}:redis")
    assert results == [False, True]
    assert row["status"] == "DELIVERED"
    assert row["attempt_count"] == 1


def test_expired_lease_is_completed_by_restarted_process(tmp_path):
    db_path = tmp_path / "process-restart.sqlite"
    _seed(db_path)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()

    first_start = context.Event()
    first = _run_process(
        context,
        db_path,
        "crashed-process",
        NOW,
        first_start,
        result_queue,
        False,
    )
    first_start.set()
    first.join(timeout=15)
    assert first.exitcode == 0
    assert result_queue.get(timeout=2) == ("crashed-process", True)

    second_start = context.Event()
    second = _run_process(
        context,
        db_path,
        "restarted-process",
        NOW + timedelta(seconds=2),
        second_start,
        result_queue,
        True,
    )
    second_start.set()
    second.join(timeout=15)
    assert second.exitcode == 0
    assert result_queue.get(timeout=2) == ("restarted-process", True)

    with sqlite3.connect(db_path) as connection:
        row = ExitEffectStore(connection).get_effect(f"{INTENT_ID}:redis")
    assert row["status"] == "DELIVERED"
    assert row["attempt_count"] == 2
    assert row["remote_id"] == "remote-restarted-process"
