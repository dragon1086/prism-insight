"""Dedicated-process Responses-only facade for the genuine fallback client.

Never import this into a running production proxy. Native handler globals are
adapted only within one separately supervised helper. OAuth stays host-side;
the model receives a Unix socket capability, not an auth file or local network.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import stat
import time
import sys
from types import SimpleNamespace

import aiohttp
from aiohttp import web

MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MODELS = frozenset({"gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-astra"})
_ACTIVE = False


def _contains_private(value, private_values):
    if isinstance(value, str):
        return any(private in value for private in private_values)
    if isinstance(value, dict):
        return any(_contains_private(k, private_values) or _contains_private(v, private_values)
                   for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_private(v, private_values) for v in value)
    return False


class ProxyRejected(aiohttp.ClientError):
    """Only fixed safe categories, never upstream text or credentials."""


def validate_body(body, allowed_tools):
    keys = {"model", "input", "instructions", "tools", "reasoning", "max_output_tokens",
            "service_tier", "store", "stream", "parallel_tool_calls", "tool_choice", "stop"}
    if (not isinstance(body, dict) or set(body) - keys or not isinstance(body.get("model"), str) or body["model"] not in MODELS
            or "input" not in body or body.get("store", False) is not False
            or body.get("stream", False) is not False):
        raise ProxyRejected("request_not_permitted")
    if not isinstance(body["input"], (str, list)) or len(json.dumps(body, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
        raise ProxyRejected("request_size_or_input_invalid")
    if isinstance(body["input"], list):
        if len(body["input"]) > 4096:
            raise ProxyRejected("too_many_input_items")
        for item in body["input"]:
            if not isinstance(item, dict):
                raise ProxyRejected("input_item_not_permitted")
            kind = item.get("type", "message")
            if kind == "message":
                if (set(item) - {"type", "role", "content"} or not isinstance(item.get("role"), str)
                        or item["role"] not in {"developer", "user", "assistant", "system"}
                        or not isinstance(item.get("content"), str)):
                    raise ProxyRejected("text_only_input_required")
            elif kind == "function_call":
                if (set(item) != {"type", "name", "call_id", "arguments"}
                        or not isinstance(item.get("name"), str) or item["name"] not in allowed_tools
                        or any(not isinstance(item.get(k), str) for k in ("call_id", "arguments"))):
                    raise ProxyRejected("read_function_input_required")
            elif kind == "function_call_output":
                if (set(item) != {"type", "call_id", "output"}
                        or any(not isinstance(item.get(k), str) for k in ("call_id", "output"))):
                    raise ProxyRejected("text_function_output_required")
            else:
                raise ProxyRejected("input_item_not_permitted")
    tools = body.get("tools") or []
    if not isinstance(tools, list) or len(tools) > 64:
        raise ProxyRejected("tools_not_permitted")
    for tool in tools:
        if (not isinstance(tool, dict) or tool.get("type") != "function"
                or not isinstance(tool.get("name"), str) or tool["name"] not in allowed_tools
                or set(tool) - {"type", "name", "description", "parameters", "strict"}):
            raise ProxyRejected("tools_not_permitted")
    if not isinstance(body.get("tool_choice", "auto"), str) or body.get("tool_choice", "auto") not in {"auto", "none", "required"}:
        raise ProxyRejected("tool_choice_not_permitted")
    return body


class FrozenTokenManager:
    """No reference to native AUTH_FILE, persistence, login, or refresh methods."""
    def __init__(self, values, run_seconds, now):
        self._values = values
        self._deadline = time.monotonic() + run_seconds
        self._expires_at = values["expires_at"]
        self._wall_started, self._mono_started = now, time.monotonic()

    @classmethod
    def load(cls, path, *, run_seconds, now=None):
        try:
            path = Path(path).absolute()
            if (".." in path.parts or type(run_seconds) not in {int, float} or not 1 <= run_seconds <= 1800 or not math.isfinite(run_seconds)
                    or any(p.is_symlink() for p in (path, *path.parents))):
                raise ProxyRejected("unsafe_private_snapshot")
            info = path.stat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 65536):
                raise ProxyRejected("unsafe_private_snapshot")
            values = json.loads(path.read_bytes())
            wall = time.time() if now is None else now
            if (not isinstance(values, dict) or set(values) != {"access_token", "account_id", "expires_at"}
                    or not isinstance(values["access_token"], str) or not 1 <= len(values["access_token"]) <= 16384
                    or not isinstance(values["account_id"], str) or len(values["account_id"]) > 128
                    or type(values["expires_at"]) not in {int, float} or not math.isfinite(values["expires_at"])
                    or values["expires_at"] <= wall + run_seconds + 300):
                raise ProxyRejected("invalid_or_expiring_snapshot")
            return cls(values, run_seconds, wall)
        except ProxyRejected:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ProxyRejected("invalid_private_snapshot") from None

    async def get_token(self):
        if (time.monotonic() >= self._deadline or
                self._wall_started + time.monotonic() - self._mono_started >= self._expires_at - 300):
            raise ProxyRejected("frozen_token_window_expired")
        return self._values["access_token"]

    async def get_account_id(self):
        await self.get_token()
        return self._values["account_id"]

    @property
    def private_values(self):
        return tuple(v for v in (self._values["access_token"], self._values["account_id"]) if v)


def prepare_private_snapshot(source, destination, *, run_seconds=600):
    """Operator-only projection into a NEW private host file; never print tokens.

    The production source is opened read-only and never refreshed or changed.
    This function must not be exposed as a model tool or run inside its stage.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    try:
        if (any(".." in path.parts for path in (source, destination))
                or any(p.is_symlink() for path in (source, destination) for p in (path, *path.parents))):
            raise ProxyRejected("unsafe_snapshot_projection_path")
        info, parent = source.stat(), destination.parent.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 65536
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077):
            raise ProxyRejected("unsafe_snapshot_projection_permissions")
        original = json.loads(source.read_bytes())
        values = {key: original[key] for key in ("access_token", "account_id", "expires_at")}
        # Match the load contract before writing; no refresh token is retained.
        if (type(run_seconds) not in {int, float} or not math.isfinite(run_seconds) or not 1 <= run_seconds <= 1800
                or not isinstance(values["access_token"], str) or not 1 <= len(values["access_token"]) <= 16384
                or not isinstance(values["account_id"], str) or len(values["account_id"]) > 128
                or type(values["expires_at"]) not in {int, float} or not math.isfinite(values["expires_at"])
                or values["expires_at"] <= time.time() + run_seconds + 300):
            raise ProxyRejected("invalid_or_expiring_snapshot")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(values, stream)
            stream.flush()
            os.fsync(stream.fileno())
        return {"status": "prepared", "refresh_disabled": True, "original_auth_modified": False}
    except ProxyRejected:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ProxyRejected("private_snapshot_projection_failed") from None


def _error_text(status, raw=b""):
    # Preserve only the known tier-capability classification needed by the
    # existing client. Never copy error excerpts, account IDs or request text.
    tier = status == 400 and b"service_tier" in raw and any(
        marker in raw.lower() for marker in (b"unsupported", b"not supported", b"unknown parameter"))
    message = "Unsupported parameter: service_tier" if tier else "isolated_fallback_upstream_error"
    return json.dumps({"error": {"message": message, "type": "invalid_request_error" if status == 400 else "server_error"}})


async def safe_response_text(response, private_values):
    chunks, size = [], 0
    async for chunk in response.content.iter_chunked(65536):
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise ProxyRejected("upstream_response_too_large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if response.status != 200:
        return _error_text(response.status, raw)
    if any(value.encode() in raw for value in private_values):
        raise ProxyRejected("private_value_in_upstream_response")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        raise ProxyRejected("upstream_encoding_invalid") from None


class _ResponseContext:
    def __init__(self, context, private_values):
        self.context, self.private_values = context, private_values

    async def __aenter__(self):
        response = await self.context.__aenter__()
        async def text():
            return await safe_response_text(response, self.private_values)
        return SimpleNamespace(status=response.status, headers=response.headers, text=text)

    async def __aexit__(self, *args):
        return await self.context.__aexit__(*args)


class _BoundedSession:
    def __init__(self, deadline, private_values):
        self.deadline, self.private_values = deadline, private_values

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), trace_configs=[])
        return self

    def post(self, url, *, json, headers, timeout):
        from cores.chatgpt_proxy.constants import CHATGPT_RESPONSES_URL
        remaining = self.deadline - time.monotonic()
        if url != CHATGPT_RESPONSES_URL or remaining <= 0:
            raise ProxyRejected("upstream_destination_or_window_invalid")
        context = self.session.post(url, json=json, headers=headers, allow_redirects=False,
                                    timeout=aiohttp.ClientTimeout(total=min(300, remaining)))
        return _ResponseContext(context, self.private_values)

    async def __aexit__(self, *args):
        await self.session.close()


def create_dedicated_app(manager, *, allowed_tools, max_requests=16):
    """For one supervised helper process only. Cleanup restores native globals."""
    global _ACTIVE
    if (_ACTIVE or not isinstance(manager, FrozenTokenManager) or type(max_requests) is not int
            or not 1 <= max_requests <= 32 or not isinstance(allowed_tools, (set, frozenset))
            or not allowed_tools or len(allowed_tools) > 64
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) for name in allowed_tools)):
        raise ProxyRejected("invalid_dedicated_service")
    from tools.probe_codex_trading_runtime import READ_TOOLS
    read_names = {name for server, names in READ_TOOLS.items() for tool in names
                  for name in (tool, f"{server}-{tool}", f"{server}_{tool}", f"{server}__{tool}")}
    if not allowed_tools <= read_names:
        raise ProxyRejected("non_read_tool_manifest")
    from cores.chatgpt_proxy import proxy_server as native
    if native._token_manager is not None:
        raise ProxyRejected("native_proxy_already_active")
    previous_http = native.aiohttp
    log_state = (native.logger.handlers[:], native.logger.propagate, native.logger.level)
    native.logger.handlers = [logging.NullHandler()]
    native.logger.propagate = False
    native.aiohttp = SimpleNamespace(
        ClientSession=lambda: _BoundedSession(manager._deadline, manager.private_values),
        ClientTimeout=aiohttp.ClientTimeout, ClientError=aiohttp.ClientError)
    calls, busy = 0, False

    @web.middleware
    async def guard(request, handler):
        nonlocal calls, busy
        if request.method != "POST" or request.path != "/v1/responses" or request.query_string:
            return web.json_response({"error": {"message": "route_not_permitted"}}, status=404)
        if calls >= max_requests or busy or time.monotonic() >= manager._deadline:
            return web.json_response({"error": {"message": "isolated_fallback_budget_exhausted"}}, status=429)
        # Reserve BEFORE the first await: two slow clients cannot both pass the
        # concurrency check and start upstream calls after parsing their bodies.
        calls += 1
        busy = True
        try:
            try:
                value = await asyncio.wait_for(request.json(), timeout=min(10, manager._deadline - time.monotonic()))
                validate_body(value, allowed_tools)
            except Exception:
                return web.json_response({"error": {"message": "request_not_permitted"}}, status=400)
            response = await handler(request)
            if response.status != 200:
                return web.Response(text=_error_text(response.status, response.body or b""),
                                    status=response.status, content_type="application/json")
            payload = json.loads(response.body)
            # Scan AFTER native JSON/SSE decoding as well: unicode escapes can
            # hide a private value from the raw-byte check upstream.
            if not isinstance(payload, dict) or payload.get("error") or _contains_private(payload, manager.private_values):
                raise ProxyRejected("invalid_success_payload")
            return response
        except Exception:
            return web.json_response({"error": {"message": "isolated_fallback_failed"}}, status=502)
        finally:
            busy = False

    app = native.create_app(manager)
    app.middlewares.append(guard)
    _ACTIVE = True

    async def cleanup(_app):
        global _ACTIVE
        native.aiohttp = previous_http
        native._token_manager = None
        native.logger.handlers, native.logger.propagate, native.logger.level = log_state
        _ACTIVE = False
    app.on_cleanup.append(cleanup)
    return app


async def serve(socket_path, auth_snapshot, *, allowed_tools, run_seconds=600, max_requests=16,
                stop_event=None, ready=None):
    """Bind only an owned private Unix socket. Caller supervises this process."""
    socket_path = Path(socket_path).absolute()
    parent = socket_path.parent
    if (".." in socket_path.parts or any(p.is_symlink() for p in (socket_path, *socket_path.parents)) or socket_path.exists()
            or not parent.is_dir() or parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077):
        raise ProxyRejected("unsafe_socket_path")
    manager = FrozenTokenManager.load(auth_snapshot, run_seconds=run_seconds)
    app = create_dedicated_app(manager, allowed_tools=allowed_tools, max_requests=max_requests)
    # aiohttp has two successive shutdown waits. This diagnostic helper does
    # not grant a production-style grace period to a stalled model request.
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=.5)
    inode = None
    try:
        await runner.setup()
        site = web.UnixSite(runner, path=str(socket_path))
        await site.start()
        socket_path.chmod(0o600)
        inode = socket_path.stat().st_ino
        if ready is not None:
            ready.set()
        stop = stop_event or asyncio.Event()
        try:
            await asyncio.wait_for(stop.wait(), timeout=run_seconds)
        except asyncio.TimeoutError:
            pass
    finally:
        await runner.cleanup()
        if inode is not None:
            try:
                info = socket_path.lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_ino == inode:
                    socket_path.unlink()
            except FileNotFoundError:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--auth-snapshot", required=True, type=Path)
    parser.add_argument("--read-tool", required=True, action="append")
    parser.add_argument("--run-seconds", type=float, default=600)
    parser.add_argument("--max-requests", type=int, default=16)
    args = parser.parse_args(argv)

    async def run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await serve(args.socket, args.auth_snapshot, allowed_tools=set(args.read_tool),
                    run_seconds=args.run_seconds, max_requests=args.max_requests, stop_event=stop)
    # This CLI must run in its own supervised process: native log paths include
    # payload excerpts. No logging configuration is changed in production.
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run())
        return 0
    except Exception:
        print(json.dumps({"status": "failed", "category": "isolated_responses_helper_failed"}))
        return 2


if __name__ == "__main__":
    # Direct script use is equivalent to the pinned `python -m tools...` form.
    repository = str(Path(__file__).resolve().parents[1])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    sys.exit(main())
