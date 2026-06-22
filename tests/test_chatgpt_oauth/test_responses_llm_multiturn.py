"""Regression test for the multi-turn tool-call loop in OpenAIResponsesLLM.

Locks in the fix for the OAuth/Codex (store=False) backend: because the proxy
strips `previous_response_id` and store=False has no server-side state, every
turn must re-send the FULL conversation in `input` — including the originating
`function_call` paired with its `function_call_output`. Sending the tool result
alone previously produced:
    "No tool call found for function call output with call_id ..." (400)

These tests assert that:
  1. No `previous_response_id` is ever sent (client-side state only).
  2. On the second turn, `input` contains the function_call AND its matching
     function_call_output (same call_id), so the pairing the backend requires
     is present.
"""
import asyncio
import types

from cores.llm.openai_responses_llm import OpenAIResponsesLLM


class _Part:
    def __init__(self, text):
        self.text = text


class _Item:
    def __init__(self, type, content=None, name=None, call_id=None, arguments=None):
        self.type = type
        self.content = content
        self.name = name
        self.call_id = call_id
        self.arguments = arguments


class _Resp:
    def __init__(self, id, output):
        self.id = id
        self.output = output


class _FakeResponses:
    """Records every responses.create() call; turn 1 asks for a tool, turn 2 ends."""

    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return _Resp(
                "resp_1",
                [_Item("function_call", name="get_time", call_id="call_abc", arguments="{}")],
            )
        return _Resp("resp_2", [_Item("message", content=[_Part("FINAL ANSWER")])])


class _FakeClient:
    def __init__(self, *a, **k):
        self.responses = _FakeResponses()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _run_multiturn():
    import cores.llm.openai_responses_llm as mod

    fake_client = _FakeClient()
    orig_async_openai = mod.AsyncOpenAI
    mod.AsyncOpenAI = lambda *a, **k: fake_client

    llm = OpenAIResponsesLLM.__new__(OpenAIResponsesLLM)
    llm.instruction = "system"

    # Stub the OpenAIAugmentedLLM machinery the loop depends on.
    params = types.SimpleNamespace(
        max_iterations=5, maxTokens=1000, reasoning_effort=None,
        stopSequences=None, systemPrompt=None, tool_filter=None,
    )
    llm.get_request_params = lambda rp: params
    llm._reasoning = lambda model: False
    llm._reasoning_effort = None

    async def _select_model(p):
        return "gpt-5.5"

    async def _list_tools(tool_filter=None):
        tool = types.SimpleNamespace(name="get_time", description="", inputSchema={})
        return types.SimpleNamespace(tools=[tool])

    async def _call_mcp_tool(name, arguments, call_id):
        return "12:00"

    llm.select_model = _select_model
    llm.agent = types.SimpleNamespace(list_tools=_list_tools)
    llm._call_mcp_tool = _call_mcp_tool
    llm.get_provider_config = lambda ctx=None: types.SimpleNamespace(
        api_key="k", base_url="http://x"
    )
    # `context` is a read-only property backed by `_context`; get_provider_config
    # is stubbed above to ignore it, so the value itself is irrelevant.
    llm._context = None
    llm._log_chat_progress = lambda **k: None
    llm._log_chat_finished = lambda **k: None

    try:
        result = await llm.generate_str("hello")
    finally:
        mod.AsyncOpenAI = orig_async_openai

    assert result == "FINAL ANSWER"
    calls = fake_client.responses.calls
    assert len(calls) == 2, "expected exactly two turns"

    # 1. previous_response_id must NEVER be sent (store=False has no server state).
    for c in calls:
        assert "previous_response_id" not in c

    # 2. Turn-2 input must contain the originating function_call AND its output,
    #    paired by call_id, so the backend can match them.
    turn2_input = calls[1]["input"]
    fc = [i for i in turn2_input if i.get("type") == "function_call"]
    fco = [i for i in turn2_input if i.get("type") == "function_call_output"]
    assert fc and fc[0]["call_id"] == "call_abc"
    assert fco and fco[0]["call_id"] == "call_abc"
    assert fco[0]["output"] == "12:00"
    # The original user message is still present (full context re-sent).
    assert any(i.get("role") == "user" for i in turn2_input)


def test_multiturn_sends_full_input_and_no_previous_response_id():
    """Sync wrapper so the suite runs without pytest-asyncio."""
    asyncio.run(_run_multiturn())


if __name__ == "__main__":
    test_multiturn_sends_full_input_and_no_previous_response_id()
    print("PASS: multi-turn loop re-sends full input, no previous_response_id")
