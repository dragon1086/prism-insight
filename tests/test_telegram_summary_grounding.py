from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pandas as pd

from cores.agents.telegram_summary_evaluator_agent import (
    create_telegram_summary_evaluator_agent,
)
from cores.agents.telegram_summary_optimizer_agent import (
    create_telegram_summary_optimizer_agent,
)
from cores.llm.ports import LLMResult


KIS_CONTEXT = """## KIS verified market data
- Reference date: 20260918
- Source: KIS

### Recent OHLCV
| Date | Close | Volume |
|---|---:|---:|
| 20260918 | 70000 | 1200000 |

### Market capitalization
| Date | Market Cap |
|---|---:|
| 20260918 | 420000000000000 |
"""


def test_summary_agents_use_prefetched_kis_context_without_mcp_tools():
    metadata = {
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "trigger_mode": "afternoon",
    }

    optimizer = create_telegram_summary_optimizer_agent(
        metadata,
        "2026.09.18",
        market_data_context=KIS_CONTEXT,
    )
    evaluator = create_telegram_summary_evaluator_agent(
        "2026.09.18",
        market_data_context=KIS_CONTEXT,
    )

    for agent in (optimizer, evaluator):
        assert agent.server_names == []
        assert KIS_CONTEXT in agent.instruction
        assert "get_stock_ohlcv" not in agent.instruction
        assert "get_stock_market_cap" not in agent.instruction
        assert "load_all_tickers" not in agent.instruction
        assert "kospi_kosdaq" not in agent.instruction


def test_prefetch_summary_context_calls_kis_adapter_directly(monkeypatch):
    from cores import data_prefetch
    from cores import market_data

    calls = []

    def fake_ohlcv(fromdate, todate, ticker):
        calls.append(("ohlcv", fromdate, todate, ticker))
        return pd.DataFrame(
            {
                "Close": [69000, 70000],
                "Volume": [1000000, 1200000],
            },
            index=pd.to_datetime(["2026-09-17", "2026-09-18"]),
        )

    def fake_market_cap(fromdate, todate, ticker):
        calls.append(("market_cap", fromdate, todate, ticker))
        return pd.DataFrame(
            {"Market Cap": [420000000000000]},
            index=pd.to_datetime(["2026-09-18"]),
        )

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", fake_ohlcv)
    monkeypatch.setattr(market_data, "get_market_cap_by_date", fake_market_cap)
    monkeypatch.setattr(
        data_prefetch,
        "_get_mcp_server_module",
        lambda: (_ for _ in ()).throw(AssertionError("MCP adapter must not be used")),
    )

    context = data_prefetch.prefetch_telegram_summary_data("005930", "20260918")

    assert calls == [
        ("ohlcv", "20260908", "20260918", "005930"),
        ("market_cap", "20260908", "20260918", "005930"),
    ]
    assert "KIS verified market data" in context
    assert "20260918" in context
    assert "70000" in context
    assert "Market capitalization" in context
    assert "### Recent OHLCV\nUNKNOWN" not in context
    assert "### Market capitalization\nUNKNOWN" not in context


def test_prefetch_summary_context_fails_closed_without_mcp_fallback(monkeypatch):
    from cores import data_prefetch
    from cores import market_data

    monkeypatch.setattr(
        market_data,
        "get_market_ohlcv_by_date",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        market_data,
        "get_market_cap_by_date",
        lambda *args, **kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        data_prefetch,
        "_get_mcp_server_module",
        lambda: (_ for _ in ()).throw(AssertionError("MCP fallback must not be used")),
    )

    context = data_prefetch.prefetch_telegram_summary_data("005930", "20260918")

    assert "KIS data unavailable" in context
    assert "UNKNOWN" in context


def test_responses_workflow_runs_optimizer_and_structured_evaluator_without_tools():
    workflow = importlib.import_module("cores.telegram_summary_workflow")
    optimizer = SimpleNamespace(
        name="telegram_summary_optimizer",
        instruction="Create a summary from supplied evidence.",
        server_names=[],
    )
    evaluator = SimpleNamespace(
        name="telegram_summary_evaluator",
        instruction="Evaluate the summary.",
        server_names=[],
    )

    class FakeBackend:
        def __init__(self):
            self.calls = []

        async def run(self, spec, user_input):
            self.calls.append((spec, user_input))
            if spec.output_schema is not None:
                result = spec.output_schema(
                    rating=workflow.QualityRating.EXCELLENT,
                    feedback="grounded",
                    needs_improvement=False,
                    focus_areas=[],
                )
                return LLMResult(structured=result, raw=result)
            return LLMResult(text="📊 grounded summary")

    backend = FakeBackend()
    result = asyncio.run(
        workflow.run_telegram_summary_workflow(
            optimizer=optimizer,
            evaluator=evaluator,
            message="report plus KIS evidence",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            backend=backend,
            max_refinements=1,
        )
    )

    assert result == "📊 grounded summary"
    assert len(backend.calls) == 2
    optimizer_spec, _ = backend.calls[0]
    evaluator_spec, evaluation_prompt = backend.calls[1]
    assert optimizer_spec.mcp_servers == ()
    assert evaluator_spec.mcp_servers == ()
    assert optimizer_spec.model == evaluator_spec.model == "gpt-5.6-luna"
    assert optimizer_spec.params.reasoning_effort == "low"
    assert evaluator_spec.params.reasoning_effort == "low"
    assert evaluator_spec.output_schema is workflow.EvaluationResult
    assert "📊 grounded summary" in evaluation_prompt


def test_responses_workflow_returns_usable_summary_when_evaluator_is_unavailable():
    workflow = importlib.import_module("cores.telegram_summary_workflow")
    optimizer = SimpleNamespace(name="optimizer", instruction="summarize", server_names=[])
    evaluator = SimpleNamespace(name="evaluator", instruction="evaluate", server_names=[])

    class FakeBackend:
        async def run(self, spec, user_input):
            if spec.output_schema is not None:
                return LLMResult(structured=None)
            return LLMResult(text="📊 usable optimizer summary")

    result = asyncio.run(
        workflow.run_telegram_summary_workflow(
            optimizer=optimizer,
            evaluator=evaluator,
            message="grounded report",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            backend=FakeBackend(),
        )
    )

    assert result == "📊 usable optimizer summary"


def test_responses_workflow_refines_a_fair_summary_and_rechecks_it():
    workflow = importlib.import_module("cores.telegram_summary_workflow")
    optimizer = SimpleNamespace(name="optimizer", instruction="summarize", server_names=[])
    evaluator = SimpleNamespace(name="evaluator", instruction="evaluate", server_names=[])

    class FakeBackend:
        def __init__(self):
            self.calls = []
            self.optimizer_count = 0
            self.evaluator_count = 0

        async def run(self, spec, user_input):
            self.calls.append((spec, user_input))
            if spec.output_schema is None:
                self.optimizer_count += 1
                return LLMResult(
                    text=(
                        "📊 initial summary"
                        if self.optimizer_count == 1
                        else "📊 revised grounded summary"
                    )
                )
            self.evaluator_count += 1
            rating = (
                workflow.QualityRating.FAIR
                if self.evaluator_count == 1
                else workflow.QualityRating.EXCELLENT
            )
            return LLMResult(
                structured=workflow.EvaluationResult(
                    rating=rating,
                    feedback="Use the supplied KIS evidence",
                    needs_improvement=rating is workflow.QualityRating.FAIR,
                    focus_areas=["accuracy"],
                )
            )

    backend = FakeBackend()
    result = asyncio.run(
        workflow.run_telegram_summary_workflow(
            optimizer=optimizer,
            evaluator=evaluator,
            message="report with KIS evidence",
            model="gpt-5.6-luna",
            reasoning_effort="low",
            backend=backend,
            max_refinements=1,
        )
    )

    assert result == "📊 revised grounded summary"
    assert backend.optimizer_count == 2
    assert backend.evaluator_count == 2
    refinement_spec, refinement_prompt = backend.calls[2]
    assert refinement_spec.mcp_servers == ()
    assert refinement_spec.model == "gpt-5.6-luna"
    assert refinement_spec.params.reasoning_effort == "low"
    assert "Use the supplied KIS evidence" in refinement_prompt


def test_generator_prefetches_once_and_uses_same_evidence_for_both_agents(monkeypatch):
    import telegram_summary_agent as summary_module

    captured = {}

    def fake_prefetch(ticker, reference_date):
        captured["prefetch"] = (ticker, reference_date)
        return KIS_CONTEXT

    async def fake_workflow(**kwargs):
        captured["workflow"] = kwargs
        return "📊 grounded summary"

    monkeypatch.setattr(summary_module, "prefetch_telegram_summary_data", fake_prefetch)
    monkeypatch.setattr(summary_module, "run_telegram_summary_workflow", fake_workflow)

    metadata = {
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "date": "2026.09.18",
        "trigger_mode": "afternoon",
    }
    result = asyncio.run(
        summary_module.TelegramSummaryGenerator().generate_telegram_message(
            "report body",
            metadata,
            "breakout",
        )
    )

    assert result == "📊 grounded summary"
    assert captured["prefetch"] == ("005930", "20260918")
    workflow_call = captured["workflow"]
    assert workflow_call["model"] == "gpt-5.6-luna"
    assert workflow_call["reasoning_effort"] == "low"
    assert workflow_call["optimizer"].server_names == []
    assert workflow_call["evaluator"].server_names == []
    assert KIS_CONTEXT in workflow_call["optimizer"].instruction
    assert KIS_CONTEXT in workflow_call["evaluator"].instruction
