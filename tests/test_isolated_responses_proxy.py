import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import isolated_responses_proxy as proxy


def body():
    return {"model": "gpt-5.6-sol", "input": [{"role": "user", "content": "synthetic"}],
            "tools": [{"type": "function", "name": "time-get_current_time", "parameters": {"type": "object"}}],
            "reasoning": {"effort": "high"}, "max_output_tokens": 30000}


def test_current_fallback_shape_allowed_without_enabling_builtin_tools():
    assert proxy.validate_body(body(), {"time-get_current_time"}) == body()


@pytest.mark.parametrize("field,value", [
    ("model", "unapproved"), ("store", True), ("stream", True),
    ("previous_response_id", "private"), ("base_url", "http://127.0.0.1/admin"),
    ("tools", [{"type": "web_search"}]),
    ("tools", [{"type": "function", "name": "sqlite-write_query"}]),
    ("input", "x" * (1024 * 1024)),
])
def test_invalid_request_rejected(field, value):
    request = body()
    request[field] = value
    with pytest.raises(proxy.ProxyRejected):
        proxy.validate_body(request, {"time-get_current_time"})


def snapshot(tmp_path, **changes):
    path = tmp_path / "private.json"
    values = {"access_token": "SECRET_CANARY_TOKEN", "account_id": "private-account", "expires_at": 10000}
    values.update(changes)
    path.write_text(json.dumps(values))
    path.chmod(0o600)
    return path


def test_private_frozen_snapshot_never_reads_or_refreshes_original_auth(tmp_path):
    manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
    assert asyncio.run(manager.get_token()) == "SECRET_CANARY_TOKEN"
    assert asyncio.run(manager.get_account_id()) == "private-account"
    assert not hasattr(manager, "_refresh_token") and not hasattr(manager, "_save_to_disk")


@pytest.mark.parametrize("change", ["expiry", "refresh", "mode", "symlink", "hardlink"])
def test_unsafe_snapshot_fails_closed(tmp_path, change):
    path = snapshot(tmp_path, **({"expires_at": 1800} if change == "expiry" else
                               {"refresh_token": "SECRET_REFRESH"} if change == "refresh" else {}))
    if change == "mode":
        path.chmod(0o644)
    if change == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    if change == "hardlink":
        os.link(path, tmp_path / "link")
    with pytest.raises(proxy.ProxyRejected) as error:
        proxy.FrozenTokenManager.load(path, run_seconds=600, now=1000)
    assert "SECRET" not in str(error.value)


class Response:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        self.headers = {"Content-Type": "application/json"}
        self.content = self
    async def iter_chunked(self, _):
        for offset in range(0, len(self.payload), 1000):
            yield self.payload[offset:offset + 1000]


def test_upstream_error_is_fixed_and_service_tier_retry_remains_classifiable():
    async def run():
        ordinary = await proxy.safe_response_text(Response(400, b'SECRET_UPSTREAM'), ())
        tier = await proxy.safe_response_text(Response(400, b'Unsupported parameter: service_tier SECRET_UPSTREAM'), ())
        assert "SECRET" not in ordinary + tier
        assert "service_tier" not in ordinary and "service_tier" in tier
    asyncio.run(run())


def test_success_body_secret_canary_and_size_limit_rejected(monkeypatch):
    async def run():
        with pytest.raises(proxy.ProxyRejected):
            await proxy.safe_response_text(Response(200, b'{"text":"SECRET_TOKEN"}'), ("SECRET_TOKEN",))
        monkeypatch.setattr(proxy, "MAX_RESPONSE_BYTES", 1024)
        with pytest.raises(proxy.ProxyRejected):
            await proxy.safe_response_text(Response(200, b"x" * 1025), ())
    asyncio.run(run())


@pytest.mark.parametrize("item", [
    {"role": "user", "content": [{"type": "input_file", "file_url": "http://private/"}]},
    {"type": "computer_call", "arguments": "{}"},
    {"type": "function_call", "name": "sqlite-write_query", "call_id": "1", "arguments": "{}"},
])
def test_nontext_or_write_input_not_forwarded(item):
    value = body()
    value["input"] = [item]
    with pytest.raises(proxy.ProxyRejected):
        proxy.validate_body(value, {"time-get_current_time"})


def test_real_native_handler_routes_budget_auth_and_cleanup(tmp_path, monkeypatch, caplog):
    from aiohttp.test_utils import TestClient, TestServer
    from cores.chatgpt_proxy import proxy_server as native
    previous_http = native.aiohttp
    calls = []

    class Context:
        async def __aenter__(self):
            return Response(200, b'{"id":"synthetic","status":"completed","output":[]}')
        async def __aexit__(self, *_):
            return False

    class Session:
        def __init__(self, _deadline, private_values):
            self.private_values = private_values
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return proxy._ResponseContext(Context(), self.private_values)

    monkeypatch.setattr(proxy, "_BoundedSession", Session)

    async def run():
        manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
        app = proxy.create_dedicated_app(manager, allowed_tools={"time-get_current_time"}, max_requests=2)
        async with TestClient(TestServer(app)) as client:
            assert (await client.get("/health")).status == 404
            assert (await client.post("/v1/chat/completions", json=body())).status == 404
            assert (await client.post("/v1/responses?redirect=private", json=body())).status == 404
            bad = body()
            bad["model"] = "not-approved"
            assert (await client.post("/v1/responses", json=bad)).status == 400
            result = await client.post("/v1/responses", json=body(), headers={"Authorization": "SHOULD_NOT_FORWARD"})
            assert result.status == 200
            assert (await result.json())["id"] == "synthetic"
            assert (await client.post("/v1/responses", json=body())).status == 429
        assert native.aiohttp is previous_http and native._token_manager is None and not proxy._ACTIVE
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0][0] == "https://chatgpt.com/backend-api/codex/responses"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer SECRET_CANARY_TOKEN"
    assert calls[0][1]["json"]["store"] is False and calls[0][1]["json"]["stream"] is True
    assert "SECRET_CANARY_TOKEN" not in caplog.text and "SHOULD_NOT_FORWARD" not in caplog.text


def test_bounded_transport_forces_fixed_destination_no_redirects_and_deadline(monkeypatch):
    created, posted = [], []
    class Session:
        def __init__(self, **kwargs):
            created.append(kwargs)
        def post(self, url, **kwargs):
            posted.append((url, kwargs))
            return object()
        async def close(self):
            pass
    monkeypatch.setattr(proxy.aiohttp, "ClientSession", Session)
    async def run():
        async with proxy._BoundedSession(time.monotonic() + 20, ()) as session:
            session.post("https://chatgpt.com/backend-api/codex/responses", json={}, headers={}, timeout=None)
            with pytest.raises(proxy.ProxyRejected):
                session.post("http://127.0.0.1/admin", json={}, headers={}, timeout=None)
    asyncio.run(run())
    assert created[0]["trust_env"] is False and created[0]["trace_configs"] == []
    assert len(posted) == 1 and posted[0][1]["allow_redirects"] is False
    assert 0 < posted[0][1]["timeout"].total <= 20


def test_actual_unix_socket_only_denied_route_and_owned_cleanup(tmp_path):
    import aiohttp
    async def run(socket_dir):
        path = snapshot(tmp_path, expires_at=time.time() + 3600)
        sock = socket_dir / "responses.sock"
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(proxy.serve(sock, path, allowed_tools={"time-get_current_time"},
                                               run_seconds=10, stop_event=stop, ready=ready))
        try:
            await asyncio.wait_for(ready.wait(), timeout=3)
            assert sock.stat().st_mode & 0o777 == 0o600
            async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(sock))) as client:
                async with client.get("http://localhost/health") as response:
                    assert response.status == 404
                    assert "SECRET" not in await response.text()
        finally:
            stop.set()
            await asyncio.wait_for(task, timeout=3)
        assert not sock.exists() and not proxy._ACTIVE
    # macOS has a short AF_UNIX path limit; pytest's nested temp path is longer.
    with tempfile.TemporaryDirectory(prefix="prx-", dir=Path("/tmp").resolve()) as directory:
        asyncio.run(run(Path(directory)))


def test_write_tool_in_operator_manifest_rejected_before_native_globals(tmp_path):
    manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
    with pytest.raises(proxy.ProxyRejected, match="non_read_tool"):
        proxy.create_dedicated_app(manager, allowed_tools={"sqlite-write_query"})
    assert not proxy._ACTIVE


@pytest.mark.parametrize("sse", [False, True])
def test_decoded_escaped_json_and_sse_canaries_blocked(tmp_path, monkeypatch, sse):
    from aiohttp.test_utils import TestClient, TestServer
    escaped = b'{"id":"synthetic","status":"completed","output":[],"nested":{"\\u0053ECRET_CANARY_TOKEN":"\\u0070rivate-account"}}'
    raw = b'event: response.completed\ndata: {"type":"response.completed","response":' + escaped + b'}\n\n' if sse else escaped
    assert b"SECRET_CANARY_TOKEN" not in raw and b"private-account" not in raw
    class Context:
        async def __aenter__(self):
            response = Response(200, raw)
            if sse:
                response.headers["Content-Type"] = "text/event-stream"
            return response
        async def __aexit__(self, *_):
            return False
    class Session:
        def __init__(self, _deadline, private_values):
            self.private_values = private_values
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def post(self, *_args, **_kwargs):
            return proxy._ResponseContext(Context(), self.private_values)
    monkeypatch.setattr(proxy, "_BoundedSession", Session)
    async def run():
        manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
        app = proxy.create_dedicated_app(manager, allowed_tools={"time-get_current_time"})
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/v1/responses", json=body())
            assert response.status == 502
            text = await response.text()
            assert "SECRET" not in text and "private-account" not in text and "\\u" not in text
    asyncio.run(run())


def test_genuine_fallback_client_two_turn_function_pairing(tmp_path, monkeypatch):
    """Real production loop + OpenAI SDK + native HTTP handler; upstream/MCP fake."""
    from aiohttp.test_utils import TestServer
    from cores.llm.openai_responses_llm import OpenAIResponsesLLM
    calls, tool_calls = [], []
    class Context:
        def __init__(self, payload):
            self.payload = payload
        async def __aenter__(self):
            return Response(200, json.dumps(self.payload).encode())
        async def __aexit__(self, *_):
            return False
    class Session:
        def __init__(self, _deadline, private_values):
            self.private_values = private_values
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def post(self, _url, **kwargs):
            calls.append(json.loads(json.dumps(kwargs["json"])))
            output = [{"type": "function_call", "id": "fc1", "name": "time-get_current_time",
                       "call_id": "call1", "arguments": "{}", "status": "completed"}] if len(calls) == 1 else [
                {"type": "message", "id": "msg1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "SYNTHETIC FINAL", "annotations": []}]}]
            value = {"id": "response" + str(len(calls)), "object": "response", "created_at": 1,
                     "model": "gpt-5.6-sol", "status": "completed", "output": output}
            return proxy._ResponseContext(Context(value), self.private_values)
    monkeypatch.setattr(proxy, "_BoundedSession", Session)

    async def run():
        manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
        async with TestServer(proxy.create_dedicated_app(manager, allowed_tools={"time-get_current_time"})) as server:
            llm = OpenAIResponsesLLM.__new__(OpenAIResponsesLLM)
            llm.instruction = "Synthetic transport test"
            params = SimpleNamespace(max_iterations=3, maxTokens=1000, reasoning_effort="high",
                                     stopSequences=None, systemPrompt=None, tool_filter=None)
            llm.get_request_params = lambda _: params
            llm._reasoning = lambda _: True
            llm._reasoning_effort = "high"
            async def select_model(_):
                return "gpt-5.6-sol"
            async def list_tools(**_):
                return SimpleNamespace(tools=[SimpleNamespace(name="time-get_current_time", description="synthetic",
                                                               inputSchema={"type": "object"})])
            async def call_tool(**kwargs):
                tool_calls.append(kwargs)
                return "SYNTHETIC TIME"
            llm.select_model = select_model
            llm.agent = SimpleNamespace(list_tools=list_tools)
            llm._call_mcp_tool = call_tool
            llm._context = SimpleNamespace()  # Never trigger ambient global context/config discovery.
            llm.get_provider_config = lambda _: SimpleNamespace(api_key="dummy-no-oauth", base_url=str(server.make_url("/v1")))
            llm._log_chat_progress = lambda **_: None
            llm._log_chat_finished = lambda **_: None
            assert await llm.generate_str("synthetic user") == "SYNTHETIC FINAL"
    asyncio.run(run())
    assert len(calls) == 2 and len(tool_calls) == 1
    assert calls[1]["input"][-2:] == [
        {"type": "function_call", "name": "time-get_current_time", "call_id": "call1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call1", "output": "SYNTHETIC TIME"}]
    assert all(call["store"] is False and "previous_response_id" not in call for call in calls)
    assert all("max_output_tokens" not in call for call in calls)  # Existing native translator behavior, NOT a token cap.


def test_direct_supervised_cli_without_pythonpath_bootstraps_and_expires(tmp_path):
    auth = snapshot(tmp_path, expires_at=time.time() + 3600)
    with tempfile.TemporaryDirectory(prefix="prx-", dir=Path("/tmp").resolve()) as directory:
        sock = Path(directory) / "proxy.sock"
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(Path(proxy.__file__).resolve()),
            "--socket", str(sock), "--auth-snapshot", str(auth), "--read-tool", "time-get_current_time",
            "--run-seconds", "1"], cwd=directory, env=env, capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == b"" and result.stderr == b""
        assert time.monotonic() - started >= .9 and not sock.exists()


@pytest.mark.parametrize("how", ["stop", "cancel", "expiry"])
def test_stalled_upstream_shutdown_closes_session_and_socket(tmp_path, monkeypatch, how):
    import aiohttp
    from cores.chatgpt_proxy import proxy_server as native
    entered, closed = None, []
    class Context:
        async def __aenter__(self):
            entered.set()
            await asyncio.Event().wait()
        async def __aexit__(self, *_):
            return False
    class Session:
        def __init__(self, *_):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            closed.append(True)
        def post(self, *_args, **_kwargs):
            return Context()
    monkeypatch.setattr(proxy, "_BoundedSession", Session)
    async def run(directory):
        nonlocal entered
        entered = asyncio.Event()
        auth = snapshot(tmp_path, expires_at=time.time() + 3600)
        sock = directory / "proxy.sock"
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(proxy.serve(sock, auth, allowed_tools={"time-get_current_time"},
            run_seconds=1 if how == "expiry" else 60, stop_event=stop, ready=ready))
        async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(sock))) as client:
            await asyncio.wait_for(ready.wait(), 3)
            async def request():
                try:
                    async with client.post("http://localhost/v1/responses", json=body()) as response:
                        await response.read()
                except aiohttp.ClientError:
                    pass
            request_task = asyncio.create_task(request())
            await asyncio.wait_for(entered.wait(), 3)
            started = time.monotonic()
            if how == "stop":
                stop.set()
            elif how == "cancel":
                task.cancel()
            try:
                await asyncio.wait_for(task, 5)
            except asyncio.CancelledError:
                assert how == "cancel"
            await asyncio.wait_for(request_task, 2)
            assert time.monotonic() - started < 5
        assert closed == [True] and not sock.exists()
        assert native._token_manager is None and not proxy._ACTIVE
    with tempfile.TemporaryDirectory(prefix="prx-", dir=Path("/tmp").resolve()) as directory:
        asyncio.run(run(Path(directory)))


def test_body_read_reservation_blocks_parallel_requests(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    from cores.chatgpt_proxy import proxy_server as native
    original_json = proxy.web.Request.json
    reads, forwards = [], []
    entered, release = None, None
    async def delayed_json(request, *args, **kwargs):
        reads.append(True)
        entered.set()
        await release.wait()
        return await original_json(request, *args, **kwargs)
    async def forward(_):
        forwards.append(True)
        return {"id": "synthetic", "output": []}, None
    monkeypatch.setattr(proxy.web.Request, "json", delayed_json)
    monkeypatch.setattr(native, "_forward_to_codex", forward)
    async def run():
        nonlocal entered, release
        entered, release = asyncio.Event(), asyncio.Event()
        manager = proxy.FrozenTokenManager.load(snapshot(tmp_path), run_seconds=600, now=1000)
        async with TestClient(TestServer(proxy.create_dedicated_app(manager, allowed_tools={"time-get_current_time"}))) as client:
            async def first_request():
                return await client.post("/v1/responses", json=body())
            first = asyncio.create_task(first_request())
            await asyncio.wait_for(entered.wait(), 2)
            second = await client.post("/v1/responses", json=body())
            assert second.status == 429 and len(reads) == 1 and not forwards
            release.set()
            assert (await asyncio.wait_for(first, 2)).status == 200
        assert len(forwards) == 1 and not proxy._ACTIVE
    asyncio.run(run())


def test_snapshot_projection_is_filtered_exclusive_and_source_unchanged(tmp_path):
    tmp_path.chmod(0o700)
    source = snapshot(tmp_path, expires_at=time.time() + 3600, refresh_token="NEVER_COPY_REFRESH", other="irrelevant")
    before = source.read_bytes()
    destination = tmp_path / "filtered.json"
    result = proxy.prepare_private_snapshot(source, destination)
    assert result == {"status": "prepared", "refresh_disabled": True, "original_auth_modified": False}
    assert source.read_bytes() == before
    assert set(json.loads(destination.read_text())) == {"access_token", "account_id", "expires_at"}
    assert "NEVER_COPY_REFRESH" not in destination.read_text()
    assert destination.stat().st_mode & 0o777 == 0o600
    proxy.FrozenTokenManager.load(destination, run_seconds=600)
    with pytest.raises(proxy.ProxyRejected):
        proxy.prepare_private_snapshot(source, destination)
    assert source.read_bytes() == before


@pytest.mark.parametrize("operation", ["load", "project", "serve"])
def test_private_paths_reject_reachable_parent_traversal(tmp_path, operation):
    tmp_path.chmod(0o700)
    source = snapshot(tmp_path, expires_at=time.time() + 3600)
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    alias = sibling / ".." / source.name
    with pytest.raises(proxy.ProxyRejected):
        if operation == "load":
            proxy.FrozenTokenManager.load(alias, run_seconds=600)
        elif operation == "project":
            proxy.prepare_private_snapshot(source, sibling / ".." / "new.json")
        else:
            asyncio.run(proxy.serve(sibling / ".." / "socket", source, allowed_tools={"time-get_current_time"}))
