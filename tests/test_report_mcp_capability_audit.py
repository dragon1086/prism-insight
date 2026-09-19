import asyncio
import json
from types import SimpleNamespace

from tools.audit_report_mcp_capabilities import (
    collect_session,
    compact_tool,
    inspect_server,
    summary,
)


class Tool:
    def model_dump(self, **kwargs):
        return {"name": "search", "inputSchema": {"type": "object"}}


def test_collect_session_paginates_without_calling_tools():
    class Session:
        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(version="1.2.0"))

        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[Tool()], nextCursor="next" if cursor is None else None)

        async def call_tool(self, *args, **kwargs):
            raise AssertionError("audit must never call tools")

    result = asyncio.run(collect_session(Session()))
    assert result["server_version"] == "1.2.0"
    assert len(result["tools"]) == 2


def test_timeout_distinct_from_reachable():
    async def operation():
        await asyncio.sleep(1)

    result = asyncio.run(inspect_server("test", operation, timeout=.001))
    assert result["availability"] == "timeout"


def test_error_message_never_exposes_credentials():
    async def operation():
        raise ValueError("Authorization: Bearer secret-credential")

    result = asyncio.run(inspect_server("test", operation))
    assert result["availability"] == "unavailable"
    assert "secret-credential" not in json.dumps(result)


def test_schema_descriptions_defaults_examples_are_not_printed():
    tool = {"name": "search", "description": "secret" * 10000,
            "inputSchema": {"properties": {"query": {"type": "string", "default": "secret", "examples": ["secret"]}},
                            "required": ["query"]}}
    result = json.dumps(compact_tool(tool))
    assert len(result) < 400
    assert "secret" not in result


def test_schema_reachability_does_not_claim_data_access():
    async def operation():
        return {"tools": [], "server_version": "1.0"}

    result = asyncio.run(inspect_server("test", operation))
    assert result["availability"] == "schema_reachable"
    assert result["data_access_verified"] is False
    assert summary([result])[0]["tools"] == []


def test_invalid_schema_identifiers_sanitized():
    result = compact_tool({"name": "secret with spaces", "inputSchema": {"required": ["bad\nvalue"]}})
    assert result["name"] == "REDACTED"
    assert result["required"] == ["REDACTED"]


def test_repeated_cursor_fails_closed():
    class Session:
        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(version="1"))

        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=[], nextCursor="same")

    result = asyncio.run(inspect_server("test", lambda: collect_session(Session())))
    assert result["availability"] == "unavailable"
