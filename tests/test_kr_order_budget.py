"""Fake broker transport; collect after test_multi_account_domestic bootstrap."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from trading import domestic_stock_trading as mod


def broker(price=300_000):
    trader = mod.DomesticStockTrading.__new__(mod.DomesticStockTrading)
    trader.buy_amount = 1_000_000
    trader.auto_trading = True
    trader.mode = 'demo'
    trader.trenv = SimpleNamespace(my_acct='fake', my_prod='01')
    trader._get_stock_lock = AsyncMock(return_value=asyncio.Lock())
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_current_price = MagicMock(return_value={'current_price': price})
    trader._request = MagicMock(return_value=SimpleNamespace(
        isOK=lambda: True, getBody=lambda: SimpleNamespace(output={'ODNO': 'fake', 'RSVN_ORD_SEQ': 'fake'})))
    return trader


@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf'), False, 500_000])
@pytest.mark.parametrize('route', ['buy_market_price', 'buy_limit_price', 'buy_reserved_order', 'buy_closing_price'])
def test_invalid_or_unaffordable_budget_never_orders(budget, route):
    trader = broker(600_000)
    kwargs = {'buy_amount': budget}
    if route in ('buy_limit_price', 'buy_reserved_order'):
        kwargs['limit_price'] = 600_000
    result = getattr(trader, route)('006400', **kwargs)
    assert not result['success']
    assert result['quantity'] == 0
    trader._request.assert_not_called()


@pytest.mark.parametrize('window', ['regular', 'reserved'])
@pytest.mark.parametrize('quote', [100_000, 600_000])
@pytest.mark.asyncio
async def test_strict_budget_uses_limit_and_final_quantity_evidence(monkeypatch, window, quote):
    trader = broker(quote)
    monkeypatch.setattr(mod, '_domestic_order_window', lambda now: window)
    monkeypatch.setattr(mod.asyncio, 'sleep', AsyncMock())
    result = await trader._execute_buy_stock('006400', 500_000, 300_000, strict_budget=True)
    assert result['success']
    assert result['quantity'] == 1
    assert result['proposed_order_notional'] == 300_000
    assert result['unallocated_order_budget'] == 200_000
    params = trader._request.call_args.args[2]
    assert params['ORD_QTY'] == '1'
    assert params.get('ORD_DVSN', params.get('ORD_DVSN_CD')) == '00'
    assert params['ORD_UNPR'] == '300000'


def test_normal_regular_route_remains_market_and_strict_no_limit_rejects(monkeypatch):
    trader = broker()
    monkeypatch.setattr(mod, '_domestic_order_window', lambda now: 'regular')
    trader.smart_buy('006400', 500_000, 300_000)
    assert trader._request.call_args.args[2]['ORD_DVSN'] == '01'
    trader._request.reset_mock()
    result = trader.smart_buy('006400', 500_000, strict_budget=True)
    assert not result['success']
    trader._request.assert_not_called()


@pytest.mark.parametrize('window', ['closing', 'unavailable'])
def test_strict_budget_never_falls_back_to_unbounded_route(monkeypatch, window):
    trader = broker()
    monkeypatch.setattr(mod, '_domestic_order_window', lambda now: window)
    result = trader.smart_buy('006400', 500_000, 300_000, strict_budget=True)
    assert not result['success']
    trader._request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [0, -1, float('nan'), float('inf')])
async def test_async_invalid_budget_rejected_before_quote(budget):
    trader = broker()
    result = await trader._execute_buy_stock('006400', budget)
    assert not result['success']
    assert not result.get('outcome_unknown')
    trader.get_current_price.assert_not_called()
    trader._request.assert_not_called()
