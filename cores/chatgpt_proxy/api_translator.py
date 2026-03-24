"""Chat Completions <-> Responses API bidirectional translator.

Pure functions with no I/O or state. Fully unit-testable.
"""

import json
import time
from typing import Any


# Models not supported on Codex endpoint -> best available replacement
_MODEL_MAP: dict[str, str] = {
    "gpt-4o": "gpt-5.4-mini",
    "gpt-4o-mini": "gpt-5.4-mini",
    "gpt-4o-2024-08-06": "gpt-5.4-mini",
    "gpt-4-turbo": "gpt-5.4-mini",
    "gpt-4": "gpt-5.4-mini",
    "gpt-3.5-turbo": "gpt-5.4-mini",
    "o4-mini": "gpt-5.4-mini",
    "o3-mini": "gpt-5.4-mini",
}


def _map_model(model: str) -> str:
    """Map unsupported model names to Codex-compatible equivalents."""
    return _MODEL_MAP.get(model, model)


def translate_request(body: dict) -> dict:
    """Translate Chat Completions request to Responses API format.

    Key mappings:
    - messages -> input (with role translations)
    - max_tokens -> max_output_tokens
    - tools[].function.* -> tools[].* (flattened)
    - response_format -> text.format
    """
    translated: dict[str, Any] = {
        "model": _map_model(body.get("model", "gpt-4o")),
    }

    # Messages -> Input (with role mapping)
    # Extract system messages as instructions (required by Codex endpoint)
    messages = body.get("messages") or []
    system_parts = [m.get("content") or "" for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    translated["instructions"] = "\n\n".join(system_parts) if system_parts else "You are a helpful assistant."
    translated["input"] = _translate_messages_to_input(non_system)

    # Parameter passthrough
    for key in ("temperature", "top_p", "stop", "seed"):
        if key in body:
            translated[key] = body[key]

    # max_tokens -> max_output_tokens
    if "max_tokens" in body:
        translated["max_output_tokens"] = body["max_tokens"]

    # Tools (flatten nested function structure)
    if body.get("tools"):
        translated["tools"] = _translate_tools_request(body["tools"])

    if "tool_choice" in body:
        translated["tool_choice"] = body["tool_choice"]

    # response_format -> text.format
    # Chat Completions: {"type":"json_schema","json_schema":{"name":"...","schema":{...}}}
    # Responses API:    {"type":"json_schema","name":"...","schema":{...}}
    if "response_format" in body:
        rf = body["response_format"]
        if isinstance(rf, dict):
            fmt = dict(rf)
            # Flatten nested json_schema to top level
            if fmt.get("type") == "json_schema" and "json_schema" in fmt:
                inner = fmt.pop("json_schema")
                fmt.update(inner)
            translated["text"] = {"format": fmt}

    # ChatGPT backend requirements (discovered from open-source analysis)
    translated["store"] = False  # MANDATORY: store:true returns 400
    translated["stream"] = True  # MANDATORY: always stream upstream

    return translated


def _translate_messages_to_input(messages: list[dict]) -> list[dict]:
    """Translate Chat Completions messages to Responses API input format."""
    result = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        # Tool result messages
        if role == "tool":
            result.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id", ""),
                "output": content if isinstance(content, str) else json.dumps(content),
            })
            continue

        # Assistant messages with tool_calls
        if role == "assistant" and msg.get("tool_calls"):
            # Add the assistant's text content if any
            if content:
                result.append({"role": "assistant", "content": content})
            # Add each tool call as a separate function_call item
            for tc in (msg.get("tool_calls") or []):
                func = tc.get("function", {})
                result.append({
                    "type": "function_call",
                    "name": func.get("name", ""),
                    "call_id": tc.get("id", ""),
                    "arguments": func.get("arguments", "{}"),
                })
            continue

        translated_msg: dict[str, Any] = {"role": role}
        if content is not None:
            translated_msg["content"] = content

        result.append(translated_msg)

    return result


def _translate_tools_request(tools: list[dict]) -> list[dict]:
    """Flatten Chat Completions tool definitions to Responses API format.

    Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    """
    result = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool.get("function", {})
            flat_tool: dict[str, Any] = {
                "type": "function",
                "name": func.get("name", ""),
            }
            if "description" in func:
                flat_tool["description"] = func["description"]
            if "parameters" in func:
                flat_tool["parameters"] = func["parameters"]
            if "strict" in func:
                flat_tool["strict"] = func["strict"]
            result.append(flat_tool)
        else:
            result.append(tool)
    return result


def translate_response(response_body: dict, model: str) -> dict:
    """Translate Responses API response to Chat Completions format.

    Key mappings:
    - output[].content[].text -> choices[].message.content
    - output[].type==function_call -> choices[].message.tool_calls[]
    - usage fields mapped
    """
    resp_id = response_body.get("id", "resp_unknown")
    output = response_body.get("output", [])

    message: dict[str, Any] = {"role": "assistant"}
    tool_calls = []
    text_parts = []
    finish_reason = "stop"

    for item in output:
        item_type = item.get("type", "")

        if item_type == "message":
            # Extract text from content array
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text_parts.append(content.get("text", ""))

        elif item_type == "function_call":
            tool_calls.append({
                "id": item.get("call_id", ""),
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", "{}"),
                },
            })
            finish_reason = "tool_calls"

    if text_parts:
        message["content"] = "\n".join(text_parts)
    else:
        message["content"] = None

    if tool_calls:
        message["tool_calls"] = tool_calls

    # Map usage
    usage_in = response_body.get("usage", {})
    usage_out = {
        "prompt_tokens": usage_in.get("input_tokens", 0),
        "completion_tokens": usage_in.get("output_tokens", 0),
        "total_tokens": usage_in.get("total_tokens", 0),
    }

    return {
        "id": f"chatcmpl-{resp_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage_out,
    }


def translate_error(error_body: dict, status_code: int) -> tuple[dict, int]:
    """Translate Responses API error to Chat Completions error format."""
    error = error_body.get("error", {})
    if isinstance(error, str):
        error = {"message": error}

    return {
        "error": {
            "message": error.get("message", "Unknown error"),
            "type": error.get("type", "api_error"),
            "code": error.get("code", str(status_code)),
        }
    }, status_code


def collect_sse_to_response(sse_text: str) -> dict:
    """Parse SSE stream text and extract the final Responses API object.

    Looks for 'event: response.completed' or 'event: response.failed' events
    and extracts the JSON data from the subsequent data: line.
    """
    lines = sse_text.split("\n")
    current_event = ""
    data_lines: list[str] = []
    completed_data: dict | None = None
    failed_data: dict | None = None

    # Also track incremental text for delta reconstruction
    text_chunks: list[str] = []

    for line in lines:
        if line.startswith("event: "):
            # If we had accumulated data for a previous event, process it
            if data_lines and current_event:
                raw = "\n".join(data_lines)
                try:
                    parsed = json.loads(raw)
                    if current_event == "response.completed":
                        completed_data = parsed
                    elif current_event == "response.failed":
                        failed_data = parsed
                    elif current_event == "response.output_text.delta":
                        delta = parsed.get("delta", "")
                        if delta:
                            text_chunks.append(delta)
                except json.JSONDecodeError:
                    pass

            current_event = line[7:].strip()
            data_lines = []

        elif line.startswith("data: "):
            data_lines.append(line[6:])

        elif line == "" and data_lines and current_event:
            raw = "\n".join(data_lines)
            try:
                parsed = json.loads(raw)
                if current_event == "response.completed":
                    completed_data = parsed
                elif current_event == "response.failed":
                    failed_data = parsed
                elif current_event == "response.output_text.delta":
                    delta = parsed.get("delta", "")
                    if delta:
                        text_chunks.append(delta)
            except json.JSONDecodeError:
                pass
            data_lines = []

    # Process any remaining data
    if data_lines and current_event:
        raw = "\n".join(data_lines)
        try:
            parsed = json.loads(raw)
            if current_event == "response.completed":
                completed_data = parsed
            elif current_event == "response.failed":
                failed_data = parsed
        except json.JSONDecodeError:
            pass

    if completed_data:
        # Unwrap nested structure: {"type":"response.completed","response":{...}} -> {...}
        return completed_data.get("response", completed_data)

    if failed_data:
        return failed_data.get("response", failed_data)

    # Fallback: reconstruct from deltas if no completed event
    if text_chunks:
        return {
            "id": "resp_reconstructed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "".join(text_chunks)}],
                }
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    raise ValueError("No response.completed or response.failed event found in SSE stream")
