"""Regression tests for empty trading-scenario LLM responses."""
from __future__ import annotations

import pytest

from stock_tracking_agent import _generate_trading_scenario_json
from tracking.helpers import default_scenario


class _FlakyScenarioLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def generate_str(self, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_empty_first_response_is_retried_and_parsed():
    llm = _FlakyScenarioLLM([
        "",
        '{"decision":"No Entry","buy_score":0,"sector":"보험"}',
    ])

    result = await _generate_trading_scenario_json(
        llm, "scenario prompt", retry_delay_seconds=0
    )

    assert llm.calls == 2
    assert result["decision"] == "No Entry"
    assert result["sector"] == "보험"


@pytest.mark.asyncio
async def test_repeated_empty_responses_return_none_without_unbounded_retry():
    llm = _FlakyScenarioLLM(["", ""])

    result = await _generate_trading_scenario_json(
        llm, "scenario prompt", retry_delay_seconds=0
    )

    assert llm.calls == 2
    assert result is None


def test_default_scenario_is_marked_as_incomplete():
    scenario = default_scenario("scenario_llm_empty_or_invalid")

    assert scenario["analysis_status"] == "failed"
    assert scenario["analysis_error"] == "scenario_llm_empty_or_invalid"
    assert scenario["decision"] == "No Entry"
