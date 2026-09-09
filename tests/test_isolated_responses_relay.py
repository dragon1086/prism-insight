import contextlib
import asyncio
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from tools import isolated_responses_relay as relay


@contextlib.contextmanager
def endpoint(monkeypatch, handler):
    with tempfile.TemporaryDirectory(prefix="rr-", dir=Path("/tmp").resolve()) as directory:
        path = Path(directory) / "responses.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(4)
        listener.settimeout(3)
        failures = []
        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    handler(connection)
            except Exception as error:
                failures.append(type(error).__name__)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        monkeypatch.setattr(relay, "RESPONSES_SOCKET", str(path))
        try:
            yield path, failures
        finally:
            listener.close()
            thread.join(timeout=4)


def connect(instance):
    return socket.create_connection(instance.server.server_address, timeout=3)


def test_large_final_response_survives_upstream_eof_and_backpressure(monkeypatch):
    payload = ("합성 응답📈" * 50000).encode()
    received = []
    def handler(connection):
        received.append(connection.recv(100))
        connection.sendall(payload)
    with endpoint(monkeypatch, handler) as (_, failures):
        with relay.ResponsesRelay() as instance, connect(instance) as client:
            client.sendall(b"synthetic request")
            time.sleep(.1)
            output = bytearray()
            while True:
                part = client.recv(65536)
                if not part:
                    break
                output.extend(part)
            assert bytes(output) == payload
        assert received == [b"synthetic request"] and not failures


def test_shutdown_closes_owned_blocked_connection(monkeypatch):
    closed = threading.Event()
    def handler(connection):
        while connection.recv(100):
            pass
        closed.set()
    with endpoint(monkeypatch, handler):
        instance = relay.ResponsesRelay().__enter__()
        with connect(instance) as client:
            client.sendall(b"x")
            time.sleep(.05)
            started = time.monotonic()
            instance.close()
            assert time.monotonic() - started < 3
            assert closed.wait(2)


def test_idle_and_client_disconnect_do_not_retry(monkeypatch):
    seen = []
    def handler(connection):
        seen.append(connection.recv(100))
        assert connection.recv(100) == b""
    with endpoint(monkeypatch, handler) as (_, failures):
        with relay.ResponsesRelay(idle_seconds=.05) as instance, connect(instance) as client:
            client.sendall(b"x")
            assert client.recv(100) == b""
        assert seen == [b"x"] and not failures


def test_missing_or_nonprivate_socket_fails_before_listen(tmp_path, monkeypatch):
    path = tmp_path / "not-a-socket"
    path.write_text("SYNTHETIC")
    monkeypatch.setattr(relay, "RESPONSES_SOCKET", str(path))
    with pytest.raises(relay.RelayRejected):
        relay.ResponsesRelay().__enter__()


def test_connection_cap_rejects_before_second_upstream_or_thread(monkeypatch):
    entered = threading.Event()
    def handler(connection):
        entered.set()
        while connection.recv(100):
            pass
    with endpoint(monkeypatch, handler):
        with relay.ResponsesRelay(max_connections=1) as instance, connect(instance) as first:
            first.sendall(b"x")
            assert entered.wait(2)
            with connect(instance) as second:
                assert second.recv(10) == b""
            assert len(instance.server.pairs) == 1
            with pytest.raises(relay.RelayRejected, match="already_started"):
                instance.__enter__()


@pytest.mark.parametrize("kwargs", [{"idle_seconds": float("inf")}, {"idle_seconds": True}, {"max_connections": 5}])
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(relay.RelayRejected):
        relay.ResponsesRelay(**kwargs)


def test_real_sdk_through_relay_and_native_proxy_with_fake_upstream(monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    from tools import isolated_responses_proxy as proxy
    calls = []
    class Upstream:
        async def __aenter__(self):
            async def text():
                return json.dumps({"id": "synthetic", "object": "response", "created_at": 1,
                                   "model": "gpt-5.6-sol", "status": "completed", "output": []})
            return SimpleNamespace(status=200, headers={"Content-Type": "application/json"}, text=text)
        async def __aexit__(self, *_):
            return False
    class Session:
        def __init__(self, *_):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            return False
        def post(self, url, **kwargs):
            calls.append(url)
            return Upstream()
    monkeypatch.setattr(proxy, "_BoundedSession", Session)
    async def run(directory):
        auth = directory / "auth.json"
        auth.write_text(json.dumps({"access_token": "SYNTHETIC", "account_id": "synthetic-account",
                                    "expires_at": time.time() + 3600}))
        auth.chmod(0o600)
        sock = directory / "responses.sock"
        monkeypatch.setattr(relay, "RESPONSES_SOCKET", str(sock))
        stop, ready = asyncio.Event(), asyncio.Event()
        server = asyncio.create_task(proxy.serve(sock, auth, allowed_tools={"time-get_current_time"},
                                                  run_seconds=10, stop_event=stop, ready=ready))
        try:
            await asyncio.wait_for(ready.wait(), 3)
            with relay.ResponsesRelay() as instance:
                async with AsyncOpenAI(api_key="dummy", base_url=instance.base_url, max_retries=0,
                                       http_client=httpx.AsyncClient(trust_env=False)) as client:
                    result = await client.responses.create(model="gpt-5.6-sol", input="synthetic")
                    assert result.id == "synthetic" and result.status == "completed"
        finally:
            stop.set()
            await asyncio.wait_for(server, 3)
        assert not sock.exists() and not proxy._ACTIVE
    with tempfile.TemporaryDirectory(prefix="rrp-", dir=Path("/tmp").resolve()) as directory:
        asyncio.run(run(Path(directory)))
    assert calls == ["https://chatgpt.com/backend-api/codex/responses"]
