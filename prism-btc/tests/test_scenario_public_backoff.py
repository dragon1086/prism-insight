"""Scenario-only public GET backoff; no model, broker, or real network."""
from types import SimpleNamespace

import pytest
import requests

from collector import bybit_public as public
from live.scenario_preview import collect_snapshot


def response(code=0, rows=None):
    return SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {"retCode": code, "result": {"list": rows or [["ok"]]}})


@pytest.fixture
def transport(monkeypatch):
    calls, sleeps, replies = [], [], []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(public.requests, "get", get)
    monkeypatch.setattr(public, "time", SimpleNamespace(sleep=sleeps.append))
    return calls, sleeps, replies


def test_scenario_recovers_only_transient_public_rate_limit(transport):
    calls, sleeps, replies = transport
    replies.extend([response(10006), response()])
    assert public._get_klines("30", retries=3, retry_rate_limit_only=True) == [["ok"]]
    assert len(calls) == 2 and sleeps == [1]


def test_persistent_rate_limit_has_three_attempt_cap_and_no_final_sleep(transport):
    calls, sleeps, replies = transport
    replies.extend([response(10006)] * 3)
    with pytest.raises(ValueError, match="^public_rate_limited$"):
        public._get_klines("30", retries=10, retry_rate_limit_only=True)
    assert len(calls) == 3 and sleeps == [1, 2]


@pytest.mark.parametrize("error", [requests.HTTPError("403 Forbidden"),
    requests.HTTPError("429"), requests.ConnectionError("connection"), requests.Timeout("timeout")])
def test_transport_errors_never_retry_in_scenario_mode(transport, error):
    calls, sleeps, replies = transport
    replies.append(error)
    with pytest.raises(type(error)):
        public._get_klines("30", retries=3, retry_rate_limit_only=True)
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize("code", [10001, 10009, 10003, "10006", None])
def test_other_api_errors_never_retry_in_scenario_mode(transport, code):
    calls, sleeps, replies = transport
    replies.append(response(code))
    with pytest.raises(RuntimeError):
        public._get_klines("30", retries=3, retry_rate_limit_only=True)
    assert len(calls) == 1 and not sleeps


@pytest.mark.parametrize("first", [response(10001), response(10006), requests.Timeout("timeout")])
def test_legacy_default_retry_behavior_is_unchanged(transport, first):
    calls, sleeps, replies = transport
    replies.extend([first, response()])
    assert public._get_klines("30", retries=2) == [["ok"]]
    assert len(calls) == 2 and sleeps == [1]


def test_legacy_default_still_sleeps_after_last_failure(transport):
    calls, sleeps, replies = transport
    replies.extend([response(10001)] * 2)
    with pytest.raises(RuntimeError, match="after 2 attempts"):
        public._get_klines("30", retries=2)
    assert len(calls) == 2 and sleeps == [1, 2]


@pytest.mark.parametrize("offset,elapsed,expected", [
    (100, 1, None), (299.5, 1, "candle_boundary_crossed_during_collection"),
    (100, 61, "collection_too_slow"),
])
def test_real_six_timeframe_collection_preserves_freshness_gates(monkeypatch, offset, elapsed, expected):
    from engine.scenario_snapshot import TIMEFRAME_MS
    from engine.config import TF_INTERVAL_MAP, PROTECTION_TF_INTERVAL_MAP
    import live.scenario_preview as preview_module
    intervals = {**TF_INTERVAL_MAP, **PROTECTION_TF_INTERVAL_MAP}
    lookup = {intervals[tf]: tf for tf in TIMEFRAME_MS}
    now = [1800000000. + offset]
    initial = now[0]
    calls, sleeps, model_calls = [], [], []
    def get(url, **kwargs):
        calls.append(kwargs["params"].copy())
        assert url.endswith("/v5/market/kline")
        if len(calls) == 1:
            return response(10006)
        tf = lookup[kwargs["params"]["interval"]]
        duration = TIMEFRAME_MS[tf]
        opened = int(now[0]*1000)//duration*duration
        rows = [[str(opened-i*duration), "100", "101", "99", "100", "10", "1000"]
                for i in range(401 if tf == "5m" else 100)]
        return response(rows=rows)
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += elapsed
    monkeypatch.setattr(public.requests, "get", get)
    monkeypatch.setattr(public, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(preview_module, "propose", lambda *_a, **_kw: model_calls.append(True))
    if expected:
        with pytest.raises(ValueError, match=f"^{expected}$"):
            collect_snapshot(clock=lambda: now[0])
    else:
        snapshot = collect_snapshot(clock=lambda: now[0])
        assert snapshot["valid"] is True
        assert snapshot["as_of_ms"] == int((initial + elapsed)*1000)
        assert snapshot["timeframes"]["30m"]["forming"]["observed_at_ms"] == snapshot["as_of_ms"]
    assert sleeps == [1] and len(calls) == 7 and not model_calls
    assert [call["interval"] for call in calls[1:]] == [intervals[tf] for tf in TIMEFRAME_MS]
    assert all(call["limit"] == (1000 if call["interval"] == intervals["5m"] else 100) for call in calls)


def test_persistent_limit_aborts_collection_before_other_timeframes_or_model(transport, monkeypatch):
    import live.scenario_preview as preview_module
    calls, sleeps, replies = transport
    model_calls = []
    replies.extend([response(10006)] * 3)
    monkeypatch.setattr(preview_module, "propose", lambda *_a, **_kw: model_calls.append(True))
    with pytest.raises(ValueError, match="^public_rate_limited$"):
        collect_snapshot(clock=lambda: 1800000100.)
    assert len(calls) == 3 and sleeps == [1, 2] and not model_calls
