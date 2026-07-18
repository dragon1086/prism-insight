import asyncio
import ast
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
