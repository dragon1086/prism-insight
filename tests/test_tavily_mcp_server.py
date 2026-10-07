from __future__ import annotations

import asyncio

import pytest

from cores.llm import tavily_mcp_server as tavily
from cores.llm.config_loader import load_mcp_registry
from cores.llm.tavily_mcp_server import Message, build_payload, build_query, format_response


def _user(content: str) -> list[Message]:
    return [Message(role="user", content=content)]


def test_query_uses_latest_user_message_and_collapses_whitespace():
    messages = [
        Message(role="system", content="You are a research assistant."),
        Message(role="user", content="old question"),
        Message(role="assistant", content="old answer"),
        Message(role="user", content="  KOSPI\n 2026-10-06  movers "),
    ]
    assert build_query(messages) == "KOSPI 2026-10-06 movers"


def test_query_is_truncated_to_tavily_limit():
    assert len(build_query(_user("x" * 3000))) == tavily.MAX_QUERY_CHARS


def test_query_requires_a_user_message():
    with pytest.raises(ValueError):
        build_query([Message(role="system", content="only system")])


def test_default_payload_is_advanced_general_search_with_answer():
    payload = build_payload(_user("Samsung Electronics news"))
    assert payload == {
        "query": "Samsung Electronics news",
        "search_depth": "advanced",
        "include_answer": "advanced",
        "max_results": tavily.MAX_RESULTS,
    }


def test_perplexity_filters_map_to_tavily_parameters():
    payload = build_payload(
        _user("q"),
        search_recency_filter="hour",
        search_domain_filter=["hankyung.com", "-reddit.com"],
        search_context_size="low",
    )
    assert payload["time_range"] == "day"
    assert payload["include_domains"] == ["hankyung.com"]
    assert payload["exclude_domains"] == ["reddit.com"]
    assert payload["search_depth"] == "basic"


def test_format_answer_with_numbered_citations():
    text = format_response({
        "answer": "KOSPI fell 0.89%.",
        "results": [
            {"url": "https://a.example", "title": "A", "published_date": "Tue, 06 Oct 2026"},
            {"url": "https://b.example", "title": None},
        ],
    })
    assert text == (
        "KOSPI fell 0.89%.\n\nCitations:\n"
        "[1] https://a.example (A | Tue, 06 Oct 2026)\n"
        "[2] https://b.example"
    )


def test_format_without_answer_falls_back_to_snippets():
    text = format_response({"answer": None, "results": [{"url": "https://a", "content": "snippet  text"}]})
    assert "No synthesized answer" in text
    assert "[1] snippet text" in text
    assert "[1] https://a" in text


def test_format_empty_response():
    assert format_response({"answer": "", "results": []}) == "No results found for this query."


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        asyncio.run(tavily.perplexity_ask(_user("q")))


def test_tool_returns_formatted_search(monkeypatch):
    captured = {}

    async def fake_search(payload):
        captured.update(payload)
        return {"answer": "ok", "results": [{"url": "https://a"}]}

    monkeypatch.setattr(tavily, "_search", fake_search)
    result = asyncio.run(tavily.perplexity_ask(_user("q"), search_recency_filter="week"))
    assert captured["time_range"] == "week"
    assert result == "ok\n\nCitations:\n[1] https://a"


def test_server_exposes_only_perplexity_ask():
    tools = asyncio.run(tavily.mcp.list_tools())
    assert [t.name for t in tools] == ["perplexity_ask"]


# --- registry switch (PRISM_WEB_SEARCH_PROVIDER) ---------------------------------


@pytest.fixture
def registry_env(monkeypatch):
    for name in ("PRISM_MCP_CONFIG", "PRISM_MCP_PYTHON", "PRISM_REPO_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-perplexity")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily")
    return monkeypatch


def test_default_provider_keeps_official_perplexity_server(registry_env):
    registry_env.delenv("PRISM_WEB_SEARCH_PROVIDER", raising=False)
    spec = load_mcp_registry().get("perplexity")
    assert spec.command == "npx"
    assert list(spec.args) == ["-y", "@perplexity-ai/mcp-server@1.2.0"]


def test_tavily_provider_is_served_under_perplexity_name(registry_env):
    registry_env.setenv("PRISM_WEB_SEARCH_PROVIDER", "Tavily")
    registry = load_mcp_registry()
    spec = registry.get("perplexity")
    assert list(spec.args) == ["-m", "cores.llm.tavily_mcp_server"]
    assert spec.env["TAVILY_API_KEY"] == "test-tavily"
    assert "PERPLEXITY_API_KEY" not in spec.env
    assert "tavily" not in registry.names()


def test_unknown_provider_fails_loudly(registry_env):
    registry_env.setenv("PRISM_WEB_SEARCH_PROVIDER", "anysearch")
    with pytest.raises(ValueError, match="anysearch"):
        load_mcp_registry()
