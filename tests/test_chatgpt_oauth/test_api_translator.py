"""Unit tests for ChatGPT OAuth API Translator."""

import json
import pytest
from cores.chatgpt_proxy.api_translator import (
    translate_request,
    translate_response,
    translate_error,
    collect_sse_to_response,
)


class TestTranslateRequest:
    """Test Chat Completions -> Responses API request translation."""

    def test_basic_text_request(self):
        body = {
            "model": "gpt-5",
            "messages": [
                {"role": "system", "content": "You are a stock analyst."},
                {"role": "user", "content": "Analyze AAPL"},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        result = translate_request(body)

        assert result["model"] == "gpt-5"
        assert result["input"][0]["role"] == "developer"
        assert result["input"][0]["content"] == "You are a stock analyst."
        assert result["input"][1]["role"] == "user"
        assert result["max_output_tokens"] == 4096
        assert result["temperature"] == 0.7
        assert result["store"] is False
        assert result["stream"] is True
        assert "messages" not in result
        assert "max_tokens" not in result

    def test_tool_definitions_flattened(self):
        body = {
            "model": "gpt-5",
            "messages": [{"role": "user", "content": "Get price"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_stock_info",
                        "description": "Get stock information",
                        "parameters": {
                            "type": "object",
                            "properties": {"ticker": {"type": "string"}},
                        },
                    },
                }
            ],
        }
        result = translate_request(body)

        assert len(result["tools"]) == 1
        tool = result["tools"][0]
        assert tool["type"] == "function"
        assert tool["name"] == "get_stock_info"
        assert tool["description"] == "Get stock information"
        assert "function" not in tool  # flattened, not nested

    def test_tool_result_message(self):
        body = {
            "model": "gpt-5",
            "messages": [
                {"role": "user", "content": "Get AAPL price"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_123",
                            "type": "function",
                            "function": {
                                "name": "get_price",
                                "arguments": '{"ticker": "AAPL"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_123",
                    "content": "AAPL: $150.00",
                },
            ],
        }
        result = translate_request(body)
        inputs = result["input"]

        # User message
        assert inputs[0]["role"] == "user"
        # Assistant tool call -> function_call
        assert inputs[1]["type"] == "function_call"
        assert inputs[1]["name"] == "get_price"
        assert inputs[1]["call_id"] == "call_123"
        # Tool result -> function_call_output
        assert inputs[2]["type"] == "function_call_output"
        assert inputs[2]["call_id"] == "call_123"
        assert inputs[2]["output"] == "AAPL: $150.00"

    def test_response_format_mapped(self):
        body = {
            "model": "gpt-5",
            "messages": [{"role": "user", "content": "Return JSON"}],
            "response_format": {"type": "json_object"},
        }
        result = translate_request(body)
        assert result["text"] == {"format": {"type": "json_object"}}

    def test_store_false_and_stream_true_always_set(self):
        body = {
            "model": "gpt-5",
            "messages": [{"role": "user", "content": "hello"}],
        }
        result = translate_request(body)
        assert result["store"] is False
        assert result["stream"] is True

    def test_passthrough_params(self):
        body = {
            "model": "gpt-5",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.5,
            "top_p": 0.9,
            "stop": ["\n"],
            "seed": 42,
        }
        result = translate_request(body)
        assert result["temperature"] == 0.5
        assert result["top_p"] == 0.9
        assert result["stop"] == ["\n"]
        assert result["seed"] == 42


class TestTranslateResponse:
    """Test Responses API -> Chat Completions response translation."""

    def test_basic_text_response(self):
        resp = {
            "id": "resp_abc123",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "AAPL analysis..."}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 150,
                "output_tokens": 800,
                "total_tokens": 950,
            },
        }
        result = translate_response(resp, "gpt-5")

        assert result["id"] == "chatcmpl-resp_abc123"
        assert result["object"] == "chat.completion"
        assert result["model"] == "gpt-5"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["message"]["content"] == "AAPL analysis..."
        assert result["choices"][0]["finish_reason"] == "stop"
        assert result["usage"]["prompt_tokens"] == 150
        assert result["usage"]["completion_tokens"] == 800

    def test_tool_call_response(self):
        resp = {
            "id": "resp_xyz",
            "output": [
                {
                    "type": "function_call",
                    "name": "get_stock_info",
                    "call_id": "call_abc",
                    "arguments": '{"ticker": "005930"}',
                }
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        }
        result = translate_response(resp, "gpt-5")

        msg = result["choices"][0]["message"]
        assert msg["content"] is None
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "call_abc"
        assert msg["tool_calls"][0]["function"]["name"] == "get_stock_info"
        assert result["choices"][0]["finish_reason"] == "tool_calls"

    def test_empty_output(self):
        resp = {"id": "resp_empty", "output": [], "usage": {}}
        result = translate_response(resp, "gpt-5")
        assert result["choices"][0]["message"]["content"] is None
        assert result["choices"][0]["finish_reason"] == "stop"


class TestTranslateError:
    """Test error translation."""

    def test_basic_error(self):
        error_body = {
            "error": {"message": "Rate limited", "type": "rate_limit", "code": 429}
        }
        result, status = translate_error(error_body, 429)
        assert status == 429
        assert result["error"]["message"] == "Rate limited"

    def test_string_error(self):
        error_body = {"error": "Something went wrong"}
        result, status = translate_error(error_body, 500)
        assert result["error"]["message"] == "Something went wrong"


class TestCollectSSE:
    """Test SSE stream parsing."""

    def test_completed_event(self):
        sse = (
            "event: response.output_text.delta\n"
            'data: {"delta": "Hello "}\n\n'
            "event: response.output_text.delta\n"
            'data: {"delta": "world"}\n\n'
            "event: response.completed\n"
            'data: {"id": "resp_123", "output": [{"type": "message", "role": "assistant", '
            '"content": [{"type": "output_text", "text": "Hello world"}]}], '
            '"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}\n\n'
        )
        result = collect_sse_to_response(sse)
        assert result["id"] == "resp_123"
        assert result["output"][0]["content"][0]["text"] == "Hello world"

    def test_failed_event(self):
        sse = (
            "event: response.failed\n"
            'data: {"error": {"message": "Model overloaded", "type": "server_error"}}\n\n'
        )
        result = collect_sse_to_response(sse)
        assert "error" in result

    def test_delta_reconstruction_fallback(self):
        sse = (
            "event: response.output_text.delta\n"
            'data: {"delta": "Hello "}\n\n'
            "event: response.output_text.delta\n"
            'data: {"delta": "world"}\n\n'
        )
        result = collect_sse_to_response(sse)
        assert result["output"][0]["content"][0]["text"] == "Hello world"

    def test_empty_stream_raises(self):
        with pytest.raises(ValueError):
            collect_sse_to_response("")
