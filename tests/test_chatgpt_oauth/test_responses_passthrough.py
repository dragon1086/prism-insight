"""Unit tests for prepare_responses_passthrough in api_translator."""

import pytest
from cores.chatgpt_proxy.api_translator import prepare_responses_passthrough


class TestPrepareResponsesPassthrough:
    """Test that prepare_responses_passthrough prepares a Responses API body correctly."""

    def test_forces_store_false(self):
        body = {"model": "gpt-5.4-mini", "input": [{"role": "user", "content": "hi"}]}
        result = prepare_responses_passthrough(body)
        assert result["store"] is False

    def test_forces_stream_true(self):
        body = {"model": "gpt-5.4-mini", "input": [{"role": "user", "content": "hi"}]}
        result = prepare_responses_passthrough(body)
        assert result["stream"] is True

    def test_maps_gpt4o_to_gpt54mini(self):
        body = {"model": "gpt-4o", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "gpt-5.4-mini"

    def test_maps_gpt4o_mini_to_gpt54mini(self):
        body = {"model": "gpt-4o-mini", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "gpt-5.4-mini"

    def test_unknown_model_passes_through_unchanged(self):
        body = {"model": "gpt-5.4-mini", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "gpt-5.4-mini"

    def test_unknown_custom_model_passes_through(self):
        body = {"model": "my-custom-model-v2", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "my-custom-model-v2"

    def test_strips_previous_response_id(self):
        # Codex/ChatGPT-account endpoint rejects previous_response_id and it is
        # non-functional under the forced store=False. Multi-turn tool-calling
        # agents (e.g. gpt-5.5 buy decision) carry full history in `input`, so
        # dropping it is lossless and prevents the 400 that fell decisions back
        # to default_scenario (No Entry).
        body = {
            "model": "gpt-5.5",
            "input": [{"role": "user", "content": "hi"}],
            "previous_response_id": "resp_abc123",
        }
        result = prepare_responses_passthrough(body)
        assert "previous_response_id" not in result
        assert result["model"] == "gpt-5.5"

    def test_maps_gpt5_nano_to_gpt54mini(self):
        body = {"model": "gpt-5-nano", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "gpt-5.4-mini"

    def test_injects_default_instructions_when_missing(self):
        body = {"model": "gpt-5.4-mini", "input": []}
        result = prepare_responses_passthrough(body)
        assert result["instructions"] == "You are a helpful assistant."

    def test_injects_default_instructions_when_empty_string(self):
        body = {"model": "gpt-5.4-mini", "input": [], "instructions": ""}
        result = prepare_responses_passthrough(body)
        assert result["instructions"] == "You are a helpful assistant."

    def test_preserves_existing_instructions(self):
        body = {"model": "gpt-5.4-mini", "input": [], "instructions": "You are a stock analyst."}
        result = prepare_responses_passthrough(body)
        assert result["instructions"] == "You are a stock analyst."

    def test_preserves_input_unchanged(self):
        input_val = [{"role": "user", "content": "analyze AAPL"}]
        body = {"model": "gpt-5.4-mini", "input": input_val}
        result = prepare_responses_passthrough(body)
        assert result["input"] == input_val

    def test_preserves_tools_unchanged(self):
        tools = [{"type": "function", "name": "get_price", "parameters": {}}]
        body = {"model": "gpt-5.4-mini", "input": [], "tools": tools}
        result = prepare_responses_passthrough(body)
        assert result["tools"] == tools

    def test_preserves_text_format(self):
        text = {"format": {"type": "json_schema", "name": "Output", "schema": {}}}
        body = {"model": "gpt-5.4-mini", "input": [], "text": text}
        result = prepare_responses_passthrough(body)
        assert result["text"] == text

    def test_preserves_reasoning(self):
        reasoning = {"effort": "high"}
        body = {"model": "gpt-5.4-mini", "input": [], "reasoning": reasoning}
        result = prepare_responses_passthrough(body)
        assert result["reasoning"] == reasoning

    def test_strips_max_output_tokens(self):
        # Codex backend rejects max_output_tokens (live-verified 400). It must be stripped.
        body = {"model": "gpt-5.4-mini", "input": [], "max_output_tokens": 4096}
        result = prepare_responses_passthrough(body)
        assert "max_output_tokens" not in result

    def test_preserves_temperature(self):
        body = {"model": "gpt-5.4-mini", "input": [], "temperature": 0.7}
        result = prepare_responses_passthrough(body)
        assert result["temperature"] == 0.7

    def test_preserves_top_p(self):
        body = {"model": "gpt-5.4-mini", "input": [], "top_p": 0.9}
        result = prepare_responses_passthrough(body)
        assert result["top_p"] == 0.9

    def test_preserves_tool_choice(self):
        body = {"model": "gpt-5.4-mini", "input": [], "tool_choice": "auto"}
        result = prepare_responses_passthrough(body)
        assert result["tool_choice"] == "auto"

    def test_does_not_mutate_input_dict(self):
        body = {"model": "gpt-4o", "input": [], "store": True, "stream": False}
        original_model = body["model"]
        original_store = body["store"]
        original_stream = body["stream"]

        prepare_responses_passthrough(body)

        # Input dict must be unchanged
        assert body["model"] == original_model
        assert body["store"] == original_store
        assert body["stream"] == original_stream

    def test_missing_model_defaults_to_gpt54mini(self):
        body = {"input": []}
        result = prepare_responses_passthrough(body)
        assert result["model"] == "gpt-5.4-mini"

    def test_overrides_caller_store_true(self):
        """Caller passing store=True must be overridden — Codex requires store=False."""
        body = {"model": "gpt-5.4-mini", "input": [], "store": True}
        result = prepare_responses_passthrough(body)
        assert result["store"] is False

    def test_overrides_caller_stream_false(self):
        """Caller passing stream=False must be overridden — Codex requires stream=True."""
        body = {"model": "gpt-5.4-mini", "input": [], "stream": False}
        result = prepare_responses_passthrough(body)
        assert result["stream"] is True
