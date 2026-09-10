"""Single-case trusted composition, not a scheduler or trading-policy engine.

One operator-registered state root is the GLOBAL lane for primary AND fallback.
Never create a fresh lane to bypass a CLAIMED/UNKNOWN record. No automatic reset
or retry exists. Completion means a bounded diagnostic only, never LIVE orders,
Telegram delivery, financial-data correctness or end-to-end SHADOW readiness.
"""
from __future__ import annotations

import fcntl
import asyncio
from collections import Counter
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time

from tools import isolated_agent_namespace as namespace
from tools.codex_probe_sandbox import ParentLease, TRUSTED_HOST_PYTHON, load_host_manifest
from tools.isolated_codex_binding import FixedCodexBinding
from tools.isolated_codex_invoker import CodexInvoker, InvocationScope
from tools.isolated_case_journal import CaseJournal, JournalRejected, CATEGORIES


class CaseRejected(ValueError):
    pass


def _private_root(path):
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts or path.resolve() != path or not path.is_dir()
            or path.stat().st_uid != os.getuid() or stat.S_IMODE(path.stat().st_mode) != 0o700):
        raise CaseRejected("private_supervisor_root_required")
    return path


def _private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise CaseRejected("private_file_required")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(case_id, payload_sha256):
    if (not isinstance(case_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", case_id) is None
            or not isinstance(payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None):
        raise CaseRejected("invalid_registered_identity")


class CaseLane:
    """Process-wide advisory lock plus durable lane-wide uncertainty barrier."""
    def __init__(self, root):
        self.root = _private_root(root)
        self.fd = self.db = None

    def __enter__(self):
        path = self.root / "case-claims.sqlite"
        try:
            lock = self.root / "case.lock"
            self.fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            _private_file(lock)
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CaseRejected("lane_busy") from None
            if path.exists() or path.is_symlink():
                _private_file(path)
                check = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
                try:
                    tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if tables != {"metadata", "cases"} or check.execute("SELECT version FROM metadata").fetchall() != [(1,)]:
                        raise CaseRejected("unknown_claim_database")
                finally:
                    check.close()
                self.db = sqlite3.connect(path, timeout=0, isolation_level=None)
            else:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                self.db = sqlite3.connect(path, timeout=0, isolation_level=None)
                self.db.executescript("BEGIN IMMEDIATE; CREATE TABLE metadata(version INTEGER NOT NULL); INSERT INTO metadata VALUES(1); "
                                      "CREATE TABLE cases(case_id TEXT PRIMARY KEY,payload_sha256 TEXT NOT NULL,state TEXT NOT NULL,"
                                      "claimed_at REAL NOT NULL,finished_at REAL,summary TEXT); COMMIT;")
            self.db.execute("PRAGMA synchronous=FULL")
            return self
        except BaseException as error:
            self.__exit__(None, None, None)
            if isinstance(error, CaseRejected):
                raise
            raise CaseRejected("claim_store_unavailable") from None

    def claim(self, case_id, payload_sha256):
        _identity(case_id, payload_sha256)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute("SELECT 1 FROM cases WHERE state IS NULL OR state!='FINISHED' LIMIT 1").fetchone():
                raise CaseRejected("lane_uncertain")
            previous = self.db.execute("SELECT payload_sha256,summary FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if previous:
                if previous[0] != payload_sha256:
                    raise CaseRejected("case_conflict")
                self.db.execute("COMMIT")
                return json.loads(previous[1])
            if self.db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] >= 4096:
                raise CaseRejected("lane_history_limit")
            self.db.execute("INSERT INTO cases(case_id,payload_sha256,state,claimed_at) VALUES(?,?,'CLAIMED',?)",
                            (case_id, payload_sha256, time.time()))
            self.db.execute("COMMIT")  # BEFORE helper/model/agent side effects.
            return None
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def finish(self, case_id, payload_sha256, summary, *, cleanup_confirmed):
        _identity(case_id, payload_sha256)
        encoded = _json(summary)
        if len(encoded.encode()) > 16384 or type(cleanup_confirmed) is not bool:
            raise CaseRejected("invalid_completion")
        if cleanup_confirmed and summary.get("status") not in {"NOT_EXECUTED", "ANALYSIS_COMPLETE", "SOURCE_RULE_FALLBACK"}:
            raise CaseRejected("nonfinal_case_outcome")
        cursor = self.db.execute("UPDATE cases SET state=?,finished_at=?,summary=? WHERE case_id=? AND payload_sha256=? AND state='CLAIMED'",
                                 ("FINISHED" if cleanup_confirmed else "UNKNOWN", time.time(), encoded, case_id, payload_sha256))
        if cursor.rowcount != 1:
            raise CaseRejected("claim_completion_conflict")

    def __exit__(self, *_):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@dataclass(frozen=True)
class RegisteredCase:
    """All fields are supervisor registration, NEVER agent request inputs."""
    case_id: str
    arm_id: str
    profile_id: str
    revision: str
    settings_json: str
    model_deadline: float
    runtime: object
    binding: object
    namespace_json: str
    helper_source_root: Path
    helper_source_files_json: str
    helper_python_sha256: str
    auth_snapshot: Path
    case_deadline: float = 650
    mode: str = "VALIDATE_ONLY"


def _registration(registration, state_root):
    reg = registration
    if (reg.mode not in {"VALIDATE_ONLY", "EXECUTE_REGISTERED"} or type(reg.case_deadline) not in {int, float}
            or not 5 <= reg.case_deadline <= 1800):
        raise CaseRejected("invalid_registered_case")
    for value in (reg.case_id, reg.arm_id, reg.profile_id, reg.revision):
        _identity(value, "0" * 64)
    inputs = json.loads(reg.namespace_json)
    if not isinstance(inputs, dict) or "sockets" in inputs:
        raise CaseRejected("invalid_namespace_registration")
    if (reg.mode == "EXECUTE_REGISTERED") != (inputs.get("execute_registered") is True):
        raise CaseRejected("parent_execution_not_registered")
    for key in ("source_root", "runtime_root", "evidence_root", "arm_root"):
        inputs[key] = Path(inputs[key])
        if state_root.is_relative_to(inputs[key]) or inputs[key].is_relative_to(state_root):
            raise CaseRejected("supervisor_state_must_remain_private")
    if Path(reg.runtime.root) != inputs["arm_root"] or reg.binding.agent_root != inputs["arm_root"]:
        raise CaseRejected("registered_arm_mismatch")
    if Path(reg.runtime.db_path) != inputs["arm_root"] / "state.sqlite":
        raise CaseRejected("fixed_agent_database_required")
    filename = inputs["case_filename"]
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
        raise CaseRejected("case_registration_invalid")
    case_path = inputs["evidence_root"] / filename
    if case_path.resolve() != case_path or case_path.stat().st_size > 256 * 1024:
        raise CaseRejected("case_registration_invalid")
    case_bytes = case_path.read_bytes()
    case = json.loads(case_bytes)
    settings = json.loads(reg.settings_json)
    if (hashlib.sha256(case_bytes).hexdigest() != inputs["case_hash"]
            or any(case.get(key) != getattr(reg, key) for key in ("case_id", "arm_id", "profile_id"))
            or case["codex"]["revision"] != reg.revision
            or case["codex"]["settings_sha256"] != hashlib.sha256(reg.settings_json.encode()).hexdigest()
            or case["codex"]["expected_request"] != {**settings, "timeout": reg.model_deadline}
            or inputs["controls"] != {"market": case["market"], "model": settings["model"],
                                       "effort": settings["reasoning_effort"], "timeout": reg.model_deadline}):
        raise CaseRejected("case_registration_mismatch")
    if state_root.is_relative_to(reg.binding.model_root) or reg.binding.model_root.is_relative_to(state_root):
        raise CaseRejected("supervisor_state_must_remain_private")
    namespace.validate_tree(reg.helper_source_root, json.loads(reg.helper_source_files_json), python_only=True)
    python = namespace._path(TRUSTED_HOST_PYTHON)
    if not os.access(python, os.X_OK) or hashlib.sha256(python.read_bytes()).hexdigest() != reg.helper_python_sha256:
        raise CaseRejected("helper_python_pin_mismatch")
    # Credentials stay outside EVERY agent-mounted tree and supervisor state.
    auth = Path(reg.auth_snapshot)
    _private_file(auth)
    if (auth.stat().st_size > 65536 or auth.resolve() != auth or auth.is_relative_to(reg.binding.model_root)
            or any(auth.is_relative_to(inputs[key]) for key in ("source_root", "runtime_root", "evidence_root", "arm_root"))):
        raise CaseRejected("helper_auth_must_remain_private")
    payload = {"case_id": reg.case_id, "arm_id": reg.arm_id, "profile_id": reg.profile_id, "revision": reg.revision,
               "settings": json.loads(reg.settings_json), "deadline": reg.model_deadline, "case_deadline": reg.case_deadline,
               "namespace": json.loads(reg.namespace_json), "helper_source": json.loads(reg.helper_source_files_json),
               "helper_python": reg.helper_python_sha256, "wrapper": reg.binding.wrapper_sha256, "mode": reg.mode,
               "binding_roots": [str(reg.binding.model_root), str(reg.binding.agent_root), str(reg.binding.host_root)],
               "runtime_owner_sha256": hashlib.sha256(_json({"marker": reg.runtime.root_marker, "accounts": reg.runtime.accounts()}).encode()).hexdigest(),
               "auth_snapshot_sha256": hashlib.sha256(auth.read_bytes()).hexdigest(),
               "environment_sha256": hashlib.sha256(reg.binding.environment_json.encode()).hexdigest()}
    return inputs, hashlib.sha256(_json(payload).encode()).hexdigest()


async def _drain(stream, *, capture=False, limit=8 * 1024 * 1024):
    size, chunks = 0, []
    while True:
        chunk = await stream.read(16384)
        if not chunk:
            return b"".join(chunks) if capture else size
        size += len(chunk)
        if size > limit:
            raise CaseRejected("child_output_limit")
        if capture:
            chunks.append(chunk)


async def _stop_process(process, receipt=None):
    """Only an owned child handle. A forced helper kill is NOT a clean ACK."""
    if receipt is None:
        receipt = {}
    receipt.update(forced_kill=False, returncode=process.returncode)
    if process.returncode is not None:
        await asyncio.wait_for(process.wait(), timeout=2)
        return process.returncode == 0
    try:
        try:
            process.terminate()
        except ProcessLookupError:
            pass  # Wait for the OWNED handle; a missing PID is not a cleanup ACK.
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            receipt["forced_kill"] = True
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), timeout=2)
        return not receipt["forced_kill"] and process.returncode == 0
    finally:
        receipt["returncode"] = process.returncode


def _failure_category(error):
    if isinstance(error, asyncio.CancelledError):
        return "CANCELLED"
    if isinstance(error, JournalRejected):
        return "JOURNAL_FAILURE"
    if isinstance(error, CaseRejected) and str(error) in CATEGORIES:
        return str(error)
    return "INTERNAL_FAILURE"


def _invoker_receipt(invoker):
    categories, snapshots = [], []
    for _, task, _ in invoker.records.values():
        try:
            value = task.result() if task.done() and not task.cancelled() else {}
            category, snapshot = value.get("category"), value.get("snapshot_sha256")
            categories.append(category if category in {"ok", "model_error", "model_timeout"} else "UNKNOWN")
            if isinstance(snapshot, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot):
                snapshots.append(snapshot)
        except BaseException:
            categories.append("UNKNOWN")
    return {"terminal_categories": categories, "snapshot_hashes": snapshots,
            "poisoned": bool(invoker.poisoned), "unjoined_count": len(invoker.unjoined_callbacks)}


@asynccontextmanager
async def _joined_context(context, journal, stage, component, receipt):
    """Receipt per callback, including an early failure later exits can mask."""
    entered, clean, failure = False, False, "NONE"
    try:
        value = await context.__aenter__()
        entered = True
        try:
            yield value
        finally:
            try:
                await context.__aexit__(*sys.exc_info())
                clean = True
            except BaseException as error:
                failure = _failure_category(error)
                raise
    except BaseException as error:
        if not entered:
            failure = _failure_category(error)
        raise
    finally:
        if journal is not None:
            details = receipt()
            if details.get("poisoned") or details.get("unjoined_count", 0):
                clean, failure = False, "invoker_cleanup_unknown"
            journal.record(stage, component=component, receipt_valid=clean,
                           failure_category=failure, **details)


@contextmanager
def _joined_bridge(context, journal, component):
    clean, failure = False, "NONE"
    try:
        try:
            value = context.__enter__()
        except BaseException:
            failure = "read_bridge_cleanup_unknown"
            raise
        try:
            yield value
        finally:
            try:
                context.__exit__(*sys.exc_info())
                clean = True
            except BaseException:
                failure = "read_bridge_cleanup_unknown"
                raise
    finally:
        if journal is not None:
            journal.record("READ_BRIDGES_JOINED", component=component,
                           receipt_valid=clean, failure_category=failure)


_HELPER_BOOTSTRAP = "import sys;sys.path.insert(0,sys.argv[1]);from tools.isolated_trading_case_supervisor import _responses_child;_responses_child(sys.argv[2:])"


def _metered_app_factory(original, counter):
    """Dedicated child only: count EVERY HTTP request before any await/route."""
    from aiohttp import web
    def create(*args, **kwargs):
        app = original(*args, **kwargs)
        @web.middleware
        async def count(request, handler):
            counter["requests"] += 1
            return await handler(request)
        app.middlewares.insert(0, count)
        return app
    return create


def _responses_child(args):
    """Dedicated child only: suppress all native payload logs before imports."""
    import logging
    logging.disable(logging.CRITICAL)
    try:
        reader, parent, socket_path, auth, tools_json, duration = args
        lease = ParentLease(int(reader), int(parent))
        from tools import isolated_responses_proxy as proxy
        counter = {"requests": 0}
        proxy.create_dedicated_app = _metered_app_factory(proxy.create_dedicated_app, counter)
        async def run():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_reader(lease.fd, stop.set)
            import signal
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            lease.check()
            try:
                await proxy.serve(Path(socket_path), Path(auth), allowed_tools=set(json.loads(tools_json)),
                                  run_seconds=float(duration), max_requests=16, stop_event=stop)
            finally:
                loop.remove_reader(lease.fd)
                os.close(lease.fd)
        asyncio.run(run())
        print(_json({"schema_version": 1, "requests": counter["requests"]}), flush=True)
    except BaseException:
        raise SystemExit(2) from None


@asynccontextmanager
async def _responses_helper(reg, socket_path, read_tools, *, cleanup_receipt=None):
    reader, writer = os.pipe()
    process = None
    drains = []
    clean = False
    evidence = {"cleanup_confirmed": False, "requests": None}
    if cleanup_receipt is None:
        cleanup_receipt = {}
    try:
        # Fixed helper and interpreter only; no agent-configurable command/env.
        process = await asyncio.create_subprocess_exec(
            TRUSTED_HOST_PYTHON, "-I", "-B", "-c", _HELPER_BOOTSTRAP, str(reg.helper_source_root),
            str(reader), str(os.getpid()), str(socket_path), str(reg.auth_snapshot), _json(sorted(read_tools)), str(reg.case_deadline),
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}, pass_fds=(reader,),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, close_fds=True)
        os.close(reader)
        reader = None
        drains = [asyncio.create_task(_drain(process.stdout, capture=True, limit=4096)), asyncio.create_task(_drain(process.stderr))]
        end = time.monotonic() + 10
        while not socket_path.exists():
            if process.returncode is not None or time.monotonic() >= end or any(task.done() for task in drains):
                raise CaseRejected("responses_helper_not_ready")
            await asyncio.sleep(.02)
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise CaseRejected("responses_helper_socket_invalid")
        yield process, drains, evidence
    finally:
        os.close(writer)  # EOF requests the same bounded child-only shutdown.
        if reader is not None:
            os.close(reader)
        if process is not None:
            clean = await _stop_process(process, cleanup_receipt)
        try:
            results = await asyncio.wait_for(asyncio.gather(*drains, return_exceptions=True), timeout=1)
        except asyncio.TimeoutError:
            raise CaseRejected("responses_cleanup_unknown") from None
        if process is not None:
            if results and isinstance(results[0], bytes):
                cleanup_receipt["stdout_bytes"] = len(results[0])
            if len(results) > 1 and type(results[1]) is int:
                cleanup_receipt["stderr_bytes"] = results[1]
            if not clean or any(isinstance(result, BaseException) for result in results):
                raise CaseRejected("responses_cleanup_unknown")
            try:
                receipt = json.loads(results[0])
                if (type(receipt) is not dict or set(receipt) != {"schema_version", "requests"} or receipt["schema_version"] != 1
                        or type(receipt["requests"]) is not int or not 0 <= receipt["requests"] <= 1000000000):
                    raise ValueError()
            except (ValueError, TypeError, IndexError):
                raise CaseRejected("responses_receipt_unknown") from None
            evidence.update(cleanup_confirmed=True, requests=receipt["requests"])
            cleanup_receipt["helper_requests"] = receipt["requests"]


@asynccontextmanager
async def _services(reg, connection, private_root, journal=None):
    env = json.loads(reg.binding.environment_json)
    module, manifest = load_host_manifest(Path(env["PRISM_PROBE_HOST_MANIFEST"]), reg.binding.model_root)
    market = json.loads(reg.namespace_json)["controls"]["market"]
    market_server = "kospi_kosdaq" if market == "KR" else "yahoo_finance"
    configs = module.host_provider_configs(manifest["providers"], ("perplexity", market_server), env,
                                          kr_profile=manifest["kr_profile"], host_runtime_root=Path(env["PRISM_PROBE_HOST_MANIFEST"]).parent)
    scope = InvocationScope(reg.arm_id, reg.profile_id, reg.case_id, reg.revision, reg.settings_json, reg.model_deadline, connection)
    binding = FixedCodexBinding(replace(reg.binding, host_root=private_root))
    invoker = CodexInvoker([scope], binding, private_root, window_seconds=reg.case_deadline)
    sockets = {"codex-invoke": private_root / "invoke.sock", "responses": private_root / "responses.sock",
               "perplexity": private_root / "perplexity.sock", "market": private_root / "market.sock"}
    tools = {"time-get_current_time", "sqlite-list_tables", "sqlite-describe_table", "sqlite-read_query"}
    for server in ("perplexity", market_server):
        tools.update(f"{server}-{name}" for name in module.TOOLS[server])
    async with AsyncExitStack() as stack:
        for name, server in (("perplexity", "perplexity"), ("market", market_server)):
            stack.enter_context(_joined_bridge(module.ReadMcpBridge(sockets[name], server_name=server, **configs[server]), journal, name))
        helper_receipt = {}
        helper_context = _responses_helper(reg, sockets["responses"], tools, cleanup_receipt=helper_receipt)
        helper, drains, evidence = await stack.enter_async_context(
            _joined_context(helper_context, journal, "RESPONSES_JOINED", "responses", lambda: helper_receipt))
        await stack.enter_async_context(_joined_context(invoker.serve(sockets["codex-invoke"]), journal,
                                                       "INVOKER_JOINED", "invoker", lambda: _invoker_receipt(invoker)))
        yield sockets, invoker, helper, drains, evidence
    if invoker.poisoned or invoker.unjoined_callbacks:
        raise CaseRejected("invoker_cleanup_unknown")


async def _agent(command, deadline, helper, helper_drains, journal=None):
    if not isinstance(command, list) or not command or command[0] != "/usr/bin/bwrap":
        raise CaseRejected("fixed_namespace_executable_required")
    process = None
    tasks = []
    completion = None
    receipt = {"forced_kill": False, "returncode": None}
    started = time.monotonic()
    try:
        if journal is not None:
            journal.record("AGENT_STARTED", component="agent", receipt_valid=False)
        # Remaining argv is the reviewed fixed namespace builder, never shell text.
        process = await asyncio.create_subprocess_exec("/usr/bin/bwrap", *command[1:], env={}, close_fds=True,  # nosemgrep
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if journal is not None:
            journal.record("AGENT_STARTED", component="agent", receipt_valid=True)
        stdout = asyncio.create_task(_drain(process.stdout, capture=True, limit=65536))
        stderr = asyncio.create_task(_drain(process.stderr))
        tasks = [stdout, stderr, asyncio.create_task(process.wait())]
        completion = asyncio.gather(*tasks)
        helper_exit = asyncio.create_task(helper.wait())
        try:
            done, _ = await asyncio.wait({completion, helper_exit, *helper_drains}, timeout=deadline, return_when=asyncio.FIRST_COMPLETED)
            if completion not in done:
                raise CaseRejected("case_deadline_or_helper_failure")
            output, stderr_bytes, returncode = completion.result()
            receipt.update(stdout_bytes=len(output), stderr_bytes=stderr_bytes, returncode=returncode)
            return returncode, output
        finally:
            helper_exit.cancel()
            await asyncio.gather(helper_exit, return_exceptions=True)
    except BaseException as error:
        if journal is not None:
            journal.record("AGENT_STARTED", component="agent", receipt_valid=False,
                           failure_category=_failure_category(error),
                           elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
        raise
    finally:
        clean = False
        try:
            if process is not None:
                await _stop_process(process, receipt)
                clean = process.returncode is not None
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if completion is not None:
                await asyncio.gather(completion, return_exceptions=True)
        finally:
            if journal is not None:
                journal.record("AGENT_REAPED", component="agent", receipt_valid=clean,
                               failure_category="NONE" if clean else "agent_cleanup_unknown",
                               elapsed_ms=max(0, int((time.monotonic() - started) * 1000)), **receipt)


def _verify_activity(attempts, invoker, helper_evidence):
    """Cross-check child claims against joined, parent-owned transport records."""
    if invoker.poisoned or invoker.unjoined_callbacks:
        raise CaseRejected("case_activity_uncertain")
    observed = []
    try:
        for _, task, _ in invoker.records.values():
            if not task.done() or task.cancelled():
                raise ValueError()
            receipt = task.result()
            category = receipt["category"]
            snapshot = receipt["snapshot_sha256"]
            if (category not in {"ok", "model_error", "model_timeout"}
                    or receipt.get("cleanup_ack") is not True
                    or receipt.get("fallback_allowed") is not (category != "ok")
                    or not isinstance(snapshot, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot) is None):
                raise ValueError()
            observed.append((category, snapshot))
        claimed = [(attempt.get("status"), attempt.get("snapshot_sha256"))
                   for attempt in attempts if attempt.get("path") == "CODEX_RPC"]
        legacy = sum(attempt.get("path") == "LEGACY_RESPONSES" for attempt in attempts)
        requests = helper_evidence["requests"]
        if (Counter(claimed) != Counter(observed) or (legacy > 0) != (requests > 0)
                or legacy > requests):
            raise ValueError()
    except (ValueError, TypeError, KeyError, RuntimeError, asyncio.CancelledError):
        raise CaseRejected("case_activity_uncertain") from None


def _outcome(reg, returncode, stdout, invoker, helper_evidence):
    """Small transport contract only; never reproduce source strategy gates."""
    path = Path(reg.runtime.root) / "case-result.json"
    no_models = (not invoker.records and not invoker.poisoned and not invoker.unjoined_callbacks
                 and helper_evidence.get("cleanup_confirmed") is True and type(helper_evidence.get("requests")) is int
                 and helper_evidence["requests"] == 0)
    if reg.mode == "VALIDATE_ONLY":
        if returncode != 0 or path.exists() or not no_models:
            raise CaseRejected("validation_outcome_uncertain")
        pending = json.loads(stdout)
        if not isinstance(pending, dict) or pending.get("status") != "PENDING_PARENT_NAMESPACE":
            raise CaseRejected("validation_outcome_uncertain")
        # Default harness validation gate is NOT a successfully executed case.
        return {"status": "NOT_EXECUTED", "category": "PENDING_PARENT_NAMESPACE", "production_parity": False}
    if returncode not in {0, 2} or not path.is_file():
        raise CaseRejected("case_outcome_uncertain")
    _private_file(path)
    if path.stat().st_size > 256 * 1024:
        raise CaseRejected("case_result_limit")
    result = json.loads(path.read_text())
    expected_fields = {"schema_version", "case_id", "arm_id", "profile_id", "status", "eligibility_evaluated", "prospective",
                       "deviations", "source_hashes", "attempts", "selected_decision", "latency_s"}
    if (not isinstance(result, dict) or type(result.get("schema_version")) is not int or result["schema_version"] != 1
            or set(result) != expected_fields
            or any(result.get(key) != getattr(reg, key) for key in ("case_id", "arm_id", "profile_id"))
            or result.get("eligibility_evaluated") is not False or result.get("prospective") is not False
            or result.get("status") not in {"ANALYSIS_COMPLETE", "SOURCE_RULE_FALLBACK", "SOURCE_NON_MODEL_DECISION", "SOURCE_QUOTE_UNAVAILABLE",
                                            "CODEX_INVOKER_PENDING", "ANALYSIS_NOT_COMPLETED"}
            or not isinstance(result.get("attempts"), list)
            or not isinstance(result.get("deviations"), list) or not isinstance(result.get("source_hashes"), dict)
            or (returncode == 0) != (result["status"] in {"ANALYSIS_COMPLETE", "SOURCE_RULE_FALLBACK", "SOURCE_NON_MODEL_DECISION"})):
        raise CaseRejected("case_result_invalid")
    if (len(result["attempts"]) > 32 or len(result["deviations"]) > 64
            or any(not isinstance(value, str) or len(value) > 256 for value in result["deviations"])
            or any(not isinstance(key, str) or len(key) > 512 or not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                   for key, value in result["source_hashes"].items())
            or type(result["latency_s"]) not in {int, float} or not math.isfinite(result["latency_s"]) or result["latency_s"] < 0
            or result["selected_decision"] is not None and not isinstance(result["selected_decision"], str)):
        raise CaseRejected("case_result_invalid")
    # Preserve full artifact privately; emit only fixed categories/hash, not
    # model decisions, prompts, broker/account metadata or arbitrary errors.
    if helper_evidence.get("cleanup_confirmed") is not True or type(helper_evidence.get("requests")) is not int:
        raise CaseRejected("case_activity_uncertain")
    terminal = {"CODEX_RPC": {"ok", "model_error", "model_timeout"}, "LEGACY_RESPONSES": {"ok"}}
    for attempt in result["attempts"]:
        if (not isinstance(attempt, dict) or attempt.get("path") not in {*terminal, "SOURCE_RULE_FALLBACK"}
                or attempt.get("path") in terminal and attempt.get("status") not in terminal[attempt["path"]]):
            raise CaseRejected("case_activity_uncertain")
    _verify_activity(result["attempts"], invoker, helper_evidence)
    if result["status"] == "ANALYSIS_COMPLETE" and not any(
            attempt.get("path") in terminal and attempt.get("status") == "ok" for attempt in result["attempts"]):
        raise CaseRejected("case_activity_uncertain")
    if result["status"] == "SOURCE_RULE_FALLBACK":
        if not any(attempt.get("path") == "SOURCE_RULE_FALLBACK" for attempt in result["attempts"]):
            raise CaseRejected("case_result_invalid")
        outcome = {"status": "SOURCE_RULE_FALLBACK", "category": "SOURCE_RULE_FALLBACK_NOT_MODEL_SUCCESS", "production_parity": False}
    elif result["status"] != "ANALYSIS_COMPLETE":
        if result["attempts"] or not no_models:
            raise CaseRejected("case_activity_uncertain")
        outcome = {"status": "NOT_EXECUTED", "category": result["status"], "production_parity": False}
    else:
        outcome = {"status": "ANALYSIS_COMPLETE", "category": "ISOLATED_CASE_DIAGNOSTIC", "production_parity": False}
    decision = result.get("selected_decision")
    if result["status"] in {"SOURCE_NON_MODEL_DECISION", "SOURCE_RULE_FALLBACK"} and decision in {"BUY", "SELL", "HOLD", "Enter", "No Entry", "Entry", "No entry", "entry", "skip", "Skip", "OTHER_SOURCE_DECISION"}:
        outcome["selected_decision"] = decision
    return {**outcome, "eligibility_evaluated": False, "prospective": False,
            "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


async def run_case(registration, state_root):
    """Call only under reviewed staging registration; never schedule or retry."""
    reg = registration
    state_root = _private_root(state_root)
    try:
        inputs, payload_hash = _registration(reg, state_root)
    except CaseRejected:
        raise
    except Exception:
        raise CaseRejected("case_registration_invalid") from None
    with CaseLane(state_root) as lane:
        previous = lane.claim(reg.case_id, payload_hash)
        if previous is not None:
            return previous
        connection = None
        journal = None
        deadline = time.monotonic() + reg.case_deadline
        try:
            journal = CaseJournal(state_root, reg.case_id, payload_hash)
            journal.record("CLAIMED")
            writer = reg.runtime.connect()  # verifies/initializes marked owner
            writer.close()
            connection = sqlite3.connect(Path(reg.runtime.db_path).as_uri() + "?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
            private = state_root / hashlib.sha256(reg.case_id.encode()).hexdigest()[:24]
            private.mkdir(mode=0o700)
            if (Path(reg.runtime.root) / "case-result.json").exists():
                raise CaseRejected("existing_case_artifact")
            journal.record("SERVICES_READY", receipt_valid=False)
            async with _services(reg, connection, private, journal) as (sockets, invoker, helper, drains, helper_evidence):
                journal.record("SERVICES_READY", receipt_valid=True)
                command = namespace.command(**inputs, sockets=sockets)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaseRejected("case_deadline")
                returncode, stdout = await _agent(command, remaining, helper, drains, journal)
            if invoker.poisoned or invoker.unjoined_callbacks:
                raise CaseRejected("invoker_cleanup_unknown")
            journal.record("OUTCOME_VALIDATED", receipt_valid=False)
            result = _outcome(reg, returncode, stdout, invoker, helper_evidence)
            journal.record("OUTCOME_VALIDATED", receipt_valid=True)
            # Finalization receipt precedes the authoritative DB update. Never
            # use a journal receipt to override CLAIMED/UNKNOWN database state.
            journal.record("FINALIZATION_READY", receipt_valid=True)
            lane.finish(reg.case_id, payload_hash, result, cleanup_confirmed=True)
            return result
        except BaseException as error:
            if journal is not None:
                try:
                    journal.record(journal.stage, component="case", receipt_valid=False,
                                   failure_category=_failure_category(error))
                except BaseException:
                    pass  # The durable claim below remains UNKNOWN regardless.
            lane.finish(reg.case_id, payload_hash, {"status": "UNKNOWN", "category": "CASE_OR_CLEANUP_UNCERTAIN"}, cleanup_confirmed=False)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise CaseRejected("case_or_cleanup_uncertain") from None
        finally:
            if connection is not None:
                connection.close()
