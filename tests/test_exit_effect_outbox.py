import sqlite3

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
