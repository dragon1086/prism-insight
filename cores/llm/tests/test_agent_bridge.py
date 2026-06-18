"""
Unit tests for cores/llm/agent_bridge.py.

Network-free, no mcp_agent import, no openai-agents import at module level.
"""

import pytest
import sys
import types

import cores.llm.agent_bridge as bridge_mod
from cores.llm.ports import AgentSpec, LLMParams


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class _FakeAgent:
    """Minimal duck-type of an mcp_agent Agent for spec_from_mcp_agent tests."""

    def __init__(self, name, instruction, server_names=None):
        self.name = name
        self.instruction = instruction
        self.server_names = server_names


# ---------------------------------------------------------------------------
# spec_from_mcp_agent
# ---------------------------------------------------------------------------

class TestSpecFromMcpAgent:
    def _params(self):
        return LLMParams(max_tokens=16000, reasoning_effort="none")

    def test_basic_fields(self):
        agent = _FakeAgent(
            name="trading_journal_agent",
            instruction="You are a journal writer.",
            server_names=["kospi_kosdaq", "sqlite", "time"],
        )
        params = self._params()
        spec = bridge_mod.spec_from_mcp_agent(agent, model="gpt-5.4-mini", params=params)

        assert isinstance(spec, AgentSpec)
        assert spec.name == "trading_journal_agent"
        assert spec.instructions == "You are a journal writer."
        assert spec.model == "gpt-5.4-mini"
        assert spec.mcp_servers == ("kospi_kosdaq", "sqlite", "time")
        assert spec.params is params

    def test_missing_server_names_gives_empty_tuple(self):
        """Agent without server_names attribute should yield empty mcp_servers."""
        agent = _FakeAgent(
            name="no_servers_agent",
            instruction="Instruction.",
        )
        # Remove attribute entirely to test getattr fallback
        del agent.server_names
        params = self._params()
        spec = bridge_mod.spec_from_mcp_agent(agent, model="gpt-5.4-mini", params=params)
        assert spec.mcp_servers == ()

    def test_none_server_names_gives_empty_tuple(self):
        """server_names=None should also yield empty mcp_servers."""
        agent = _FakeAgent(
            name="none_servers_agent",
            instruction="Instruction.",
            server_names=None,
        )
        spec = bridge_mod.spec_from_mcp_agent(
            agent, model="gpt-5.4-mini", params=self._params()
        )
        assert spec.mcp_servers == ()

    def test_mcp_servers_is_tuple(self):
        """mcp_servers must be a tuple (AgentSpec is frozen)."""
        agent = _FakeAgent("a", "b", ["x", "y"])
        spec = bridge_mod.spec_from_mcp_agent(
            agent, model="gpt-5.4-mini", params=self._params()
        )
        assert isinstance(spec.mcp_servers, tuple)


# ---------------------------------------------------------------------------
# get_llm_backend
# ---------------------------------------------------------------------------

class _FakeRegistry:
    """Minimal stand-in for McpServerRegistry."""


class TestGetLlmBackend:
    def test_openai_agents_returns_backend(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "openai_agents")
        registry = _FakeRegistry()
        backend = bridge_mod.get_llm_backend(registry)
        # Check by name attribute — avoids importing OpenAIAgentsBackend
        assert backend.name == "openai_agents"

    def test_default_mcp_agent_raises_not_implemented(self, monkeypatch):
        monkeypatch.delenv("LLM_BACKEND", raising=False)
        with pytest.raises(NotImplementedError):
            bridge_mod.get_llm_backend(_FakeRegistry())

    def test_unknown_value_raises_not_implemented(self, monkeypatch):
        monkeypatch.setenv("LLM_BACKEND", "some_future_backend")
        with pytest.raises(NotImplementedError):
            bridge_mod.get_llm_backend(_FakeRegistry())


# ---------------------------------------------------------------------------
# ensure_openai_agents_configured
# ---------------------------------------------------------------------------

def _reset_configured():
    """Reset the module-level _configured flag between tests."""
    bridge_mod._configured = False


class TestEnsureOpenaiAgentsConfigured:
    def setup_method(self):
        _reset_configured()

    def teardown_method(self):
        _reset_configured()

    def test_proxy_branch_called_when_base_url_set(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:18741/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")

        calls = []

        # Monkeypatch configure_openai_agents_for_proxy inside the backend module
        import cores.llm.backends.openai_agents_backend as backend_mod
        monkeypatch.setattr(
            backend_mod,
            "configure_openai_agents_for_proxy",
            lambda url, key: calls.append((url, key)),
        )

        bridge_mod.ensure_openai_agents_configured()

        assert len(calls) == 1
        assert calls[0] == ("http://localhost:18741/v1", "test-key")
        assert bridge_mod._configured is True

    def test_proxy_branch_idempotent(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:18741/v1")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        calls = []
        import cores.llm.backends.openai_agents_backend as backend_mod
        monkeypatch.setattr(
            backend_mod,
            "configure_openai_agents_for_proxy",
            lambda url, key: calls.append((url, key)),
        )

        bridge_mod.ensure_openai_agents_configured()
        bridge_mod.ensure_openai_agents_configured()  # second call — must be no-op

        assert len(calls) == 1  # recorded exactly once

    def test_no_env_raises_runtime_error(self, monkeypatch):
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
            bridge_mod.ensure_openai_agents_configured()

    def test_api_key_branch_taken_when_no_base_url(self, monkeypatch):
        """When only OPENAI_API_KEY is set, the direct-API branch runs."""
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-real")

        set_calls = []

        # Inject fake agents SDK into sys.modules so the lazy import inside
        # ensure_openai_agents_configured succeeds without the real SDK.
        fake_agents = types.ModuleType("agents")
        fake_agents.set_default_openai_client = lambda c: set_calls.append(("client", c))
        fake_agents.set_default_openai_api = lambda v: set_calls.append(("api", v))
        fake_agents.set_default_openai_key = lambda k: set_calls.append(("key", k))

        fake_openai = types.ModuleType("openai")

        class _FakeAsyncOpenAI:
            def __init__(self, **kw):
                self.kw = kw

        fake_openai.AsyncOpenAI = _FakeAsyncOpenAI

        monkeypatch.setitem(sys.modules, "agents", fake_agents)
        monkeypatch.setitem(sys.modules, "openai", fake_openai)

        bridge_mod.ensure_openai_agents_configured()

        assert ("api", "responses") in set_calls
        assert ("key", "sk-test-real") in set_calls
        assert bridge_mod._configured is True
