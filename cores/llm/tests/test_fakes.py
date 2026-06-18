"""Tests for cores.llm.fakes — FakeLLMBackend."""

import pytest
from cores.llm.fakes import FakeLLMBackend
from cores.llm.ports import AgentSpec, LLMParams, LLMResult


def _make_spec(name="test_agent"):
    return AgentSpec(
        name=name,
        instructions="do something",
        model="gpt-5.5",
        mcp_servers=("sqlite",),
        params=LLMParams(max_tokens=1000),
    )


class TestFakeLLMBackendScripted:
    @pytest.mark.asyncio
    async def test_returns_scripted_results_in_order(self):
        backend = FakeLLMBackend([
            LLMResult(text="first"),
            LLMResult(text="second"),
        ])
        spec = _make_spec()
        r1 = await backend.run(spec, "input A")
        r2 = await backend.run(spec, "input B")
        assert r1.text == "first"
        assert r2.text == "second"

    @pytest.mark.asyncio
    async def test_records_calls(self):
        backend = FakeLLMBackend([LLMResult(text="ok"), LLMResult(text="ok2")])
        spec = _make_spec()
        await backend.run(spec, "query 1")
        await backend.run(spec, "query 2")
        assert len(backend.calls) == 2
        assert backend.calls[0] == (spec, "query 1")
        assert backend.calls[1] == (spec, "query 2")

    @pytest.mark.asyncio
    async def test_empty_queue_raises_index_error(self):
        backend = FakeLLMBackend([LLMResult(text="only")])
        spec = _make_spec()
        await backend.run(spec, "first")
        with pytest.raises(IndexError, match="empty"):
            await backend.run(spec, "second")

    @pytest.mark.asyncio
    async def test_structured_result(self):
        payload = {"decision": "buy", "confidence": 0.9}
        backend = FakeLLMBackend([LLMResult(text="buy", structured=payload)])
        result = await backend.run(_make_spec(), "analyse")
        assert result.structured == payload

    @pytest.mark.asyncio
    async def test_calls_empty_before_run(self):
        backend = FakeLLMBackend([LLMResult(text="x")])
        assert backend.calls == []


class TestFakeLLMBackendCallable:
    @pytest.mark.asyncio
    async def test_callable_receives_spec_and_input(self):
        received = []

        def handler(spec, user_input):
            received.append((spec, user_input))
            return LLMResult(text=f"echo:{user_input}")

        backend = FakeLLMBackend(handler)
        spec = _make_spec()
        result = await backend.run(spec, "hello")
        assert result.text == "echo:hello"
        assert received[0] == (spec, "hello")

    @pytest.mark.asyncio
    async def test_callable_records_calls(self):
        backend = FakeLLMBackend(lambda s, i: LLMResult(text="ok"))
        spec = _make_spec()
        await backend.run(spec, "a")
        await backend.run(spec, "b")
        assert len(backend.calls) == 2

    @pytest.mark.asyncio
    async def test_callable_dynamic_response(self):
        def handler(spec, user_input):
            return LLMResult(text=f"model={spec.model}")

        backend = FakeLLMBackend(handler)
        result = await backend.run(_make_spec("x"), "q")
        assert result.text == "model=gpt-5.5"


class TestMcpAgentBackendImportAndRuntime:
    """Verify the backend imports cleanly and raises the right error when SDK absent."""

    def test_module_imports_without_error(self):
        # Must not raise even though mcp_agent is not installed.
        import cores.llm.backends.mcp_agent_backend as mod
        assert hasattr(mod, "McpAgentBackend")

    def test_constructor_does_not_raise(self):
        from cores.llm.backends.mcp_agent_backend import McpAgentBackend
        backend = McpAgentBackend()
        assert backend.name == "mcp_agent"

    @pytest.mark.asyncio
    async def test_run_raises_runtime_error_when_sdk_absent(self):
        from cores.llm.backends.mcp_agent_backend import McpAgentBackend, _mcp_agent_available
        if _mcp_agent_available:
            pytest.skip("mcp_agent IS installed; skipping absent-SDK test")
        backend = McpAgentBackend()
        spec = _make_spec()
        with pytest.raises(RuntimeError, match="mcp-agent"):
            await backend.run(spec, "test")
