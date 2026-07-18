"""
agent_bridge.py — lightweight helpers for wiring mcp_agent Agent objects into
the cores/llm port layer.

Design constraints:
- No mcp_agent or openai imports at module top-level (keeps import safe even
  when those SDKs are absent; used in tests and non-journal code paths).
- Only stdlib + cores.llm imports at module level.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from cores.llm.ports import AgentSpec, LLMBackend, LLMParams

if TYPE_CHECKING:
    from cores.llm.mcp_registry import McpServerRegistry

# ---------------------------------------------------------------------------
# Idempotency flag for ensure_openai_agents_configured()
# ---------------------------------------------------------------------------
_configured: bool = False


def spec_from_mcp_agent(agent: Any, *, model: str, params: LLMParams) -> AgentSpec:
    """Build an AgentSpec from any object that duck-types an mcp_agent Agent.

    Reads:
    - ``agent.name``        → AgentSpec.name
    - ``agent.instruction`` → AgentSpec.instructions  (mcp_agent uses singular)
    - ``getattr(agent, "server_names", []) or []`` → AgentSpec.mcp_servers

    Works on real mcp_agent Agents and on plain test fakes — no SDK import needed.

    Args:
        agent:  Any object exposing .name, .instruction, and optionally .server_names.
        model:  Model identifier string (e.g. "gpt-5.4-mini").
        params: LLMParams instance with max_tokens / reasoning_effort / etc.

    Returns:
        A frozen AgentSpec ready to pass to any LLMBackend.run().
    """
    server_names = getattr(agent, "server_names", []) or []
    return AgentSpec(
        name=agent.name,
        instructions=agent.instruction,
        model=model,
        mcp_servers=tuple(server_names),
        params=params,
    )


def ensure_openai_agents_configured() -> None:
    """Idempotently configure the openai-agents SDK for the current environment.

    Call order priority:
    1. If OPENAI_BASE_URL is set → proxy mode (configure_openai_agents_for_proxy).
    2. Elif OPENAI_API_KEY is set → real OpenAI API (set_default_openai_* helpers).
    3. Else → RuntimeError with a clear message.

    All SDK imports are guarded inside this function so the module can be
    imported even when openai-agents / openai are not installed.

    Raises:
        RuntimeError: if neither OPENAI_BASE_URL nor OPENAI_API_KEY is set,
                      or if the openai-agents SDK is not installed.
    """
    global _configured
    if _configured:
        return

    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        from cores.llm.config_loader import resolve_openai_api_key

        api_key = resolve_openai_api_key()

    if base_url:
        # Proxy branch — delegate entirely to the existing helper which already
        # imports and configures the SDK.
        from cores.llm.backends.openai_agents_backend import (
            configure_openai_agents_for_proxy,
        )

        proxy_key = api_key or "chatgpt-oauth-placeholder"
        configure_openai_agents_for_proxy(base_url, proxy_key)
    elif api_key:
        # Direct OpenAI API branch — bind a default client explicitly so that
        # the Responses API is used (required by openai-agents 0.7.x).
        try:
            from agents import set_default_openai_api, set_default_openai_client, set_default_openai_key
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "ensure_openai_agents_configured: openai-agents SDK is not installed. "
                "Install it with: pip install openai-agents"
            ) from exc

        client = AsyncOpenAI(api_key=api_key)
        set_default_openai_client(client)
        set_default_openai_api("responses")
        set_default_openai_key(api_key)
    else:
        raise RuntimeError(
            "ensure_openai_agents_configured: neither OPENAI_BASE_URL nor "
            "OPENAI_API_KEY is set. Set one of these environment variables before "
            "using the openai_agents LLM backend."
        )

    _configured = True


def get_llm_backend(registry: "McpServerRegistry") -> LLMBackend:
    """Return the active LLMBackend based on the LLM_BACKEND environment variable.

    Supported values:
    - ``"openai_agents"`` → OpenAIAgentsBackend(registry)
    - anything else       → NotImplementedError (mcp_agent stays inline in callers)

    Args:
        registry: McpServerRegistry built by load_mcp_registry().

    Returns:
        A configured LLMBackend instance.

    Raises:
        NotImplementedError: if LLM_BACKEND is not "openai_agents".
    """
    backend_name = os.environ.get("LLM_BACKEND", "mcp_agent")
    if backend_name == "openai_agents":
        from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend

        return OpenAIAgentsBackend(registry)
    raise NotImplementedError(
        f"get_llm_backend: LLM_BACKEND={backend_name!r} is not handled here. "
        "The mcp_agent default path remains inline in the calling module."
    )
