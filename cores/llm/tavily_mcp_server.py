"""Tavily-backed drop-in for the Perplexity MCP server (``perplexity_ask``).

Agents, prompts and the Codex tool allowlist all name the ``perplexity`` server
and its ``perplexity_ask`` tool.  Users without a Perplexity subscription can
set ``PRISM_WEB_SEARCH_PROVIDER=tavily`` (see ``config_loader``); the registry
then launches this server under the same ``perplexity`` name, so no prompt or
allowlist changes.  Only ``perplexity_ask`` is exposed because it is the only
Perplexity tool the pipeline calls.

Tavily returns a synthesized answer plus the source list, which is the closest
match to Perplexity's cited answer.  Failures raise instead of returning an
empty string, so an agent cannot mistake "search failed" for "nothing found".

Run:

    python -m cores.llm.tavily_mcp_server
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Literal, Optional

import aiohttp
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
# Tavily rejects longer queries with HTTP 400 ("Max query length is 1500 characters").
MAX_QUERY_CHARS = 1500
MAX_RESULTS = 8
_TIMEOUT_SECONDS = 60
_SNIPPET_CHARS = 500

# Tavily has no "hour" window; "day" is the narrowest available.
_RECENCY_TO_TIME_RANGE = {
    "hour": "day",
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}

mcp = FastMCP("perplexity")


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


def build_query(messages: list[Message]) -> str:
    """Use the latest user message as the search query (Tavily takes one query)."""
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            query = re.sub(r"\s+", " ", message.content).strip()
            if len(query) > MAX_QUERY_CHARS:
                logger.warning(
                    "query truncated from %d to %d chars", len(query), MAX_QUERY_CHARS
                )
                query = query[:MAX_QUERY_CHARS].rstrip()
            return query
    raise ValueError("perplexity_ask requires at least one non-empty user message")


def build_payload(
    messages: list[Message],
    search_recency_filter: Optional[str] = None,
    search_domain_filter: Optional[list[str]] = None,
    search_context_size: Optional[str] = None,
) -> dict[str, Any]:
    """Map Perplexity ``perplexity_ask`` arguments onto a Tavily search request."""
    payload: dict[str, Any] = {
        "query": build_query(messages),
        "search_depth": "basic" if search_context_size == "low" else "advanced",
        "include_answer": "advanced",
        "max_results": MAX_RESULTS,
    }
    if search_recency_filter:
        payload["time_range"] = _RECENCY_TO_TIME_RANGE[search_recency_filter]
    if search_domain_filter:
        include = [d for d in search_domain_filter if not d.startswith("-")]
        exclude = [d[1:] for d in search_domain_filter if d.startswith("-") and d[1:]]
        if include:
            payload["include_domains"] = include
        if exclude:
            payload["exclude_domains"] = exclude
    return payload


def format_response(data: dict[str, Any]) -> str:
    """Render Tavily output like Perplexity: answer, then numbered citations."""
    results = data.get("results") or []
    answer = (data.get("answer") or "").strip()
    if not answer and not results:
        return "No results found for this query."

    lines: list[str] = []
    if answer:
        lines.append(answer)
    else:
        lines.append("No synthesized answer was returned. Top search results:")
        for i, result in enumerate(results, 1):
            snippet = re.sub(r"\s+", " ", result.get("content") or "").strip()
            lines.append(f"[{i}] {snippet[:_SNIPPET_CHARS]}")

    if results:
        lines.append("")
        lines.append("Citations:")
        for i, result in enumerate(results, 1):
            details = [d for d in (result.get("title"), result.get("published_date")) if d]
            suffix = f" ({' | '.join(details)})" if details else ""
            lines.append(f"[{i}] {result.get('url', '')}{suffix}")
    return "\n".join(lines)


async def _search(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Add TAVILY_API_KEY=<key> to .env "
            "(PRISM_WEB_SEARCH_PROVIDER=tavily routes perplexity_ask to Tavily)."
        )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(TAVILY_SEARCH_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = (await resp.text())[:300]
                raise RuntimeError(f"Tavily search failed (HTTP {resp.status}): {body}")
            return await resp.json()


@mcp.tool()
async def perplexity_ask(
    messages: list[Message],
    search_recency_filter: Optional[Literal["hour", "day", "week", "month", "year"]] = None,
    search_domain_filter: Optional[list[str]] = None,
    search_context_size: Optional[Literal["low", "medium", "high"]] = None,
) -> str:
    """Answer a question using web search (Tavily backend).

    Returns a synthesized answer followed by numbered citations with titles and
    publication dates. Supports filtering by recency (hour/day/week/month/year),
    domain restrictions ('-' prefix excludes a domain), and search context size.
    """
    payload = build_payload(
        messages, search_recency_filter, search_domain_filter, search_context_size
    )
    return format_response(await _search(payload))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
