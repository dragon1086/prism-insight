"""Lazy fixed-endpoint MCP adapter for already valid, caller-supplied grants.

No credential storage, refresh, login or production registration. P2 owns the
cooperative deadline; the pinned SDK may reconnect SSE inside that deadline.
"""

import json
import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

_ENDPOINT = "https://mcp.tradingview.com/mcp"
_LOG_SCOPE = ContextVar("tradingview_private_logs", default=False)
_LOG_LOCK = threading.Lock()
_LOG_USERS = 0
_PREVIOUS_FACTORY = None
_OWNED_FACTORY = None


class TradingViewTransportError(Exception):
    """Static diagnostic code only; never attach provider exceptions."""


@dataclass(frozen=True)
class TVAccessGrant:
    access_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True)
class TransportDependencies:
    http_client_factory: object
    stream_factory: object
    session_factory: object


@contextmanager
def _private_logs():
    """Sanitize standard record fields only in this task and inherited tasks.

    Application-added extras/memory dumps are outside this boundary. Preserve
    unrelated concurrent logs and do not overwrite another owner's replacement.
    """
    global _LOG_USERS, _PREVIOUS_FACTORY, _OWNED_FACTORY
    with _LOG_LOCK:
        if _LOG_USERS == 0:
            previous = logging.getLogRecordFactory()
            _PREVIOUS_FACTORY = previous

            def factory(*args, **kwargs):
                record = previous(*args, **kwargs)
                if _LOG_SCOPE.get():
                    record.msg = "TradingView transport diagnostic (content suppressed)"
                    record.args = ()
                    record.exc_info = None
                    record.exc_text = None
                    record.stack_info = None
                return record

            _OWNED_FACTORY = factory
            logging.setLogRecordFactory(factory)
        _LOG_USERS += 1
    token = _LOG_SCOPE.set(True)
    try:
        yield
    finally:
        _LOG_SCOPE.reset(token)
        with _LOG_LOCK:
            _LOG_USERS -= 1
            if _LOG_USERS == 0:
                if logging.getLogRecordFactory() is _OWNED_FACTORY:
                    logging.setLogRecordFactory(_PREVIOUS_FACTORY)
                _OWNED_FACTORY = _PREVIOUS_FACTORY = None


def _dependencies():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    return TransportDependencies(httpx.AsyncClient, streamable_http_client, ClientSession)


def _valid_grant(grant, now):
    if not isinstance(grant, TVAccessGrant) or type(grant.access_token) is not str:
        return False
    token = grant.access_token
    if not 0 < len(token) <= 8192 or not all(33 <= ord(char) <= 126 for char in token):
        return False
    return (isinstance(now, datetime) and now.utcoffset() is not None
            and isinstance(grant.expires_at, datetime) and grant.expires_at.utcoffset() is not None
            and grant.expires_at.astimezone(timezone.utc)
            > now.astimezone(timezone.utc) + timedelta(seconds=30))


def _object_json(text):
    def no_constant(_):
        raise ValueError("INVALID_PAYLOAD")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("INVALID_PAYLOAD")
            result[key] = value
        return result

    value = json.loads(text, parse_constant=no_constant, object_pairs_hook=unique_object)
    if type(value) is not dict:
        raise ValueError("INVALID_PAYLOAD")
    from prism_core.tradingview_collection import _json_bytes
    _json_bytes(value)  # Also reject overflow such as 1e999 and malformed Unicode.
    return value


def _decode(result):
    if result.isError:
        return {"success": False, "error": "MCP_ERROR"}
    structured = result.structuredContent
    blocks = result.content
    if type(blocks) is not list or len(blocks) > 1:
        raise TradingViewTransportError("INVALID_PAYLOAD")
    if structured is not None:
        # Reuse strict JSON validation without accepting Python-only structures.
        from prism_core.tradingview_collection import _json_bytes
        if type(structured) is not dict:
            raise TradingViewTransportError("INVALID_PAYLOAD")
        structured = _object_json(_json_bytes(structured))
    if blocks:
        if blocks[0].type != "text" or type(blocks[0].text) is not str:
            raise TradingViewTransportError("INVALID_PAYLOAD")
        text = _object_json(blocks[0].text)
        # JSON booleans and numbers are not interchangeable even though Python == is.
        if structured is not None and json.dumps(structured, sort_keys=True) != json.dumps(
                text, sort_keys=True):
            raise TradingViewTransportError("AMBIGUOUS_PAYLOAD")
        return text
    if structured is None:
        raise TradingViewTransportError("INVALID_PAYLOAD")
    return structured


class _Transport:
    def __init__(self, session):
        self._session = session

    async def call(self, tool, arguments):
        from prism_core.tradingview_collection import READ_TOOLS
        if type(tool) is not str or tool not in READ_TOOLS:
            raise TradingViewTransportError("UNSUPPORTED_TOOL")
        try:
            result = await self._session.call_tool("mcp-tv-" + tool.replace("_", "-"),
                                                   arguments=arguments)
        except Exception:  # noqa: BLE001 - suppress credential-bearing SDK errors
            raise TradingViewTransportError("CALL_FAILED") from None
        try:
            return _decode(result)
        except TradingViewTransportError:
            raise
        except Exception:  # noqa: BLE001 - malformed response is untrusted
            raise TradingViewTransportError("INVALID_PAYLOAD") from None


def make_transport_factory(credential_supplier, *, dependency_loader=None, clock=None):
    """Construct without imports/I/O; enter once per explicitly enabled collection."""
    @asynccontextmanager
    async def factory():
        with _private_logs():
            try:
                grant = await credential_supplier()
                now = clock() if clock is not None else datetime.now(timezone.utc)
                valid = _valid_grant(grant, now)
            except Exception:  # noqa: BLE001 - supplier must not leak token or auth errors
                raise TradingViewTransportError("AUTH_UNAVAILABLE") from None
            if not valid:
                raise TradingViewTransportError("AUTH_UNAVAILABLE")
            consumer_error = None
            try:
                deps = (dependency_loader or _dependencies)()
                async with (
                    deps.http_client_factory(
                        trust_env=False, follow_redirects=False, timeout=15,
                        headers={"Authorization": "Bearer " + grant.access_token},
                    ) as client,
                    deps.stream_factory(_ENDPOINT, http_client=client) as streams,
                    deps.session_factory(streams[0], streams[1]) as session,
                ):
                    await session.initialize()
                    try:
                        yield _Transport(session)
                    except BaseException as exc:
                        consumer_error = exc
                        raise
            except BaseException as exc:
                # SDK task groups may wrap the body error during cleanup. Preserve
                # caller ownership/identity rather than relabeling it as an SDK fault.
                if consumer_error is not None:
                    raise consumer_error from None
                if isinstance(exc, Exception):
                    raise TradingViewTransportError("TRANSPORT_FAILED") from None
                raise  # Cancellation and process-control exceptions retain semantics.

    return factory
