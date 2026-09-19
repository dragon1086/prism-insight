"""Report research MCP output guard, applied before SDK serialization/logging.

Large documents belong in private prefetch storage, not agent tool messages.
No raw result is persisted here. UTF-8 serialized bytes conservatively bound
text tokens; image/resource blocks are refused rather than assigned fake token
counts. The cumulative limit covers admitted evidence, with fixed-size refusal
overhead measured separately (not falsely advertised as a total-context cap).
"""

import hashlib
import json
from typing import Any

RESEARCH_SERVERS = frozenset({
    "perplexity", "firecrawl", "yahoo_finance", "kospi_kosdaq",
    "webresearch", "deepsearch", "time",
})
DIRECT_RESEARCH_TOOLS = {
    "firecrawl": frozenset({"firecrawl_scrape", "firecrawl_search", "firecrawl_map"}),
    "perplexity": frozenset({"perplexity_search", "perplexity_ask"}),
}


def _serialized_size(result: Any) -> tuple[int, str]:
    """Cover both content and structuredContent SDK branches, plus metadata."""
    raw = result.model_dump_json().encode("utf-8")
    if len(result.content) == 1:
        content = result.content[0].model_dump_json()
    else:
        content = json.dumps([item.model_dump(mode="json") for item in result.content])
    structured = json.dumps(result.structuredContent) if result.structuredContent else ""
    return max(len(raw), len(content.encode("utf-8")), len(structured.encode("utf-8"))), hashlib.sha256(raw).hexdigest()


class ResearchToolResultBudget:
    """One instance per agent run, shared across all research servers.

    Admission has no await, so concurrent asyncio tool calls cannot oversubscribe
    the evidence budget. Refusal envelopes are <=512 serialized UTF-8 bytes.
    notice_bytes includes all such envelopes; it is distinct from evidence_bytes.
    """

    def __init__(self, per_result_bytes: int, cumulative_evidence_bytes: int):
        if any(type(v) is not int or v < 512 for v in (per_result_bytes, cumulative_evidence_bytes)):
            raise ValueError("Research tool budgets must be integer byte limits >=512")
        self.per_result_bytes = per_result_bytes
        self.cumulative_evidence_bytes = cumulative_evidence_bytes
        self.evidence_bytes = 0
        self.notice_bytes = 0
        self.notice_count = 0
        self.fallback_count = 0
        self.original_withheld_bytes = 0

    def refuse(self, reason: str, *, is_error: bool = True,
               size: int | None = None, digest: str | None = None):
        from mcp.types import CallToolResult, TextContent

        # No URL, local path, server text, or exception message enters this note.
        withheld = reason in {'result_limit', 'run_evidence_limit', 'non_text_requires_prefetch'}
        note = json.dumps({"status": "UNKNOWN", "reason": reason,
                           "retrieval": "returned" if withheld else "unknown",
                           "serialized_utf8_bytes": size, "sha256": digest,
                           "action": ("Returned data withheld; not provider failure or absent facts. Use bounded prefetch."
                                      if withheld else "Use bounded prefetched evidence; do not infer missing facts.")},
                          separators=(",", ":"))
        result = CallToolResult(content=[TextContent(type="text", text=note)], isError=is_error)
        size, _ = _serialized_size(result)
        self.notice_bytes += size
        self.notice_count += 1
        return result

    def _discovery(self, result, size, digest):
        from mcp.types import CallToolResult, TextContent

        from prism_core.search_discovery_fallback import discovery_fallback

        remaining = self.cumulative_evidence_bytes - self.evidence_bytes
        if remaining <= 0:
            return None
        envelope = discovery_fallback([item.text for item in result.content], result.structuredContent,
                                      size=size, digest=digest)
        if envelope is None:
            return None
        while envelope["sources"]:
            fresh = CallToolResult(content=[TextContent(type="text", text=json.dumps(envelope, separators=(",", ":")))],
                                   isError=False)
            visible_size, _ = _serialized_size(fresh)
            if visible_size <= min(self.per_result_bytes, remaining):
                self.evidence_bytes += visible_size
                self.fallback_count += 1
                self.original_withheld_bytes += size
                return fresh
            envelope["sources"].pop()
            envelope["truncated"] = True
        return None

    def admit(self, result: Any, *, server_name: str | None = None, tool_name: str | None = None):
        from mcp.types import CallToolResult

        is_error = True
        try:
            if not isinstance(result, CallToolResult):
                return self.refuse("invalid_result")
            is_error = bool(result.isError)
            size, digest = _serialized_size(result)
            if is_error:
                reason = "tool_reported_error"
            elif any(item.type != "text" for item in result.content):
                reason = "non_text_requires_prefetch"
            elif size > self.per_result_bytes:
                reason = "result_limit"
                if server_name == "perplexity" and tool_name in DIRECT_RESEARCH_TOOLS["perplexity"]:
                    fallback = self._discovery(result, size, digest)
                    if fallback is not None:
                        return fallback
            elif self.evidence_bytes + size > self.cumulative_evidence_bytes:
                reason = "run_evidence_limit"
            else:
                self.evidence_bytes += size
                return result
            return self.refuse(reason, is_error=is_error, size=size, digest=digest)
        except Exception:  # noqa: BLE001 - serialization must fail closed, without raw fallback
            # Fail closed even for malformed SDK objects/serialization failures.
            return self.refuse("invalid_result", is_error=is_error)

    def wrap(self, server: Any, server_name: str) -> None:
        """Wrap the real SDK object's call_tool, retaining its lifecycle/type."""
        if server_name not in RESEARCH_SERVERS:
            return
        original = server.call_tool
        allowed = DIRECT_RESEARCH_TOOLS.get(server_name)
        original_list = getattr(server, "list_tools", None)
        if allowed is not None and callable(original_list):
            async def guarded_list_tools(*args, **kwargs):
                return [tool for tool in await original_list(*args, **kwargs) if tool.name in allowed]

            server.list_tools = guarded_list_tools

        async def guarded_call_tool(tool_name, arguments):
            if allowed is not None and tool_name not in allowed:
                return self.refuse("tool_requires_prefetch")
            try:
                result = await original(tool_name, arguments)
            except Exception:  # noqa: BLE001 - external tool exceptions must not leak secrets
                return self.refuse("tool_call_failed")
            return self.admit(result, server_name=server_name, tool_name=tool_name)

        server.call_tool = guarded_call_tool
