"""
OpenAI Responses API LLM for mcp-agent trading agents.

Replaces Chat Completions (client.chat.completions.create) with the Responses API
(client.responses.create), driving the agentic tool-call loop turn by turn.

Conversation state is carried *client-side*: each turn we append the model's
function_call items and our function_call_output items to the running ``input``
list, and re-send the whole list. We deliberately do NOT use
``previous_response_id`` for state, because the ChatGPT/Codex (OAuth account)
backend forces ``store=False`` (``store=true`` returns 400) and the proxy strips
``previous_response_id`` — there is no server-side response to reference, so a
tool result sent alone (without its originating function_call in ``input``)
fails with "No tool call found for function call output with call_id ...".
Re-sending the full input is the only correct multi-turn flow under store=False,
and remains correct under store=True (API-key mode).

Drop-in replacement: swap attach_llm(OpenAIAugmentedLLM) →
                               attach_llm(OpenAIResponsesLLM)
"""

import json
import logging
import os
import time

from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    EmbeddedResource,
    TextContent,
    TextResourceContents,
)
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from openai import AsyncOpenAI, BadRequestError

logger = logging.getLogger(__name__)

# Trading requests provide their model and effort per call.  Suppress the
# mcp-agent default-model INFO message and emit authoritative telemetry below.
logging.getLogger("mcp_agent.workflows.llm.augmented_llm_openai").setLevel(
    logging.WARNING
)

_SERVICE_TIER_ALIASES = {
    "fast": "priority",
    "priority": "priority",
    "default": "default",
    "auto": "auto",
    "flex": "flex",
    "scale": "scale",
}
OPENAI_SERVICE_TIER = _SERVICE_TIER_ALIASES.get(
    os.getenv("OPENAI_SERVICE_TIER", "default").strip().lower(),
    "default",
)


def _service_tier_error(exc: Exception) -> bool:
    """Return whether a bad request specifically rejected tier negotiation."""

    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "service_tier",
            "service tier",
            "unsupported parameter",
            "priority",
            "fast mode",
        )
    )


def _usage_value(usage, name: str):
    return getattr(usage, name, None) if usage is not None else None


class OpenAIResponsesLLM(OpenAIAugmentedLLM):
    """
    OpenAIAugmentedLLM variant that drives the agentic tool-call loop via the
    Responses API instead of Chat Completions.

    Conversation state remains client-side: every turn re-sends the complete
    function-call and function-output chain because OAuth requests use store=False.
    """

    async def generate_str(
        self,
        message,
        request_params: RequestParams | None = None,
    ) -> str:
        params = self.get_request_params(request_params)
        model = await self.select_model(params)

        # Collect MCP tools in Responses API format (flat, no "function" wrapper)
        tools_result = await self.agent.list_tools(tool_filter=params.tool_filter)
        tools: list | None = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            }
            for tool in tools_result.tools
        ] or None

        # Build initial input (developer system prompt + user message)
        input_items: list = []
        system_prompt = self.instruction or params.systemPrompt
        if system_prompt:
            input_items.append({"role": "developer", "content": system_prompt})

        if isinstance(message, str):
            input_items.append({"role": "user", "content": message})
        elif isinstance(message, list):
            for m in message:
                if isinstance(m, str):
                    input_items.append({"role": "user", "content": m})
                elif isinstance(m, dict):
                    input_items.append(m)
        else:
            input_items.append({"role": "user", "content": str(message)})

        # Build kwargs shared across all iterations
        base_kwargs: dict = {"model": model, "tools": tools}
        requested_service_tier = OPENAI_SERVICE_TIER
        if requested_service_tier not in {"auto", "default"}:
            base_kwargs["service_tier"] = requested_service_tier

        if self._reasoning(model):
            effort = params.reasoning_effort or self._reasoning_effort
            if effort and effort != "none":
                # Responses API uses reasoning={"effort": ...} instead of reasoning_effort=
                base_kwargs["reasoning"] = {"effort": effort}
            base_kwargs["max_output_tokens"] = params.maxTokens
        else:
            base_kwargs["max_output_tokens"] = params.maxTokens

        if params.stopSequences:
            base_kwargs["stop"] = params.stopSequences

        provider_config = self.get_provider_config(self.context)
        if provider_config is None:
            raise RuntimeError(
                "OpenAI provider config is missing from mcp_agent.config.yaml"
            )

        final_text = ""
        agent_name = (
            getattr(self, "name", None)
            or getattr(getattr(self, "agent", None), "name", None)
            or "unknown"
        )

        async with AsyncOpenAI(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        ) as client:
            for i in range(params.max_iterations):
                self._log_chat_progress(chat_turn=i, model=model)

                # Always send the full accumulated conversation as `input`.
                # State is carried client-side (no previous_response_id) so the
                # loop works under store=False (OAuth/Codex) where the originating
                # function_call must accompany its function_call_output.
                call_kwargs = {**base_kwargs, "input": input_items}

                started = time.monotonic()
                tier_fallback = False
                try:
                    response = await client.responses.create(**call_kwargs)  # type: ignore[attr-defined]
                except BadRequestError as exc:
                    if "service_tier" not in call_kwargs or not _service_tier_error(
                        exc
                    ):
                        raise
                    fallback_kwargs = dict(call_kwargs)
                    fallback_kwargs.pop("service_tier", None)
                    # Remember the proxy capability for the remaining tool turns in
                    # this generation; do not pay for another guaranteed 400.
                    base_kwargs.pop("service_tier", None)
                    tier_fallback = True
                    logger.warning(
                        "[LLM] service tier rejected; retrying standard request "
                        "agent=%s model=%s requested_tier=%s error=%s",
                        agent_name,
                        model,
                        requested_service_tier,
                        str(exc)[:240],
                    )
                    response = await client.responses.create(**fallback_kwargs)  # type: ignore[attr-defined]

                # Separate text content and function calls from output items
                text_parts: list[str] = []
                function_calls = []
                for item in response.output:
                    if item.type == "message":
                        for part in item.content:
                            if hasattr(part, "text"):
                                text_parts.append(part.text)
                    elif item.type == "function_call":
                        function_calls.append(item)

                elapsed_ms = int((time.monotonic() - started) * 1000)
                usage = getattr(response, "usage", None)
                usage_details = getattr(usage, "output_tokens_details", None)
                response_tier = getattr(response, "service_tier", None)
                effective_tier = response_tier or (
                    "default(fallback)" if tier_fallback else "unknown"
                )
                logger.info(
                    "[LLM] agent=%s turn=%d model_requested=%s model_effective=%s "
                    "reasoning_effort=%s max_output_tokens=%s "
                    "service_tier_requested=%s service_tier_effective=%s "
                    "latency_ms=%d output_tokens=%s reasoning_tokens=%s tool_calls=%d",
                    agent_name,
                    i,
                    model,
                    getattr(response, "model", None) or "unknown",
                    base_kwargs.get("reasoning", {}).get("effort", "none"),
                    base_kwargs.get("max_output_tokens"),
                    requested_service_tier,
                    effective_tier,
                    elapsed_ms,
                    _usage_value(usage, "output_tokens"),
                    _usage_value(usage_details, "reasoning_tokens"),
                    len(function_calls),
                )

                if not function_calls:
                    final_text = "\n".join(text_parts)
                    break

                # Append the model's function_call items to the running input so
                # the next turn pairs each output with its originating call.
                for fc in function_calls:
                    input_items.append(
                        {
                            "type": "function_call",
                            "name": fc.name,
                            "call_id": fc.call_id,
                            "arguments": fc.arguments,
                        }
                    )

                # Execute all tool calls via MCP and append their results.
                for fc in function_calls:
                    result_str = await self._call_mcp_tool(
                        name=fc.name,
                        arguments=fc.arguments,
                        call_id=fc.call_id,
                    )
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": fc.call_id,
                            "output": result_str,
                        }
                    )

        self._log_chat_finished(model=model)
        return final_text

    async def _call_mcp_tool(self, name: str, arguments: str, call_id: str) -> str:
        """Execute one MCP tool call and return the result as a plain string."""
        try:
            args = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            args = {}

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=args),
        )
        result = await self.call_tool(request=request, tool_call_id=call_id)

        parts = []
        for content in result.content:
            if isinstance(content, TextContent):
                parts.append(content.text)
            elif isinstance(content, EmbeddedResource) and isinstance(
                content.resource, TextResourceContents
            ):
                parts.append(content.resource.text)
        return "\n".join(parts) if parts else ""
