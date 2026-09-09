"""Final broker quote validation, with fake broker transport only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import trading.domestic_stock_trading as mod
from stock_tracking_agent import StockTrackingAgent


def broker():
    trader = mod.DomesticStockTrading.__new__(mod.DomesticStockTrading)
    trader.buy_amount = 100000
    trader._get_stock_lock = AsyncMock(return_value=asyncio.Lock())
    trader._semaphore = asyncio.Semaphore(1)
    trader._global_lock = asyncio.Lock()
    trader.get_current_price = MagicMock(return_value={"current_price": 72000})
    trader.smart_buy = MagicMock(return_value={"success": True, "order_no": "fake"})
    return trader


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["existing RR gate", "connection timeout exception"])
async def test_internal_quote_rejection_is_known_no_submit(reason):
    from prism_core.execution_service import ExecutionService
    trader = broker()
    validator = MagicMock(side_effect=ValueError(reason))
    result = await trader._execute_buy_stock("005930", quote_validator=validator)
    assert result["success"] is False
    assert not result.get("outcome_unknown", False)
    assert result["message"] == "BUY quote validation rejected before submission"
    assert ExecutionService._classify_result(result)[0] == "FAILED"
    trader.smart_buy.assert_not_called()
    validator.assert_called_once_with(72000)


@pytest.mark.asyncio
async def test_accepted_quote_passed_to_actual_sizing_without_refetch():
    trader = broker()
    validator = MagicMock()
    result = await trader._execute_buy_stock("005930", limit_price=70000, quote_validator=validator)
    assert result["success"] is True
    assert [call.args[0] for call in validator.call_args_list] == [72000, 70000]
    trader.smart_buy.assert_called_once_with("005930", 100000, 70000, quote_price=72000)
    trader.get_current_price.assert_called_once()


def test_final_market_quantity_consumes_validated_quote(monkeypatch):
    trader = broker()
    trader.auto_trading = True
    trader.mode = "demo"
    trader.trenv = SimpleNamespace(my_acct="fake", my_prod="01")
    trader._request = MagicMock(return_value=SimpleNamespace(isOK=lambda: True, getBody=lambda: SimpleNamespace(output={"ODNO": "fake"})))
    trader.get_current_price.side_effect = AssertionError("hidden quote refetch")
    result = mod.DomesticStockTrading.buy_market_price(trader, "005930", 150000, quote_price=72000)
    assert result["success"] is True
    assert result["quantity"] == 2
    trader.get_current_price.assert_not_called()


def test_broker_callback_runs_full_gate_even_inside_stop_target():
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    scenario = {"decision": "Enter", "buy_score": 9, "entry_price": 70000,
                "target_price": 90000, "stop_loss": 65000,
                "risk_reward_ratio": 4, "expected_return_pct": 28, "expected_loss_pct": 7}
    agent._evaluate_production_buy_gate = MagicMock(return_value={"allowed": False, "reason": "existing RR gate"})
    with pytest.raises(ValueError, match="existing RR gate"):
        agent._buy_quote_validator(scenario)(85000)
    assert agent._evaluate_production_buy_gate.call_args.args[1] == 85000
    assert scenario["entry_price"] == 70000  # independent ledger unchanged
