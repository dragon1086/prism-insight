# openai_agents_backend.py

"""
openai-agents SDK backend: implements LLMBackend using the openai-agents 0.7.x SDK.

This is the Phase 2 LLM port adapter.  All openai-agents imports are guarded so
this module can be imported (and tests collected) even if the SDK is not installed.
A clear RuntimeError is raised at *call time* rather than at import time.
"""

import contextlib
import json
from decimal import Decimal
from typing import Any, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from cores.llm.mcp_registry import McpServerRegistry
from cores.llm.ports import (
    AgentSpec,
    DeferredValidationSchema,
    LLMBackend,
    LLMParams,
    LLMResult,
)

# --- SDK import guard ---------------------------------------------------
try:
    from agents import Agent, AgentOutputSchemaBase, ModelSettings, Runner
    from agents.exceptions import ModelBehaviorError
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_default_openai_key,
        set_tracing_disabled,
    )
    from agents.mcp import MCPServerStdio, MCPServerStdioParams
    from agents.strict_schema import ensure_strict_json_schema
    from openai import APITimeoutError, AsyncOpenAI, Timeout
    from openai.types.shared import Reasoning

    _sdk_available = True
except ImportError:
    Agent = None  # type: ignore[assignment,misc]
    AgentOutputSchemaBase = object  # type: ignore[assignment,misc]
    ModelSettings = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]
    MCPServerStdio = None  # type: ignore[assignment]
    MCPServerStdioParams = None  # type: ignore[assignment]
    Reasoning = None  # type: ignore[assignment]
    ModelBehaviorError = None  # type: ignore[assignment,misc]
    ensure_strict_json_schema = None  # type: ignore[assignment]
    set_default_openai_api = None  # type: ignore[assignment]
    set_default_openai_client = None  # type: ignore[assignment]
    set_default_openai_key = None  # type: ignore[assignment]
    set_tracing_disabled = None  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]
    APITimeoutError = TimeoutError  # type: ignore[assignment,misc]
    Timeout = None  # type: ignore[assignment,misc]
    _sdk_available = False
# ------------------------------------------------------------------------


CHATGPT_OAUTH_CONNECT_TIMEOUT_SECONDS = 5.0
CHATGPT_OAUTH_TRANSPORT_TIMEOUT_SECONDS = 300.0
CHATGPT_OAUTH_MAX_RETRIES = 0


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
    assert AsyncOpenAI is not None and Timeout is not None

    # The SDK trace exporter uses the default API key and OpenAI's public
    # endpoint rather than this custom client.  With the OAuth proxy's
    # placeholder key that creates an unrelated 401 and leaks trace metadata
    # outside the explicitly bounded proxy call.
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=Timeout(
            CHATGPT_OAUTH_TRANSPORT_TIMEOUT_SECONDS,
            connect=CHATGPT_OAUTH_CONNECT_TIMEOUT_SECONDS,
        ),
        max_retries=CHATGPT_OAUTH_MAX_RETRIES,
    )
    set_default_openai_client(client)
    set_default_openai_api("responses")
    set_default_openai_key(api_key)


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

    output_type = spec.output_schema
    if isinstance(output_type, DeferredValidationSchema):
        output_type = _DeferredOpenAIAgentsOutputSchema(output_type.model_type)

    return Agent(
        name=spec.name,
        instructions=spec.instructions,
        model=spec.model,
        model_settings=build_model_settings(spec.params),
        mcp_servers=mcp_servers,
        output_type=output_type,
    )


class _DeferredOpenAIAgentsOutputSchema(AgentOutputSchemaBase):  # type: ignore[misc]
    """OpenAI Agents schema that validates JSON shape without model semantics."""

    def __init__(self, model_type: type) -> None:
        try:
            schema = model_type.model_json_schema()
        except AttributeError as exc:
            raise TypeError("deferred validation requires a Pydantic model type") from exc
        strict_schema = ensure_strict_json_schema
        if strict_schema is None:
            raise RuntimeError("OpenAI Agents strict schema support is unavailable")
        self._model_type = model_type
        self._schema = strict_schema(_shape_only_json_schema(schema))
        self._validator = Draft202012Validator(self._schema)

    def is_plain_text(self) -> bool:
        return False

    def name(self) -> str:
        return self._model_type.__name__

    def json_schema(self) -> dict[str, Any]:
        return self._schema

    def is_strict_json_schema(self) -> bool:
        return True

    def validate_json(self, json_str: str) -> Any:
        try:
            decoded = json.loads(json_str, parse_float=Decimal)
            self._validator.validate(decoded)
        except (json.JSONDecodeError, ValidationError):
            raise ModelBehaviorError(  # type: ignore[misc]
                "Model output did not match the required JSON shape"
            ) from None
        return decoded


_NON_STRUCTURAL_JSON_SCHEMA_KEYS = frozenset(
    {
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)
_NAMED_SCHEMA_MAP_KEYS = frozenset(
    {"$defs", "dependentSchemas", "patternProperties", "properties"}
)


def _shape_only_json_schema(value: Any) -> Any:
    """Remove value-level constraints while preserving JSON container shape."""

    if isinstance(value, list):
        return [_shape_only_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in _NON_STRUCTURAL_JSON_SCHEMA_KEYS:
            continue
        if key in _NAMED_SCHEMA_MAP_KEYS and isinstance(item, dict):
            result[key] = {
                name: _shape_only_json_schema(schema)
                for name, schema in item.items()
            }
        else:
            result[key] = _shape_only_json_schema(item)
    return result


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

            try:
                result = await self._runner.run(
                    agent,
                    user_input,
                    max_turns=spec.params.max_iterations,
                )
            except APITimeoutError as exc:
                raise TimeoutError("LLM backend transport timed out") from exc

        text = result.final_output if isinstance(result.final_output, str) else ""
        structured = result.final_output if spec.output_schema is not None else None

        return LLMResult(
            text=text,
            structured=structured,
            response_id=getattr(result, "last_response_id", None),
            raw=result,
        )
