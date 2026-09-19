import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from prism_core.tradingview_collection import (
    READ_TOOLS,
    ReadRequest,
    TradingViewOptions,
    collect_tradingview,
)


def run(config=None, requests=None, payloads=None):
    events = []

    @asynccontextmanager
    async def factory():
        events.append("open")

        class Transport:
            async def call(self, tool, arguments):
                events.append((tool, arguments))
                value = payloads.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value

        try:
            yield Transport()
        finally:
            events.append("close")

    result = asyncio.run(collect_tradingview(config, requests, factory))
    return result, events


def news():
    return {"headlines": [{"title": "Verified source required", "id": "1"}]}


def request():
    return ReadRequest("get_news", {"symbol": "NASDAQ:AAPL"})


@pytest.mark.parametrize("config", [None, {}, {"enabled": False}, {"enabled": 1},
    {"enabled": "true"}, {"enabled": True, "endpoint": "secret"},
    {"enabled": True, "max_calls": True}, {"enabled": True, "max_calls": 9},
    {"enabled": True, "total_seconds": float("nan")},
    {"enabled": True, "max_evidence_bytes": 0}])
def test_disabled_never_touches_requests_or_factory(config):
    result, events = run(config, object())
    assert result is None
    assert events == []


def test_defaults_and_allowlist():
    options = TradingViewOptions.parse({"enabled": True})
    assert options.max_calls == 4
    assert options.max_response_bytes == 262144
    assert len(READ_TOOLS) == 10
    assert "run_screener" not in READ_TOOLS


@pytest.mark.parametrize("requests", [None, [object()],
    [ReadRequest("create_alert", {})], [ReadRequest("mcp-tv-get-news", {})],
    [ReadRequest("get_news", {"x": float("nan")})],
    [ReadRequest("get_news", {1: "bad key"})],
    [ReadRequest("get_news", {"x": (1, 2)})],
    [ReadRequest("get_news", {}, ["AAPL"])],
    [ReadRequest("get_news", {"x": "x" * 4096})],
    [request()] * 5, [request(), ReadRequest("unknown", {})]])
def test_all_requests_validated_before_io(requests):
    result, events = run({"enabled": True}, requests)
    assert result["status"] == "INVALID_REQUEST"
    assert events == []


def test_empty_and_complete():
    result, events = run({"enabled": True}, [])
    assert result["status"] == "EMPTY" and not events
    result, events = run({"enabled": True}, [request()], [news()])
    assert result["status"] == "COMPLETE"
    assert result["research_only"] and not result["execution_authorized"]
    assert events[0] == "open" and events[-1] == "close"
    evidence = result["results"][0]["evidence"]
    assert result["metrics"]["evidence_bytes"] == len(
        json.dumps(evidence, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())


def test_failure_does_not_leak_and_next_call_runs():
    result, _ = run({"enabled": True}, [request(), request()],
                    [RuntimeError("secret-token"), news()])
    assert result["status"] == "PARTIAL"
    assert result["metrics"]["calls_attempted"] == 2
    assert "secret-token" not in json.dumps(result)
    assert result["results"][0]["reason"] == "CALL_ERROR"


@pytest.mark.parametrize("payload,reason", [({"x": "a" * 100}, "RESPONSE_LIMIT"),
    ({"x": float("inf")}, "INVALID_RESPONSE"), ([], "INVALID_RESPONSE")])
def test_invalid_or_oversized_payload(payload, reason):
    result, _ = run({"enabled": True, "max_response_bytes": 50}, [request()], [payload])
    assert result["results"][0]["reason"] == reason
    assert result["metrics"]["evidence_bytes"] == 0


def test_evidence_limit_includes_envelope_metadata():
    result, _ = run({"enabled": True, "max_evidence_bytes": 30}, [request()], [news()])
    assert result["results"][0]["reason"] == "EVIDENCE_LIMIT"
    assert "evidence" not in result["results"][0]


def test_cumulative_limit_omits_whole_record():
    first, _ = run({"enabled": True}, [request()], [news()])
    size = first["metrics"]["evidence_bytes"]
    result, _ = run({"enabled": True, "total_evidence_bytes": size},
                    [request(), request()], [news(), news()])
    assert result["status"] == "PARTIAL"
    assert result["results"][1]["reason"] == "RUN_EVIDENCE_LIMIT"
    assert result["metrics"]["evidence_bytes"] == size


def test_empty_provider_response_is_not_complete():
    result, _ = run({"enabled": True}, [request()], [{"headlines": []}])
    assert result["status"] == "FAILED"
    assert result["results"][0]["evidence"]["status"] == "EMPTY"


def test_timeout_close_error_and_cancellation():
    async def scenario(mode):
        @asynccontextmanager
        async def factory():
            if mode == "open":
                await asyncio.sleep(1)

            class Transport:
                async def call(self, tool, arguments):
                    if mode == "cancel":
                        raise asyncio.CancelledError
                    if mode == "call":
                        await asyncio.sleep(1)
                    return news()

            yield Transport()
            if mode == "close":
                raise RuntimeError("secret")
            if mode == "close_timeout":
                await asyncio.sleep(1)

        return await collect_tradingview(
            {"enabled": True, "total_seconds": .03, "per_call_seconds": .01},
            [request()], factory)

    for mode, reason in [("open", "TOTAL_TIMEOUT"), ("close_timeout", "TOTAL_TIMEOUT"),
                         ("close", "TRANSPORT_ERROR")]:
        result = asyncio.run(scenario(mode))
        assert result["reason"] == reason
        assert result["status"] != "COMPLETE"
    assert asyncio.run(scenario("call"))["results"][0]["reason"] == "CALL_TIMEOUT"
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario("cancel"))


@pytest.mark.parametrize("key,maximum", [("max_calls", 8), ("per_call_seconds", 30),
    ("total_seconds", 90), ("max_response_bytes", 1048576),
    ("max_evidence_bytes", 24000), ("total_evidence_bytes", 72000)])
def test_config_boundary_types(key, maximum):
    assert TradingViewOptions.parse({"enabled": True, key: maximum}) is not None
    for value in (0, -1, maximum + 1, True, "1", None, float("inf")):
        assert TradingViewOptions.parse({"enabled": True, key: value}) is None
    if not key.endswith("seconds"):
        assert TradingViewOptions.parse({"enabled": True, key: 1.0}) is None


def test_request_copy_and_symbols_are_isolated():
    original = ReadRequest("get_news", {"nested": ["original"]}, ("NASDAQ:AAPL",))

    @asynccontextmanager
    async def factory():
        original.arguments["nested"].append("external mutation")

        class Transport:
            async def call(self, tool, arguments):
                assert arguments == {"nested": ["original"]}
                arguments["nested"].append("transport mutation")
                return news()

        yield Transport()

    result = asyncio.run(collect_tradingview({"enabled": True}, [original], factory))
    # A requested coverage denominator is preserved; the headline lacks its symbol.
    assert result["status"] == "PARTIAL"
    assert "transport mutation" not in original.arguments["nested"]


def test_factory_failure_and_cyclic_input_are_bounded():
    def factory():
        raise RuntimeError("private credential")

    result = asyncio.run(collect_tradingview({"enabled": True}, [request()], factory))
    assert result["reason"] == "TRANSPORT_ERROR"
    assert result["results"][0]["reason"] == "NOT_ATTEMPTED"
    assert "private" not in json.dumps(result)
    cyclic = {}
    cyclic["cycle"] = cyclic
    result, events = run({"enabled": True}, [ReadRequest("get_news", cyclic)])
    assert result["status"] == "INVALID_REQUEST" and not events
