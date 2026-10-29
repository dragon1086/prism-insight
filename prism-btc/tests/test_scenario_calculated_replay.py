from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_replay import run_replay, network_boundary, contract_for
from analysis.scenario_calculated_replay import EconomicClock
from backtest.scenario_tape import TapeMismatch
from tests.test_scenario_replay import inputs, wait_policy
from tests.test_scenario_review_runtime import wire as runtime_wire
from live.scenario_model_input import restore_model_input
from live.scenario_review_storage import review_text


def wire(ctx, action):
    result = runtime_wire(ctx, action)
    if action == "OPEN":
        result["hard_stop"] = 98
        result["entries"][0]["quantity"] = .1
    for entry in result["entries"]:
        entry["trigger_price"] = None
    return result


def test_runtime_event_hash_literal_queries_preserve_all_columns_and_rowid_order():
    from analysis.scenario_replay import _runtime_event_hash
    from backtest.scenario_tape import digest
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE llm_scenario_state (key TEXT, business_time REAL, body TEXT)")
        conn.execute("CREATE TABLE llm_scenario_recovery_events (kind TEXT, call_id TEXT, amount REAL)")
        conn.execute("INSERT INTO llm_scenario_state VALUES('z',1000.001,'original')")
        conn.execute("INSERT INTO llm_scenario_state VALUES('a',1000.002,'later')")
        conn.execute("INSERT INTO llm_scenario_recovery_events VALUES('EXCHANGE_REQUEST','exact-call',1.25)")
        expected={
            "llm_scenario_recovery_events":dict(columns=["kind","call_id","amount"],
                rows=[("EXCHANGE_REQUEST","exact-call",1.25)]),
            "llm_scenario_state":dict(columns=["key","business_time","body"],
                rows=[("z",1000.001,"original"),("a",1000.002,"later")]),
        }
        assert _runtime_event_hash(conn)==digest(expected)


def test_runtime_event_hash_preserves_recovery_input_reference():
    from analysis.scenario_replay import _runtime_event_hash
    from utils.btc_recovery_references import canonical, compact_input, pack
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER, context TEXT)')
        conn.execute('CREATE TABLE llm_scenario_recovery_events(slot INTEGER, kind TEXT, body TEXT)')
        context = canonical({'recovery': {'baseline': 'x' * 2000}})
        original = canonical({'decision_slot': 7, 'recovery': json.loads(context)['recovery']})
        conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?)', (7, context))
        conn.execute('INSERT INTO llm_scenario_recovery_events VALUES(?,?,?)', (7, 'INPUT', original))
        before = _runtime_event_hash(conn)
        compacted = compact_input(context, original, 7)
        assert compacted != original
        conn.execute('UPDATE llm_scenario_recovery_events SET body=?', (pack(compacted),))
        assert _runtime_event_hash(conn) == before


@pytest.mark.parametrize("create_statement", [
    'CREATE TABLE llm_scenario_unknown (value INTEGER)',
    'CREATE TABLE "llm_scenario_unknown""quote" (value INTEGER)',
    'CREATE TABLE "llm_scenario_unknown""; DROP TABLE llm_scenario_state; --" (value INTEGER)',
])
def test_runtime_event_hash_rejects_unknown_quoted_or_injection_table(create_statement):
    from analysis.scenario_replay import _runtime_event_hash
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE llm_scenario_state (value INTEGER)")
        conn.execute("INSERT INTO llm_scenario_state VALUES(7)")
        conn.execute(create_statement)
        statements=[]
        conn.set_trace_callback(statements.append)
        with pytest.raises(TapeMismatch,match="unknown_runtime_event_table"):
            _runtime_event_hash(conn)
        assert all(statement in {
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'llm_scenario%' ORDER BY name",
            "SELECT * FROM llm_scenario_state ORDER BY rowid",
        } for statement in statements)
        assert conn.execute("SELECT value FROM llm_scenario_state").fetchone()==(7,)


def test_runtime_event_query_map_is_complete_literal_sql_without_formatting():
    import ast
    import inspect
    from pathlib import Path
    import re
    import analysis.scenario_replay as module
    tree=ast.parse(inspect.getsource(module))
    assignment=next(node for node in tree.body if isinstance(node,ast.Assign)
        and any(isinstance(target,ast.Name) and target.id=="RUNTIME_EVENT_QUERIES" for target in node.targets))
    assert isinstance(assignment.value,ast.Dict)
    for key,value in zip(assignment.value.keys,assignment.value.values):
        assert isinstance(key,ast.Constant) and isinstance(value,ast.Constant)
        assert isinstance(key.value,str) and isinstance(value.value,str)
        assert value.value.split()==["SELECT","*","FROM",key.value,"ORDER","BY","rowid"]
    helper=ast.parse(inspect.getsource(module._runtime_event_hash))
    assert not any(isinstance(node,(ast.JoinedStr,ast.BinOp)) for node in ast.walk(helper))
    assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
        and node.func.attr in {"format","replace","join"} for node in ast.walk(helper))
    live=Path(module.__file__).resolve().parents[1]/"live"
    declared=set()
    for path in live.glob("*.py"):
        declared.update(re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(llm_scenario_[A-Za-z_]+)",path.read_text(),re.IGNORECASE))
    assert set(module.RUNTIME_EVENT_QUERIES)==declared


@pytest.mark.parametrize("path", ["OHLC", "OLHC"])
@pytest.mark.parametrize("profile", ["single-pass-economic-v4", "calculated-economic-v4"])
@pytest.mark.parametrize("quantity", [.1, 6.])
def test_zero_network_native_economic_fresh_frozen_exact_full_tick(tmp_path, path, profile, quantity):
    bundle, market = inputs(1200000)
    wall, requests = [0.], []
    def generate(**request):
        assert request["retry_transport"] is False
        requests.append(deepcopy(request))
        wall[0] += 2.5
        payload = json.loads(request["user_prompt"])
        ctx = payload["contract_context"]
        if "selection_token" in request["response_schema"]["properties"]:
            receipts = payload["calculated_candidates"]
            selected = next((r for r in receipts["candidates"] if r["eligible"]), receipts["wait"])
            result = dict(selection_token=f"{selected['id']}:{selected['receipt_sha256']}", rationale="계산 확인")
        elif "wait_proposal" in request["response_schema"]["properties"]:
            if ctx.get("scenario_id") and ctx["now"] >= bundle["start_ms"]/1000 + 600:
                candidates = [wire(ctx, "EXIT")]
            else:
                candidates = [] if ctx.get("scenario_id") else [wire(ctx, "OPEN")]
                if candidates:
                    candidates[0]["entries"][0]["quantity"] = quantity
            result = dict(wait_proposal=wire(ctx, "WAIT"), candidates=candidates,
                no_candidate_reason="" if candidates else "기존 포지션 유지")
        else:
            action = "EXIT" if ctx.get("scenario_id") and ctx["now"] >= bundle["start_ms"]/1000 + 600 else "WAIT" if ctx.get("scenario_id") else "OPEN"
            result = wire(ctx, action)
            if action == "OPEN":
                result["entries"][0]["quantity"] = quantity
        return SimpleNamespace(text=json.dumps(result))
    tape = tmp_path / "tape"
    with network_boundary("frozen") as blocked:
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tape,
            path=path, policy_profile=profile, model_generator=generate, clock_monotonic=lambda:wall[0])
        frozen = run_replay(bundle, market, tmp_path / "frozen", mode="frozen", tape_path=tape,
            path=path, policy_profile=profile)
    assert blocked == [] and requests
    assert fresh["execution_metadata"]["actual_model_calls"] == 0
    assert fresh["execution_metadata"]["fixture_generator_calls"] == len(requests)
    assert fresh["result_hash"] == frozen["result_hash"]
    assert fresh["exchange_hash"] == frozen["exchange_hash"]
    if quantity == .1:
        assert fresh["economic"]["completed_scenarios"] >= 1
    assert fresh["economic"]["fees"] > 0
    assert frozen["execution_metadata"]["actual_model_calls"] == 0
    assert json.loads((tmp_path / "fresh" / "equity.json").read_text()) == json.loads((tmp_path / "frozen" / "equity.json").read_text())
    for table in ["llm_scenario_decisions", "llm_scenario_intents", "llm_scenario_settlements"]:
        with sqlite3.connect(tmp_path / "fresh" / "replay.sqlite") as one, sqlite3.connect(tmp_path / "frozen" / "replay.sqlite") as two:
            assert one.execute(f"SELECT * FROM {table}").fetchall() == two.execute(f"SELECT * FROM {table}").fetchall()


def test_single_pass_synthetic_policy_exact_not_semantic(tmp_path):
    bundle, market = inputs()
    tape = tmp_path / "tape"
    run_replay(bundle, market, tmp_path / "one", mode="fixture", tape_path=tape,
        policy_profile="single-pass-economic-v4", policy=wait_policy, clock_monotonic=lambda:0.)
    run_replay(bundle, market, tmp_path / "two", mode="frozen", tape_path=tape,
        policy_profile="single-pass-economic-v4")


@pytest.mark.parametrize("profile", ["single-pass-economic-v4", "calculated-economic-v4"])
@pytest.mark.parametrize("cancel_ack", ["exact", "timeout", "missing_identity"])
def test_exit_cancel_latency_is_not_submission_failure_and_replays_exactly(tmp_path, monkeypatch, profile, cancel_ack):
    from backtest.scenario_exchange import ScenarioExchange
    original=ScenarioExchange.cancel_order
    def cancel(self, **params):
        response=original(self,**params)
        if cancel_ack == "timeout":
            raise TimeoutError("unknown cancellation")
        if cancel_ack == "missing_identity":
            return dict(retCode=0,result={})
        return response
    monkeypatch.setattr(ScenarioExchange,"cancel_order",cancel)
    bundle,market=inputs(1200000)
    wall=[0.]
    def generate(**request):
        wall[0]+=1
        payload=json.loads(request["user_prompt"])
        ctx=payload["contract_context"]
        if "selection_token" in request["response_schema"]["properties"]:
            receipts=payload["calculated_candidates"]
            selected=next((r for r in receipts["candidates"] if r["eligible"]),receipts["wait"])
            result=dict(selection_token=f"{selected['id']}:{selected['receipt_sha256']}",rationale="fixture")
        else:
            action="EXIT" if ctx.get("scenario_id") else ("OPEN" if ctx["now"] < bundle["start_ms"]/1000+300 else "WAIT")
            result=wire(ctx,action)
            result["expires_at"]=ctx["now"]+1200
            if "wait_proposal" in request["response_schema"]["properties"]:
                result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[result] if action != "WAIT" else [],no_candidate_reason="fixture")
        return SimpleNamespace(text=json.dumps(result))
    with network_boundary("frozen") as blocked:
        fresh=run_replay(bundle,market,tmp_path/"fresh",mode="fixture",tape_path=tmp_path/"tape",
            policy_profile=profile,model_generator=generate,clock_monotonic=lambda:wall[0])
        frozen=run_replay(bundle,market,tmp_path/"frozen",mode="frozen",tape_path=tmp_path/"tape",policy_profile=profile)
    assert not blocked
    outcomes=[json.loads(line).get("outcome",{}) for line in (tmp_path/"tape").read_text().splitlines()]
    expected_reason="awaiting_cancellation" if cancel_ack == "exact" else "submission_unknown"
    assert any(row.get("reason") == expected_reason for row in outcomes)
    assert fresh["execution_status"] == ("COMPLETE" if cancel_ack == "exact" else "STOPPED")
    assert fresh["result_hash"] == frozen["result_hash"]
    assert fresh["economic"]["completed_scenarios"] == 1
    with sqlite3.connect(tmp_path/"fresh/replay.sqlite") as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='exit'").fetchone()[0] == 1
    assert frozen["execution_metadata"]["actual_model_calls"] == 0


@pytest.mark.parametrize("cancel_ack", ["exact", "timeout", "missing_identity"])
def test_management_adjust_cancel_waits_for_exact_ack_and_replays(tmp_path, monkeypatch, cancel_ack):
    from backtest.scenario_exchange import ScenarioExchange
    original = ScenarioExchange.cancel_order
    def cancel(self, **params):
        response = original(self, **params)
        if cancel_ack == "timeout":
            raise TimeoutError("unknown cancellation")
        if cancel_ack == "missing_identity":
            return dict(retCode=0, result={})
        return response
    monkeypatch.setattr(ScenarioExchange, "cancel_order", cancel)
    bundle, market = inputs(1200000)
    wall = [0.]
    def generate(**request):
        wall[0] += 1
        ctx = json.loads(request["user_prompt"])["contract_context"]
        if not ctx.get("scenario_id") and ctx["now"] < bundle["start_ms"] / 1000 + 300:
            result = wire(ctx, "OPEN")
        elif ctx.get("scenario_id") and ctx.get("positions") and ctx["now"] < bundle["start_ms"] / 1000 + 600:
            result = wire(ctx, "ADJUST")
            result.update(side="LONG", hard_stop=98, chase=dict(max_bps=0, max_reprices=0),
                take_profits=[dict(id="tp", price=120, fraction=.5)])
        else:
            result = wire(ctx, "WAIT")
        result["expires_at"] = ctx["now"] + 1200
        return SimpleNamespace(text=json.dumps(result))
    with network_boundary("frozen") as blocked:
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile="single-pass-economic-v4", model_generator=generate, clock_monotonic=lambda: wall[0])
        frozen = run_replay(bundle, market, tmp_path / "frozen", mode="frozen", tape_path=tmp_path / "tape",
            policy_profile="single-pass-economic-v4")
    assert not blocked
    outcomes = [json.loads(line).get("outcome", {}) for line in (tmp_path / "tape").read_text().splitlines()]
    expected = "awaiting_cancellation" if cancel_ack == "exact" else "submission_unknown"
    assert any(row.get("reason") == expected for row in outcomes)
    assert fresh["execution_status"] == ("COMPLETE" if cancel_ack == "exact" else "STOPPED")
    assert fresh["result_hash"] == frozen["result_hash"]
    assert frozen["execution_metadata"]["actual_model_calls"] == 0
    with sqlite3.connect(tmp_path / "fresh" / "replay.sqlite") as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='entry'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='native_sl' AND status='LIVE'").fetchone()[0] == 1


@pytest.mark.parametrize("profile,fail_at", [("single-pass-economic-v4",1),
    ("calculated-economic-v4",1),("calculated-economic-v4",2)])
def test_first_transport_failure_stops_new_calls_and_frozen_drains_identically(tmp_path,profile,fail_at):
    bundle, market = inputs()
    wall, calls, attempts, failures = [0.], [], [], []
    def generate(**request):
        calls.append(request)
        wall[0] += 2.
        if len(calls)==fail_at:
            raise RuntimeError("fixture_transport_failure")
        ctx=json.loads(request["user_prompt"])["contract_context"]
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx,"WAIT"),
            candidates=[wire(ctx,"OPEN")],no_candidate_reason="")))
    tape=tmp_path/"tape"
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tape,
        policy_profile=profile,model_generator=generate,clock_monotonic=lambda:wall[0],
        before_http=attempts.append,failure_callback=failures.append)
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tape,
        policy_profile=profile,failure_callback=failures.append)
    assert len(calls)==len(attempts)==fail_at and len(failures)==2
    assert fresh["execution_status"]=="STOPPED" and fresh["coverage"]["eligible"] is False
    assert fresh["coverage"]["processed_slots"]==1
    assert fresh["coverage"]["model_free_drain_status"]=="COMPLETE"
    assert fresh["result_hash"]==frozen["result_hash"]


def test_post_review_durable_record_delay_cannot_extend_deadline(tmp_path,monkeypatch):
    bundle,market=inputs()
    wall=[0.]
    original=sqlite3.connect
    class DelayedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result=super().execute(sql,parameters)
            if sql.startswith("INSERT INTO llm_scenario_review_events") and json.loads(review_text(parameters[2]))["kind"]=="FRESH_REVIEW_RECEIPT":
                wall[0]+=76
            return result
    monkeypatch.setattr(sqlite3,"connect",lambda *args,**kwargs:original(*args,factory=DelayedConnection,**kwargs))
    def generate(**request):
        wall[0]+=1
        payload=json.loads(request["user_prompt"])
        if "selection_token" in request["response_schema"]["properties"]:
            receipt=payload["calculated_candidates"]["candidates"][0]
            result=dict(selection_token=f"{receipt['id']}:{receipt['receipt_sha256']}",rationale="검산")
        else:
            ctx=payload["contract_context"]
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[wire(ctx,"OPEN")],
                no_candidate_reason="")
        return SimpleNamespace(text=json.dumps(result))
    tape=tmp_path/"tape"
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tape,
        policy_profile="calculated-economic-v4",model_generator=generate,clock_monotonic=lambda:wall[0])
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tape,
        policy_profile="calculated-economic-v4")
    assert fresh["failure_evidence"]["reason"]=="review_deadline_failure"
    assert fresh["result_hash"]==frozen["result_hash"]
    for name in ("one","two"):
        with sqlite3.connect(tmp_path/name/"replay.sqlite") as conn:
            assert conn.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==0


@pytest.mark.parametrize("delay", [40.,76.])
def test_durable_attempt_latency_rechecks_dispatch_before_any_generator(tmp_path,delay):
    bundle,market=inputs()
    wall,calls=[0.],[]
    def attempt(event):
        wall[0]+=delay
    def generate(**request):
        calls.append(request)
        wall[0]+=1
        ctx=json.loads(request["user_prompt"])["contract_context"]
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wire(ctx,"WAIT"),candidates=[],
            no_candidate_reason="구조 없음")))
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4",model_generator=generate,
        before_http=attempt,clock_monotonic=lambda:wall[0])
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4")
    assert fresh["result_hash"]==frozen["result_hash"]
    if delay>75:
        assert calls==[] and fresh["execution_status"]=="STOPPED"
    else:
        assert calls and all(0<request["timeout"]<=35 for request in calls)


def test_calculated_fixture_policy_cannot_fall_back_to_oauth(tmp_path):
    bundle,market=inputs()
    with pytest.raises(ValueError,match="calculated_fixture_generator_required"):
        run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tmp_path/"tape",
            policy_profile="calculated-economic-v4",policy=wait_policy)
    assert not (tmp_path/"tape").exists()


def test_final_guard_validation_latency_is_not_invisible(tmp_path,monkeypatch):
    import live.scenario_review_guard as guard_module
    bundle,market=inputs()
    wall,guards=[0.],[0]
    original=guard_module.deepcopy
    def slow_copy(value):
        result=original(value)
        if isinstance(value,dict) and value.get("input_id") and value.get("now")==bundle["start_ms"]/1000:
            guards[0]+=1
            if guards[0]==2:
                wall[0]+=76
        return result
    monkeypatch.setattr(guard_module,"deepcopy",slow_copy)
    def generate(**request):
        wall[0]+=1
        payload=json.loads(request["user_prompt"])
        if "selection_token" in request["response_schema"]["properties"]:
            receipt=payload["calculated_candidates"]["candidates"][0]
            result=dict(selection_token=f"{receipt['id']}:{receipt['receipt_sha256']}",rationale="검산")
        else:
            ctx=payload["contract_context"]
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[wire(ctx,"OPEN")],
                no_candidate_reason="")
        return SimpleNamespace(text=json.dumps(result))
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4",model_generator=generate,clock_monotonic=lambda:wall[0])
    guards[0]=0
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4")
    assert fresh["result_hash"]==frozen["result_hash"]
    assert fresh["failure_evidence"]["reason"]=="review_deadline_failure"
    for name in ("one","two"):
        with sqlite3.connect(tmp_path/name/"replay.sqlite") as conn:
            assert conn.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==0
            assert conn.execute("SELECT count(*) FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()[0]==0


@pytest.mark.parametrize("fault", ["before_commit","committed_then_sync_failed"])
def test_record_fault_replays_true_durable_effect_without_duplicate_guard_effect(tmp_path,monkeypatch,fault):
    bundle,market=inputs()
    wall=[0.]
    original=sqlite3.connect
    class FaultConnection(sqlite3.Connection):
        def __init__(self,path,*args,**kwargs):
            super().__init__(path,*args,**kwargs)
            self.fresh="/one/" in str(path)
            self.injected=False
        def execute(self,sql,parameters=()):
            inject=(self.fresh and not self.injected and sql.startswith("INSERT INTO llm_scenario_review_events")
                    and json.loads(review_text(parameters[2]))["kind"]=="MODEL_REQUEST")
            if inject:
                self.injected=True
                if fault=="before_commit":
                    raise sqlite3.OperationalError("fixture_db_busy")
            result=super().execute(sql,parameters)
            if inject:
                wall[0]+=400
            return result
    monkeypatch.setattr(sqlite3,"connect",lambda *args,**kwargs:original(*args,factory=FaultConnection,**kwargs))
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4",model_generator=lambda **kw:pytest.fail("no generator dispatch"),
        clock_monotonic=lambda:wall[0])
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4")
    assert fresh["result_hash"]==frozen["result_hash"]
    assert fresh["execution_status"]=="STOPPED" and fresh["coverage"]["eligible"] is False
    expected=0 if fault=="before_commit" else 1
    for name in ("one","two"):
        with sqlite3.connect(tmp_path/name/"replay.sqlite") as conn:
            assert conn.execute("SELECT count(*) FROM llm_scenario_review_events").fetchone()[0]==expected
            assert conn.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==0


@pytest.mark.parametrize("profile,guard_delay,holding_action", [("single-pass-economic-v4",0.,"EXIT"),
    ("calculated-economic-v4",0.,"EXIT"),("calculated-economic-v4",.0011,"EXIT"),
    ("calculated-economic-v4",.0011,"WAIT"),("calculated-economic-v4",40.,"EXIT")])
def test_consumed_recovery_exact_all_runtime_review_exchange_tables(tmp_path,profile,guard_delay,holding_action,monkeypatch):
    from live.scenario_contract import identity_fields
    from tests.test_scenario_replay import recovery_inputs, cycle_policy
    bundle,market=recovery_inputs(native_stops=not guard_delay)
    bundle["end_ms"]=bundle["start_ms"]+900000
    calls,wall=[],[0.]
    guard_reads=[0]
    if guard_delay:
        import live.scenario_review_guard as guard_module
        stop_minute=bundle["start_ms"]+600000
        for frame in (market.source,market.mark):
            frame.loc[frame.index.view("i8")//1000000==stop_minute,"low"]=97.5
        original=guard_module.deepcopy
        def slow_copy(value):
            result=original(value)
            if isinstance(value,dict) and value.get("input_id") and value.get("now")==stop_minute/1000:
                guard_reads[0]+=1
                if guard_reads[0]==2:
                    wall[0]+=guard_delay
            return result
        monkeypatch.setattr(guard_module,"deepcopy",slow_copy)
    policy=cycle_policy(calls,native_stops=True)
    def generate(**request):
        wall[0]+=1
        # This scripted model reads original recovery paths, not wire encoding
        # markers. Resolve the same lossless references described to the model.
        payload=restore_model_input(json.loads(request["user_prompt"]))
        if "selection_token" in request["response_schema"]["properties"]:
            candidate=next(r for r in payload["calculated_candidates"]["candidates"] if r["eligible"])
            result=dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}",rationale="계산 검토")
        else:
            ctx=payload["contract_context"]
            proposal=policy(payload["market_snapshot"],ctx)
            if ctx.get("scenario_id") and holding_action=="WAIT":
                proposal=wire(ctx,"WAIT")
            proposal.update(identity_fields(ctx))
            for field in ("side","hard_stop","chase","review","stop_revision"):
                proposal.setdefault(field,None)
            proposal.setdefault("cancel_entry_ids",[])
            proposal.setdefault("leverage",10)
            if proposal["action"]=="EXIT" and ctx.get("recovery_contract_version"):
                proposal["recovery"]=wire(ctx,"EXIT")["recovery"]
            for entry in proposal["entries"]:
                entry["trigger_price"]=None
            candidates=[] if proposal["action"]=="WAIT" else [proposal]
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=candidates,
                no_candidate_reason="" if candidates else "첫 관측")
        return SimpleNamespace(text=json.dumps(result))
    tape=tmp_path/"tape"
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tape,
        policy_profile=profile,initial_state="recovery-stage0",
        policy=policy if profile=="single-pass-economic-v4" else None,
        model_generator=generate if profile=="calculated-economic-v4" else None,
        clock_monotonic=lambda:wall[0])
    guard_reads[0]=0
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tape,
        policy_profile=profile,initial_state="recovery-stage0")
    assert fresh["result_hash"]==frozen["result_hash"]
    with sqlite3.connect(tmp_path/"one"/"replay.sqlite") as one, sqlite3.connect(tmp_path/"two"/"replay.sqlite") as two:
        events=one.execute("SELECT kind FROM llm_scenario_recovery_events").fetchall()
        assert ("PERMIT_CONSUMED",) in events and ("EXCHANGE_REQUEST",) in events
        if guard_delay:
            outcome=json.loads(one.execute("SELECT outcome FROM llm_scenario_decisions WHERE slot=?",(stop_minute//300000,)).fetchone()[0])
            if guard_delay>=40:
                # Native SL fills after the coherent final observation while
                # pure validation is running. An EXIT intent may be committed,
                # but the real executor's fresh capture sees flat and MUST NOT
                # submit a second close. Intent is not exchange authority proof.
                assert outcome["status"]=="intent_pending"
                assert one.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==2
                assert one.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='exit'").fetchone()[0]==0
                assert one.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='native_sl'").fetchone()[0]==1
            else:
                assert outcome["status"]==("wait" if holding_action=="WAIT" else "intent_pending")
                assert one.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==(1 if holding_action=="WAIT" else 2)
                if holding_action=="EXIT":
                    events = (json.loads(review_text(r[0])) for r in one.execute("SELECT body FROM llm_scenario_review_events WHERE slot=?",(stop_minute//300000,)))
                    final = next(event for event in events if event['kind'] == 'FINAL_FRESH_REVIEW_RECEIPT')
                    assert round((final["post_validation_checked_at"]-final["receipt"]["checked_at"])*1000)==1
                else:
                    tick=next(json.loads(line) for line in tape.read_text().splitlines()[1:] if json.loads(line)["at_ms"]==stop_minute)
                    assert tick["trace"][-1]["at_ms"]==stop_minute+1001
            assert one.execute("SELECT count(*) FROM llm_scenario_settlements").fetchone()[0]==1
            assert json.loads(one.execute("SELECT body FROM llm_scenario_state").fetchone()[0])["active"] is None
        tables=[row[0] for row in one.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'llm_scenario%'")]
        for table in tables:
            assert one.execute(f"SELECT * FROM {table}").fetchall()==two.execute(f"SELECT * FROM {table}").fetchall(), table


def test_observing_post_record_stop_crossing_never_consumes_permit(tmp_path,monkeypatch):
    from live.scenario_contract import identity_fields
    from tests.test_scenario_replay import recovery_inputs, cycle_policy
    bundle,market=recovery_inputs()
    bundle["end_ms"]=bundle["start_ms"]+900000
    stop_minute=bundle["start_ms"]+300000
    for frame in (market.source,market.mark):
        frame.loc[frame.index.view("i8")//1000000==stop_minute,"low"]=97.5
    wall=[0.]
    original=sqlite3.connect
    class DelayedConnection(sqlite3.Connection):
        def execute(self,sql,parameters=()):
            result=super().execute(sql,parameters)
            if (sql.startswith("INSERT INTO llm_scenario_review_events") and parameters[0]==stop_minute//300000
                    and json.loads(review_text(parameters[2]))["kind"]=="FRESH_REVIEW_RECEIPT"):
                wall[0]+=40
            return result
    monkeypatch.setattr(sqlite3,"connect",lambda *args,**kwargs:original(*args,factory=DelayedConnection,**kwargs))
    policy=cycle_policy([])
    def generate(**request):
        wall[0]+=1
        data=restore_model_input(json.loads(request["user_prompt"]))
        if "selection_token" in request["response_schema"]["properties"]:
            candidate=next(r for r in data["calculated_candidates"]["candidates"] if r["eligible"])
            result=dict(selection_token=f"{candidate['id']}:{candidate['receipt_sha256']}",rationale="계산 확인")
        else:
            ctx=data["contract_context"]
            candidates=[]
            if ctx["now"]==stop_minute/1000:
                proposal=policy(data["market_snapshot"],ctx)
                proposal.update(identity_fields(ctx))
                for field in ("review","stop_revision"):
                    proposal.setdefault(field,None)
                proposal.setdefault("cancel_entry_ids",[])
                for entry in proposal["entries"]:
                    entry["trigger_price"]=None
                candidates=[proposal]
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=candidates,
                no_candidate_reason="" if candidates else "관측 유지")
        return SimpleNamespace(text=json.dumps(result))
    fresh=run_replay(bundle,market,tmp_path/"one",mode="fixture",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4",initial_state="recovery-stage0",
        model_generator=generate,clock_monotonic=lambda:wall[0])
    frozen=run_replay(bundle,market,tmp_path/"two",mode="frozen",tape_path=tmp_path/"tape",
        policy_profile="calculated-economic-v4",initial_state="recovery-stage0")
    assert fresh["result_hash"]==frozen["result_hash"]
    with sqlite3.connect(tmp_path/"one"/"replay.sqlite") as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0]==0
        assert conn.execute("SELECT count(*) FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'").fetchone()[0]==0
        outcome=json.loads(conn.execute("SELECT outcome FROM llm_scenario_decisions WHERE slot=?",(stop_minute//300000,)).fetchone()[0])
        assert outcome["failure_code"]=="fresh_safety_rejection"
        assert outcome["failure_detail"]=="stop_crossed_market"


def test_profiles_preserve_legacy_contract_schema():
    bundle, _ = inputs()
    assert contract_for(bundle,"OHLC",1.,10000.)["schema"] == 2
    contract = contract_for(bundle,"OHLC",1.,10000.,policy_profile="calculated-economic-v4")
    assert contract["schema"] == 4 and contract["review_binding"]


def test_calculated_separate_process_identity_first_next_recovery_seed(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys
    package = Path(__file__).resolve().parents[1]
    script = '''
import sys,json
from pathlib import Path
from types import SimpleNamespace
from tests.test_scenario_calculated_replay import wire
from tests.test_scenario_replay import inputs
from analysis.scenario_replay import network_boundary,run_replay
root,mode=Path(sys.argv[1]),sys.argv[2]
wall=[0.]
def generate(**request):
    wall[0]+=1.25
    ctx=json.loads(request['user_prompt'])['contract_context']
    result=dict(wait_proposal=wire(ctx,'WAIT'),candidates=[],no_candidate_reason='관측 유지')
    return SimpleNamespace(text=json.dumps(result))
b,m=inputs()
with network_boundary('frozen') as blocked:
    run_replay(b,m,root/mode,mode=mode,tape_path=root/'tape',
        policy_profile='calculated-economic-v4',initial_state='recovery-stage0',
        model_generator=generate if mode=='fixture' else None,clock_monotonic=lambda:wall[0])
assert not blocked
'''
    env = dict(os.environ, PYTHONPATH=str(package))
    for mode in ("fixture", "frozen"):
        result = subprocess.run([sys.executable,"-c",script,str(tmp_path),mode],env=env,
                                capture_output=True,text=True,timeout=120)
        assert result.returncode == 0, result.stderr
    fresh=json.loads((tmp_path/"fixture"/"report.json").read_text())
    frozen=json.loads((tmp_path/"frozen"/"report.json").read_text())
    assert fresh["result_hash"] == frozen["result_hash"]


@pytest.mark.parametrize("target,reason", [(100,"coverage"), (99,"coverage"), (-1,"reversal")])
def test_clock_never_clamps_out_of_coverage_or_reverses(target, reason):
    session = SimpleNamespace(ts_ms=0, clock=lambda:0)
    driver = SimpleNamespace(session=session,end=99,advance_to=lambda at:setattr(session,"ts_ms",at))
    tape = SimpleNamespace(mode="frozen",current=dict(at_ms=0,trace=[dict(label="clock",at_ms=target)]))
    clock = EconomicClock(driver,tape,monotonic=lambda:0)
    clock.begin()
    with pytest.raises(TapeMismatch, match=reason):
        clock()
    assert session.ts_ms == 0 and clock.fatal
