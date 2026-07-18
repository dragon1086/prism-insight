from pathlib import Path
from unittest.mock import Mock

import pytest

from cores.agents.report_agent import ReportAgent
from cores.llm.ports import LLMResult
import cores.report_generation as report_generation


class _RecordingBackend:
    def __init__(self, text="generated report"):
        self.text = text
        self.calls = []

    async def run(self, spec, user_input):
        self.calls.append((spec, user_input))
        return LLMResult(text=self.text)


@pytest.mark.asyncio
async def test_generate_agent_text_maps_report_contract(monkeypatch):
    backend = _RecordingBackend()
    monkeypatch.setattr(report_generation, "_report_backend", backend)
    agent = ReportAgent(
        name="news_analysis_agent",
        instruction="Analyze verified news only.",
        server_names=["perplexity", "firecrawl"],
    )

    result = await report_generation._generate_agent_text(
        agent,
        "user prompt",
        max_tokens=32000,
        max_iterations=3,
    )

    assert result == "generated report"
    spec, user_input = backend.calls[0]
    assert spec.name == agent.name
    assert spec.instructions == agent.instruction
    assert spec.model == report_generation.REPORT_MODEL
    assert spec.mcp_servers == ("perplexity", "firecrawl")
    assert spec.params.max_tokens == 32000
    assert spec.params.reasoning_effort == report_generation.REPORT_EFFORT
    assert spec.params.parallel_tool_calls is True
    assert spec.params.max_iterations == 3
    assert user_input == "user prompt"


@pytest.mark.asyncio
async def test_four_report_paths_preserve_limits_and_prompts(monkeypatch):
    backend = _RecordingBackend()
    monkeypatch.setattr(report_generation, "_report_backend", backend)
    logger = Mock()
    section_agent = ReportAgent("section_agent", "section instructions")

    assert await report_generation.generate_report(
        section_agent, "company_status", "SK하이닉스", "000660", "20260718", logger
    ) == "generated report"
    assert await report_generation.generate_market_report(
        section_agent, "market_index_analysis", "20260718", logger
    ) == "generated report"
    assert await report_generation.generate_summary(
        {"company_status": "status report"},
        "SK하이닉스",
        "000660",
        "20260718",
        logger,
    ) == "generated report"
    assert await report_generation.generate_investment_strategy(
        {"company_status": "status report"},
        "combined report",
        "SK하이닉스",
        "000660",
        "20260718",
        logger,
    ) == "generated report"

    assert [call[0].name for call in backend.calls] == [
        "section_agent",
        "section_agent",
        "summary_agent",
        "investment_strategy_agent",
    ]
    assert [call[0].params.max_tokens for call in backend.calls] == [
        32000,
        32000,
        16000,
        32000,
    ]
    assert [call[0].params.max_iterations for call in backend.calls] == [10, 3, 2, 3]
    assert "SK하이닉스(000660)" in backend.calls[0][1]
    assert "시장과 거시환경" in backend.calls[1][1]
    assert "status report" in backend.calls[2][1]
    assert "combined report" in backend.calls[3][1]


def test_kr_report_pipeline_has_no_mcp_agent_runtime_imports():
    project_root = Path(__file__).parent.parent
    paths = [
        "cores/analysis.py",
        "cores/report_generation.py",
        "cores/agents/report_agent.py",
        "cores/agents/stock_price_agents.py",
        "cores/agents/company_info_agents.py",
        "cores/agents/news_strategy_agents.py",
        "cores/agents/market_index_agents.py",
    ]

    for relative_path in paths:
        source = (project_root / relative_path).read_text(encoding="utf-8")
        assert "mcp_agent" not in source, relative_path
        assert "MCPApp" not in source, relative_path
