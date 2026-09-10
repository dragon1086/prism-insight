"""No model/broker/Telegram calls; durable claims and harmless composition."""
import tempfile
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import isolated_trading_case_supervisor as supervisor


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="case-", dir="/tmp") as directory:
        yield Path(directory).resolve()


def test_claim_crash_blocks_entire_lane_including_new_case_ids(root):
    with supervisor.CaseLane(root) as lane:
        assert lane.claim("case1", "a" * 64) is None
    with supervisor.CaseLane(root) as restarted:
        for case in ("case1", "case2"):
            with pytest.raises(supervisor.CaseRejected, match="lane_uncertain"):
                restarted.claim(case, "a" * 64)


def test_completed_claim_is_idempotent_but_conflicting_payload_rejected(root):
    with supervisor.CaseLane(root) as lane:
        lane.claim("case1", "a" * 64)
        summary = {"status": "NOT_EXECUTED", "category": "PENDING_PARENT_NAMESPACE"}
        lane.finish("case1", "a" * 64, summary, cleanup_confirmed=True)
    with supervisor.CaseLane(root) as restarted:
        assert restarted.claim("case1", "a" * 64) == summary
        with pytest.raises(supervisor.CaseRejected, match="case_conflict"):
            restarted.claim("case1", "b" * 64)


def test_unconfirmed_cleanup_poison_is_durable(root):
    with supervisor.CaseLane(root) as lane:
        lane.claim("case1", "a" * 64)
        lane.finish("case1", "a" * 64, {"status": "UNKNOWN"}, cleanup_confirmed=False)
    with supervisor.CaseLane(root) as restarted:
        with pytest.raises(supervisor.CaseRejected, match="lane_uncertain"):
            restarted.claim("case2", "b" * 64)


def test_whole_case_lock_blocks_other_supervisors(root):
    with supervisor.CaseLane(root):
        with pytest.raises(supervisor.CaseRejected, match="lane_busy"):
            with supervisor.CaseLane(root):
                pytest.fail("concurrent lane acquired")


def test_existing_unknown_database_is_never_adopted(root):
    (root / "case-claims.sqlite").write_bytes(b"not-a-ledger")
    before = (root / "case-claims.sqlite").read_bytes()
    with pytest.raises(supervisor.CaseRejected):
        with supervisor.CaseLane(root):
            pass
    assert (root / "case-claims.sqlite").read_bytes() == before


def composition(root, monkeypatch, *, script=None, poison=False, cleanup_error=False):
    state, arm = root / "state", root / "arm"
    state.mkdir(mode=0o700)
    arm.mkdir(mode=0o700)
    events = []
    class Runtime:
        db_path = str(arm / "agent.sqlite")
        root = arm
        def connect(self):
            # A separate observer proves CLAIMED was committed before setup.
            with sqlite3.connect(state / "case-claims.sqlite") as audit:
                assert audit.execute("SELECT state FROM cases").fetchone() == ("CLAIMED",)
            events.append("setup_writer")
            connection = sqlite3.connect(self.db_path)
            connection.execute("CREATE TABLE owner(id)")
            connection.commit()
            return connection
    reg = SimpleNamespace(case_id="case1", runtime=Runtime(), case_deadline=2, mode="VALIDATE_ONLY")
    monkeypatch.setattr(supervisor, "_registration", lambda *_: ({}, "a" * 64))
    class Helper:
        async def wait(self):
            await asyncio.sleep(30)
    @asynccontextmanager
    async def services(registration, connection, private, journal=None):
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        events.append("services_started")
        try:
            yield {}, SimpleNamespace(poisoned=poison, unjoined_callbacks=set(), records={}), Helper(), [], {"cleanup_confirmed": True, "requests": 0}
        finally:
            events.append("services_joined")
            if cleanup_error:
                raise RuntimeError("PRIVATE_CLEANUP_CANARY")
    monkeypatch.setattr(supervisor, "_services", services)
    command = script or "import os,json;assert 'PRIVATE_SUPERVISOR_CANARY' not in os.environ;print(json.dumps({'status':'PENDING_PARENT_NAMESPACE'}))"
    monkeypatch.setattr(supervisor.namespace, "command", lambda **kwargs: ["/usr/bin/bwrap", "-c", command])
    actual_spawn = supervisor.asyncio.create_subprocess_exec
    async def harmless_namespace_double(executable, *args, **kwargs):
        assert executable == "/usr/bin/bwrap"
        return await actual_spawn(sys.executable, *args, **kwargs)
    monkeypatch.setattr(supervisor.asyncio, "create_subprocess_exec", harmless_namespace_double)
    return reg, state, events


def test_supervisor_composes_and_joins_before_cacheable_nonexecution(root, monkeypatch):
    monkeypatch.setenv("PRIVATE_SUPERVISOR_CANARY", "SECRET")
    reg, state, events = composition(root, monkeypatch)
    result = asyncio.run(supervisor.run_case(reg, state))
    assert result["status"] == "NOT_EXECUTED" and result["production_parity"] is False
    assert events == ["setup_writer", "services_started", "services_joined"]
    assert asyncio.run(supervisor.run_case(reg, state)) == result
    assert events == ["setup_writer", "services_started", "services_joined"]


@pytest.mark.parametrize("failure", ["poison", "cleanup"])
def test_composition_cleanup_uncertainty_blocks_new_case_on_restart(root, monkeypatch, failure):
    reg, state, events = composition(root, monkeypatch, poison=failure == "poison", cleanup_error=failure == "cleanup")
    with pytest.raises(supervisor.CaseRejected, match="case_or_cleanup_uncertain"):
        asyncio.run(supervisor.run_case(reg, state))
    assert "services_joined" in events
    reg.case_id = "differentcase"
    with pytest.raises(supervisor.CaseRejected, match="lane_uncertain"):
        asyncio.run(supervisor.run_case(reg, state))


def test_case_timeout_reaps_harmless_agent_and_poisons_lane(root, monkeypatch):
    reg, state, events = composition(root, monkeypatch, script="import time;time.sleep(30)")
    reg.case_deadline = .03
    processes = []
    real_spawn = supervisor.asyncio.create_subprocess_exec
    async def record(*args, **kwargs):
        assert kwargs["env"] == {}
        child = await real_spawn(*args, **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(supervisor.asyncio, "create_subprocess_exec", record)
    with pytest.raises(supervisor.CaseRejected):
        asyncio.run(supervisor.run_case(reg, state))
    assert len(processes) == 1 and processes[0].returncode is not None
    stages = [json.loads(line) for line in next(state.glob("*.stages.jsonl")).read_text().splitlines()]
    origin = next(row for row in stages if row.get("failure_category") == "case_deadline_or_helper_failure")
    assert origin["stage"] == "AGENT_STARTED" and origin["receipt_valid"] is False
    assert origin["elapsed_ms"] >= 0
    with supervisor.CaseLane(state) as lane:
        with pytest.raises(supervisor.CaseRejected, match="lane_uncertain"):
            lane.claim("newcase", "a" * 64)


def test_cross_process_lease_spans_whole_case(root):
    code = """
import sys
from pathlib import Path
from tools.isolated_trading_case_supervisor import CaseLane,CaseRejected
try:
 with CaseLane(Path(sys.argv[1])):raise SystemExit(1)
except CaseRejected as error:
 raise SystemExit(0 if str(error)=='lane_busy' else 2)
"""
    with supervisor.CaseLane(root):
        result = subprocess.run([sys.executable, "-c", code, str(root)], timeout=3, capture_output=True)
    assert result.returncode == 0


def test_real_supervisor_crash_leaves_durable_lane_barrier(root):
    code = """
import os,sys
from pathlib import Path
from tools.isolated_trading_case_supervisor import CaseLane
with CaseLane(Path(sys.argv[1])) as lane:
 lane.claim('crashedcase','a'*64)
 os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root)], timeout=3, capture_output=True)
    assert result.returncode == 9
    with supervisor.CaseLane(root) as lane:
        with pytest.raises(supervisor.CaseRejected, match="lane_uncertain"):
            lane.claim("unrelatednewcase", "b" * 64)


@pytest.mark.parametrize("failure", ["denied_route", "invalid_body", "cancelled"])
def test_helper_counts_every_http_request_before_route_body_or_await(failure):
    async def run():
        counter = {"requests": 0}
        app = supervisor._metered_app_factory(lambda: SimpleNamespace(middlewares=[]), counter)()
        async def handler(request):
            assert counter["requests"] == 1
            if failure == "cancelled":
                raise asyncio.CancelledError()
            if failure == "invalid_body":
                raise ValueError("PRIVATE_BODY_CANARY")
            return "denied"
        if failure == "denied_route":
            assert await app.middlewares[0](object(), handler) == "denied"
        else:
            with pytest.raises(asyncio.CancelledError if failure == "cancelled" else ValueError):
                await app.middlewares[0](object(), handler)
        assert counter["requests"] == 1
    asyncio.run(run())


def outcome_fixture(root, status="ANALYSIS_NOT_COMPLETED", attempts=None):
    reg = SimpleNamespace(runtime=SimpleNamespace(root=root), mode="EXECUTE_REGISTERED", case_id="case1", arm_id="arm1", profile_id="profile1")
    artifact = {"schema_version": 1, "case_id": "case1", "arm_id": "arm1", "profile_id": "profile1", "status": status,
                "eligibility_evaluated": False, "prospective": False, "attempts": attempts or [], "selected_decision": "HOLD",
                "deviations": [], "source_hashes": {}, "latency_s": 0.0}
    path = root / "case-result.json"
    path.write_text(json.dumps(artifact))
    path.chmod(0o600)
    invoker = SimpleNamespace(records={}, poisoned=False, unjoined_callbacks=set())
    return reg, invoker


def test_known_pre_model_failure_is_terminal_nonexecution_after_zero_activity(root):
    reg, invoker = outcome_fixture(root)
    result = supervisor._outcome(reg, 2, b"", invoker, {"cleanup_confirmed": True, "requests": 0})
    assert result["status"] == "NOT_EXECUTED" and result["category"] == "ANALYSIS_NOT_COMPLETED"
    assert result["production_parity"] is False


@pytest.mark.parametrize("evidence", [{}, {"cleanup_confirmed": False, "requests": 0}, {"cleanup_confirmed": True, "requests": 1}])
def test_missing_or_nonzero_helper_evidence_cannot_downgrade_unknown(root, evidence):
    reg, invoker = outcome_fixture(root)
    with pytest.raises(supervisor.CaseRejected, match="case_activity_uncertain"):
        supervisor._outcome(reg, 2, b"", invoker, evidence)


def test_non_model_decision_preserved_without_claiming_model_execution(root):
    reg, invoker = outcome_fixture(root, status="SOURCE_NON_MODEL_DECISION")
    result = supervisor._outcome(reg, 0, b"", invoker, {"cleanup_confirmed": True, "requests": 0})
    assert result["status"] == "NOT_EXECUTED" and result["category"] == "SOURCE_NON_MODEL_DECISION"
    assert result["selected_decision"] == "HOLD"


_FAKE_HELPER = r'''
import json,os,select,signal,socket,sys
assert sys.dont_write_bytecode is True
stopped=False
def stop(*args):
 global stopped
 stopped=True
signal.signal(signal.SIGTERM,stop)
sys.stderr.buffer.write(b'x'*300000);sys.stderr.flush()
listener=socket.socket(socket.AF_UNIX);listener.bind(sys.argv[4]);os.chmod(sys.argv[4],0o600);listener.listen()
while not stopped and not select.select([int(sys.argv[2])],[],[],.02)[0]:pass
listener.close();os.unlink(sys.argv[4])
print(json.dumps({'schema_version':1,'requests':0}),flush=True)
'''


def test_dedicated_helper_joins_then_publishes_only_numeric_cleanup_receipt(root, monkeypatch):
    monkeypatch.setattr(supervisor, "TRUSTED_HOST_PYTHON", sys.executable)
    monkeypatch.setattr(supervisor, "_HELPER_BOOTSTRAP", _FAKE_HELPER)
    reg = SimpleNamespace(helper_source_root=root, auth_snapshot=root / "unused-auth", case_deadline=5)
    async def run():
        async with supervisor._responses_helper(reg, root / "response.sock", {"time-get_current_time"}) as (process, drains, evidence):
            assert evidence == {"cleanup_confirmed": False, "requests": None}
            assert process.returncode is None
        assert process.returncode == 0
        assert evidence == {"cleanup_confirmed": True, "requests": 0}
        assert all(task.done() for task in drains)
    asyncio.run(run())


def test_helper_kill_without_receipt_is_unknown_not_zero_requests(root, monkeypatch):
    monkeypatch.setattr(supervisor, "TRUSTED_HOST_PYTHON", sys.executable)
    monkeypatch.setattr(supervisor, "_HELPER_BOOTSTRAP", _FAKE_HELPER)
    reg = SimpleNamespace(helper_source_root=root, auth_snapshot=root / "unused-auth", case_deadline=5)
    async def run():
        with pytest.raises(supervisor.CaseRejected, match="responses_cleanup_unknown"):
            async with supervisor._responses_helper(reg, root / "response.sock", {"time-get_current_time"}) as (process, _, evidence):
                process.kill()
                await process.wait()
        assert evidence["cleanup_confirmed"] is False and evidence["requests"] is None
    asyncio.run(run())


@pytest.mark.parametrize("status", ["ok", "UNKNOWN"])
def test_rule_fallback_is_distinct_and_only_terminal_attempts_are_cacheable(root, status):
    reg, invoker = outcome_fixture(root, status="SOURCE_RULE_FALLBACK", attempts=[
        {"path": "CODEX_RPC", "status": "model_error", "snapshot_sha256": "b" * 64}, {"path": "LEGACY_RESPONSES", "status": status},
        {"path": "SOURCE_RULE_FALLBACK"},
    ])
    invoker.records = completed_record("model_error")
    evidence = {"cleanup_confirmed": True, "requests": 1}
    if status == "UNKNOWN":
        with pytest.raises(supervisor.CaseRejected, match="case_activity_uncertain"):
            supervisor._outcome(reg, 0, b"", invoker, evidence)
    else:
        result = supervisor._outcome(reg, 0, b"", invoker, evidence)
        assert result["status"] == "SOURCE_RULE_FALLBACK" and result["selected_decision"] == "HOLD"
        assert result["category"] == "SOURCE_RULE_FALLBACK_NOT_MODEL_SUCCESS"
        assert result["eligibility_evaluated"] is False


def completed_record(category):
    receipt = {"category": category, "cleanup_ack": True, "snapshot_sha256": "b" * 64,
               "fallback_allowed": category != "ok"}
    task = SimpleNamespace(done=lambda: True, cancelled=lambda: False, result=lambda: receipt)
    return {"request1": ("digest", task, None)}


def test_empty_attempts_cannot_claim_analysis_complete(root):
    reg, invoker = outcome_fixture(root, "ANALYSIS_COMPLETE")
    with pytest.raises(supervisor.CaseRejected, match="case_activity_uncertain"):
        supervisor._outcome(reg, 0, b"", invoker, {"cleanup_confirmed": True, "requests": 0})


def test_agent_refuses_non_namespace_executable():
    with pytest.raises(supervisor.CaseRejected, match="fixed_namespace_executable_required"):
        asyncio.run(supervisor._agent([sys.executable, "-c", "raise AssertionError()"], 1, None, []))


@pytest.mark.parametrize("code", [0, 2, -9])
def test_process_lookup_race_awaits_owned_handle_not_blind_ack(code):
    class Process:
        returncode = None
        waited = False
        def terminate(self):
            raise ProcessLookupError("SECRET_PROCESS_CANARY")
        async def wait(self):
            self.waited = True
            self.returncode = code
            return code
    process, receipt = Process(), {}
    assert asyncio.run(supervisor._stop_process(process, receipt)) is (code == 0)
    assert process.waited and receipt["returncode"] == code


def test_early_cleanup_failure_survives_later_masking_exception(root):
    from contextlib import AsyncExitStack
    journal = supervisor.CaseJournal(root, "case1", "a" * 64)
    @asynccontextmanager
    async def resource(category):
        try:
            yield None
        finally:
            raise supervisor.CaseRejected(category)
    async def run():
        with pytest.raises(supervisor.CaseRejected, match="responses_cleanup_unknown"):
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(supervisor._joined_context(
                    resource("responses_cleanup_unknown"), journal, "RESPONSES_JOINED", "responses", lambda: {}))
                await stack.enter_async_context(supervisor._joined_context(
                    resource("invoker_cleanup_unknown"), journal, "INVOKER_JOINED", "invoker", lambda: {}))
    asyncio.run(run())
    rows = [json.loads(row) for row in journal.path.read_text().splitlines()]
    assert [row["failure_category"] for row in rows] == ["invoker_cleanup_unknown", "responses_cleanup_unknown"]
    assert all(row["receipt_valid"] is False for row in rows)


def test_journal_failure_leaves_claim_unknown_and_does_not_start_agent(root, monkeypatch):
    reg, state, events = composition(root, monkeypatch)
    def fail(*args, **kwargs):
        raise supervisor.JournalRejected("SECRET_JOURNAL_CANARY")
    monkeypatch.setattr(supervisor.CaseJournal, "record", fail)
    with pytest.raises(supervisor.CaseRejected, match="case_or_cleanup_uncertain"):
        asyncio.run(supervisor.run_case(reg, state))
    assert events == []
    with sqlite3.connect(state / "case-claims.sqlite") as db:
        assert db.execute("SELECT state FROM cases").fetchone() == ("UNKNOWN",)


def test_success_stage_journal_has_safe_counts_not_output(root, monkeypatch):
    reg, state, _ = composition(root, monkeypatch)
    asyncio.run(supervisor.run_case(reg, state))
    path = next(state.glob("*.stages.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1]["stage"] == "FINALIZATION_READY"
    reap = next(row for row in rows if row["stage"] == "AGENT_REAPED")
    assert reap["returncode"] == 0 and reap["stdout_bytes"] > 0
    assert "PENDING_PARENT_NAMESPACE" not in path.read_text()


def test_forced_kill_lookup_race_never_clean_even_zero_final_code():
    class Process:
        returncode = None
        calls = 0
        def terminate(self):
            pass
        def kill(self):
            raise ProcessLookupError()
        async def wait(self):
            self.calls += 1
            if self.calls == 1:
                raise asyncio.TimeoutError()
            self.returncode = 0
            return 0
    process, receipt = Process(), {}
    assert asyncio.run(supervisor._stop_process(process, receipt)) is False
    assert process.calls == 2 and receipt == {"returncode": 0, "forced_kill": True}


def test_helper_rc_zero_without_valid_receipt_is_journaled_unknown(root, monkeypatch):
    monkeypatch.setattr(supervisor, "TRUSTED_HOST_PYTHON", sys.executable)
    monkeypatch.setattr(supervisor, "_HELPER_BOOTSTRAP", _FAKE_HELPER.replace("'requests':0", "'requests':'SECRET_CANARY'"))
    reg = SimpleNamespace(helper_source_root=root, auth_snapshot=root / "unused-auth", case_deadline=5)
    journal = supervisor.CaseJournal(root, "case1", "a" * 64)
    receipt = {}
    async def run():
        with pytest.raises(supervisor.CaseRejected, match="responses_receipt_unknown"):
            helper = supervisor._responses_helper(reg, root / "response.sock", set(), cleanup_receipt=receipt)
            async with supervisor._joined_context(helper, journal, "RESPONSES_JOINED", "responses", lambda: receipt):
                pass
    asyncio.run(run())
    row = json.loads(journal.path.read_text())
    assert row["returncode"] == 0 and row["receipt_valid"] is False
    assert row["failure_category"] == "responses_receipt_unknown"
    assert row["stdout_bytes"] > 0 and "SECRET_CANARY" not in journal.path.read_text()


@pytest.mark.parametrize("mismatch", ["absent_record", "wrong_status", "wrong_snapshot", "hidden_http"])
def test_analysis_attempts_must_match_host_activity(root, mismatch):
    reg, invoker = outcome_fixture(root, "ANALYSIS_COMPLETE", attempts=[
        {"path": "CODEX_RPC", "status": "ok", "snapshot_sha256": "b" * 64}])
    invoker.records = completed_record("model_error" if mismatch == "wrong_status" else "ok")
    if mismatch == "absent_record":
        invoker.records = {}
    elif mismatch == "wrong_snapshot":
        invoker.records["request1"][1].result = lambda: {"category": "ok", "cleanup_ack": True,
            "snapshot_sha256": "c" * 64, "fallback_allowed": False}
    with pytest.raises(supervisor.CaseRejected, match="case_activity_uncertain"):
        supervisor._outcome(reg, 0, b"", invoker, {"cleanup_confirmed": True, "requests": int(mismatch == "hidden_http")})


def test_verified_primary_activity_can_claim_analysis_complete(root):
    reg, invoker = outcome_fixture(root, "ANALYSIS_COMPLETE", attempts=[
        {"path": "CODEX_RPC", "status": "ok", "snapshot_sha256": "b" * 64}])
    invoker.records = completed_record("ok")
    result = supervisor._outcome(reg, 0, b"", invoker, {"cleanup_confirmed": True, "requests": 0})
    assert result["status"] == "ANALYSIS_COMPLETE"
