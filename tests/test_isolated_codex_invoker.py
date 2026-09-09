"""No model, broker, production database or credentials are used."""
import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

from tools import isolated_codex_invoker as rpc


@pytest.fixture
def tmp_path():
    # AF_UNIX has a small path limit on both supported operating systems.
    with tempfile.TemporaryDirectory(prefix="ipc-", dir="/tmp") as directory:
        yield Path(directory).resolve()


def setup(tmp_path, callback, **kwargs):
    source = sqlite3.connect(tmp_path / "agent.db")
    source.execute("CREATE TABLE holding(ticker TEXT)")
    source.execute("INSERT INTO holding VALUES ('FIXTURE')")
    source.commit()
    private = tmp_path / "host"
    private.mkdir(mode=0o700)
    scope = rpc.InvocationScope("arm1", "profile1", "case1", "revision1", '{"model":"fixed"}', .15, source)
    service = rpc.CodexInvoker([scope], callback, private, window_seconds=5, cleanup_slack=.5, **kwargs)
    return service, source, private / "invoke.sock"


def request(**changes):
    return {"request_id": "request1", "arm_id": "arm1", "profile_id": "profile1", "case_id": "case1",
            "system_prompt": "system", "user_prompt": "user", **changes}


def test_completed_duplicate_is_cached_with_frozen_snapshot(tmp_path):
    async def run():
        calls = []
        async def callback(invocation):
            calls.append(invocation)
            assert invocation.snapshot.path.stat().st_mode & 0o777 == 0o400
            with sqlite3.connect(f"file:{invocation.snapshot.path}?mode=ro", uri=True) as db:
                assert db.execute("SELECT ticker FROM holding").fetchone() == ("FIXTURE",)
            return rpc.BackendCompletion('{"answer":1}')
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            first = await rpc.invoke(socket, request(), deadline=2)
            source.execute("UPDATE holding SET ticker='LATER'")
            source.commit()
            assert await rpc.invoke(socket, request(), deadline=2) == first
            assert first["cleanup_ack"] is True and first["fallback_allowed"] is False
            assert first["revision_basis"] == "HOST_REGISTERED_CASE_REVISION"
            assert len(calls) == 1
        source.close()
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"argv": ["/bin/sh"]}, {"profile_id": "unknown"}, {"user_prompt": "x" * 1048577}])
def test_unregistered_or_extra_capability_is_abort_not_fallback(tmp_path, change):
    async def run():
        async def callback(_):
            pytest.fail("must not invoke")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(**change), deadline=2)
        source.close()
    asyncio.run(run())


def test_conflict_cannot_reexecute_and_model_error_requires_explicit_receipt(tmp_path):
    async def run():
        calls = []
        async def callback(_):
            calls.append(1)
            return rpc.BackendCompletion(None, error="model_error")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            reply = await rpc.invoke(socket, request(), deadline=2)
            assert reply["fallback_allowed"] is True and reply["cleanup_ack"] is True
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(user_prompt="different"), deadline=2)
            assert calls == [1]
        source.close()
    asyncio.run(run())


def test_timeout_waits_for_cancellation_cleanup_before_ack(tmp_path):
    async def run():
        cleaned = asyncio.Event()
        async def callback(_):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(.04)
                cleaned.set()
                raise
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            reply = await rpc.invoke(socket, request(), deadline=2)
            assert cleaned.is_set()
            assert reply["category"] == "model_timeout" and reply["cleanup_ack"] is True
        source.close()
    asyncio.run(run())


def test_active_duplicate_aborts_without_cancelling_original(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def callback(_):
            calls.append(1)
            entered.set()
            await release.wait()
            return rpc.BackendCompletion("{}")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            original = asyncio.create_task(rpc.invoke(socket, request(), deadline=2))
            await entered.wait()
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
            release.set()
            assert (await original)["category"] == "ok"
            assert calls == [1]
        source.close()
    asyncio.run(run())


def test_snapshot_is_standalone_from_wal_and_has_actual_hash(tmp_path):
    import hashlib
    source = sqlite3.connect(tmp_path / "source.db")
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("CREATE TABLE value(n)")
    source.execute("INSERT INTO value VALUES(7)")
    source.commit()
    host = tmp_path / "host"
    host.mkdir(mode=0o700)
    snapshot = rpc.snapshot_sqlite(source, host, "opaque")
    assert snapshot.sha256 == hashlib.sha256(snapshot.path.read_bytes()).hexdigest()
    assert not list(host.glob("*-wal")) and not list(host.glob("*-shm"))
    assert json.loads(json.dumps(snapshot.sha256)) == snapshot.sha256
    source.close()


def test_disconnect_cancels_and_reaps_harmless_child_before_next_call(tmp_path):
    async def run():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        children = []
        async def callback(_):
            child = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time;time.sleep(30)")
            children.append(child)
            entered.set()
            try:
                await child.wait()
            except asyncio.CancelledError:
                child.terminate()
                await child.wait()
                cleaned.set()
                raise
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            client = asyncio.create_task(rpc.invoke(socket, request(), deadline=2))
            await entered.wait()
            client.cancel()
            with pytest.raises(asyncio.CancelledError):
                await client
            await asyncio.wait_for(cleaned.wait(), 2)
            assert children[0].returncode is not None
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
            assert len(children) == 1
        source.close()
    asyncio.run(run())


def test_cleanup_unknown_poison_blocks_new_model_calls(tmp_path):
    async def run():
        release = asyncio.Event()
        tasks = []
        async def callback(_):
            tasks.append(asyncio.current_task())
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await release.wait()  # deliberately violates timely-cleanup contract
                return rpc.BackendCompletion("{}")
        service, source, socket = setup(tmp_path, callback)
        service.cleanup_slack = .02
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
            assert service.poisoned
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(request_id="request2"), deadline=2)
            assert len(tasks) == 1
            release.set()
            await asyncio.gather(*tasks)
        source.close()
    asyncio.run(run())


def test_arbitrary_callback_error_is_sanitized_abort_not_fallback(tmp_path, caplog):
    async def run():
        async def callback(_):
            raise RuntimeError("SECRET_CALLBACK_CANARY")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted) as error:
                await rpc.invoke(socket, request(), deadline=2)
            assert "SECRET" not in str(error.value)
            assert service.poisoned
        source.close()
    asyncio.run(run())
    assert "SECRET_CALLBACK_CANARY" not in caplog.text


def test_request_cap_never_evicts_or_reexecutes_completed_ids(tmp_path):
    async def run():
        calls = []
        async def callback(_):
            calls.append(1)
            return rpc.BackendCompletion("{}")
        service, source, socket = setup(tmp_path, callback, max_requests=1)
        async with service.serve(socket):
            first = await rpc.invoke(socket, request(), deadline=2)
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(request_id="request2"), deadline=2)
            assert await rpc.invoke(socket, request(), deadline=2) == first
            assert calls == [1]
        source.close()
    asyncio.run(run())


def test_partial_frame_and_transport_timeout_abort_without_fallback(tmp_path):
    async def run():
        async def partial(reader, writer):
            await reader.read(4)
            writer.write(b"\x00\x00")
            await writer.drain()
            writer.close()
        socket = tmp_path / "fake.sock"
        server = await asyncio.start_unix_server(partial, str(socket))
        try:
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=.2)
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_server_close_waits_for_active_cleanup(tmp_path):
    async def run():
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def callback(_):
            entered.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(.02)
                cleaned.set()
                raise
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            client = asyncio.create_task(rpc.invoke(socket, request(), deadline=2))
            await entered.wait()
        assert cleaned.is_set() and not socket.exists()
        with pytest.raises(rpc.InvocationAborted):
            await client
        source.close()
    asyncio.run(run())


def test_different_request_cannot_queue_behind_active_call(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def callback(_):
            calls.append(1)
            entered.set()
            await release.wait()
            return rpc.BackendCompletion("{}")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            first = asyncio.create_task(rpc.invoke(socket, request(), deadline=2))
            await entered.wait()
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(request_id="other"), deadline=2)
            release.set()
            await first
            assert calls == [1]
        source.close()
    asyncio.run(run())


def test_client_deadline_is_uncertain_abort_and_host_joins_cleanup(tmp_path):
    async def run():
        cleaned = asyncio.Event()
        async def callback(_):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(.01)
                cleaned.set()
                raise
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=.04)
            await asyncio.wait_for(cleaned.wait(), 2)
            assert not service.poisoned
        source.close()
    asyncio.run(run())


@pytest.mark.parametrize("raw", [b'{"request_id":"a","request_id":"b"}', b'[]', b'not-json'])
def test_malformed_raw_frame_never_reaches_callback(tmp_path, raw):
    async def run():
        async def callback(_):
            pytest.fail("invalid wire data invoked model")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            reader, writer = await asyncio.open_unix_connection(str(socket))
            writer.write(len(raw).to_bytes(4, "big") + raw)
            await writer.drain()
            reply = await rpc._read(reader)
            assert reply["cleanup_ack"] is False and reply["fallback_allowed"] is False
            writer.close()
        source.close()
    asyncio.run(run())


def test_window_with_insufficient_cleanup_slack_never_launches(tmp_path):
    async def run():
        async def callback(_):
            pytest.fail("insufficient bounded window")
        service, source, socket = setup(tmp_path, callback)
        service.window_seconds = .1
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
        source.close()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "invalid_receipt"])
def test_cleanup_failure_never_grants_timeout_fallback(tmp_path, failure):
    async def run():
        async def callback(_):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                if failure == "exception":
                    raise RuntimeError("SECRET_FAILED_CLEANUP") from None
                return "not-a-cleanup-receipt"
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
            assert service.poisoned
            response = service.records["request1"][1].result()
            assert response["cleanup_ack"] is False and response["fallback_allowed"] is False
        source.close()
    asyncio.run(run())


def test_expired_window_never_replays_cached_fallback_permission(tmp_path):
    async def run():
        async def callback(_):
            return rpc.BackendCompletion(None, error="model_error")
        service, source, socket = setup(tmp_path, callback)
        async with service.serve(socket):
            assert (await rpc.invoke(socket, request(), deadline=2))["fallback_allowed"] is True
            service.started -= 6
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
        source.close()
    asyncio.run(run())


def test_slow_snapshot_cannot_start_model_without_remaining_cleanup_slack(tmp_path, monkeypatch):
    async def run():
        async def callback(_):
            pytest.fail("slow snapshot consumed registered window")
        service, source, socket = setup(tmp_path, callback)
        original = rpc.snapshot_sqlite
        def slow_snapshot(*args, **kwargs):
            snapshot = original(*args, **kwargs)
            service.started -= 4.5
            return snapshot
        monkeypatch.setattr(rpc, "snapshot_sqlite", slow_snapshot)
        async with service.serve(socket):
            with pytest.raises(rpc.InvocationAborted):
                await rpc.invoke(socket, request(), deadline=2)
        source.close()
    asyncio.run(run())
