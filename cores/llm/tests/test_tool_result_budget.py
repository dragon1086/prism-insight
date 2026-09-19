"""Exercise the actual installed SDK serialization boundary, without network."""
import asyncio
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("agents")
from agents import Agent, RunContextWrapper
from agents.mcp import MCPServerStdio
from agents.mcp.util import MCPUtil
from mcp.types import CallToolResult, ImageContent, TextContent, Tool

from cores.llm.tool_result_budget import ResearchToolResultBudget, _serialized_size


def text_result(text, **kwargs):
    return CallToolResult(content=[TextContent(type="text", text=text)], **kwargs)


def server_for(result, structured=False):
    server = MCPServerStdio(params={"command": "unused"}, name="firecrawl",
                            use_structured_content=structured)

    async def call_tool(tool_name, arguments):
        if isinstance(result, Exception):
            raise result
        return result

    server.call_tool = call_tool
    return server


async def sdk_output(server, tool_name="firecrawl_scrape"):
    return await MCPUtil.invoke_mcp_tool(
        server, Tool(name=tool_name, inputSchema={"type": "object"}),
        RunContextWrapper(context=None), "{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_multimegabyte_sdk_visible_result(structured):
    result = text_result("private raw 원문" * 200000,
                         structuredContent={"secret": "원문" * 500000})
    server = server_for(result, structured)
    budget = ResearchToolResultBudget(1024, 2048)
    budget.wrap(server, "firecrawl")
    visible = await sdk_output(server)
    assert len(visible.encode()) <= 512
    assert "private raw" not in visible and "원문" not in visible
    assert "UNKNOWN" in visible and "sha256" in visible
    assert budget.evidence_bytes == 0
    assert budget.notice_count == 1


@pytest.mark.asyncio
async def test_small_structured_content_is_counted_using_sdk_escaping():
    result = text_result("fine", structuredContent={"korean": "가" * 90})
    server = server_for(result, True)
    budget = ResearchToolResultBudget(600, 600)
    budget.wrap(server, "perplexity")
    visible = await sdk_output(server, "perplexity_ask")
    assert json.loads(visible)["korean"] == "가" * 90
    assert budget.evidence_bytes >= len(visible.encode())


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_short_text_cannot_hide_huge_structured_payload(structured):
    server = server_for(text_result("short", structuredContent={"raw": "HIDDEN" * 400000}), structured)
    budget = ResearchToolResultBudget(1024, 2048)
    budget.wrap(server, "firecrawl")
    output = await sdk_output(server)
    assert "result_limit" in output and "HIDDEN" not in output
    assert len(output.encode()) <= 512


def test_korean_is_bounded_by_utf8_bytes_not_characters():
    budget = ResearchToolResultBudget(1024, 2048)
    result = budget.admit(text_result("가" * 400))
    assert "result_limit" in result.content[0].text
    assert budget.evidence_bytes == 0


def test_hidden_metadata_cannot_bypass_size_check():
    budget = ResearchToolResultBudget(1024, 2048)
    result = budget.admit(text_result("short", _meta={"raw": "SECRET" * 1000}))
    assert "result_limit" in result.content[0].text
    assert result.meta is None


@pytest.mark.parametrize("limits", [(0, 1024), (1024, -1), (True, 1024), (1024, 1.5)])
def test_invalid_configuration_fails_before_tool_calls(limits):
    with pytest.raises(ValueError, match="integer byte limits"):
        ResearchToolResultBudget(*limits)


@pytest.mark.parametrize("content", [
    {"type": "image", "mimeType": "image/png", "data": "a" * 20000},
    {"type": "resource", "resource": {"uri": "file:///private/foo", "text": "secret"}},
])
def test_non_text_cannot_bypass_budget(content):
    budget = ResearchToolResultBudget(1024, 2048)
    result = budget.admit(CallToolResult(content=[content]))
    assert "non_text_requires_prefetch" in result.content[0].text
    assert "private" not in result.content[0].text
    assert result.structuredContent is None


def test_oversize_is_atomic_not_truncated_and_error_preserved():
    budget = ResearchToolResultBudget(1024, 2048)
    for error in (False, True):
        result = budget.admit(text_result("UNIQUE_RAW" * 1000, isError=error))
        assert result.isError is error
        assert "UNIQUE_RAW" not in result.content[0].text
        assert _serialized_size(result)[0] <= 512


def test_malformed_result_and_short_error_fail_closed():
    budget = ResearchToolResultBudget(1024, 2048)
    malformed = CallToolResult.model_construct(content=[object()], isError=False)
    for raw in (object(), malformed, text_result("/private/secret/token", isError=True)):
        result = budget.admit(raw)
        assert "UNKNOWN" in result.content[0].text
        assert "private" not in result.content[0].text


@pytest.mark.asyncio
async def test_shared_concurrent_cumulative_limit_and_notice_accounting():
    budget = ResearchToolResultBudget(1024, 1024)
    servers = [server_for(text_result("x" * 300)) for _ in range(12)]
    for server, name in zip(servers, ["perplexity", "firecrawl"] * 6):
        budget.wrap(server, name)
    outputs = await asyncio.gather(*(sdk_output(server, "perplexity_ask" if i % 2 == 0 else "firecrawl_scrape")
                                     for i, server in enumerate(servers)))
    assert 0 < budget.evidence_bytes <= 1024
    assert budget.notice_count > 0
    assert budget.notice_bytes <= 512 * budget.notice_count
    assert sum(len(value.encode()) for value in outputs) <= budget.evidence_bytes + budget.notice_bytes


@pytest.mark.asyncio
async def test_call_exception_no_sensitive_message():
    budget = ResearchToolResultBudget(1024, 2048)
    server = server_for(RuntimeError("/private/token=sensitive"))
    budget.wrap(server, "firecrawl")
    output = await sdk_output(server)
    assert "tool_call_failed" in output
    assert "sensitive" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("server_name", [
    "perplexity", "firecrawl", "yahoo_finance", "kospi_kosdaq",
    "webresearch", "deepsearch", "time",
])
async def test_registered_report_read_servers_are_guarded(server_name):
    server = server_for(text_result("OVERSIZE" * 1000))
    ResearchToolResultBudget(1024, 2048).wrap(server, server_name)
    output = await sdk_output(server, "perplexity_ask" if server_name == "perplexity" else "firecrawl_scrape")
    assert "result_limit" in output and "OVERSIZE" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("server_name", ["trading", "custom_broker"])
async def test_unrelated_server_unchanged(server_name):
    raw = text_result("x" * 2000)
    server = server_for(raw)
    original = server.call_tool
    ResearchToolResultBudget(1024, 2048).wrap(server, server_name)
    assert server.call_tool is original


@pytest.mark.asyncio
@pytest.mark.parametrize("server_name,allowed,blocked", [
    ("firecrawl", ["firecrawl_scrape", "firecrawl_search", "firecrawl_map"],
     ["firecrawl_crawl", "firecrawl_extract", "firecrawl_agent", "firecrawl_interact"]),
    ("perplexity", ["perplexity_search", "perplexity_ask"],
     ["perplexity_research", "perplexity_reason"]),
])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_sdk_tool_schema_visibility_and_blocked_calls(server_name, allowed, blocked, enabled):
    schema = {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    tools = [Tool(name=name, description="unchanged description", inputSchema=schema) for name in allowed + blocked]
    server = server_for(text_result("RAW_WAS_CALLED"))
    calls = []
    executed = []
    original_call = server.call_tool

    async def counted_call(name, arguments):
        executed.append(name)
        return await original_call(name, arguments)

    server.call_tool = counted_call

    async def list_tools(*args, **kwargs):
        calls.append((args, kwargs))
        return tools

    server.list_tools = list_tools
    if enabled:
        ResearchToolResultBudget(1024, 2048).wrap(server, server_name)
    context = RunContextWrapper(context=None)
    agent = Agent(name="report", instructions="report")
    offered = await MCPUtil.get_function_tools(server, False, context, agent)
    assert [tool.name for tool in offered] == (allowed if enabled else allowed + blocked)
    assert all(tool.params_json_schema == schema for tool in offered)
    assert all(tool.description == "unchanged description" for tool in offered)
    assert calls == [((context, agent), {})]
    assert [tool.name for tool in tools] == allowed + blocked  # no mutation of cached list
    visible = await sdk_output(server, blocked[0])
    assert ("tool_requires_prefetch" in visible) is enabled
    assert ("RAW_WAS_CALLED" not in visible) is enabled
    assert executed == ([] if enabled else [blocked[0]])


@pytest.mark.asyncio
@pytest.mark.parametrize("server_name", ["yahoo_finance", "kospi_kosdaq", "webresearch", "deepsearch", "time"])
async def test_other_report_servers_retain_exact_list_tools(server_name):
    server = server_for(text_result("okay"))
    original = server.list_tools
    ResearchToolResultBudget(1024, 2048).wrap(server, server_name)
    assert server.list_tools == original


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_backend_runner_sees_guarded_sdk_result(monkeypatch, enabled):
    import cores.llm.backends.openai_agents_backend as backend
    from cores.llm.ports import AgentSpec, LLMParams

    server = server_for(text_result("SOURCE_SENTINEL" * 200000))

    async def connect(self):
        pass

    async def cleanup(self):
        pass

    monkeypatch.setattr(MCPServerStdio, "connect", connect)
    monkeypatch.setattr(MCPServerStdio, "cleanup", cleanup)
    monkeypatch.setattr(backend, "build_mcp_server", lambda *args: server)
    captured = []

    class Runner:
        @staticmethod
        async def run(agent, user_input, **kwargs):
            captured.append(await sdk_output(agent.mcp_servers[0]))
            return SimpleNamespace(final_output="done")

    spec = AgentSpec(name="report", instructions="report", model="test",
                     mcp_servers=("firecrawl",),
                     params=LLMParams(report_research_tool_budget=(1024, 2048) if enabled else None))
    await backend.OpenAIAgentsBackend(None, runner=Runner).run(spec, "test")
    assert ("SOURCE_SENTINEL" not in captured[0]) is enabled
    if enabled:
        assert len(captured[0].encode()) <= 512


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_discovery_sdk_replacement_and_full_accounting(structured):
    prose = "WITHHELD_CAUSE " * 300 + "\n[1] https://example.com/wrong-issuer"
    raw = text_result(prose, structuredContent={"response": prose}, _meta={"secret": "PRIVATE"})
    server = server_for(raw, structured)
    budget = ResearchToolResultBudget(1400, 2000)
    budget.wrap(server, "perplexity")
    visible = await sdk_output(server, "perplexity_ask")
    assert "wrong-issuer" in visible and "WITHHELD_CAUSE" not in visible and "PRIVATE" not in visible
    assert "discovery_only" in visible and "UNKNOWN" in visible
    assert budget.fallback_count == 1
    assert budget.original_withheld_bytes == _serialized_size(raw)[0]
    assert len(visible.encode()) <= budget.evidence_bytes <= 1400


def test_discovery_admission_exact_limits_and_legacy_identity():
    raw = text_result("x" * 4000 + "\n" + "\n".join(f"[{i}] https://example.com/{i}" for i in range(10)))
    context = {"server_name": "perplexity", "tool_name": "perplexity_search"}
    budget = ResearchToolResultBudget(1400, 1600)
    first = budget.admit(raw, **context)
    assert first.structuredContent is None and first.meta is None
    assert budget.evidence_bytes == _serialized_size(first)[0]
    assert json.loads(first.content[0].text)["sha256"] == _serialized_size(raw)[1]
    second = budget.admit(raw, **context)
    assert "discovery_only" not in second.content[0].text
    assert budget.evidence_bytes <= 1600 and _serialized_size(second)[0] <= 512
    assert "discovery_only" not in ResearchToolResultBudget(1400, 1600).admit(raw).content[0].text
    small = text_result("small")
    assert budget.admit(small, **context) is small


@pytest.mark.asyncio
async def test_discovery_shared_parallel_budget():
    raw = text_result("x" * 4000 + "\n[1] https://example.com/a")
    budget = ResearchToolResultBudget(1400, 2000)
    servers = [server_for(raw) for _ in range(8)]
    for server in servers:
        budget.wrap(server, "perplexity")
    await asyncio.gather(*(sdk_output(server, "perplexity_ask") for server in servers))
    assert 0 < budget.evidence_bytes <= 2000
    assert budget.fallback_count + budget.notice_count == 8


@pytest.mark.parametrize("case", ["error", "image", "wrong_server", "wrong_tool", "large", "tight", "exhausted"])
def test_discovery_ineligible_cases_remain_refusals(case):
    raw = text_result("x" * (140000 if case == "large" else 4000) + "\n[1] https://example.com/a",
                      isError=case == "error")
    if case == "image":
        raw.content.append(ImageContent(type="image", data="abc", mimeType="image/png"))
    budget = ResearchToolResultBudget(512 if case == "tight" else 1400, 2000)
    if case == "exhausted":
        budget.evidence_bytes = 2000
    output = budget.admit(raw, server_name="firecrawl" if case == "wrong_server" else "perplexity",
                          tool_name="perplexity_reason" if case == "wrong_tool" else "perplexity_ask")
    assert "discovery_only" not in output.content[0].text
    assert _serialized_size(output)[0] <= 512
    assert budget.fallback_count == 0


def test_discovery_exact_fit_and_trailing_record_removal():
    raw = text_result("x" * 4000 + "\n[1] https://example.com/a\n[2] https://example.com/b")
    context = {"server_name": "perplexity", "tool_name": "perplexity_ask"}
    first = ResearchToolResultBudget(2000, 2000).admit(raw, **context)
    size = _serialized_size(first)[0]
    exact = ResearchToolResultBudget(size, size)
    assert len(json.loads(exact.admit(raw, **context).content[0].text)["sources"]) == 2
    assert exact.evidence_bytes == size
    tight = ResearchToolResultBudget(size - 1, size - 1)
    note = json.loads(tight.admit(raw, **context).content[0].text)
    assert len(note["sources"]) == 1 and note["truncated"] is True
    assert tight.evidence_bytes < size
