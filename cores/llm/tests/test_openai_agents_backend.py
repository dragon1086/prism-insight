# test_openai_agents_backend.py

"""
Network-free unit tests for cores/llm/backends/openai_agents_backend.py.

All SDK interactions are replaced with fakes/monkeypatches so no real API
calls are made.  Each async test is decorated with @pytest.mark.asyncio.
"""

import contextlib
from typing import Any

import pytest

from cores.llm.backends.openai_agents_backend import (
    OpenAIAgentsBackend,
    build_agent,
    build_model_settings,
    build_mcp_server,
)
from cores.llm.mcp_registry import McpServerRegistry
from cores.llm.ports import AgentSpec, LLMParams


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

SAMPLE_YAML: dict = {
    "mcp": {
        "servers": {
            "perplexity": {
                "command": "uvx",
                "args": ["mcp-server-perplexity-ask"],
                "env": {"PERPLEXITY_API_KEY": "test-key"},
                "read_timeout_seconds": 120,
            },
            "sqlite": {
                "command": "uvx",
                "args": ["mcp-server-sqlite", "--db", "/tmp/test.db"],
                "read_timeout_seconds": None,
            },
        }
    }
}


def make_registry() -> McpServerRegistry:
    return McpServerRegistry.from_yaml_dict(SAMPLE_YAML)


def make_spec(
    *,
    mcp_servers: tuple = (),
    output_schema: Any = None,
    params: LLMParams = LLMParams(),
) -> AgentSpec:
    return AgentSpec(
        name="test-agent",
        instructions="You are a test agent.",
        model="gpt-4o",
        mcp_servers=mcp_servers,
        output_schema=output_schema,
        params=params,
    )


# ---------------------------------------------------------------------------
# build_model_settings tests
# ---------------------------------------------------------------------------


def test_build_model_settings_no_reasoning_effort_none():
    """reasoning_effort=None → ModelSettings.reasoning must be None."""
    params = LLMParams(max_tokens=1000, reasoning_effort=None)
    ms = build_model_settings(params)
    assert ms.reasoning is None


def test_build_model_settings_no_reasoning_effort_string_none():
    """reasoning_effort='none' → ModelSettings.reasoning must be None."""
    params = LLMParams(max_tokens=1000, reasoning_effort="none")
    ms = build_model_settings(params)
    assert ms.reasoning is None


def test_build_model_settings_reasoning_high():
    """reasoning_effort='high' → ModelSettings.reasoning.effort == 'high'."""
    params = LLMParams(max_tokens=500, reasoning_effort="high")
    ms = build_model_settings(params)
    assert ms.reasoning is not None
    assert ms.reasoning.effort == "high"


def test_build_model_settings_reasoning_medium():
    """reasoning_effort='medium' → ModelSettings.reasoning.effort == 'medium'."""
    params = LLMParams(reasoning_effort="medium")
    ms = build_model_settings(params)
    assert ms.reasoning is not None
    assert ms.reasoning.effort == "medium"


def test_build_model_settings_reasoning_low():
    """reasoning_effort='low' → ModelSettings.reasoning.effort == 'low'."""
    params = LLMParams(reasoning_effort="low")
    ms = build_model_settings(params)
    assert ms.reasoning is not None
    assert ms.reasoning.effort == "low"


def test_build_model_settings_max_tokens_propagated():
    """max_tokens is forwarded to ModelSettings."""
    params = LLMParams(max_tokens=4096)
    ms = build_model_settings(params)
    assert ms.max_tokens == 4096


def test_build_model_settings_temperature_none_not_forced():
    """temperature=None → ModelSettings.temperature is None (not forced to 0)."""
    params = LLMParams(temperature=None)
    ms = build_model_settings(params)
    # The field should remain None, not coerced to 0 or any other default
    assert ms.temperature is None


def test_build_model_settings_temperature_set():
    """temperature value is forwarded to ModelSettings."""
    params = LLMParams(temperature=0.7)
    ms = build_model_settings(params)
    assert ms.temperature == 0.7


# ---------------------------------------------------------------------------
# build_mcp_server tests
# ---------------------------------------------------------------------------


def test_build_mcp_server_perplexity_command_and_args():
    """build_mcp_server for 'perplexity' produces correct command/args."""
    registry = make_registry()
    server = build_mcp_server("perplexity", registry)
    # params is a Pydantic StdioServerParameters — use attribute access
    assert server.params.command == "uvx"
    assert server.params.args == ["mcp-server-perplexity-ask"]


def test_build_mcp_server_perplexity_env():
    """build_mcp_server for 'perplexity' passes env dict."""
    registry = make_registry()
    server = build_mcp_server("perplexity", registry)
    assert server.params.env == {"PERPLEXITY_API_KEY": "test-key"}


def test_build_mcp_server_perplexity_name():
    """build_mcp_server sets the server name correctly."""
    registry = make_registry()
    server = build_mcp_server("perplexity", registry)
    assert server.name == "perplexity"


def test_build_mcp_server_sqlite_no_env():
    """build_mcp_server for 'sqlite' (no env) passes env=None."""
    registry = make_registry()
    server = build_mcp_server("sqlite", registry)
    assert server.params.env is None


def test_build_mcp_server_sqlite_args():
    """build_mcp_server for 'sqlite' passes multi-element args list."""
    registry = make_registry()
    server = build_mcp_server("sqlite", registry)
    assert server.params.args == ["mcp-server-sqlite", "--db", "/tmp/test.db"]


def test_build_mcp_server_timeout_passed():
    """read_timeout_seconds is forwarded as client_session_timeout_seconds."""
    registry = make_registry()
    server = build_mcp_server("perplexity", registry)
    assert server.client_session_timeout_seconds == 120


# ---------------------------------------------------------------------------
# build_agent tests
# ---------------------------------------------------------------------------


def test_build_agent_model():
    """build_agent sets the model from AgentSpec."""
    spec = make_spec()
    agent = build_agent(spec, [])
    assert agent.model == "gpt-4o"


def test_build_agent_output_type_none():
    """build_agent with output_schema=None sets output_type=None."""
    spec = make_spec(output_schema=None)
    agent = build_agent(spec, [])
    assert agent.output_type is None


def test_build_agent_output_type_set():
    """build_agent with output_schema propagates it as output_type."""
    class MySchema:
        pass

    spec = make_spec(output_schema=MySchema)
    agent = build_agent(spec, [])
    assert agent.output_type is MySchema


def test_build_agent_mcp_servers_count():
    """build_agent stores the passed mcp_servers list."""

    class DummyServer:
        pass

    dummy_servers = [DummyServer(), DummyServer()]
    spec = make_spec()
    agent = build_agent(spec, dummy_servers)
    assert len(agent.mcp_servers) == 2


def test_build_agent_no_mcp_servers():
    """build_agent with empty servers list results in len 0."""
    spec = make_spec()
    agent = build_agent(spec, [])
    assert len(agent.mcp_servers) == 0


# ---------------------------------------------------------------------------
# FakeServer / FakeRunner for run() orchestration tests
# ---------------------------------------------------------------------------


class FakeRunResult:
    """Minimal stub matching the RunResult interface used by the backend."""

    def __init__(self, final_output: Any, last_response_id: str = "resp-123"):
        self.final_output = final_output
        self.last_response_id = last_response_id


class FakeRunner:
    """Injectable Runner replacement — async classmethod run()."""

    def __init__(self, result: FakeRunResult):
        self._result = result

    async def run(self, agent: Any, user_input: Any, **kwargs: Any) -> FakeRunResult:
        return self._result


class FakeRunnerRaises:
    """Injectable Runner that raises on run()."""

    async def run(self, agent: Any, user_input: Any, **kwargs: Any) -> None:
        raise RuntimeError("runner exploded")


class FakeServer:
    """Async context manager that records connect/cleanup lifecycle calls."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self.connected = False
        self.cleaned_up = False

    async def __aenter__(self) -> "FakeServer":
        self.connected = True
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self.cleaned_up = True


# ---------------------------------------------------------------------------
# run() orchestration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_text_output_mapped(monkeypatch):
    """run() maps string final_output to LLMResult.text."""
    fake_result = FakeRunResult(final_output="hello world", last_response_id="r-1")
    fake_runner = FakeRunner(fake_result)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec()
    result = await backend.run(spec, "test input")

    assert result.text == "hello world"
    assert result.response_id == "r-1"
    assert result.raw is fake_result


@pytest.mark.asyncio
async def test_run_output_schema_none_structured_is_none(monkeypatch):
    """When output_schema=None, structured must be None regardless of final_output."""
    fake_result = FakeRunResult(final_output="some text")
    fake_runner = FakeRunner(fake_result)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec(output_schema=None)
    result = await backend.run(spec, "q")

    assert result.structured is None


@pytest.mark.asyncio
async def test_run_output_schema_set_structured_populated(monkeypatch):
    """When output_schema is set, structured = final_output."""

    class MySchema:
        pass

    obj = MySchema()
    fake_result = FakeRunResult(final_output=obj)
    fake_runner = FakeRunner(fake_result)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec(output_schema=MySchema)
    result = await backend.run(spec, "q")

    assert result.structured is obj
    # text should be empty string (final_output is not a str)
    assert result.text == ""


@pytest.mark.asyncio
async def test_run_server_connected_and_cleaned_up(monkeypatch):
    """MCP server is connected and cleaned up during a successful run."""
    fake_server = FakeServer("perplexity")
    fake_result = FakeRunResult(final_output="ok")
    fake_runner = FakeRunner(fake_result)

    import cores.llm.backends.openai_agents_backend as mod

    monkeypatch.setattr(mod, "build_mcp_server", lambda name, registry: fake_server)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec(mcp_servers=("perplexity",))
    await backend.run(spec, "input")

    assert fake_server.connected, "server.connect() was not called"
    assert fake_server.cleaned_up, "server.cleanup() was not called"


@pytest.mark.asyncio
async def test_run_cleanup_called_even_on_runner_failure(monkeypatch):
    """MCP server cleanup is guaranteed even when runner.run() raises."""
    fake_server = FakeServer("perplexity")
    fake_runner = FakeRunnerRaises()

    import cores.llm.backends.openai_agents_backend as mod

    monkeypatch.setattr(mod, "build_mcp_server", lambda name, registry: fake_server)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec(mcp_servers=("perplexity",))

    with pytest.raises(RuntimeError, match="runner exploded"):
        await backend.run(spec, "input")

    assert fake_server.connected, "server was never entered"
    assert fake_server.cleaned_up, "server was NOT cleaned up after runner failure"


@pytest.mark.asyncio
async def test_run_response_id_from_last_response_id(monkeypatch):
    """response_id in LLMResult comes from result.last_response_id."""
    fake_result = FakeRunResult(final_output="text", last_response_id="unique-id-xyz")
    fake_runner = FakeRunner(fake_result)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec()
    result = await backend.run(spec, "q")

    assert result.response_id == "unique-id-xyz"


@pytest.mark.asyncio
async def test_run_no_mcp_servers_no_build_mcp_server_called(monkeypatch):
    """When spec.mcp_servers is empty, build_mcp_server is never called."""
    called = []

    import cores.llm.backends.openai_agents_backend as mod

    def record_call(name, registry):
        called.append(name)
        return FakeServer(name)

    monkeypatch.setattr(mod, "build_mcp_server", record_call)

    fake_result = FakeRunResult(final_output="ok")
    fake_runner = FakeRunner(fake_result)

    registry = make_registry()
    backend = OpenAIAgentsBackend(registry, runner=fake_runner)

    spec = make_spec(mcp_servers=())
    await backend.run(spec, "q")

    assert called == []
