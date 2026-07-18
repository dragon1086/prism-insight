import asyncio
import sys
import types
from pathlib import Path

sys.modules.setdefault("markdown", types.SimpleNamespace(markdown=lambda text: text))

import report_generator
from cores.agents.report_agent import ReportAgent
from cores.llm.ports import LLMResult


class RecordingBackend:
    def __init__(self):
        self.calls = []

    async def run(self, spec, user_input):
        self.calls.append((spec, user_input))
        return LLMResult(text="backend-result")


def test_generate_telegram_text_preserves_runtime_contract(monkeypatch):
    backend = RecordingBackend()
    monkeypatch.setattr(report_generator, "_telegram_backend", backend)
    monkeypatch.setattr(report_generator, "TELEGRAM_ANALYSIS_MODEL", "test-model")
    monkeypatch.setattr(report_generator, "TELEGRAM_ANALYSIS_EFFORT", "high")
    agent = ReportAgent(
        name="contract-agent",
        instruction="contract instructions",
        server_names=("perplexity", "time"),
    )

    result = asyncio.run(
        report_generator._generate_telegram_text(
            agent=agent,
            message="contract message",
            max_tokens=4321,
        )
    )

    assert result == "backend-result"
    spec, user_input = backend.calls[0]
    assert spec.name == "contract-agent"
    assert spec.instructions == "contract instructions"
    assert spec.model == "test-model"
    assert spec.mcp_servers == ("perplexity", "time")
    assert spec.params.max_tokens == 4321
    assert spec.params.reasoning_effort == "high"
    assert spec.params.parallel_tool_calls is True
    assert spec.params.max_iterations == 10
    assert user_input == "contract message"


def test_telegram_runtime_sources_are_mcp_agent_free():
    root = Path(__file__).resolve().parents[1]
    for relative in ("report_generator.py", "telegram_ai_bot.py"):
        source = (root / relative).read_text()
        assert "mcp_agent" not in source
        assert "MCPApp" not in source
