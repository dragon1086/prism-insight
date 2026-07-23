# openai_agents_backend.py

"""
openai-agents SDK backend: implements LLMBackend using the openai-agents 0.7.x SDK.

This is the Phase 2 LLM port adapter.  All openai-agents imports are guarded so
this module can be imported (and tests collected) even if the SDK is not installed.
A clear RuntimeError is raised at *call time* rather than at import time.
"""

import contextlib
from typing import Any, Optional

from cores.llm.mcp_registry import McpServerRegistry
from cores.llm.ports import AgentSpec, LLMBackend, LLMParams, LLMResult

# --- SDK import guard ---------------------------------------------------
try:
    from agents import Agent, ModelSettings, Runner
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_default_openai_key,
        set_tracing_disabled,
    )
    from agents.mcp import MCPServerStdio, MCPServerStdioParams
    from openai import AsyncOpenAI
    from openai.types.shared import Reasoning

    _sdk_available = True
except ImportError:
    Agent = None  # type: ignore[assignment,misc]
    ModelSettings = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    MCPServerStdio = None  # type: ignore[assignment]
    MCPServerStdioParams = None  # type: ignore[assignment]
    Reasoning = None  # type: ignore[assignment]
    set_default_openai_api = None  # type: ignore[assignment]
    set_default_openai_client = None  # type: ignore[assignment]
    set_default_openai_key = None  # type: ignore[assignment]
    set_tracing_disabled = None  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]
    _sdk_available = False
# ------------------------------------------------------------------------


def configure_openai_agents_for_proxy(
    base_url: str,
    api_key: str = "chatgpt-oauth-placeholder",
) -> None:
    """Point the openai-agents SDK at the ChatGPT OAuth proxy's Responses endpoint.

    After calling this, Runner will send Responses API requests to
    ``{base_url}/responses`` (i.e. the proxy's /v1/responses route) instead
    of the real OpenAI API.  The proxy translates the OAuth token, forces
    ``store=False`` and ``stream=True``, and forwards the request to the
    Codex backend.

    Must be called before the first Runner.run() call.  Do NOT call at
    module import time — only call explicitly from application startup code.

    Args:
        base_url: Base URL of the proxy, e.g. ``http://localhost:18741/v1``.
        api_key:  Placeholder key accepted by the proxy (no real auth needed).

    Raises:
        RuntimeError: if the openai-agents SDK is not installed.
    """
    if not _sdk_available:
        raise RuntimeError(
            "configure_openai_agents_for_proxy requires the 'openai-agents' package, "
            "which is not installed in this environment."
        )

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    set_default_openai_client(client)
    set_default_openai_api("responses")
    set_default_openai_key(api_key)
    # The OAuth proxy supports Responses requests but not OpenAI trace export.
    # The placeholder proxy key would otherwise produce one non-fatal 401 per span.
    set_tracing_disabled(True)


def build_model_settings(params: LLMParams) -> "ModelSettings":
    """Map LLMParams to an openai-agents ModelSettings instance.

    - max_tokens is always forwarded.
    - temperature is only set when not None.
    - reasoning is only set when reasoning_effort is truthy and != "none".

    Raises:
        RuntimeError: if the openai-agents SDK is not installed.
    """
    if not _sdk_available:
        raise RuntimeError(
            "OpenAIAgentsBackend requires the 'openai-agents' package, which is not "
            "installed in this environment."
        )

    kwargs: dict[str, Any] = {"max_tokens": params.max_tokens}

    if params.temperature is not None:
        kwargs["temperature"] = params.temperature

    if params.parallel_tool_calls is not None:
        kwargs["parallel_tool_calls"] = params.parallel_tool_calls

    if params.reasoning_effort and params.reasoning_effort != "none":
        kwargs["reasoning"] = Reasoning(effort=params.reasoning_effort)

    return ModelSettings(**kwargs)


def build_mcp_server(name: str, registry: McpServerRegistry) -> "MCPServerStdio":
    """Build an MCPServerStdio for *name* using spec from *registry*.

    Raises:
        RuntimeError: if the openai-agents SDK is not installed.
        KeyError: if *name* is not in the registry.
    """
    if not _sdk_available:
        raise RuntimeError(
            "OpenAIAgentsBackend requires the 'openai-agents' package, which is not "
            "installed in this environment."
        )

    spec = registry.get(name)

    params = MCPServerStdioParams(
        command=spec.command,
        args=list(spec.args),
        env=dict(spec.env) if spec.env else None,
        cwd=None,
    )

    return MCPServerStdio(
        params=params,
        client_session_timeout_seconds=spec.read_timeout_seconds,
        cache_tools_list=True,
        name=name,
    )


def build_agent(spec: AgentSpec, mcp_servers: list) -> "Agent":
    """Construct an openai-agents Agent from an AgentSpec.

    Raises:
        RuntimeError: if the openai-agents SDK is not installed.
    """
    if not _sdk_available:
        raise RuntimeError(
            "OpenAIAgentsBackend requires the 'openai-agents' package, which is not "
            "installed in this environment."
        )

    return Agent(
        name=spec.name,
        instructions=spec.instructions,
        model=spec.model,
        model_settings=build_model_settings(spec.params),
        mcp_servers=mcp_servers,
        output_type=spec.output_schema,
    )


class OpenAIAgentsBackend(LLMBackend):
    """LLMBackend adapter that delegates to the openai-agents 0.7.x SDK.

    Must be run in an environment where openai-agents is installed.
    Calling ``run()`` when the SDK is absent raises a clear RuntimeError;
    the constructor itself never fails.

    Import guard: the module-level try/except means this file can be imported
    (and tests collected) even when openai-agents is not installed.  A clear
    RuntimeError is raised at call time instead.
    """

    name = "openai_agents"

    def __init__(
        self,
        registry: McpServerRegistry,
        runner: Optional[Any] = None,
    ) -> None:
        self._registry = registry
        # Injectable for testing; defaults to the real SDK Runner class.
        self._runner = runner if runner is not None else Runner

    async def run(self, spec: AgentSpec, user_input: Any) -> LLMResult:
        """Build an openai-agents Agent, attach MCP servers, run, return result.

        Uses AsyncExitStack to guarantee each MCPServerStdio is connected on
        entry and cleaned up on exit — even if runner.run() raises.

        Raises:
            RuntimeError: if openai-agents is not installed in the current environment.
        """
        if not _sdk_available:
            raise RuntimeError(
                "OpenAIAgentsBackend requires the 'openai-agents' package, which is not "
                "installed in this environment.  Install it or switch to a different "
                "LLMBackend."
            )

        async with contextlib.AsyncExitStack() as stack:
            servers = [
                await stack.enter_async_context(build_mcp_server(srv_name, self._registry))
                for srv_name in spec.mcp_servers
            ]

            agent = build_agent(spec, servers)

            result = await self._runner.run(
                agent,
                user_input,
                max_turns=spec.params.max_iterations,
            )

        text = result.final_output if isinstance(result.final_output, str) else ""
        structured = result.final_output if spec.output_schema is not None else None

        return LLMResult(
            text=text,
            structured=structured,
            response_id=getattr(result, "last_response_id", None),
            raw=result,
        )
