"""Real OAuth adapter, fake opener only: no network or credentials needed."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.btc_research_model_call as research_model_call


@pytest.fixture(autouse=True)
def offline_runtime_readiness(monkeypatch):
    monkeypatch.setattr(research_model_call, '_runtime_readiness', lambda phase, timeout: {'status': 'PASSED', 'phase': phase})
    monkeypatch.setattr(research_model_call, '_readiness_still_safe', lambda proof, phase, timeout: None)

from live import scenario_oauth
from tools.btc_research_call_guard import CallGuard, CallGuardError
from tools.btc_research_model_call import GuardedModelCall, MAX_BYTES


ENDPOINT = "http://127.0.0.1:18741/v1/responses"


def request(**updates):
    return dict(system_prompt="research", user_prompt="frozen input", model="gpt-6-luna",
                reasoning_effort="high", fast_tier=True, timeout=10,
                response_schema={"type": "object", "properties": {}}, **updates)


def response_body(**updates):
    body = dict(status="completed", model="gpt-6-luna", output=[dict(type="message", role="assistant",
                content=[dict(type="output_text", text='{"action":"WAIT"}')])])
    body.update(updates)
    return json.dumps(body).encode()


class Response(BytesIO):
    status = 200

    def geturl(self):
        return ENDPOINT


@pytest.fixture(autouse=True)
def local_endpoint(monkeypatch):
    monkeypatch.setenv("PRISM_BTC_SCENARIO_OAUTH_URL", ENDPOINT)


def setup(tmp_path, *, cap=24, record=None, open_url=None, monotonic=None, arm="A"):
    g = CallGuard(tmp_path, "a" * 64, cap)
    events, opened = [], []

    def opener(req, timeout):
        opened.append((req, timeout))
        return Response(response_body())

    options = dict(open_url=opener if open_url is None else open_url)
    if monotonic is not None:
        options["monotonic"] = monotonic
    call = GuardedModelCall(g, arm, events.append if record is None else record, **options)
    return g, call, events, opened


def test_success_final_wire_hash_and_no_auth_headers(tmp_path, monkeypatch):
    g, call, events, opened = setup(tmp_path)
    real_generate = scenario_oauth.generate_scenario
    flags = []

    def generate(**kwargs):
        flags.append(kwargs["retry_transport"])
        return real_generate(**kwargs)

    monkeypatch.setattr(scenario_oauth, "generate_scenario", generate)
    assert call(**request()).text == '{"action":"WAIT"}'
    req = opened[0][0]
    sha = hashlib.sha256(req.data).hexdigest()
    assert [e["kind"] for e in events] == ["HTTP_REQUEST", "HTTP_RESPONSE", "MODEL_RESULT"]
    assert all(e["request_sha256"] == sha for e in events)
    assert events[0]["request_body"].encode() == req.data
    assert events[0]["generate_kwargs"]["retry_transport"] is False
    assert "headers" not in events[0]
    assert "authorization" not in json.dumps(events).lower()
    assert flags == [False]
    assert g.snapshot()["actual_http_calls"] == 1
    assert call.actual_http_calls == 1


@pytest.mark.parametrize("code", [401, 403, 502])
def test_http_failure_body_preserved_no_retry_other_arm_zero(tmp_path, code):
    opened = []
    body = json.dumps({"error": {"type": "server_error", "message": "local failure"}}).encode()

    def opener(req, timeout):
        opened.append(req)
        raise HTTPError(ENDPOINT, code, "failure", {}, BytesIO(body))

    g, call, events, _ = setup(tmp_path, open_url=opener)
    with pytest.raises(ValueError, match="scenario_oauth_failed"):
        call(**request())
    assert len(opened) == 1
    raw = next(e for e in events if e["kind"] == "HTTP_RESPONSE")
    assert raw["raw_response"].encode() == body
    failure = next(e for e in events if e["kind"] == "HTTP_ERROR")
    assert failure["http_status"] == code
    assert failure["authentication_error"] is (code in (401, 403))
    other = GuardedModelCall(g, "B", events.append, open_url=opener)
    with pytest.raises(CallGuardError):
        other(**request())
    assert len(opened) == 1
    assert g.snapshot()["actual_http_calls"] == 1


def test_http_error_body_available_to_adapter_again(tmp_path, monkeypatch):
    body = b'{"error":{"type":"server_error","message":"truncated"}}'

    def opener(req, timeout):
        raise HTTPError(ENDPOINT, 502, "bad", {}, BytesIO(body))

    observed = []

    def verify(error, endpoint):
        observed.append(error.read())
        return True

    monkeypatch.setattr(scenario_oauth, "_verified_truncation", verify)
    _, call, _, _ = setup(tmp_path, open_url=opener)
    with pytest.raises(ValueError):
        call(**request())
    assert observed == [body]


@pytest.mark.parametrize("delay, expected_http", [(2, 1), (11, 0)])
def test_request_record_elapsed_reduces_transport_timeout(tmp_path, delay, expected_http):
    now, events, opened = [100.], [], []

    def record(event):
        events.append(event)
        if event["kind"] == "HTTP_REQUEST":
            now[0] += delay

    def opener(req, timeout):
        opened.append(timeout)
        return Response(response_body())

    g, call, _, _ = setup(tmp_path, record=record, open_url=opener, monotonic=lambda: now[0])
    if expected_http:
        call(**request())
        assert 7.9 < opened[0] <= 8
    else:
        with pytest.raises(ValueError):
            call(**request())
        assert g.snapshot()["stopped"]
    assert len(opened) == g.snapshot()["actual_http_calls"] == expected_http
    assert call.actual_http_calls == expected_http
    assert g.snapshot()["reserved_attempts"] == 1


def test_record_failure_before_http_latches_without_call(tmp_path):
    def record(event):
        if event["kind"] == "HTTP_REQUEST":
            raise OSError("disk full")

    g, call, _, opened = setup(tmp_path, record=record)
    with pytest.raises(ValueError):
        call(**request())
    assert not opened
    assert g.snapshot()["actual_http_calls"] == 0
    assert g.snapshot()["stopped"]


@pytest.mark.parametrize("body", [b"invalid JSON", response_body(model="wrong"), response_body(status="failed")])
def test_model_errors_keep_raw_before_stop(tmp_path, body):
    g, call, events, _ = setup(tmp_path, open_url=lambda req, timeout: Response(body))
    with pytest.raises(ValueError):
        call(**request())
    assert next(e for e in events if e["kind"] == "HTTP_RESPONSE")["raw_response"].encode() == body
    assert g.snapshot()["stopped"]
    assert g.snapshot()["actual_http_calls"] == 1


def test_transport_error_counts_one_not_zero(tmp_path):
    def opener(req, timeout):
        raise TimeoutError("secret material must not enter error log")

    g, call, events, _ = setup(tmp_path, open_url=opener)
    with pytest.raises(ValueError):
        call(**request())
    assert g.snapshot()["actual_http_calls"] == 1
    assert "secret material" not in json.dumps(events)


def test_reservation_and_request_receipt_durable_before_opener(tmp_path):
    events = []

    def opener(req, timeout):
        state = g.snapshot()
        assert state["inflight"] == events[-1]["attempt_id"]
        assert state["reserved_attempts"] == 1
        assert events[-1]["kind"] == "HTTP_REQUEST"
        assert hashlib.sha256(req.data).hexdigest() == events[-1]["request_sha256"]
        return Response(response_body())

    g, call, _, _ = setup(tmp_path, record=events.append, open_url=opener)
    call(**request())


def test_second_opener_attempt_forbidden_even_if_adapter_regresses(tmp_path, monkeypatch):
    real_generate = scenario_oauth.generate_scenario

    def bad_adapter(**kwargs):
        real_generate(**kwargs)
        return real_generate(**kwargs)

    monkeypatch.setattr(scenario_oauth, "generate_scenario", bad_adapter)
    g, call, _, opened = setup(tmp_path)
    with pytest.raises(ValueError):
        call(**request())
    assert len(opened) == 1
    assert g.snapshot()["reserved_attempts"] == g.snapshot()["actual_http_calls"] == 1
    assert g.snapshot()["stopped"]


@pytest.mark.parametrize("extra", [{"retry_transport": True}, {"open_url": lambda *args: None}])
def test_retry_flag_or_opener_override_forbidden_before_http(tmp_path, extra):
    g, call, _, opened = setup(tmp_path)
    kwargs = request()
    kwargs.update(extra)
    with pytest.raises(ValueError):
        call(**kwargs)
    assert not opened
    assert g.snapshot()["reserved_attempts"] == 0
    assert g.snapshot()["stopped"]


def test_external_failure_stops_both_arms(tmp_path):
    g, call, events, opened = setup(tmp_path)
    call.on_failure(ValueError("schema_failure"))
    other = GuardedModelCall(g, "B", events.append, open_url=lambda *args: opened.append("B"))
    with pytest.raises(CallGuardError):
        other(**request())
    assert not opened


def test_phase_inferred_from_schema_and_bounded_raw(tmp_path):
    g, call, events, _ = setup(tmp_path, open_url=lambda req, timeout: Response(b"x" * (MAX_BYTES + 50)))
    kwargs = request()
    kwargs["response_schema"]["properties"] = {"selection_token": {"type": "string"}}
    with pytest.raises(ValueError):
        call(**kwargs)
    assert all(e["phase"] == "SELECT" for e in events)
    raw = next(e for e in events if e["kind"] == "HTTP_RESPONSE")
    assert len(raw["raw_response"]) == MAX_BYTES + 1
    assert raw["truncated"]
    assert g.snapshot()["stopped"]


def test_nonlocal_endpoint_fails_before_reserve(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_BTC_SCENARIO_OAUTH_URL", "https://remote.invalid/v1/responses")
    g, call, _, opened = setup(tmp_path)
    with pytest.raises(ValueError):
        call(**request())
    assert not opened
    assert g.snapshot()["reserved_attempts"] == 0


def test_two_arms_cap_race_never_more_than_one_http(tmp_path):
    g, call, events, opened = setup(tmp_path, cap=1)
    other = GuardedModelCall(g, "B", events.append,
                             open_url=lambda req, timeout: (opened.append((req, timeout)) or Response(response_body())))

    def attempt(generator):
        try:
            generator(**request())
        except (ValueError, CallGuardError):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(attempt, [call, other]))
    assert len(opened) <= 1
    assert g.snapshot()["reserved_attempts"] <= 1
    assert g.snapshot()["actual_http_calls"] == len(opened)
