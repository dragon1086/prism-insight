"""Tests for cores.llm.ports — dataclass defaults and immutability."""

import pytest
from cores.llm.ports import AgentSpec, LLMParams, LLMResult


class TestLLMParams:
    def test_defaults(self):
        p = LLMParams()
        assert p.max_tokens == 8000
        assert p.reasoning_effort is None
        assert p.temperature is None
        assert p.max_iterations == 10
        assert p.stop_sequences == ()

    def test_custom_values(self):
        p = LLMParams(
            max_tokens=30000,
            reasoning_effort="none",
            temperature=0.0,
            max_iterations=5,
            stop_sequences=("DONE",),
        )
        assert p.max_tokens == 30000
        assert p.reasoning_effort == "none"
        assert p.temperature == 0.0
        assert p.max_iterations == 5
        assert p.stop_sequences == ("DONE",)

    def test_frozen(self):
        p = LLMParams()
        with pytest.raises((AttributeError, TypeError)):
            p.max_tokens = 999  # type: ignore[misc]

    def test_hashable(self):
        p = LLMParams(max_tokens=100)
        assert hash(p) is not None
        d = {p: "ok"}
        assert d[p] == "ok"


class TestAgentSpec:
    def test_defaults(self):
        spec = AgentSpec(name="test", instructions="do stuff", model="gpt-5.5")
        assert spec.mcp_servers == ()
        assert spec.output_schema is None
        assert isinstance(spec.params, LLMParams)

    def test_with_mcp_servers(self):
        spec = AgentSpec(
            name="trader",
            instructions="trade",
            model="gpt-5.5",
            mcp_servers=("sqlite", "yahoo_finance"),
        )
        assert "sqlite" in spec.mcp_servers
        assert len(spec.mcp_servers) == 2

    def test_frozen(self):
        spec = AgentSpec(name="a", instructions="b", model="c")
        with pytest.raises((AttributeError, TypeError)):
            spec.name = "other"  # type: ignore[misc]


class TestLLMResult:
    def test_defaults(self):
        r = LLMResult()
        assert r.text == ""
        assert r.structured is None
        assert r.response_id is None
        assert r.usage is None
        assert r.raw is None

    def test_mutable(self):
        r = LLMResult(text="hello")
        r.text = "world"
        assert r.text == "world"

    def test_with_usage(self):
        r = LLMResult(text="ok", usage={"input": 10, "output": 20})
        assert r.usage["input"] == 10
