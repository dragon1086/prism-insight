import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from prism_core.tradingview_transport import (
    TradingViewTransportError,
    TransportDependencies,
    TVAccessGrant,
    make_transport_factory,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
MARKER = "PRIVATE_MARKER_123"


def grant():
    return TVAccessGrant(MARKER, NOW + timedelta(minutes=5))


def text_result(value):
    return SimpleNamespace(isError=False, structuredContent=None,
                           content=[SimpleNamespace(type="text", text=json.dumps(value))])


def fake_factory(response=None, access=None):
    events = []

    async def supplier():
        events.append("credential")
        return grant() if access is None else access

    @asynccontextmanager
    async def http(**kwargs):
        events.append(kwargs)
        try:
            yield "http"
        finally:
            events.append("http_closed")

    @asynccontextmanager
    async def stream(url, **kwargs):
        events.append((url, kwargs))
        yield ("read", "write", None)

    @asynccontextmanager
    async def session(read, write):
        class Session:
            async def initialize(self):
                events.append("initialize")

            async def call_tool(self, name, arguments):
                events.append((name, arguments))
                if isinstance(response, BaseException):
                    raise response
                return response or text_result({"headlines": []})

        yield Session()

    def loader():
        events.append("loader")
        return TransportDependencies(http, stream, session)

    return make_transport_factory(supplier, dependency_loader=loader, clock=lambda: NOW), events


def test_lazy_factory_and_exact_mapping():
    factory, events = fake_factory()
    assert not events

    async def execute():
        async with factory() as transport:
            assert await transport.call("get_news", {}) == {"headlines": []}
            with pytest.raises(TradingViewTransportError, match="UNSUPPORTED_TOOL"):
                await transport.call("create_alert", {})

    asyncio.run(execute())
    assert events.count("credential") == events.count("initialize") == 1
    assert events[2] == {"trust_env": False, "follow_redirects": False, "timeout": 15,
                         "headers": {"Authorization": f"Bearer {MARKER}"}}
    assert events[3] == ("https://mcp.tradingview.com/mcp", {"http_client": "http"})
    assert ("mcp-tv-get-news", {}) in events
    assert events[-1] == "http_closed"
    assert MARKER not in repr(grant())


@pytest.mark.parametrize("access", [object(), TVAccessGrant("", NOW),
    TVAccessGrant("bad token", NOW + timedelta(hours=1)),
    TVAccessGrant("a" * 8193, NOW + timedelta(hours=1)),
    TVAccessGrant(MARKER, NOW + timedelta(seconds=30)),
    TVAccessGrant(MARKER, NOW.replace(tzinfo=None))])
def test_bad_grant_never_loads_http(access):
    factory, events = fake_factory(access=access)

    async def execute():
        async with factory():
            pytest.fail("invalid auth entered")

    with pytest.raises(TradingViewTransportError, match="AUTH_UNAVAILABLE"):
        asyncio.run(execute())
    assert events == ["credential"]


@pytest.mark.parametrize("response,code", [
    (SimpleNamespace(isError=False, content=[], structuredContent=None), "INVALID_PAYLOAD"),
    (text_result([]), "INVALID_PAYLOAD"),
    (SimpleNamespace(isError=False, content=[SimpleNamespace(type="image")],
                     structuredContent={}), "INVALID_PAYLOAD"),
    (SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text='{"a":1}')],
                     structuredContent={"a": 2}), "AMBIGUOUS_PAYLOAD"),
    (RuntimeError(MARKER), "CALL_FAILED"),
])
def test_static_errors(response, code):
    factory, _ = fake_factory(response)

    async def execute():
        async with factory() as transport:
            await transport.call("get_news", {})

    with pytest.raises(TradingViewTransportError, match=code) as caught:
        asyncio.run(execute())
    assert MARKER not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("response,expected", [
    (SimpleNamespace(isError=True, content=MARKER), {"success": False, "error": "MCP_ERROR"}),
    (SimpleNamespace(isError=False, content=[], structuredContent={"a": 1}), {"a": 1}),
    (SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text='{"a":1}')],
                     structuredContent={"a": 1}), {"a": 1}),
])
def test_decode_branches(response, expected):
    factory, _ = fake_factory(response)

    async def execute():
        async with factory() as transport:
            return await transport.call("get_news", {})

    assert asyncio.run(execute()) == expected


def test_concurrent_context_logging_and_restore(caplog):
    previous = logging.getLogRecordFactory()
    caplog.set_level(logging.DEBUG)

    async def execute():
        entered, release = asyncio.Event(), asyncio.Event()

        async def protected():
            first, _ = fake_factory()
            second, _ = fake_factory()
            async with first(), second():
                logging.getLogger(__name__).warning(MARKER)
                entered.set()
                await release.wait()
                raise asyncio.CancelledError

        task = asyncio.create_task(protected())
        await entered.wait()
        logging.getLogger(__name__).warning("unrelated visible")
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())
    assert MARKER not in caplog.text
    assert "unrelated visible" in caplog.text
    assert logging.getLogRecordFactory() is previous


@pytest.mark.parametrize("mode", ["initialize", "json", "sse"])
def test_real_sdk_malformed_payload_logs_are_sanitized(mode, caplog):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    caplog.set_level(logging.DEBUG)
    methods = []

    async def handler(request):
        body = json.loads(request.content) if request.content else {}
        methods.append(body.get("method"))
        if body.get("method") == "initialize" and mode != "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                "result": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "serverInfo": {"name": "offline", "version": "1"}}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if mode == "sse":
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text=f"event: message\ndata: {MARKER}\n\n")
        return httpx.Response(200, headers={"content-type": "application/json"}, text=MARKER)

    def loader():
        return TransportDependencies(
            lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs),
            streamable_http_client, ClientSession)

    async def supplier():
        return grant()

    async def execute():
        factory = make_transport_factory(supplier, dependency_loader=loader, clock=lambda: NOW)
        async with factory() as transport:
            await transport.call("get_news", {})

    try:
        asyncio.run(asyncio.wait_for(execute(), timeout=.5))
    except (TradingViewTransportError, asyncio.TimeoutError) as exc:
        assert MARKER not in str(exc)
    else:
        pytest.fail("malformed response unexpectedly succeeded")
    assert "initialize" in methods
    if mode != "initialize":
        assert "tools/call" in methods
    assert MARKER not in caplog.text
    assert "content suppressed" in caplog.text


@pytest.mark.parametrize("body", ['{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_nonfinite_duplicate_json_rejected(body):
    result = SimpleNamespace(isError=False, structuredContent=None,
                             content=[SimpleNamespace(type="text", text=body)])
    factory, _ = fake_factory(result)

    async def execute():
        async with factory() as transport:
            await transport.call("get_news", {})

    with pytest.raises(TradingViewTransportError, match="INVALID_PAYLOAD"):
        asyncio.run(execute())


def test_sdk_cancellation_propagates_and_closes():
    factory, events = fake_factory(asyncio.CancelledError())

    async def execute():
        async with factory() as transport:
            await transport.call("get_news", {})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute())
    assert events[-1] == "http_closed"


def test_supplier_exception_is_private_and_logging_fields_removed(caplog):
    caplog.set_level(logging.DEBUG)
    previous = logging.getLogRecordFactory()

    async def supplier():
        try:
            raise RuntimeError(MARKER)
        except RuntimeError:
            logging.getLogger(__name__).exception("credential %s", MARKER, stack_info=True)
            raise

    async def execute():
        async with make_transport_factory(supplier)():
            pytest.fail("supplier failed")

    with pytest.raises(TradingViewTransportError, match="AUTH_UNAVAILABLE"):
        asyncio.run(execute())
    assert MARKER not in caplog.text
    protected = [record for record in caplog.records if "suppressed" in record.getMessage()]
    assert protected
    assert all(record.exc_info is None and record.exc_text is None and record.stack_info is None
               and record.args == () for record in protected)
    assert logging.getLogRecordFactory() is previous


def test_overlapping_tasks_keep_scope_until_last_exit(caplog):
    caplog.set_level(logging.DEBUG)
    previous = logging.getLogRecordFactory()

    async def execute():
        entered, first_done = asyncio.Event(), asyncio.Event()

        async def second():
            factory, _ = fake_factory()
            async with factory():
                entered.set()
                await first_done.wait()
                logging.getLogger(__name__).warning(MARKER)

        factory, _ = fake_factory()
        async with factory():
            task = asyncio.create_task(second())
            await entered.wait()
        assert logging.getLogRecordFactory() is not previous
        first_done.set()
        await task

    asyncio.run(execute())
    assert MARKER not in caplog.text
    assert logging.getLogRecordFactory() is previous


@pytest.mark.parametrize("now_fold,expiry_hour,expiry_minute,expiry_fold,valid", [
    (0, 1, 0, 1, True), (1, 1, 59, 0, False),
])
def test_grant_expiry_compares_utc_instants(now_fold, expiry_hour, expiry_minute,
                                          expiry_fold, valid):
    from prism_core.tradingview_transport import _valid_grant

    zone = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=now_fold)
    expires = datetime(2026, 11, 1, expiry_hour, expiry_minute, tzinfo=zone, fold=expiry_fold)
    assert _valid_grant(TVAccessGrant(MARKER, expires), now) is valid


def test_collector_off_does_not_enter_transport():
    from prism_core.tradingview_collection import collect_tradingview

    factory, events = fake_factory()
    assert asyncio.run(collect_tradingview({}, object(), factory)) is None
    assert events == []


def test_real_sdk_success_through_collector():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from prism_core.tradingview_collection import ReadRequest, collect_tradingview

    methods, clients = [], []

    async def handler(request):
        assert str(request.url) == "https://mcp.tradingview.com/mcp"
        assert request.headers["authorization"] == f"Bearer {MARKER}"
        body = json.loads(request.content) if request.content else {}
        method = body.get("method")
        methods.append(method)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {},
                      "serverInfo": {"name": "offline", "version": "1"}}
        elif method == "notifications/initialized":
            return httpx.Response(202)
        elif method == "tools/list":
            result = {"tools": [{"name": "mcp-tv-get-news", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            assert body["params"]["name"] == "mcp-tv-get-news"
            result = {"content": [{"type": "text", "text": json.dumps(
                {"headlines": [{"id": "1", "title": "Offline headline"}]})}], "isError": False}
        else:
            pytest.fail(f"unexpected method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def http_factory(**kwargs):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client

    def loader():
        return TransportDependencies(http_factory, streamable_http_client, ClientSession)

    async def supplier():
        return grant()

    factory = make_transport_factory(supplier, dependency_loader=loader, clock=lambda: NOW)
    result = asyncio.run(collect_tradingview({"enabled": True, "total_seconds": 2},
                                            [ReadRequest("get_news", {})], factory))
    assert result["status"] == "COMPLETE"
    assert result["results"][0]["evidence"]["status"] == "AVAILABLE"
    assert methods.count("initialize") == methods.count("tools/call") == 1
    assert len(clients) == 1 and clients[0].is_closed


def test_consumer_exception_identity_is_preserved():
    failure = ValueError("caller_validation_failure")
    factory, events = fake_factory()

    async def execute():
        async with factory():
            raise failure

    with pytest.raises(ValueError) as caught:
        asyncio.run(execute())
    assert caught.value is failure
    assert events[-1] == "http_closed"


def test_consumer_exception_survives_sdk_cleanup_group():
    import anyio

    failure = ValueError("caller_validation_failure")

    @asynccontextmanager
    async def http(**kwargs):
        yield None

    @asynccontextmanager
    async def stream(*args, **kwargs):
        async with anyio.create_task_group():
            try:
                yield (None, None, None)
            finally:
                raise RuntimeError(MARKER)

    @asynccontextmanager
    async def session(*args):
        async def initialize():
            pass
        yield SimpleNamespace(initialize=initialize)

    async def supplier():
        return grant()

    async def execute():
        factory = make_transport_factory(supplier, clock=lambda: NOW, dependency_loader=lambda:
            TransportDependencies(http, stream, session))
        async with factory():
            raise failure

    with pytest.raises(ValueError) as caught:
        asyncio.run(execute())
    assert caught.value is failure


def test_sdk_cleanup_error_without_consumer_error_is_sanitized():
    @asynccontextmanager
    async def http(**kwargs):
        yield None
        raise RuntimeError(MARKER)

    @asynccontextmanager
    async def stream(*args, **kwargs):
        yield (None, None, None)

    @asynccontextmanager
    async def session(*args):
        async def initialize():
            pass
        yield SimpleNamespace(initialize=initialize)

    async def supplier():
        return grant()

    async def execute():
        factory = make_transport_factory(supplier, clock=lambda: NOW, dependency_loader=lambda:
            TransportDependencies(http, stream, session))
        async with factory():
            pass

    with pytest.raises(TradingViewTransportError, match="TRANSPORT_FAILED") as caught:
        asyncio.run(execute())
    assert MARKER not in str(caught.value)
