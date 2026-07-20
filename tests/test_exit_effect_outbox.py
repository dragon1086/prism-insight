import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from prism_core.exit_effects import EXIT_EFFECT_TYPES, ExitEffectStore


INTENT_ID = "intent-exit-1"


def _payload(**overrides):
    payload = {
        "version": 1,
        "event_id": INTENT_ID,
        "market": "KR",
        "source": "kr_batch",
        "account_id": "vps:kr-primary:01",
        "account_name": "kr-primary",
        "symbol": "005930",
        "company_name": "Samsung Electronics",
        "sell_price": 72000.0,
        "buy_price": 70000.0,
        "profit_rate": 2.85,
        "holding_days": 19,
        "sell_reason": "risk exit",
        "exit_kind": "stop",
        "message": "sold",
        "journal_stock_data": {"ticker": "005930"},
    }
    payload.update(overrides)
    return payload


def _store(connection: sqlite3.Connection) -> ExitEffectStore:
    store = ExitEffectStore(connection)
    store.ensure_schema()
    connection.commit()
    return store


def _enqueue(store: ExitEffectStore, payload=None) -> int:
    return store.enqueue_exit_effects(
        intent_id=INTENT_ID,
        market="KR",
        account_id="vps:kr-primary:01",
        symbol="005930",
        source="kr_batch",
        payload=payload or _payload(),
    )


def test_enqueue_requires_active_caller_transaction():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    try:
        with pytest.raises(RuntimeError, match="active caller-owned transaction"):
            _enqueue(store)
    finally:
        connection.close()


def test_enqueue_is_atomic_deterministic_and_idempotent():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    store = _store(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert _enqueue(store) == len(EXIT_EFFECT_TYPES)
        assert _enqueue(store) == 0
        connection.commit()

        rows = store.list_for_intent(INTENT_ID)
    finally:
        connection.close()

    assert [row["effect_type"] for row in rows] == list(EXIT_EFFECT_TYPES)
    assert {row["id"] for row in rows} == {
        f"{INTENT_ID}:{effect_type.lower()}" for effect_type in EXIT_EFFECT_TYPES
    }
    assert {row["status"] for row in rows} == {"PENDING"}
    assert {row["attempt_count"] for row in rows} == {0}
    assert all(row["payload"]["event_id"] == INTENT_ID for row in rows)


def test_enqueue_rejects_same_effect_identity_with_different_payload():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ValueError, match="payload conflict"):
            _enqueue(store, _payload(sell_price=71000.0))
        connection.rollback()
    finally:
        connection.close()


def test_enqueue_rolls_back_with_caller_transaction():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert _enqueue(store) == len(EXIT_EFFECT_TYPES)
        connection.rollback()
        rows = store.list_for_intent(INTENT_ID)
    finally:
        connection.close()

    assert rows == []


def test_claim_requires_active_caller_transaction():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    try:
        with pytest.raises(RuntimeError, match="active caller-owned transaction"):
            store.claim_ready_effects(owner="worker-a", limit=1)
    finally:
        connection.close()


def test_claim_filters_ready_effects_and_respects_limit():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        connection.execute(
            "UPDATE exit_effect_outbox SET next_attempt_at=? WHERE effect_type='REDIS'",
            ((now + timedelta(minutes=5)).isoformat(),),
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        claimed = store.claim_ready_effects(
            owner="worker-a",
            limit=2,
            effect_types=("JOURNAL", "REDIS", "GCP"),
            now=now,
            lease_seconds=30,
        )
        connection.commit()
    finally:
        connection.close()

    assert [row["effect_type"] for row in claimed] == ["JOURNAL", "GCP"]
    assert {row["status"] for row in claimed} == {"IN_PROGRESS"}
    assert {row["attempt_count"] for row in claimed} == {1}
    assert {row["lease_owner"] for row in claimed} == {"worker-a"}


def test_expired_lease_can_be_reclaimed_but_live_lease_cannot():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        first = store.claim_ready_effects(
            owner="worker-a",
            limit=1,
            effect_types=("JOURNAL",),
            now=now,
            lease_seconds=30,
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        live = store.claim_ready_effects(
            owner="worker-b",
            limit=1,
            effect_types=("JOURNAL",),
            now=now + timedelta(seconds=29),
            lease_seconds=30,
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        expired = store.claim_ready_effects(
            owner="worker-b",
            limit=1,
            effect_types=("JOURNAL",),
            now=now + timedelta(seconds=31),
            lease_seconds=30,
        )
        connection.commit()
    finally:
        connection.close()

    assert first[0]["attempt_count"] == 1
    assert live == []
    assert expired[0]["attempt_count"] == 2
    assert expired[0]["lease_owner"] == "worker-b"


def test_redis_and_gcp_delivery_require_remote_message_id():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        claimed = store.claim_ready_effects(
            owner="worker-a", limit=1, effect_types=("GCP",)
        )
        effect_id = claimed[0]["id"]
        with pytest.raises(ValueError, match="remote message id"):
            store.mark_delivered(effect_id=effect_id, owner="worker-a")
        assert store.mark_delivered(
            effect_id=effect_id,
            owner="worker-a",
            remote_id="gcp-message-1",
        )
        connection.commit()
        row = store.list_for_intent(INTENT_ID)[3]
    finally:
        connection.close()

    assert row["effect_type"] == "GCP"
    assert row["status"] == "DELIVERED"
    assert row["remote_id"] == "gcp-message-1"
    assert row["lease_owner"] is None


def test_only_active_lease_owner_can_finalize_effect():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        effect = store.claim_ready_effects(
            owner="worker-a", limit=1, effect_types=("JOURNAL",), now=now
        )[0]
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="active lease owner"):
            store.mark_delivered(effect_id=effect["id"], owner="worker-b")
        with pytest.raises(RuntimeError, match="active lease owner"):
            store.record_failure(
                effect_id=effect["id"],
                owner="worker-b",
                error_type="RuntimeError",
                next_attempt_at=now + timedelta(seconds=10),
                max_attempts=3,
                now=now,
            )
        connection.rollback()
        row = store.list_for_intent(INTENT_ID)[0]
    finally:
        connection.close()

    assert row["status"] == "IN_PROGRESS"
    assert row["lease_owner"] == "worker-a"


def test_failure_reschedules_then_dead_letters_at_max_attempts():
    connection = sqlite3.connect(":memory:")
    store = _store(connection)
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _enqueue(store)
        first = store.claim_ready_effects(
            owner="worker-a",
            limit=1,
            effect_types=("REDIS",),
            now=now,
        )[0]
        first_status = store.record_failure(
            effect_id=first["id"],
            owner="worker-a",
            error_type="DeliveryNotConfirmed",
            next_attempt_at=now + timedelta(seconds=10),
            max_attempts=2,
            now=now,
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        second = store.claim_ready_effects(
            owner="worker-b",
            limit=1,
            effect_types=("REDIS",),
            now=now + timedelta(seconds=11),
        )[0]
        second_status = store.record_failure(
            effect_id=second["id"],
            owner="worker-b",
            error_type="RuntimeError",
            next_attempt_at=now + timedelta(seconds=30),
            max_attempts=2,
            now=now + timedelta(seconds=11),
        )
        connection.commit()
        row = store.list_for_intent(INTENT_ID)[2]
    finally:
        connection.close()

    assert first_status == "PENDING"
    assert second["attempt_count"] == 2
    assert second_status == "DEAD"
    assert row["status"] == "DEAD"
    assert row["last_error"] == "RuntimeError"
    assert row["next_attempt_at"] is None
