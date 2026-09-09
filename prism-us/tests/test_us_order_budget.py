"""Fake broker transport and pending queue; no credentials or orders."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_multi_account_us as base

mod = base.ust


def broker(price=300):
    trader = mod.USStockTrading.__new__(mod.USStockTrading)
    trader.buy_amount = 1000
    trader.auto_trading = True
    trader.mode = 'demo'
    trader.trenv = SimpleNamespace(my_acct='fake', my_prod='01')
    trader._get_stock_lock = AsyncMock(return_value=asyncio.Lock())
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_current_price = MagicMock(return_value={'current_price': price})
    trader.is_market_open = MagicMock(return_value=True)
    trader.is_reserved_order_available = MagicMock(return_value=True)
    trader._queue_pending_order = MagicMock()
    trader._request = MagicMock(return_value=SimpleNamespace(
        isOK=lambda: True, getBody=lambda: SimpleNamespace(output={'ODNO': 'fake'})))
    return trader


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [None, 0, -1, False, float("nan"), float("inf"), "bad"])
async def test_strict_budget_never_uses_default_before_quote(budget):
    trader = broker()
    result = await trader._execute_buy_stock("SNDK", budget, "NASD", 300, strict_budget=True)
    assert not result["success"]
    assert result["quantity"] == 0
    trader.get_current_price.assert_not_called()
    trader._request.assert_not_called()
    trader._queue_pending_order.assert_not_called()


@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf'), False, 500])
@pytest.mark.parametrize('route', ['buy_limit_price', 'buy_reserved_order'])
def test_invalid_or_unaffordable_budget_never_orders_or_queues(budget, route):
    trader = broker(600)
    trader.is_reserved_order_available.return_value = False
    result = getattr(trader, route)('SNDK', 600, budget, 'NASD')
    assert not result['success']
    assert result['quantity'] == 0
    trader._request.assert_not_called()
    trader._queue_pending_order.assert_not_called()


@pytest.mark.parametrize('route', ['buy_limit_price', 'buy_reserved_order'])
def test_sizing_uses_exact_serialized_cent_price(route):
    trader = broker()
    result = getattr(trader, route)('SNDK', 10.006, 20.015, 'NASD')
    assert result['success']
    assert result['quantity'] == 1  # 2 * serialized $10.01 exceeds $20.015.
    assert result['limit_price'] == 10.01


@pytest.mark.asyncio
async def test_final_limit_quantity_and_unused_budget_not_quote_quantity(monkeypatch):
    trader = broker(100)
    monkeypatch.setattr(mod.asyncio, 'sleep', AsyncMock())
    validator = MagicMock()
    result = await trader._execute_buy_stock('SNDK', 500, 'NASD', 300.004,
                                            quote_validator=validator, strict_budget=True)
    assert result['success']
    assert result['quantity'] == 1
    assert result['proposed_order_notional'] == 300
    assert result['unallocated_order_budget'] == 200
    assert [call.args[0] for call in validator.call_args_list] == [100, 300]


@pytest.mark.asyncio
@pytest.mark.parametrize('checks', [[ValueError('quote gate')], [None, ValueError('limit gate')]])
async def test_final_gate_rejection_never_submits(monkeypatch, checks):
    trader = broker()
    monkeypatch.setattr(mod.asyncio, 'sleep', AsyncMock())
    result = await trader._execute_buy_stock('SNDK', 500, 'NASD', 300,
        quote_validator=MagicMock(side_effect=checks))
    assert result['message'] == 'BUY quote validation rejected before submission'
    assert not result.get('outcome_unknown')
    trader._request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf')])
async def test_async_invalid_budget_rejected_before_quote(budget):
    trader = broker()
    result = await trader._execute_buy_stock('SNDK', budget, 'NASD', 300)
    assert not result['success']
    assert not result.get('outcome_unknown')
    trader.get_current_price.assert_not_called()
    trader._request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('options', [{'quote_validator': lambda price: None}, {'strict_budget': True}])
async def test_validated_runtime_buy_cannot_queue_stale_policy(monkeypatch, options):
    trader = broker()
    trader.is_market_open.return_value = False
    trader.is_reserved_order_available.return_value = False
    monkeypatch.setattr(mod.asyncio, 'sleep', AsyncMock())
    result = await trader._execute_buy_stock('SNDK', 500, 'NASD', 300, **options)
    assert not result['success']
    assert not result.get('outcome_unknown')
    assert 'local queue disabled' in result['message']
    trader._queue_pending_order.assert_not_called()
    trader._request.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_local_queue_preserved_and_evidence_is_only_plan(monkeypatch):
    trader = broker()
    trader.is_market_open.return_value = False
    trader.is_reserved_order_available.return_value = False
    trader._queue_pending_order.return_value = {
        'success': True, 'quantity': 0, 'order_no': 'PENDING-fake',
        'order_type': 'queued_buy', 'limit_price': 300, 'message': 'Queued, not submitted'}
    monkeypatch.setattr(mod.asyncio, 'sleep', AsyncMock())
    result = await trader._execute_buy_stock('SNDK', 500, 'NASD', 300)
    assert result['success']
    assert result['quantity'] == 0
    assert result['total_amount'] == 0
    assert result['message'] == 'Queued, not submitted'
    assert result['proposed_order_notional'] == 300
    assert result['unallocated_order_budget'] == 200
    assert result['budget_evidence_basis'] == 'queued_order_plan_not_fill'
    trader._queue_pending_order.assert_called_once()
    trader._request.assert_not_called()
