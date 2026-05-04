"""
OpenAI Responses API LLM for mcp-agent trading agents.

Replaces Chat Completions (client.chat.completions.create) with the Responses API
(client.responses.create), using previous_response_id so only tool results—not the
full accumulated message history—are re-sent on each tool-call iteration.

Drop-in replacement: swap attach_llm(OpenAIAugmentedLLM) →
                               attach_llm(OpenAIResponsesLLM)
"""
import json
from typing import List, Optional

from openai import AsyncOpenAI
from mcp.types import (
    CallToolRequest,
    CallToolRequestParams,
    EmbeddedResource,
    TextContent,
    TextResourceContents,
)

from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM


class OpenAIResponsesLLM(OpenAIAugmentedLLM):
    """
    OpenAIAugmentedLLM variant that drives the agentic tool-call loop via the
    Responses API instead of Chat Completions.

    Token efficiency improvement: after the first turn, only the new tool results
    (not the entire conversation history) are sent to OpenAI on each iteration,
    because the server tracks state via previous_response_id.
    """

    async def generate_str(
        self,
        message,
        request_params: Optional[RequestParams] = None,
    ) -> str:
        params = self.get_request_params(request_params)
        model = await self.select_model(params)

        # Collect MCP tools in Responses API format (flat, no "function" wrapper)
        tools_result = await self.agent.list_tools(tool_filter=params.tool_filter)
        tools: Optional[List] = (
            [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema,
                }
                for tool in tools_result.tools
            ]
            or None
        )

        # Build initial input (developer system prompt + user message)
        input_items: List = []
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
            raise RuntimeError("OpenAI provider config is missing from mcp_agent.config.yaml")

        previous_response_id: Optional[str] = None
        final_text = ""

        async with AsyncOpenAI(
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        ) as client:
            for i in range(params.max_iterations):
                self._log_chat_progress(chat_turn=i, model=model)

                call_kwargs = {**base_kwargs, "input": input_items}
                if previous_response_id:
                    call_kwargs["previous_response_id"] = previous_response_id

                response = await client.responses.create(**call_kwargs)  # type: ignore[attr-defined]
                previous_response_id = response.id

                # Separate text content and function calls from output items
                text_parts: List[str] = []
                function_calls = []
                for item in response.output:
                    if item.type == "message":
                        for part in item.content:
                            if hasattr(part, "text"):
                                text_parts.append(part.text)
                    elif item.type == "function_call":
                        function_calls.append(item)

                if not function_calls:
                    final_text = "\n".join(text_parts)
                    break

                # Execute all tool calls via MCP and collect results
                tool_result_items = []
                for fc in function_calls:
                    result_str = await self._call_mcp_tool(
                        name=fc.name,
                        arguments=fc.arguments,
                        call_id=fc.call_id,
                    )
                    tool_result_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": fc.call_id,
                            "output": result_str,
                        }
                    )

                # Next iteration only sends tool results; full context lives server-side
                input_items = tool_result_items

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
