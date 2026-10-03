import json
from types import SimpleNamespace

import pytest

from live.scenario_llm import ScenarioModelError, parse_proposal, propose


@pytest.mark.parametrize("text", ['[]', '{"x":NaN}', '{"x":1,"x":2}',
                                   '```json\n{}\n```', '{} trailing', 'x'*24001])
def test_reject_ambiguous_or_invalid_output(text):
    with pytest.raises(ScenarioModelError):
        parse_proposal(text)


def test_exact_oauth_model_effort_fast_and_no_tools():
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text='{"action":"WAIT"}')
    p = propose({"valid": True, "as_of_ms": 1000000}, {}, {},
                generate=generate, clock=lambda: 1000)
    assert p == {"action": "WAIT"}
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-6-luna"
    assert calls[0]["reasoning_effort"] == "high"
    assert calls[0]["fast_tier"] is True
    assert calls[0]["mcp_profile"] is None
    assert json.loads(calls[0]["user_prompt"])["market_snapshot"]["valid"] is True


def test_late_result_rejected():
    times = iter([1000,1000,1080])
    with pytest.raises(ScenarioModelError, match="late_response"):
        propose({"valid":True,"as_of_ms":1000000}, {}, {},
                generate=lambda **kw:SimpleNamespace(text='{}'), clock=lambda:next(times))


@pytest.mark.parametrize("snapshot", [{"valid":False}, {"valid":True,"as_of_ms":0},
                                      {"valid":True,"as_of_ms":1001000}])
def test_stale_invalid_or_future_never_calls_model(snapshot):
    def forbidden(**kw):
        pytest.fail("model should not run")
    with pytest.raises(ScenarioModelError):
        propose(snapshot, {}, {}, generate=forbidden, clock=lambda:1000)


def test_model_failure_sanitized_without_retry():
    calls = []
    def fail(**kwargs):
        calls.append(1)
        raise RuntimeError("sensitive data")
    with pytest.raises(ScenarioModelError, match="oauth_model_failed") as exc:
        propose({"valid":True,"as_of_ms":1000000}, {}, {}, generate=fail, clock=lambda:1000)
    assert "sensitive" not in str(exc.value)
    assert calls == [1]
