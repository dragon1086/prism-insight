import asyncio
import ast
import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest


class FakeBroker:
    def __init__(self, result=None, error=None, delay=0):
        self.result = result or {
            "success": True,
            "order_no": "ORDER-1",
            "message": "accepted",
            "quantity": 3,
        }
        self.error = error
        self.delay = delay
        self.calls = 0

    async def async_buy_stock(self, *args, **kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return dict(self.result)

    async def async_sell_stock(self, *args, **kwargs):
        return await self.async_buy_stock(*args, **kwargs)

    def buy_reserved_order(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return dict(self.result)

    def sell_reserved_order(self, *args, **kwargs):
        return self.buy_reserved_order(*args, **kwargs)


def _intent(*, side="buy", source_position_id="17"):
    from prism_core.order_intents import OrderIntent

    return OrderIntent.create(
        market="KR",
        account_id="acct-1",
        symbol="005930",
        side=side,
        order_style="market",
        source="test",
        source_position_id=source_position_id,
        quantity=3,
        limit_price=71000,
        reason="contract test",
    )


def _rows(db_path):
    with sqlite3.connect(db_path) as conn:
        intent = conn.execute(
            "SELECT status, idempotency_key, symbol, side FROM order_intents"
        ).fetchall()
        broker = conn.execute(
            "SELECT accepted, status, broker_order_id, raw_response_json, broker "
            "FROM broker_orders"
        ).fetchall()
    return intent, broker


def test_success_is_recorded_as_submitted(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker()
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    result = asyncio.run(
        service.execute_buy("005930", quantity=3, intent=_intent())
    )

    assert result["success"] is True
    assert broker.calls == 1
    intents, orders = _rows(db_path)
    assert intents[0][0] == "SUBMITTED"
    assert intents[0][2:] == ("005930", "BUY")
    assert orders[0][0:3] == (1, "SUBMITTED", "ORDER-1")
    assert '"success": true' in orders[0][3]


def test_schema_is_additive_and_preserves_existing_tables(tmp_path):
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT)"
        )
        conn.execute("INSERT INTO stock_holdings VALUES (1, '005930')")
        before = conn.execute("PRAGMA table_info(stock_holdings)").fetchall()

    IntentStore(db_path)

    with sqlite3.connect(db_path) as conn:
        after = conn.execute("PRAGMA table_info(stock_holdings)").fetchall()
        row = conn.execute("SELECT * FROM stock_holdings").fetchone()
        tables = {
            value[0]
            for value in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert after == before
    assert row == (1, "005930")
    assert {"order_intents", "broker_orders"} <= tables


def test_transaction_reservation_obeys_caller_commit_and_rollback(tmp_path):
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    rolled_back = _intent(source_position_id="rollback")
    committed = _intent(source_position_id="commit")

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created, reservation = store.reserve_in_transaction(conn, rolled_back)
        assert created is True
        assert reservation["status"] == "CREATED"
        conn.rollback()

        conn.execute("BEGIN IMMEDIATE")
        created, reservation = store.reserve_in_transaction(conn, committed)
        assert created is True
        assert reservation["status"] == "CREATED"
        conn.commit()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT id, status FROM order_intents ORDER BY id"
        ).fetchall() == [(committed.id, "CREATED")]


def test_transaction_reservation_rejects_autocommit_without_begin(tmp_path):
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    connection = sqlite3.connect(db_path, isolation_level=None)
    intent = _intent(source_position_id="autocommit")

    with pytest.raises(RuntimeError, match="active caller-owned transaction"):
        store.reserve_in_transaction(connection, intent)

    assert connection.execute(
        "SELECT COUNT(*) FROM order_intents WHERE id=?", (intent.id,)
    ).fetchone() == (0,)


def test_transaction_reservation_matches_duplicate_result_of_reserve(tmp_path):
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    first = _intent(source_position_id="same")
    duplicate = _intent(source_position_id="same")
    assert store.reserve(first)[0] is True

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created, existing = store.reserve_in_transaction(conn, duplicate)
        conn.rollback()

    assert created is False
    assert existing == {
        "id": first.id,
        "status": "CREATED",
        "idempotency_key": first.idempotency_key,
    }


def test_pre_reserved_execution_requires_store_capability_and_calls_broker_once(
    tmp_path,
):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    intent = _intent(source_position_id="pre-reserved")
    broker = FakeBroker()
    service = ExecutionService(broker, intent_store=store)

    with pytest.raises(TypeError):
        asyncio.run(
            service.execute_pre_reserved_buy(
                "005930", intent=intent, reservation={"id": intent.id}
            )
        )
    assert broker.calls == 0

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created, reservation = store.reserve_in_transaction(conn, intent)
        assert created is True
        conn.commit()

    result = asyncio.run(
        service.execute_pre_reserved_buy(
            "005930", intent=intent, reservation=reservation
        )
    )

    assert result["intent_status"] == "SUBMITTED"
    assert broker.calls == 1
    with pytest.raises(TypeError, match="unused"):
        ExecutionService(
            broker, intent_store=store
        ).execute_pre_reserved_reserved_buy(
            "AAPL", intent=intent, reservation=reservation
        )
    assert broker.calls == 1
    intents, orders = _rows(db_path)
    assert intents[0][0] == "SUBMITTED"
    assert len(orders) == 1


def test_rolled_back_pre_reservation_blocks_broker(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    intent = _intent(source_position_id="rolled-back-capability")
    broker = FakeBroker()

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created, reservation = store.reserve_in_transaction(conn, intent)
        assert created is True
        conn.rollback()

    with pytest.raises(RuntimeError):
        asyncio.run(
            ExecutionService(
                broker, intent_store=store
            ).execute_pre_reserved_buy(
                "005930", intent=intent, reservation=reservation
            )
        )
    assert broker.calls == 0


def test_pre_reserved_execution_rejects_active_transaction_and_foreign_db(
    tmp_path,
):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    foreign_path = tmp_path / "foreign.sqlite"
    store = IntentStore(db_path, timeout=0.05)
    intent = _intent(source_position_id="active-transaction")
    broker = FakeBroker()
    conn = sqlite3.connect(db_path)
    conn.execute("BEGIN IMMEDIATE")
    created, reservation = store.reserve_in_transaction(conn, intent)
    assert created is True

    with pytest.raises(RuntimeError, match="must commit"):
        asyncio.run(
            ExecutionService(
                broker, intent_store=store
            ).execute_pre_reserved_buy(
                "005930", intent=intent, reservation=reservation
            )
        )
    assert broker.calls == 0
    conn.rollback()

    sqlite3.connect(foreign_path).close()
    with sqlite3.connect(foreign_path) as foreign:
        with pytest.raises(ValueError, match="does not target"):
            store.reserve_in_transaction(foreign, _intent(source_position_id="foreign"))


def test_pre_reserved_sync_reserved_order_uses_capability_once(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore, OrderIntent

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    intent = OrderIntent.create(
        market="US",
        account_id="acct-us",
        symbol="AAPL",
        side="BUY",
        order_style="reserved",
        source="test",
        source_decision_id="reserved-capability",
    )
    broker = FakeBroker()
    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        created, reservation = store.reserve_in_transaction(conn, intent)
        assert created is True
        conn.commit()

    result = ExecutionService(
        broker, intent_store=store
    ).execute_pre_reserved_reserved_buy(
        "AAPL", intent=intent, reservation=reservation
    )

    assert result["intent_status"] == "SUBMITTED"
    assert broker.calls == 1


def test_closed_reservation_connection_uses_committed_row_as_authority(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    committed = _intent(source_position_id="closed-committed")
    rolled_back = _intent(source_position_id="closed-rolled-back")

    committed_connection = sqlite3.connect(db_path)
    committed_connection.execute("BEGIN IMMEDIATE")
    _, committed_reservation = store.reserve_in_transaction(
        committed_connection, committed
    )
    committed_connection.commit()
    committed_connection.close()

    rolled_back_connection = sqlite3.connect(db_path)
    rolled_back_connection.execute("BEGIN IMMEDIATE")
    _, rolled_back_reservation = store.reserve_in_transaction(
        rolled_back_connection, rolled_back
    )
    rolled_back_connection.rollback()
    rolled_back_connection.close()

    committed_broker = FakeBroker()
    result = asyncio.run(
        ExecutionService(
            committed_broker, intent_store=store
        ).execute_pre_reserved_buy(
            "005930", intent=committed, reservation=committed_reservation
        )
    )
    assert result["intent_status"] == "SUBMITTED"
    assert committed_broker.calls == 1

    rolled_back_broker = FakeBroker()
    with pytest.raises(RuntimeError, match="not in CREATED"):
        asyncio.run(
            ExecutionService(
                rolled_back_broker, intent_store=store
            ).execute_pre_reserved_buy(
                "005930", intent=rolled_back, reservation=rolled_back_reservation
            )
        )
    assert rolled_back_broker.calls == 0


def test_cancellation_during_pre_reserved_claim_marks_unknown_without_broker(
    tmp_path, monkeypatch
):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    intent = _intent(source_position_id="cancelled-claim")
    connection = sqlite3.connect(db_path)
    connection.execute("BEGIN IMMEDIATE")
    _, reservation = store.reserve_in_transaction(connection, intent)
    connection.commit()
    broker = FakeBroker()
    started = threading.Event()
    release = threading.Event()
    real_claim = store.claim_reservation

    def delayed_claim(*args, **kwargs):
        started.set()
        assert release.wait(timeout=2)
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_reservation", delayed_claim)

    async def exercise():
        task = asyncio.create_task(
            ExecutionService(
                broker, intent_store=store
            ).execute_pre_reserved_buy(
                "005930", intent=intent, reservation=reservation
            )
        )
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert broker.calls == 0
    with sqlite3.connect(db_path) as verify:
        assert verify.execute(
            "SELECT status FROM order_intents WHERE id=?", (intent.id,)
        ).fetchone() == ("UNKNOWN",)


def test_pre_reserved_claim_write_failure_never_calls_broker(tmp_path, monkeypatch):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    intent = _intent(source_position_id="claim-write-failure")
    connection = sqlite3.connect(db_path)
    connection.execute("BEGIN IMMEDIATE")
    _, reservation = store.reserve_in_transaction(connection, intent)
    connection.commit()
    broker = FakeBroker()

    def fail_mark_submitting(_intent_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "mark_submitting", fail_mark_submitting)

    with pytest.raises(sqlite3.OperationalError, match="locked"):
        asyncio.run(
            ExecutionService(
                broker, intent_store=store
            ).execute_pre_reserved_buy(
                "005930", intent=intent, reservation=reservation
            )
        )
    assert broker.calls == 0


def test_pre_reserved_sync_sell_binds_capability_side_before_broker(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)
    sell_intent = _intent(side="sell", source_position_id="sync-sell")
    connection = sqlite3.connect(db_path)
    connection.execute("BEGIN IMMEDIATE")
    _, reservation = store.reserve_in_transaction(connection, sell_intent)
    connection.commit()
    broker = FakeBroker()

    result = ExecutionService(
        broker, intent_store=store
    ).execute_pre_reserved_reserved_sell(
        "005930", intent=sell_intent, reservation=reservation
    )

    assert result["intent_status"] == "SUBMITTED"
    assert broker.calls == 1

    wrong_side = _intent(source_position_id="wrong-side")
    connection.execute("BEGIN IMMEDIATE")
    _, wrong_reservation = store.reserve_in_transaction(connection, wrong_side)
    connection.commit()
    with pytest.raises(ValueError, match="side"):
        ExecutionService(
            broker, intent_store=store
        ).execute_pre_reserved_reserved_sell(
            "005930", intent=wrong_side, reservation=wrong_reservation
        )
    assert broker.calls == 1


def test_explicit_broker_rejection_is_recorded_as_failed(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(result={"success": False, "message": "rejected"})
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    result = asyncio.run(
        service.execute_sell("005930", quantity=3, intent=_intent(side="sell"))
    )

    assert result["success"] is False
    intents, orders = _rows(db_path)
    assert intents[0][0] == "FAILED"
    assert orders[0][0:2] == (0, "FAILED")


def test_structured_ambiguous_result_is_recorded_as_unknown(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(
        result={
            "success": False,
            "outcome_unknown": True,
            "message": "Reserved buy order error: Expecting value",
        }
    )
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    result = asyncio.run(service.execute_buy("005930", intent=_intent()))

    assert result["success"] is False
    assert result["intent_status"] == "UNKNOWN"
    intents, orders = _rows(db_path)
    assert intents[0][0] == "UNKNOWN"
    assert orders[0][0:2] == (0, "UNKNOWN")


def test_exception_is_unknown_and_same_intent_never_reaches_broker_again(tmp_path):
    from prism_core.execution_service import ExecutionService, OrderOutcomeUnknown
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    intent = _intent()
    failing = FakeBroker(error=TimeoutError("ambiguous timeout"))
    service = ExecutionService(failing, intent_store=IntentStore(db_path))

    with pytest.raises(OrderOutcomeUnknown) as raised:
        asyncio.run(service.execute_buy("005930", intent=intent))
    assert isinstance(raised.value.cause, TimeoutError)

    retry_broker = FakeBroker()
    retry_service = ExecutionService(
        retry_broker, intent_store=IntentStore(db_path)
    )
    blocked = asyncio.run(
        retry_service.execute_buy("005930", intent=intent)
    )

    assert blocked["success"] is False
    assert blocked["blocked"] is True
    assert blocked["duplicate_intent"] is True
    assert retry_broker.calls == 0
    intents, orders = _rows(db_path)
    assert intents[0][0] == "UNKNOWN"
    assert orders[0][0:2] == (0, "UNKNOWN")


def test_async_cancellation_is_unknown_and_propagates(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(error=asyncio.CancelledError())
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.execute_buy("005930", intent=_intent()))

    intents, orders = _rows(db_path)
    assert intents[0][0] == "UNKNOWN"
    assert orders[0][0:2] == (0, "UNKNOWN")


def test_concurrent_duplicate_is_reserved_once(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(delay=0.05)
    first_intent = _intent()
    second_intent = _intent()
    assert first_intent.id != second_intent.id
    assert first_intent.idempotency_key == second_intent.idempotency_key
    first = ExecutionService(broker, intent_store=IntentStore(db_path))
    second = ExecutionService(broker, intent_store=IntentStore(db_path))

    async def exercise():
        return await asyncio.gather(
            first.execute_buy("005930", intent=first_intent),
            second.execute_buy("005930", intent=second_intent),
        )

    results = asyncio.run(exercise())

    assert broker.calls == 1
    assert sum(bool(result.get("blocked")) for result in results) == 1
    intents, orders = _rows(db_path)
    assert len(intents) == 1
    assert len(orders) == 1


def test_existing_call_without_intent_preserves_delegation():
    from prism_core.execution_service import ExecutionService

    broker = FakeBroker()
    result = asyncio.run(
        ExecutionService(broker).execute_buy("005930", quantity=2)
    )

    assert result["success"] is True
    assert broker.calls == 1


def test_reserved_order_uses_the_same_intent_state_machine(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore, OrderIntent

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker()
    service = ExecutionService(broker, intent_store=IntentStore(db_path))
    intent = OrderIntent.create(
        market="US",
        account_id="acct-us",
        symbol="AAPL",
        side="buy",
        order_style="reserved",
        source="us_pending_order_batch",
        source_decision_id="pending:9",
        cash_amount=1000,
        limit_price=200,
    )

    result = service.execute_reserved_buy(
        ticker="AAPL",
        limit_price=200,
        buy_amount=1000,
        exchange="NASD",
        intent=intent,
    )

    assert result["success"] is True
    intents, orders = _rows(db_path)
    assert intents[0][0] == "SUBMITTED"
    assert orders[0][0:3] == (1, "SUBMITTED", "ORDER-1")


def test_local_pending_queue_is_not_recorded_as_kis_submission(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore, OrderIntent

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(
        result={
            "success": True,
            "order_no": "PENDING-7",
            "order_type": "queued_buy",
            "message": "Reserved buy order queued",
        }
    )
    service = ExecutionService(broker, intent_store=IntentStore(db_path))
    intent = OrderIntent.create(
        market="US",
        account_id="acct-us",
        symbol="AAPL",
        side="buy",
        order_style="reserved",
        source="us_batch",
        source_decision_id="report:aapl.pdf",
        limit_price=200,
    )

    result = service.execute_reserved_buy("AAPL", intent=intent)

    assert result["success"] is True
    assert result["intent_status"] == "QUEUED"
    assert result["intent_broker"] == "LOCAL_QUEUE"
    intents, orders = _rows(db_path)
    assert intents[0][0] == "QUEUED"
    assert orders[0][0:3] == (1, "QUEUED", "PENDING-7")
    assert orders[0][4] == "LOCAL_QUEUE"


def test_broker_success_then_ledger_failure_is_unknown(tmp_path):
    from prism_core.execution_service import ExecutionService, OrderOutcomeUnknown
    from prism_core.order_intents import IntentStore, OrderIntent

    db_path = tmp_path / "orders.sqlite"
    store = IntentStore(db_path)

    def fail_result_persistence(*args, **kwargs):
        raise sqlite3.OperationalError("simulated ledger write failure")

    store.record_result = fail_result_persistence
    service = ExecutionService(FakeBroker(), intent_store=store)
    intent = OrderIntent.create(
        market="US",
        account_id="acct-us",
        symbol="AAPL",
        side="buy",
        order_style="reserved",
        source="us_pending_order_batch",
        source_decision_id="pending:10",
        limit_price=200,
    )

    with pytest.raises(OrderOutcomeUnknown) as raised:
        service.execute_reserved_buy("AAPL", intent=intent)

    assert raised.value.broker_result["success"] is True
    intents, orders = _rows(db_path)
    assert intents[0][0] == "SUBMITTING"
    assert orders == []


def test_broker_payload_secrets_are_redacted(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(
        result={
            "success": True,
            "order_no": "ORDER-SECRET-TEST",
            "message": "accepted token=plain-token Bearer bearer-token",
            "authorization": "Bearer header-token",
            "nested": {"api_key": "nested-api-key", "quantity": 3},
        }
    )
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    asyncio.run(service.execute_buy("005930", intent=_intent()))

    with sqlite3.connect(db_path) as conn:
        raw_message, raw_json = conn.execute(
            "SELECT raw_message, raw_response_json FROM broker_orders"
        ).fetchone()
    combined = f"{raw_message}\n{raw_json}"
    assert "plain-token" not in combined
    assert "bearer-token" not in combined
    assert "header-token" not in combined
    assert "nested-api-key" not in combined
    assert "[REDACTED]" in combined
    assert "ORDER-SECRET-TEST" in combined


def test_broker_exception_secrets_are_redacted(tmp_path):
    from prism_core.execution_service import ExecutionService, OrderOutcomeUnknown
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(
        error=RuntimeError("request failed token=exception-token Bearer bearer-secret")
    )
    service = ExecutionService(broker, intent_store=IntentStore(db_path))

    with pytest.raises(OrderOutcomeUnknown):
        asyncio.run(service.execute_buy("005930", intent=_intent()))

    with sqlite3.connect(db_path) as conn:
        error_message = conn.execute(
            "SELECT error_message FROM order_intents"
        ).fetchone()[0]
        raw_json = conn.execute(
            "SELECT raw_response_json FROM broker_orders"
        ).fetchone()[0]
    combined = f"{error_message}\n{raw_json}"
    assert "exception-token" not in combined
    assert "bearer-secret" not in combined
    assert "[REDACTED]" in combined


def test_same_position_key_is_shared_across_batch_and_loop_sources():
    batch = _intent(side="sell", source_position_id="42")
    from prism_core.order_intents import OrderIntent

    loop = OrderIntent.create(
        market="KR",
        account_id="acct-1",
        symbol="005930",
        side="sell",
        order_style="market",
        source="hardstop",
        source_position_id="42",
    )

    assert batch.id != loop.id
    assert batch.idempotency_key == loop.idempotency_key


def test_same_buy_decision_key_is_shared_across_sources():
    from prism_core.order_intents import OrderIntent

    kwargs = {
        "market": "US",
        "account_id": "acct-us",
        "symbol": "AAPL",
        "side": "buy",
        "order_style": "smart",
        "source_decision_id": "report:AAPL_20260719.pdf",
    }
    first = OrderIntent.create(source="us_batch", **kwargs)
    second = OrderIntent.create(source="retry_worker", **kwargs)

    assert first.idempotency_key == second.idempotency_key


def test_buy_prefers_decision_identity_when_position_is_also_present():
    from prism_core.order_intents import OrderIntent

    kwargs = {
        "market": "US",
        "account_id": "acct-us",
        "symbol": "AAPL",
        "side": "buy",
        "order_style": "smart",
        "source": "us_batch",
        "source_decision_id": "report:AAPL_20260719.pdf",
    }
    first = OrderIntent.create(source_position_id="position:101", **kwargs)
    second = OrderIntent.create(source_position_id="position:102", **kwargs)

    assert first.idempotency_key == second.idempotency_key
    assert first.request_payload()["source_decision_id"] == kwargs[
        "source_decision_id"
    ]
    assert first.request_payload()["source_position_id"] == "position:101"


def test_sell_prefers_position_identity_when_decision_is_also_present():
    from prism_core.order_intents import OrderIntent

    kwargs = {
        "market": "KR",
        "account_id": "acct-1",
        "symbol": "005930",
        "side": "sell",
        "order_style": "market",
        "source": "kr_batch",
        "source_decision_id": "decision:shared",
    }
    first = OrderIntent.create(source_position_id="position:101", **kwargs)
    second = OrderIntent.create(source_position_id="position:102", **kwargs)

    assert first.idempotency_key != second.idempotency_key
    assert first.request_payload()["source_decision_id"] == "decision:shared"
    assert first.request_payload()["source_position_id"] == "position:101"


@pytest.mark.parametrize(
    ("side", "identity_kwargs", "identity"),
    (
        ("buy", {"source_decision_id": "decision:only"}, "decision:decision:only"),
        ("buy", {"source_position_id": "position:only"}, "position:position:only"),
        ("sell", {"source_decision_id": "decision:only"}, "decision:decision:only"),
        ("sell", {"source_position_id": "position:only"}, "position:position:only"),
    ),
)
def test_single_source_identity_keeps_existing_idempotency(
    side, identity_kwargs, identity
):
    from prism_core.order_intents import OrderIntent

    kwargs = {
        "market": "KR",
        "account_id": "acct-1",
        "symbol": "005930",
        "side": side,
        "order_style": "market",
        **identity_kwargs,
    }
    first = OrderIntent.create(source="batch", **kwargs)
    second = OrderIntent.create(source="retry", **kwargs)
    key_source = f"v1|KR|acct-1|005930|{side.upper()}|{identity}"

    assert first.idempotency_key == second.idempotency_key
    assert first.idempotency_key == hashlib.sha256(key_source.encode()).hexdigest()


def test_all_production_new_order_calls_supply_an_intent():
    root = Path(__file__).resolve().parents[1]
    files = (
        "stock_tracking_agent.py",
        "stock_tracking_enhanced_agent.py",
        "prism-us/us_stock_tracking_agent.py",
        "prism-us/us_pending_order_batch.py",
        "tools/hardstop_seller.py",
        "tools/trend_exit_seller.py",
    )
    methods = {
        "execute_buy",
        "execute_sell",
        "execute_reserved_buy",
        "execute_reserved_sell",
    }
    violations = []
    for relative in files:
        tree = ast.parse((root / relative).read_text(), filename=relative)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in methods:
                continue
            if not any(keyword.arg == "intent" for keyword in node.keywords):
                violations.append(f"{relative}:{node.lineno}:{node.func.attr}")
    assert violations == []
