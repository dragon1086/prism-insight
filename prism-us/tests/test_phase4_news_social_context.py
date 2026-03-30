"""
Phase 4: News Agent Social Context Tests

Focused tests for optional prefetched social sentiment context in the US news
analysis agent flow.
"""

import importlib
import sys
import types
from pathlib import Path

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

try:
    from mcp_agent.agents.agent import Agent as _Agent  # noqa: F401
except ModuleNotFoundError:
    class _DummyAgent:
        def __init__(self, name, instruction, server_names):
            self.name = name
            self.instruction = instruction
            self.server_names = server_names

    mcp_agent = types.ModuleType("mcp_agent")
    agents_module = types.ModuleType("mcp_agent.agents")
    agent_module = types.ModuleType("mcp_agent.agents.agent")
    agent_module.Agent = _DummyAgent
    sys.modules.setdefault("mcp_agent", mcp_agent)
    sys.modules.setdefault("mcp_agent.agents", agents_module)
    sys.modules.setdefault("mcp_agent.agents.agent", agent_module)

from cores.agents import get_us_agent_directory


def test_news_agent_receives_prefetched_social_sentiment(sample_reference_date):
    """News agent should embed prefetched social sentiment context when provided."""
    agents = get_us_agent_directory(
        company_name="Tesla, Inc.",
        ticker="TSLA",
        reference_date=sample_reference_date,
        base_sections=["news_analysis"],
        language="en",
        prefetched_data={"social_sentiment": "### Structured Social Sentiment Snapshot (7d)\n- Average Buzz: 74.3/100"},
    )

    agent = agents["news_analysis"]
    assert "Structured Social Sentiment Snapshot" in agent.instruction
    assert "do not make extra tool calls for social sentiment" in agent.instruction
    assert "Social sentiment alignment" in agent.instruction
    assert agent.instruction.index("Structured Social Sentiment Snapshot") < agent.instruction.index("## Output Format")
    assert agent.server_names == ["perplexity", "firecrawl"]


def _import_us_analysis_with_stubbed_mcp_agent(monkeypatch):
    """Import us_analysis with lightweight mcp_agent stubs for unit testing."""
    class DummyAgent:
        def __init__(self, name, instruction, server_names):
            self.name = name
            self.instruction = instruction
            self.server_names = server_names

    mcp_agent = types.ModuleType("mcp_agent")
    app_module = types.ModuleType("mcp_agent.app")
    app_module.MCPApp = object
    agents_module = types.ModuleType("mcp_agent.agents")
    agent_module = types.ModuleType("mcp_agent.agents.agent")
    agent_module.Agent = DummyAgent
    workflows_module = types.ModuleType("mcp_agent.workflows")
    llm_module = types.ModuleType("mcp_agent.workflows.llm")
    augmented_module = types.ModuleType("mcp_agent.workflows.llm.augmented_llm")
    augmented_module.RequestParams = object
    openai_module = types.ModuleType("mcp_agent.workflows.llm.augmented_llm_openai")
    openai_module.OpenAIAugmentedLLM = object

    monkeypatch.setitem(sys.modules, "mcp_agent", mcp_agent)
    monkeypatch.setitem(sys.modules, "mcp_agent.app", app_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.agents", agents_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.agents.agent", agent_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.workflows", workflows_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.workflows.llm", llm_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.workflows.llm.augmented_llm", augmented_module)
    monkeypatch.setitem(sys.modules, "mcp_agent.workflows.llm.augmented_llm_openai", openai_module)

    sys.modules.pop("cores.us_analysis", None)
    return importlib.import_module("cores.us_analysis")


@pytest.mark.asyncio
async def test_analyze_us_stock_logs_rendered_social_prefetch_ticker(monkeypatch):
    """Social prefetch logging should emit a fully rendered ticker string."""
    us_analysis = _import_us_analysis_with_stubbed_mcp_agent(monkeypatch)

    class DummyLogger:
        def __init__(self):
            self.info_messages = []
            self.warning_messages = []
            self.error_messages = []

        def info(self, message):
            self.info_messages.append(message)

        def warning(self, message):
            self.warning_messages.append(message)

        def error(self, message):
            self.error_messages.append(message)

    created_loggers = []

    class DummyRunContext:
        def __init__(self, logger):
            self.logger = logger

        async def __aenter__(self):
            return types.SimpleNamespace(logger=self.logger)

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class DummyApp:
        def __init__(self, name):
            self.logger = DummyLogger()
            created_loggers.append(self.logger)

        def run(self):
            return DummyRunContext(self.logger)

    class DummyPrefetchLoader:
        def exec_module(self, module):
            module.prefetch_us_analysis_data = lambda ticker: {}

    class FakeSocialClient:
        def get_social_sentiment_markdown(self, ticker, days):
            return "### Structured Social Sentiment Snapshot (7d)"

    class DummyStock:
        def history(self, period):
            return types.SimpleNamespace(empty=True)

    async def fake_generate_report(*args, **kwargs):
        return "news report"

    async def fake_generate_summary(*args, **kwargs):
        return "summary"

    async def fake_generate_strategy(*args, **kwargs):
        return "strategy"

    monkeypatch.setenv("ADANOS_API_KEY", "sk_test")
    monkeypatch.setattr(us_analysis, "MCPApp", DummyApp)
    monkeypatch.setattr(us_analysis, "USSocialSentimentClient", FakeSocialClient)
    monkeypatch.setattr(
        us_analysis.importlib.util,
        "spec_from_file_location",
        lambda *args, **kwargs: types.SimpleNamespace(loader=DummyPrefetchLoader()),
    )
    monkeypatch.setattr(
        us_analysis.importlib.util,
        "module_from_spec",
        lambda spec: types.SimpleNamespace(),
    )
    monkeypatch.setattr(
        us_analysis,
        "get_us_agent_directory",
        lambda *args, **kwargs: {"news_analysis": object()},
    )
    monkeypatch.setattr(us_analysis, "generate_report", fake_generate_report)
    monkeypatch.setattr(us_analysis, "generate_summary", fake_generate_summary)
    monkeypatch.setattr(us_analysis, "generate_investment_strategy", fake_generate_strategy)
    monkeypatch.setattr(us_analysis, "clean_markdown", lambda text: text)
    monkeypatch.setattr(us_analysis, "get_disclaimer", lambda language: "disclaimer")
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=lambda ticker: DummyStock()))

    report = await us_analysis.analyze_us_stock(
        ticker="TSLA",
        company_name="Tesla, Inc.",
        reference_date="20260327",
        language="en",
    )

    root_logger = created_loggers[0]
    assert "Prefetched social sentiment for TSLA" in root_logger.info_messages
    assert not any(
        "US social sentiment prefetch failed" in message
        for message in root_logger.warning_messages
    )
    assert "news report" in report
