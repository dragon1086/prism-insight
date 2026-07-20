import asyncio
import sqlite3
from datetime import datetime, timezone

import pytest

from prism_core.exit_effect_replay import (
    deliver_exit_effect_once,
    run_exit_effect_replay,
)
from prism_core.exit_effects import ExitEffectStore


INTENT_ID = "intent-replay-1"
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
            payload={
                "version": 1,
                "event_id": INTENT_ID,
                "market": "KR",
                "source": "kr_batch",
                "account_id": "vps:kr-primary:01",
                "symbol": "005930",
                "message": "sold",
            },
        )
        connection.commit()


def _rows(path):
    with sqlite3.connect(path) as connection:
        return ExitEffectStore(connection).list_for_intent(INTENT_ID)


@pytest.mark.asyncio
async def test_replay_rejects_missing_database_without_creating_it(tmp_path):
    db_path = tmp_path / "missing.sqlite"

    async def journal(_payload):
        return True

    with pytest.raises(FileNotFoundError, match="does not exist"):
        await run_exit_effect_replay(
            db_path,
            handlers={"JOURNAL": journal},
            owner="runner-a",
        )

    assert not db_path.exists()


@pytest.mark.asyncio
async def test_replay_rejects_database_without_outbox_schema(tmp_path):
    db_path = tmp_path / "no-outbox.sqlite"
    with sqlite3.connect(db_path):
        pass

    async def journal(_payload):
        return True

    with pytest.raises(RuntimeError, match="schema is missing"):
        await run_exit_effect_replay(
            db_path,
            handlers={"JOURNAL": journal},
            owner="runner-a",
        )


@pytest.mark.asyncio
async def test_replay_completes_each_effect_independently(tmp_path):
    db_path = tmp_path / "mixed.sqlite"
    _seed(db_path)

    async def journal(_payload):
        return True

    async def telegram(_payload):
        return None

    async def redis(_payload):
        return "redis-message-1"

    async def gcp(_payload):
        raise RuntimeError("transport failed with secret detail")

    summary = await run_exit_effect_replay(
        db_path,
        handlers={
            "JOURNAL": journal,
            "TELEGRAM": telegram,
            "REDIS": redis,
            "GCP": gcp,
        },
        owner="runner-a",
        limit=4,
        max_attempts=3,
        base_delay_seconds=10,
        now=lambda: NOW,
    )
    rows = {row["effect_type"]: row for row in _rows(db_path)}

    assert summary == {
        "claimed": 4,
        "delivered": 2,
        "rescheduled": 2,
        "dead": 0,
    }
    assert rows["JOURNAL"]["status"] == "DELIVERED"
    assert rows["REDIS"]["status"] == "DELIVERED"
    assert rows["REDIS"]["remote_id"] == "redis-message-1"
    assert rows["TELEGRAM"]["status"] == "PENDING"
    assert rows["TELEGRAM"]["last_error"] == "DeliveryNotConfirmed"
    assert rows["GCP"]["status"] == "PENDING"
    assert rows["GCP"]["last_error"] == "RuntimeError"
    assert "secret detail" not in str(rows)


@pytest.mark.asyncio
async def test_redis_boolean_success_is_not_a_message_id(tmp_path):
    db_path = tmp_path / "redis-bool.sqlite"
    _seed(db_path)

    async def redis(_payload):
        return True

    summary = await run_exit_effect_replay(
        db_path,
        handlers={"REDIS": redis},
        owner="runner-a",
        limit=1,
        now=lambda: NOW,
    )
    redis_row = _rows(db_path)[2]

    assert summary["delivered"] == 0
    assert summary["rescheduled"] == 1
    assert redis_row["status"] == "PENDING"
    assert redis_row["remote_id"] is None


@pytest.mark.asyncio
async def test_cancellation_reschedules_before_propagating(tmp_path):
    db_path = tmp_path / "cancel.sqlite"
    _seed(db_path)

    async def cancel(_payload):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_exit_effect_replay(
            db_path,
            handlers={"JOURNAL": cancel},
            owner="runner-a",
            limit=1,
            now=lambda: NOW,
        )

    row = _rows(db_path)[0]
    assert row["status"] == "PENDING"
    assert row["attempt_count"] == 1
    assert row["last_error"] == "CancelledError"
    assert row["lease_owner"] is None


@pytest.mark.asyncio
async def test_handler_timeout_reschedules_before_lease_expiry(tmp_path):
    db_path = tmp_path / "timeout.sqlite"
    _seed(db_path)

    async def slow(_payload):
        await asyncio.sleep(1)
        return True

    summary = await run_exit_effect_replay(
        db_path,
        handlers={"JOURNAL": slow},
        owner="runner-a",
        limit=1,
        lease_seconds=2,
        handler_timeout_seconds=0.01,
        now=lambda: NOW,
    )
    row = _rows(db_path)[0]

    assert summary["rescheduled"] == 1
    assert row["status"] == "PENDING"
    assert row["last_error"] == "TimeoutError"
    assert row["lease_owner"] is None


@pytest.mark.asyncio
async def test_handler_runs_without_database_write_lock(tmp_path):
    db_path = tmp_path / "unlocked.sqlite"
    _seed(db_path)

    async def journal(_payload):
        with sqlite3.connect(db_path, timeout=0.1) as competitor:
            competitor.execute("BEGIN IMMEDIATE")
            competitor.rollback()
        return True

    summary = await run_exit_effect_replay(
        db_path,
        handlers={"JOURNAL": journal},
        owner="runner-a",
        limit=1,
        now=lambda: NOW,
    )

    assert summary["delivered"] == 1


@pytest.mark.asyncio
async def test_two_runners_do_not_handle_same_effect_concurrently(tmp_path):
    db_path = tmp_path / "concurrent.sqlite"
    _seed(db_path)
    calls = []

    async def redis(_payload):
        calls.append("redis")
        await asyncio.sleep(0.05)
        return "redis-message-1"

    first, second = await asyncio.gather(
        run_exit_effect_replay(
            db_path,
            handlers={"REDIS": redis},
            owner="runner-a",
            limit=1,
            now=lambda: NOW,
        ),
        run_exit_effect_replay(
            db_path,
            handlers={"REDIS": redis},
            owner="runner-b",
            limit=1,
            now=lambda: NOW,
        ),
    )

    assert calls == ["redis"]
    assert sorted((first["claimed"], second["claimed"])) == [0, 1]
    assert _rows(db_path)[2]["status"] == "DELIVERED"


@pytest.mark.asyncio
async def test_exact_delivery_claims_only_requested_effect(tmp_path):
    db_path = tmp_path / "exact.sqlite"
    _seed(db_path)
    calls = []

    async def redis(payload):
        calls.append(payload["event_id"])
        return "redis-message-exact"

    outcome = await deliver_exit_effect_once(
        db_path,
        effect_id=f"{INTENT_ID}:redis",
        effect_type="REDIS",
        handler=redis,
        owner="immediate-a",
        now=lambda: NOW,
    )
    rows = {row["effect_type"]: row for row in _rows(db_path)}

    assert outcome.status == "delivered"
    assert outcome.remote_id == "redis-message-exact"
    assert calls == [INTENT_ID]
    assert rows["REDIS"]["status"] == "DELIVERED"
    assert rows["JOURNAL"]["status"] == "PENDING"
    assert rows["TELEGRAM"]["status"] == "PENDING"
    assert rows["GCP"]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_exact_delivery_skips_already_delivered_without_handler_call(tmp_path):
    db_path = tmp_path / "exact-delivered.sqlite"
    _seed(db_path)
    calls = 0

    async def redis(_payload):
        nonlocal calls
        calls += 1
        return "redis-message-first"

    first = await deliver_exit_effect_once(
        db_path,
        effect_id=f"{INTENT_ID}:redis",
        effect_type="REDIS",
        handler=redis,
        owner="immediate-a",
        now=lambda: NOW,
    )
    second = await deliver_exit_effect_once(
        db_path,
        effect_id=f"{INTENT_ID}:redis",
        effect_type="REDIS",
        handler=redis,
        owner="immediate-b",
        now=lambda: NOW,
    )

    assert first.status == "delivered"
    assert second.status == "already_delivered"
    assert second.remote_id == "redis-message-first"
    assert calls == 1


@pytest.mark.asyncio
async def test_exact_delivery_rejects_effect_type_mismatch(tmp_path):
    db_path = tmp_path / "exact-type.sqlite"
    _seed(db_path)

    async def telegram(_payload):
        return "telegram-message"

    with pytest.raises(ValueError, match="effect type does not match"):
        await deliver_exit_effect_once(
            db_path,
            effect_id=f"{INTENT_ID}:redis",
            effect_type="TELEGRAM",
            handler=telegram,
            owner="immediate-a",
            now=lambda: NOW,
        )
