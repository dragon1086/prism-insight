"""Regression tests for Codex-backend constraints discovered during LIVE verification
of the openai-agents -> /v1/responses path (2026-06-18).

Each constraint below was observed as a real 400 from chatgpt.com/backend-api/codex:
  - {"detail":"Input must be a list"}            -> input string must be wrapped
  - {"detail":"Unsupported parameter: max_output_tokens"} -> strip max_output_tokens
  - empty `tools: []` rejected                   -> drop empty tools
These tests lock in `prepare_responses_passthrough` so the fixes never regress.
"""
from cores.chatgpt_proxy.api_translator import prepare_responses_passthrough


def test_string_input_wrapped_into_list():
    out = prepare_responses_passthrough({"model": "gpt-5.4-mini", "input": "say hi"})
    assert out["input"] == [{"role": "user", "content": "say hi"}]


def test_list_input_preserved():
    items = [{"role": "user", "content": "x"}, {"type": "function_call_output", "call_id": "c", "output": "y"}]
    out = prepare_responses_passthrough({"model": "m", "input": items})
    assert out["input"] == items


def test_max_output_tokens_stripped():
    out = prepare_responses_passthrough({"model": "m", "input": [], "max_output_tokens": 500})
    assert "max_output_tokens" not in out


def test_include_stripped():
    out = prepare_responses_passthrough({"model": "m", "input": [], "include": []})
    assert "include" not in out


def test_empty_tools_dropped():
    out = prepare_responses_passthrough({"model": "m", "input": [], "tools": []})
    assert "tools" not in out


def test_nonempty_tools_kept():
    tools = [{"type": "function", "name": "t", "parameters": {}}]
    out = prepare_responses_passthrough({"model": "m", "input": [], "tools": tools})
    assert out["tools"] == tools


def test_store_stream_forced_and_model_mapped():
    out = prepare_responses_passthrough({"model": "gpt-4o", "input": [], "store": True, "stream": False})
    assert out["store"] is False
    assert out["stream"] is True
    assert out["model"] == "gpt-5.4-mini"


def test_default_instructions_only_when_missing():
    assert prepare_responses_passthrough({"model": "m", "input": []})["instructions"] == "You are a helpful assistant."
    assert prepare_responses_passthrough({"model": "m", "input": [], "instructions": "custom"})["instructions"] == "custom"


def test_does_not_mutate_caller():
    body = {"model": "m", "input": "hi", "max_output_tokens": 10}
    prepare_responses_passthrough(body)
    assert body["input"] == "hi"
    assert body["max_output_tokens"] == 10
