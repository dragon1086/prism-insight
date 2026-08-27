from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import test_us_stock_tracking_agent_process_reports as base_tests

agent_module = base_tests.us_agent_module
USStockTrackingAgent = base_tests.USStockTrackingAgent


def _make_agent() -> USStockTrackingAgent:
    agent = USStockTrackingAgent.__new__(USStockTrackingAgent)
    agent.account_configs = [
        {
            "name": "us-primary",
            "account_key": "vps:us-primary:01",
            "product": "01",
        }
    ]
    agent.active_account = None
    agent._safe_account_log_label = MagicMock(return_value="us-primary")
    agent.update_holdings = AsyncMock(return_value=[])
    return agent


def test_us_trading_analysis_concurrency_defaults_to_two() -> None:
    key = "US_TRADING_ANALYSIS_CONCURRENCY"
    shared_key = "TRADING_ANALYSIS_CONCURRENCY"
    assert agent_module._resolve_us_trading_analysis_concurrency({}) == 2
    assert agent_module._resolve_us_trading_analysis_concurrency({key: "3"}) == 3
    assert agent_module._resolve_us_trading_analysis_concurrency(
        {shared_key: "4"}
    ) == 4
    assert agent_module._resolve_us_trading_analysis_concurrency({key: "0"}) == 2
    assert agent_module._resolve_us_trading_analysis_concurrency(
        {key: "invalid"}
    ) == 2


@pytest.mark.asyncio
async def test_us_buy_analysis_prepass_is_bounded_and_preserves_input_order(
    monkeypatch,
) -> None:
    monkeypatch.setattr(agent_module, "US_TRADING_ANALYSIS_CONCURRENCY", 2)
    agent = _make_agent()
    paths = ["report-a.pdf", "report-b.pdf", "report-c.pdf"]
    delays = {
        "report-a.pdf": 0.04,
        "report-b.pdf": 0.03,
        "report-c.pdf": 0.01,
    }
    active = 0
    peak = 0
    failure_order: list[str] = []

    async def fake_core(path: str) -> dict:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(delays[path])
        active -= 1
        return {
            "success": False,
            "ticker": path,
            "company_name": path,
            "error": "probe",
        }

    original_error = agent_module.logger.error

    def capture_error(message, *args, **kwargs):
        text = str(message)
        if text.startswith("[ANALYSIS_FAILED] Report analysis skipped"):
            failure_order.append(text.rsplit(" — ", 1)[-1])
        return original_error(message, *args, **kwargs)

    agent._analyze_report_core = fake_core
    monkeypatch.setattr(agent_module.logger, "error", capture_error)

    assert await agent.process_reports(paths) == (0, 0)
    assert peak == 2
    assert failure_order == paths
