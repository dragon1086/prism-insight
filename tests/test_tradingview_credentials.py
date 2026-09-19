"""Synthetic credentials only: never inspect the user's credential store."""
import asyncio
import gzip
import json
import logging
import os
import stat
import sys
import threading
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from prism_core import tradingview_credentials as credentials
from prism_core.tradingview_credentials import (
    TradingViewCredentialError,
    make_credential_supplier,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "auth.json"
    write(path, document())
    return path


def document(seconds=60):
    return {"opaque": {"server_name": "tradingview", "server_url": "https://mcp.tradingview.com/mcp",
                       "issuer": "https://www.tradingview.com", "client_id": "SYNTHETIC_CLIENT",
                       "access_token": "SYNTHETIC_OLD", "refresh_token": "SYNTHETIC_REFRESH",
                       "expires_at": int(NOW.timestamp() * 1000) + seconds * 1000},
            "unrelated": {"keep": [1, 2]}}


def write(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def supplier(path, handler=None, **kwargs):
    def factory(**options):
        assert options == {"trust_env": False, "follow_redirects": False, "timeout": 20}
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
    return make_credential_supplier(path, clock=lambda: NOW,
                                    http_client_factory=factory if handler else None, **kwargs)


def response(request):
    assert str(request.url) == "https://www.tradingview.com/mcp/oauth/token"
    return httpx.Response(200, json={"access_token": "SYNTHETIC_NEW", "token_type": "Bearer",
                                    "expires_in": 3600, "refresh_token": "SYNTHETIC_ROTATED"})


def test_factory_is_lazy(tmp_path):
    make_credential_supplier(tmp_path / "missing", http_client_factory=lambda **kw: pytest.fail())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_fresh_no_http_no_rewrite(store):
    data = document(601)
    del data["opaque"]["client_id"]
    del data["opaque"]["refresh_token"]
    write(store, data)
    before = store.stat().st_mtime_ns
    grant = await supplier(store, lambda r: pytest.fail("HTTP"))()
    assert grant.access_token == "SYNTHETIC_OLD"
    assert store.stat().st_mtime_ns == before
    assert not store.with_name(store.name + ".refresh-state.json").exists()


@pytest.mark.asyncio
async def test_concurrent_refresh_once_and_preserve(store):
    calls = []
    async def handler(request):
        calls.append(request)
        marker = json.loads(store.with_name(store.name + ".refresh-state.json").read_text())
        assert marker["state"] == "IN_FLIGHT"
        await asyncio.sleep(0.03)
        return response(request)
    grants = await asyncio.gather(*(supplier(store, handler)() for _ in range(3)))
    assert len(calls) == 1
    assert all(g.access_token == "SYNTHETIC_NEW" for g in grants)
    assert json.loads(store.read_text())["unrelated"] == {"keep": [1, 2]}
    assert store.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_ambiguous_refresh_blocks_metadata_and_expiry_edits(store):
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ReadError("SYNTHETIC_SECRET")
    for index in range(3):
        with pytest.raises(TradingViewCredentialError) as exc:
            await supplier(store, fail)()
        assert str(exc.value) == ("AUTH_REFRESH_FAILED" if index == 0 else "AUTH_REFRESH_IN_FLIGHT")
        data = json.loads(store.read_text())
        data["opaque"]["metadata"] = index
        data["opaque"]["expires_at"] += 900000
        write(store, data)
    assert len(calls) == 1
    data["opaque"]["access_token"] = "SYNTHETIC_REAUTHORIZED"
    write(store, data)
    assert (await supplier(store)()).access_token == "SYNTHETIC_REAUTHORIZED"


@pytest.mark.parametrize("mutation", ["symlink", "hardlink", "mode", "directory", "duplicate", "nan", "oversize"])
@pytest.mark.asyncio
async def test_unsafe_store_rejected(store, mutation):
    if mutation == "symlink":
        actual = store.with_name("actual")
        store.rename(actual)
        store.symlink_to(actual)
    elif mutation == "hardlink":
        os.link(store, store.with_name("other"))
    elif mutation == "mode":
        store.chmod(0o644)
    elif mutation == "directory":
        store.parent.chmod(0o755)
    else:
        store.write_text({"duplicate": '{"x":1,"x":2}', "nan": '{"x":NaN}',
                          "oversize": " " * (256 * 1024 + 1)}[mutation])
    with pytest.raises(TradingViewCredentialError, match="AUTH_"):
        await supplier(store, lambda r: pytest.fail("HTTP"))()


@pytest.mark.parametrize("payload", [None, {}, {"token_type": "bearer", "access_token": "a", "expires_in": True},
                                    {"token_type": "Bearer", "access_token": "a b", "expires_in": 60}])
@pytest.mark.asyncio
async def test_invalid_refresh_response(store, payload):
    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_RESPONSE"):
        await supplier(store, lambda r: httpx.Response(200, json=payload))()


@pytest.mark.asyncio
async def test_external_writer_not_overwritten(store):
    def handler(request):
        changed = document()
        changed["unrelated"] = "external"
        write(store, changed)
        return response(request)
    with pytest.raises(TradingViewCredentialError, match="AUTH_STORE_CHANGED"):
        await supplier(store, handler)()
    assert json.loads(store.read_text())["unrelated"] == "external"


@pytest.mark.asyncio
async def test_cancel_drains_refresh_and_commits(store):
    started = asyncio.Event()
    release = asyncio.Event()
    async def handler(request):
        started.set()
        await release.wait()
        return response(request)
    task = asyncio.create_task(supplier(store, handler)())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await supplier(store)()).access_token == "SYNTHETIC_NEW"


@pytest.mark.asyncio
async def test_real_subprocess_lock_rereads_committed_store(store):
    # A separate process holds the exact sibling lock, then publishes a fresh grant.
    script = '''import fcntl,json,os,sys,time
p=sys.argv[1]
fd=os.open(p+".lock",os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(fd,fcntl.LOCK_EX)
print("LOCKED",flush=True)
time.sleep(.2)
with open(p) as f: d=json.load(f)
d["opaque"]["access_token"]="SYNTHETIC_SUBPROCESS"
d["opaque"]["expires_at"]+=3600000
with open(p,"w") as f: json.dump(d,f)
fcntl.flock(fd,fcntl.LOCK_UN)
os.close(fd)
'''
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(store),
                                                   stdout=asyncio.subprocess.PIPE)
    try:
        assert (await process.stdout.readline()).strip() == b"LOCKED"
        grant = await supplier(store, lambda r: pytest.fail("HTTP"))()
        assert grant.access_token == "SYNTHETIC_SUBPROCESS"
    finally:
        assert await asyncio.wait_for(process.wait(), 5) == 0


@pytest.mark.parametrize("field,value", [("expires_at", True), ("expires_at", "1790000000000"),
    ("expires_at", -1), ("expires_at", 10**100), ("access_token", "bad token"),
    ("refresh_token", ""), ("client_id", "x" * 8193)])
@pytest.mark.asyncio
async def test_invalid_entry_never_creates_marker(store, field, value):
    data = document()
    data["opaque"][field] = value
    write(store, data)
    with pytest.raises(TradingViewCredentialError, match="AUTH_ENTRY_INVALID"):
        await supplier(store, lambda r: pytest.fail("HTTP"))()
    assert not store.with_name(store.name + ".refresh-state.json").exists()


@pytest.mark.parametrize("variant", ["missing", "duplicate", "issuer", "url"])
@pytest.mark.asyncio
async def test_exact_single_matching_identity(store, variant):
    data = document()
    if variant == "missing":
        del data["opaque"]
    elif variant == "duplicate":
        data["another"] = data["opaque"].copy()
    else:
        data["opaque"]["issuer" if variant == "issuer" else "server_url"] += "/"
    write(store, data)
    with pytest.raises(TradingViewCredentialError, match="AUTH_ENTRY_INVALID"):
        await supplier(store)()


@pytest.mark.parametrize("boundary", [1, 2])
@pytest.mark.asyncio
async def test_directory_fsync_failure_no_grant_and_marker_retained(store, monkeypatch, boundary):
    original = credentials.os.fsync
    count = 0
    calls = []
    def fsync(fd):
        nonlocal count
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            count += 1
            if count == boundary:
                raise OSError("SYNTHETIC_PRIVATE_ERROR")
        original(fd)
    monkeypatch.setattr(credentials.os, "fsync", fsync)
    def handler(request):
        calls.append(request)
        assert count == 1  # marker directory was fsynced before POST
        return response(request)
    with pytest.raises(TradingViewCredentialError, match="AUTH_STORE_UNAVAILABLE"):
        await supplier(store, handler)()
    assert len(calls) == boundary - 1
    assert store.with_name(store.name + ".refresh-state.json").exists()
    assert not list(store.parent.glob(".tv-*"))


@pytest.mark.parametrize("suffix", [".lock", ".refresh-state.json"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "mode"])
@pytest.mark.asyncio
async def test_unsafe_siblings_rejected(store, suffix, kind):
    sibling = store.with_name(store.name + suffix)
    if kind == "symlink":
        sibling.symlink_to(store)
    elif kind == "hardlink":
        os.link(store, sibling)
    else:
        write(sibling, {})
        sibling.chmod(0o644)
    with pytest.raises(TradingViewCredentialError, match="AUTH_"):
        await supplier(store, lambda r: pytest.fail("HTTP"))()


@pytest.mark.asyncio
async def test_wrong_owner_rejected(store, monkeypatch):
    monkeypatch.setattr(credentials.os, "getuid", lambda: -1)
    with pytest.raises(TradingViewCredentialError, match="AUTH_STORE_UNSAFE"):
        await supplier(store)()


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500])
@pytest.mark.asyncio
async def test_http_failure_not_retried(store, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.invalid"}, text="SYNTHETIC_SECRET")
    for _ in range(2):
        with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_"):
            await supplier(store, handler)()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_stream_body_budget(store):
    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_RESPONSE"):
        await supplier(store, lambda r: httpx.Response(200, content=b" " * (64 * 1024 + 1)))()


@pytest.mark.asyncio
async def test_compressed_stream_rejected_before_consumption(store):
    consumed = []
    requests = []
    compressed = gzip.compress(b"x" * (8 * 1024 * 1024))
    assert len(compressed) < 64 * 1024

    class MaliciousStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True)
            yield compressed

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"Content-Encoding": " GZip "}, stream=MaliciousStream())

    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_RESPONSE"):
        await supplier(store, handler)()
    assert consumed == []
    assert requests[0].headers["Accept-Encoding"] == "identity"


@pytest.mark.parametrize("raw", [b'{"access_token":"a","access_token":"b"}',
                                  b'{"expires_in":NaN}', b'{"expires_in":1e999}', b'[]'])
@pytest.mark.asyncio
async def test_strict_response_json(store, raw):
    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_RESPONSE"):
        await supplier(store, lambda r: httpx.Response(200, content=raw))()


@pytest.mark.parametrize("expiry", [30, 86401, "3600", False, None])
@pytest.mark.asyncio
async def test_response_expiry_contract(store, expiry):
    def handler(request):
        return httpx.Response(200, json={"access_token": "NEW", "token_type": "Bearer", "expires_in": expiry})
    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_RESPONSE"):
        await supplier(store, handler)()


@pytest.mark.asyncio
async def test_private_marker_temp_fsync_failure_prevents_post(store, monkeypatch):
    def fail(fd):
        raise OSError("SYNTHETIC_PRIVATE_ERROR")
    monkeypatch.setattr(credentials.os, "fsync", fail)
    with pytest.raises(TradingViewCredentialError, match="AUTH_STORE_UNAVAILABLE"):
        await supplier(store, lambda r: pytest.fail("POST before durable marker"))()
    assert not store.with_name(store.name + ".refresh-state.json").exists()
    assert not list(store.parent.glob(".tv-*"))


@pytest.mark.asyncio
async def test_refresh_without_rotated_token(store):
    def handler(request):
        return httpx.Response(200, json={"access_token": "SYNTHETIC_NEW", "token_type": "Bearer", "expires_in": 3600})
    await supplier(store, handler)()
    assert json.loads(store.read_text())["opaque"]["refresh_token"] == "SYNTHETIC_REFRESH"


@pytest.mark.asyncio
async def test_timeout_and_secret_safe_logs_traceback(store, caplog):
    async def handler(request):
        logging.getLogger(__name__).warning("SYNTHETIC_PRIVATE_LOG %s", request.content)
        await asyncio.sleep(1)
        return response(request)
    with pytest.raises(TradingViewCredentialError, match="AUTH_REFRESH_FAILED") as caught:
        await supplier(store, handler, refresh_timeout_seconds=.01)()
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert "SYNTHETIC_" not in rendered
    assert "SYNTHETIC_" not in caplog.text
    assert "content suppressed" in caplog.text


@pytest.mark.asyncio
async def test_cancel_during_commit_drains_thread_and_double_cancel(store, monkeypatch):
    original = credentials._atomic_write
    started, release = threading.Event(), threading.Event()
    def delayed(directory, name, value, expected=None):
        if expected is not None:
            started.set()
            assert release.wait(5)
        return original(directory, name, value, expected)
    monkeypatch.setattr(credentials, "_atomic_write", delayed)
    task = asyncio.create_task(supplier(store, response)())
    while not started.is_set():
        await asyncio.sleep(.005)
    task.cancel()
    await asyncio.sleep(.01)
    task.cancel()
    await asyncio.sleep(.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await supplier(store)()).access_token == "SYNTHETIC_NEW"


@pytest.mark.asyncio
async def test_lock_timeout_and_cancel_before_post(store):
    import fcntl
    lock = os.open(str(store) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with pytest.raises(TradingViewCredentialError, match="AUTH_LOCK_TIMEOUT"):
            await supplier(store, lock_timeout_seconds=.01)()
        task = asyncio.create_task(supplier(store)())
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not store.with_name(store.name + ".refresh-state.json").exists()
    finally:
        os.close(lock)


@pytest.mark.asyncio
async def test_real_p2_p3a_composition_off_and_on(store):
    from prism_core.tradingview_collection import ReadRequest, collect_tradingview
    from prism_core.tradingview_transport import (
        TransportDependencies,
        make_transport_factory,
    )
    events = []
    @asynccontextmanager
    async def http(**options):
        assert options["headers"] == {"Authorization": "Bearer SYNTHETIC_NEW"}
        yield None
    @asynccontextmanager
    async def stream(*args, **kwargs):
        yield None, None, None
    @asynccontextmanager
    async def session(*args):
        class Session:
            async def initialize(self):
                events.append("initialized")
            async def call_tool(self, name, arguments):
                return SimpleNamespace(isError=False, structuredContent={"headlines": []}, content=[])
        yield Session()
    transport = make_transport_factory(supplier(store, response), clock=lambda: NOW,
        dependency_loader=lambda: TransportDependencies(http, stream, session))
    before = store.read_bytes()
    assert await collect_tradingview({"enabled": False}, None, transport) is None
    assert store.read_bytes() == before
    assert not store.with_name(store.name + ".lock").exists()
    result = await collect_tradingview({"enabled": True}, [ReadRequest("get_news", {})], transport)
    assert result["metrics"]["calls_attempted"] == 1
    assert events == ["initialized"]


@pytest.mark.asyncio
async def test_actual_supplier_across_processes_refreshes_once(store):
    script = '''import asyncio,sys,httpx
from datetime import datetime,timezone
from prism_core.tradingview_credentials import make_credential_supplier
async def handler(request):
 print("POST",flush=True)
 await asyncio.sleep(.2)
 return httpx.Response(200,json={"access_token":"SYNTHETIC_CHILD_NEW","token_type":"Bearer","expires_in":3600})
def factory(**options):
 return httpx.AsyncClient(transport=httpx.MockTransport(handler),**options)
async def main():
 grant=await make_credential_supplier(sys.argv[1],http_client_factory=factory,clock=lambda:datetime(2026,9,19,tzinfo=timezone.utc))()
 assert grant.access_token=="SYNTHETIC_CHILD_NEW"
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(store),
                                                   stdout=asyncio.subprocess.PIPE)
    try:
        assert (await asyncio.wait_for(process.stdout.readline(), 5)).strip() == b"POST"
        grant = await supplier(store, lambda r: pytest.fail("second refresh"))()
        assert grant.access_token == "SYNTHETIC_CHILD_NEW"
    finally:
        assert await asyncio.wait_for(process.wait(), 5) == 0
    assert await process.stdout.read() == b""
