"""Fixed-capability invocation primitive; NOT a production launcher.

Only a trusted supervisor registers scopes, settings and already-authorized
SQLite connections. Requests cannot choose executable, environment, filesystem
paths, endpoints, model or deadline. One active invocation, no queue, bounded
in-memory deduplication for this service lifetime; restarting requires a NEW
window/case registration, not replaying uncertain IDs.

The callback MUST join its owned resources before returning/raising cancellation.
An ACK proves that callback contract was joined, not independent process cleanup.
Production binding must reuse verified generate_codex_fast_async and add a
supervisor-loss watchdog BEFORE deployment. SIGKILL/parent-loss cleanup is NOT
implemented here. There is deliberately no executable CLI or backend import.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import struct
import time

MAX_FRAME = 2 * 1024 * 1024
MAX_PROMPT = 1024 * 1024
MAX_RESULT = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_FIELDS = {"request_id", "arm_id", "profile_id", "case_id", "system_prompt", "user_prompt"}


class InvocationAborted(asyncio.CancelledError):
    """Unknown/invalid transport MUST bypass ordinary model-error fallback."""


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


def _bounded_seconds(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .01 <= value <= maximum:
        raise ValueError("invalid_deadline")
    return float(value)


def _private_directory(path):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or path.resolve() != path or not path.is_dir()
            or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700):
        raise ValueError("unsafe_private_directory")
    return path


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str


def snapshot_sqlite(source, private_root, invocation_id, *, timeout=2):
    """Backup an already-authorized connection; never infer revision from DB.

    The supervisor must open/authorize source BEFORE exposing untrusted agents.
    SQLite backup includes committed WAL state. No live DB is model-mounted.
    """
    if not isinstance(source, sqlite3.Connection) or source.in_transaction:
        raise ValueError("committed_sqlite_connection_required")
    root = _private_directory(private_root)
    name = hashlib.sha256(_identifier(invocation_id).encode()).hexdigest() + ".sqlite"
    target = root / name
    end = time.monotonic() + _bounded_seconds(timeout, 10)
    page_size = source.execute("PRAGMA page_size").fetchone()[0]
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    try:
        def progress(status, remaining, total):
            if time.monotonic() >= end or total * page_size > 256 * 1024 * 1024:
                raise ValueError("snapshot_limit")
        with closing(sqlite3.connect(target, timeout=0)) as destination:
            source.backup(destination, pages=128, progress=progress, sleep=.01)
            destination.execute("PRAGMA journal_mode=DELETE")
        if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("snapshot_not_standalone")
        target.chmod(0o400)
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return Snapshot(target, digest.hexdigest())
    except BaseException:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class InvocationScope:
    arm_id: str
    profile_id: str
    case_id: str
    revision: str
    fixed_settings_json: str
    model_deadline: float
    sqlite_source: sqlite3.Connection = field(repr=False, compare=False)

    def __post_init__(self):
        for value in (self.arm_id, self.profile_id, self.case_id, self.revision):
            _identifier(value)
        _bounded_seconds(self.model_deadline, 600)
        if (not isinstance(self.fixed_settings_json, str) or len(self.fixed_settings_json.encode()) > 65536
                or not isinstance(json.loads(self.fixed_settings_json), dict)):
            raise ValueError("invalid_host_settings")
        if not isinstance(self.sqlite_source, sqlite3.Connection):
            raise ValueError("authorized_connection_required")


@dataclass(frozen=True)
class Invocation:
    scope: InvocationScope
    system_prompt: str
    user_prompt: str
    snapshot: Snapshot


@dataclass(frozen=True)
class BackendCompletion:
    """Trusted callback receipt: construct only AFTER owned cleanup completes."""
    result_json: str | None
    error: str | None = None


def _valid_receipt(receipt):
    if not isinstance(receipt, BackendCompletion):
        return False
    if receipt.error == "model_error" and receipt.result_json is None:
        return True
    if receipt.error is not None or not isinstance(receipt.result_json, str) or len(receipt.result_json.encode()) > MAX_RESULT:
        return False
    try:
        json.loads(receipt.result_json)
    except (ValueError, RecursionError):
        return False
    return True


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


async def _read(reader):
    size = struct.unpack("!I", await reader.readexactly(4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise ValueError("frame_limit")
    return json.loads(await reader.readexactly(size), object_pairs_hook=_unique_object)


async def _write(writer, value):
    data = _json(value)
    if len(data) > MAX_FRAME:
        raise ValueError("frame_limit")
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()


def _abort(category="invocation_aborted"):
    return {"category": category, "cleanup_ack": False, "fallback_allowed": False}


class CodexInvoker:
    def __init__(self, scopes, callback, private_snapshot_root, *, window_seconds=600, max_requests=32, cleanup_slack=10):
        self.root = _private_directory(private_snapshot_root)
        self.window_seconds = _bounded_seconds(window_seconds, 3600)
        self.cleanup_slack = _bounded_seconds(cleanup_slack, 60)
        if type(max_requests) is not int or not 1 <= max_requests <= 64 or not callable(callback):
            raise ValueError("invalid_host_registration")
        self.scopes = {}
        for scope in scopes:
            key = (scope.arm_id, scope.profile_id, scope.case_id)
            if key in self.scopes:
                raise ValueError("duplicate_scope")
            self.scopes[key] = scope
        if not self.scopes or len(self.scopes) > 64:
            raise ValueError("invalid_host_registration")
        self.callback, self.max_requests = callback, max_requests
        self.records, self.handlers, self.unjoined_callbacks = {}, set(), set()
        self.active = None
        self.poisoned = self.closed = False
        self.started = None

    async def _cancel_join(self, task):
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=self.cleanup_slack)
        if not done:
            self.poisoned = True
            self.unjoined_callbacks.add(task)
            def finished_callback(finished):
                self.unjoined_callbacks.discard(finished)
                if not finished.cancelled():
                    finished.exception()
            task.add_done_callback(finished_callback)
            return False
        try:
            receipt = task.result()
        except asyncio.CancelledError:
            # Explicit registered callback cancellation contract, not OS proof.
            return True
        except BaseException:
            self.poisoned = True
            return False
        if not _valid_receipt(receipt):
            self.poisoned = True
            return False
        return True

    async def _run(self, scope, request, cancel):
        callback_task = None
        cancelled = None
        try:
            snapshot = snapshot_sqlite(scope.sqlite_source, self.root, request["request_id"])
            if self.window_seconds - (time.monotonic() - self.started) < scope.model_deadline + self.cleanup_slack:
                return _abort("window_unavailable")
            metadata = {"snapshot_sha256": snapshot.sha256, "revision": scope.revision,
                        "revision_basis": "HOST_REGISTERED_CASE_REVISION",
                        "settings_sha256": hashlib.sha256(scope.fixed_settings_json.encode()).hexdigest(),
                        "cleanup_basis": "TRUSTED_CALLBACK_CONTRACT_NOT_EXTERNAL_PROCESS_PROOF"}
            callback_task = asyncio.create_task(self.callback(Invocation(scope, request["system_prompt"], request["user_prompt"], snapshot)))
            cancelled = asyncio.create_task(cancel.wait())
            done, _ = await asyncio.wait({callback_task, cancelled}, timeout=scope.model_deadline, return_when=asyncio.FIRST_COMPLETED)
            if callback_task not in done:
                joined = await self._cancel_join(callback_task)
                if not joined:
                    return _abort("cleanup_unknown")
                if cancel.is_set():
                    return {**_abort("cancelled"), "cleanup_ack": True, **metadata}
                return {"category": "model_timeout", "cleanup_ack": True, "fallback_allowed": True, **metadata}
            receipt = callback_task.result()
            if not _valid_receipt(receipt):
                self.poisoned = True
                return _abort("invalid_callback_receipt")
            if receipt.error == "model_error" and receipt.result_json is None:
                return {"category": "model_error", "cleanup_ack": True, "fallback_allowed": True, **metadata}
            return {"category": "ok", "cleanup_ack": True, "fallback_allowed": False,
                    "result_json": receipt.result_json, **metadata}
        except BaseException:
            if callback_task is not None:
                self.poisoned = True
            if callback_task is not None and not callback_task.done():
                await self._cancel_join(callback_task)
            return _abort()
        finally:
            if cancelled is not None:
                cancelled.cancel()
                await asyncio.gather(cancelled, return_exceptions=True)

    def _accept(self, request):
        if not isinstance(request, dict) or set(request) != _FIELDS:
            raise ValueError("invalid_request")
        for key in ("request_id", "arm_id", "profile_id", "case_id"):
            _identifier(request[key])
        for key in ("system_prompt", "user_prompt"):
            if not isinstance(request[key], str) or len(request[key].encode()) > MAX_PROMPT:
                raise ValueError("prompt_limit")
        scope = self.scopes.get(tuple(request[key] for key in ("arm_id", "profile_id", "case_id")))
        if scope is None:
            raise ValueError("unregistered_scope")
        # Never replay fallback permission while any other invocation is active
        # or cleanup is uncertain, even if this particular ID completed earlier.
        if (self.closed or self.poisoned or time.monotonic() - self.started >= self.window_seconds
                or self.active is not None and not self.active.done()):
            raise ValueError("window_unavailable")
        digest = hashlib.sha256(_json(request)).hexdigest()
        prior = self.records.get(request["request_id"])
        if prior is not None:
            if prior[0] != digest or not prior[1].done():
                raise ValueError("duplicate_active_or_conflicting")
            return prior[1], None
        if (self.closed or self.poisoned or self.window_seconds - (time.monotonic() - self.started) < scope.model_deadline + self.cleanup_slack + 2
                or len(self.records) >= self.max_requests or self.active is not None and not self.active.done()):
            raise ValueError("window_unavailable")
        cancel = asyncio.Event()
        task = asyncio.create_task(self._run(scope, request, cancel))
        self.records[request["request_id"]] = (digest, task, cancel)
        self.active = task
        return task, cancel

    async def _handle(self, reader, writer):
        if len(self.handlers) >= 8:
            writer.close()
            return
        handler = asyncio.current_task()
        self.handlers.add(handler)
        disconnected = None
        task = cancel = None
        try:
            request = await asyncio.wait_for(_read(reader), timeout=3)
            task, cancel = self._accept(request)
            disconnected = asyncio.create_task(reader.read(1))
            done, _ = await asyncio.wait({task, disconnected}, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done and cancel is not None:
                cancel.set()
                await asyncio.shield(task)
                return
            await asyncio.wait_for(_write(writer, task.result()), timeout=2)
        except asyncio.CancelledError:
            if cancel is not None:
                cancel.set()
                await asyncio.shield(task)
            raise
        except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, RecursionError):
            if cancel is not None:
                cancel.set()
                await asyncio.shield(task)
            try:
                await asyncio.wait_for(_write(writer, _abort()), timeout=1)
            except (OSError, asyncio.TimeoutError):
                pass
        finally:
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
            writer.close()
            self.handlers.discard(handler)

    @asynccontextmanager
    async def serve(self, socket_path):
        path = Path(socket_path)
        if self.started is not None or path.parent != self.root or path.exists() or path.is_symlink():
            raise ValueError("new_private_socket_required")
        self.started = time.monotonic()
        server = await asyncio.start_unix_server(self._handle, path=str(path), limit=MAX_FRAME + 4)
        path.chmod(0o600)
        try:
            yield self
        finally:
            self.closed = True
            server.close()
            for _, task, cancel in self.records.values():
                if not task.done():
                    cancel.set()
            await asyncio.gather(*(record[1] for record in self.records.values()), return_exceptions=True)
            for handler in tuple(self.handlers):
                handler.cancel()
            await asyncio.gather(*tuple(self.handlers), return_exceptions=True)
            await server.wait_closed()
            path.unlink(missing_ok=True)


async def invoke(socket_path, request, *, deadline):
    """Unknown transport is cancellation-class, NEVER an ordinary fallback."""
    writer = None
    try:
        async def exchange():
            nonlocal writer
            reader, writer = await asyncio.open_unix_connection(str(socket_path), limit=MAX_FRAME + 4)
            await _write(writer, request)
            response = await _read(reader)
            if (not isinstance(response, dict) or response.get("cleanup_ack") is not True
                    or response.get("category") not in {"ok", "model_error", "model_timeout"}
                    or response.get("fallback_allowed") is not (response.get("category") != "ok")
                    or response.get("cleanup_basis") != "TRUSTED_CALLBACK_CONTRACT_NOT_EXTERNAL_PROCESS_PROOF"
                    or response.get("revision_basis") != "HOST_REGISTERED_CASE_REVISION"
                    or any(not isinstance(response.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", response[key]) is None
                           for key in ("snapshot_sha256", "settings_sha256"))
                    or not isinstance(response.get("revision"), str)):
                raise InvocationAborted("invocation_aborted")
            expected = {"category", "cleanup_ack", "fallback_allowed", "cleanup_basis", "revision_basis",
                        "snapshot_sha256", "settings_sha256", "revision"}
            if response["category"] == "ok":
                expected.add("result_json")
                if not isinstance(response.get("result_json"), str) or len(response["result_json"].encode()) > MAX_RESULT:
                    raise InvocationAborted("invocation_aborted")
            if set(response) != expected:
                raise InvocationAborted("invocation_aborted")
            return response
        return await asyncio.wait_for(exchange(), timeout=_bounded_seconds(deadline, 700))
    except asyncio.CancelledError:
        raise
    except Exception:
        raise InvocationAborted("invocation_transport_unknown") from None
    finally:
        if writer is not None:
            writer.close()
