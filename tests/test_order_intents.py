import asyncio
import ast
import sqlite3
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


def test_timeout_result_is_recorded_as_unknown(tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    db_path = tmp_path / "orders.sqlite"
    broker = FakeBroker(
        result={"success": False, "message": "Buy request timeout (30s)"}
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
