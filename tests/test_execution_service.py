import asyncio
import ast
import sqlite3
import sys
import types
from pathlib import Path

import pytest


class FakeTrader:
    def __init__(self):
        self.calls = []

    async def async_buy_stock(self, *args, **kwargs):
        self.calls.append(("buy", args, kwargs))
        return {"success": True, "kind": "buy"}

    async def async_sell_stock(self, *args, **kwargs):
        self.calls.append(("sell", args, kwargs))
        return {"success": True, "kind": "sell"}

    def amend_order(self, *args, **kwargs):
        self.calls.append(("amend", args, kwargs))
        return {"success": True, "kind": "amend"}

    def cancel_order(self, *args, **kwargs):
        self.calls.append(("cancel", args, kwargs))
        return {"success": True, "kind": "cancel"}

    def buy_reserved_order(self, *args, **kwargs):
        self.calls.append(("reserved_buy", args, kwargs))
        return {"success": True, "kind": "reserved_buy"}

    def sell_reserved_order(self, *args, **kwargs):
        self.calls.append(("reserved_sell", args, kwargs))
        return {"success": True, "kind": "reserved_sell"}

    def get_holding_quantity(self, ticker):
        self.calls.append(("holding", (ticker,), {}))
        return 17


class FakeContext:
    def __init__(self, trader):
        self.trader = trader
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.trader

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True


def test_async_context_and_order_arguments_are_preserved():
    from prism_core.execution_service import ExecutionService

    trader = FakeTrader()
    context = FakeContext(trader)

    async def exercise():
        async with ExecutionService(context) as execution:
            buy = await execution.execute_buy(stock_code="005930", limit_price=81000)
            sell = await execution.execute_sell("005930", quantity=3)
            assert execution.get_holding_quantity("005930") == 17
        return buy, sell

    buy, sell = asyncio.run(exercise())

    assert context.entered is True
    assert context.exited is True
    assert buy == {"success": True, "kind": "buy"}
    assert sell == {"success": True, "kind": "sell"}
    assert trader.calls == [
        ("buy", (), {"stock_code": "005930", "limit_price": 81000}),
        ("sell", ("005930",), {"quantity": 3}),
        ("holding", ("005930",), {}),
    ]


def test_amend_cancel_and_reserved_orders_delegate_without_rewriting_arguments():
    from prism_core.execution_service import ExecutionService

    trader = FakeTrader()
    execution = ExecutionService(trader)

    async def exercise():
        amended = await execution.amend_or_cancel(
            "amend", "AAPL", "123", 201.5, 2, "NASD", dry_run=True
        )
        cancelled = await execution.amend_or_cancel(
            "cancel", "AAPL", "123", 2, "NASD", dry_run=True
        )
        return amended, cancelled

    amended, cancelled = asyncio.run(exercise())
    dry_run_cancelled = execution.amend_or_cancel_sync(
        "cancel", "MSFT", "456", 1, "NASD", dry_run=True
    )
    reserved_buy = execution.execute_reserved_buy(
        ticker="AAPL", limit_price=200.0, buy_amount=1000.0, exchange="NASD"
    )
    reserved_sell = execution.execute_reserved_sell(
        ticker="AAPL", limit_price=199.0, exchange="NASD"
    )

    assert amended["kind"] == "amend"
    assert cancelled["kind"] == "cancel"
    assert dry_run_cancelled["kind"] == "cancel"
    assert reserved_buy["kind"] == "reserved_buy"
    assert reserved_sell["kind"] == "reserved_sell"
    assert trader.calls == [
        ("amend", ("AAPL", "123", 201.5, 2, "NASD"), {"dry_run": True}),
        ("cancel", ("AAPL", "123", 2, "NASD"), {"dry_run": True}),
        ("cancel", ("MSFT", "456", 1, "NASD"), {"dry_run": True}),
        (
            "reserved_buy",
            (),
            {"ticker": "AAPL", "limit_price": 200.0, "buy_amount": 1000.0, "exchange": "NASD"},
        ),
        ("reserved_sell", (), {"ticker": "AAPL", "limit_price": 199.0, "exchange": "NASD"}),
    ]


def test_unknown_amend_or_cancel_action_is_rejected():
    from prism_core.execution_service import ExecutionService

    async def exercise():
        with pytest.raises(ValueError, match="unsupported order action"):
            await ExecutionService(FakeTrader()).amend_or_cancel("replace", "AAPL")

    asyncio.run(exercise())


def test_direct_order_methods_cannot_bypass_service_boundary():
    from prism_core.execution_service import ExecutionService

    execution = ExecutionService(FakeTrader())
    for method_name in ExecutionService._DIRECT_ORDER_METHODS:
        with pytest.raises(AttributeError, match="direct order method"):
            getattr(execution, method_name)


def test_cancellation_propagates_when_unknown_result_persistence_fails(
    monkeypatch, tmp_path, caplog
):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore, OrderIntent

    class CancellingTrader(FakeTrader):
        async def async_buy_stock(self, *args, **kwargs):
            self.calls.append(("buy", args, kwargs))
            raise asyncio.CancelledError()

    intent_store = IntentStore(tmp_path / "cancel-persistence.sqlite")
    intent = OrderIntent.create(
        market="KR",
        account_id="acct",
        symbol="005930",
        side="BUY",
        order_style="smart",
        source="test",
        source_decision_id="cancel-persistence-failure",
    )

    def fail_record_result(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected UNKNOWN persistence failure")

    monkeypatch.setattr(intent_store, "record_result", fail_record_result)
    caplog.set_level("CRITICAL")
    execution = ExecutionService(CancellingTrader(), intent_store=intent_store)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await execution.execute_buy("005930", intent=intent)

    asyncio.run(exercise())

    assert "cancellation UNKNOWN persistence failed" in caplog.text


def test_us_factory_recovers_when_root_trading_package_is_cached(monkeypatch):
    import trading  # noqa: F401 - cache the root package intentionally
    from prism_core.execution_service import ExecutionService

    class FakeUSContext:
        def __init__(self, account_name=None):
            self.account_name = account_name

    fake_module = types.ModuleType("us_stock_trading")
    fake_module.AsyncUSTradingContext = FakeUSContext
    monkeypatch.delitem(sys.modules, "trading.us_stock_trading", raising=False)
    monkeypatch.setitem(sys.modules, "us_stock_trading", fake_module)

    execution = ExecutionService.us(account_name="us-primary")

    assert isinstance(execution._resource, FakeUSContext)
    assert execution._resource.account_name == "us-primary"


def test_domestic_factory_accepts_originating_intent_store(monkeypatch, tmp_path):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore, OrderIntent

    class FakeDomesticContext:
        def __init__(self, account_name=None):
            self.account_name = account_name
            self.trader = FakeTrader()

        async def __aenter__(self):
            return self.trader

        async def __aexit__(self, exc_type, exc, tb):
            return False

    fake_module = types.ModuleType("trading.domestic_stock_trading")
    fake_module.AsyncTradingContext = FakeDomesticContext
    monkeypatch.setitem(sys.modules, "trading.domestic_stock_trading", fake_module)
    db_path = tmp_path / "orders.sqlite"
    intent_store = IntentStore(db_path)
    intent = OrderIntent.create(
        market="KR",
        account_id="acct",
        symbol="005930",
        side="BUY",
        order_style="market",
        source="test",
        source_decision_id="decision-1",
        source_position_id="legacy:KR:1",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        created, reservation = intent_store.reserve_in_transaction(connection, intent)
        assert created is True
        connection.commit()

    execution = ExecutionService.domestic(
        account_name="kr-primary",
        intent_store=intent_store,
    )

    async def exercise():
        async with execution:
            return await execution.execute_pre_reserved_buy(
                "005930",
                intent=intent,
                reservation=reservation,
            )

    result = asyncio.run(exercise())

    assert isinstance(execution._resource, FakeDomesticContext)
    assert execution._resource.account_name == "kr-primary"
    assert execution._intent_store is intent_store
    assert result["intent_status"] == "SUBMITTED"
    assert execution._resource.trader.calls == [("buy", ("005930",), {})]


def test_domestic_factory_rejects_db_path_with_different_intent_store(
    monkeypatch, tmp_path
):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    class FakeDomesticContext:
        def __init__(self, account_name=None):
            self.account_name = account_name

    fake_module = types.ModuleType("trading.domestic_stock_trading")
    fake_module.AsyncTradingContext = FakeDomesticContext
    monkeypatch.setitem(sys.modules, "trading.domestic_stock_trading", fake_module)
    requested_path = tmp_path / "requested.sqlite"
    different_store = IntentStore(tmp_path / "different.sqlite")

    with pytest.raises(ValueError, match="IntentStore|intent store|db_path"):
        ExecutionService.domestic(
            account_name="kr-primary",
            db_path=requested_path,
            intent_store=different_store,
        )


@pytest.mark.parametrize(
    ("result", "expected_status", "expected_accepted"),
    [
        (
            {"success": True, "order_no": "KR-ORDER-1", "message": "accepted"},
            "SUBMITTED",
            True,
        ),
        (
            {"success": False, "order_no": "", "message": "rejected"},
            "FAILED",
            False,
        ),
        (
            {"success": False, "order_no": "", "message": "request timeout"},
            "UNKNOWN",
            False,
        ),
    ],
)
def test_normal_kr_domestic_results_never_classify_as_queued(
    result, expected_status, expected_accepted
):
    from prism_core.execution_service import ExecutionService

    status, accepted, broker = ExecutionService._classify_result(result)

    assert status == expected_status
    assert status != "QUEUED"
    assert accepted is expected_accepted
    assert broker == "KIS"


@pytest.mark.parametrize(
    "result",
    [
        {
            "success": True,
            "order_no": "LOCAL-7",
            "order_type": "queued_buy",
            "message": "queued locally",
        },
        {
            "success": True,
            "order_no": "PENDING-7",
            "order_type": "reserved",
            "message": "queued locally",
        },
    ],
)
def test_only_explicit_local_queue_markers_classify_as_queued(result):
    from prism_core.execution_service import ExecutionService

    status, accepted, broker = ExecutionService._classify_result(result)

    assert status == "QUEUED"
    assert accepted is True
    assert broker == "LOCAL_QUEUE"


def test_transient_empty_portfolio_is_rechecked_before_sell(monkeypatch):
    from trading import domestic_stock_trading as domestic

    trader = domestic.DomesticStockTrading.__new__(domestic.DomesticStockTrading)
    trader._stock_locks = {}
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()

    holding = {
        "stock_code": "005930",
        "quantity": 7,
        "avg_price": 70000,
        "profit_amount": 7000,
        "profit_rate": 1.0,
    }
    portfolio_reads = [[holding], [], [holding]]
    submitted = []

    def get_portfolio():
        return portfolio_reads.pop(0)

    def smart_sell_all(stock_code, limit_price, quantity):
        submitted.append((stock_code, limit_price, quantity))
        return {
            "success": True,
            "quantity": quantity,
            "order_no": "ORDER-1",
            "message": "ok",
        }

    async def no_sleep(_delay):
        return None

    trader.get_portfolio = get_portfolio
    trader.get_current_price = lambda _stock_code: {"current_price": 71000}
    trader.smart_sell_all = smart_sell_all
    monkeypatch.setattr(domestic.asyncio, "sleep", no_sleep)

    result = asyncio.run(trader._execute_sell_stock("005930", limit_price=70500))

    assert result["success"] is True
    assert result["quantity"] == 7
    assert portfolio_reads == []
    assert submitted == [("005930", 70500, 7)]


def test_all_real_order_entrypoints_route_through_execution_service():
    repo_root = Path(__file__).resolve().parents[1]
    entrypoint_files = [
        "stock_tracking_agent.py",
        "stock_tracking_enhanced_agent.py",
        "tools/hardstop_seller.py",
        "tools/trend_exit_seller.py",
        "tools/fill_chaser.py",
        "prism-us/us_stock_tracking_agent.py",
        "prism-us/us_pending_order_batch.py",
    ]
    direct_order_methods = {
        "async_buy_stock",
        "async_sell_stock",
        "buy_reserved_order",
        "sell_reserved_order",
        "amend_order",
        "cancel_order",
    }
    direct_contexts = {"AsyncTradingContext", "AsyncUSTradingContext"}
    violations = []

    for relative_path in entrypoint_files:
        path = repo_root / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in direct_order_methods
            ):
                violations.append(f"{relative_path}:{node.lineno} calls {node.func.attr}")
            elif isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in direct_contexts:
                        violations.append(
                            f"{relative_path}:{node.lineno} imports {imported.name}"
                        )

    assert violations == [], "direct order entrypoints remain:\n" + "\n".join(violations)
