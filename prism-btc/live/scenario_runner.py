"""Explicit opt-in DEMO MAIN scenario entrypoint. Never edits cron/legacy policy.

Activation requires a verified broker adapter and a separately verified legacy
handoff. No implicit fallback to the old strategy or to real-money credentials.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import time
import uuid
from pathlib import Path

from live.scenario_llm import propose
from live.scenario_preview import collect_snapshot, response_contract
from live.scenario_runtime import ScenarioRuntime
from live.shared_entry_coordinator import mutation_lock
from live.entry_reservations import LockBusy
from live.scenario_provenance import run_capture
from live.scenario_ledger import forward_run


def operational_propose(*args, **kwargs):
    """Only the production runner opts into one audited transport retry."""
    from functools import partial
    from live.scenario_oauth import generate_scenario
    return propose(*args, **kwargs, generate=partial(generate_scenario, retry_transport=True))


CALCULATED_REVIEW_MODE = "economic-reject-calculated-v1"


def operational_calculated_propose(snapshot, context, *, record):
    """DEMO-only caller supplies durable recording; no retry or legacy fallback."""
    from live.scenario_oauth import generate_scenario
    from live.scenario_review_adapter import make_calculated_review_proposer
    return make_calculated_review_proposer(generate=generate_scenario, clock=time.time,
        research_mode=CALCULATED_REVIEW_MODE)(snapshot, context, record=record)


_FAILURE_STAGES = {"database_init", "control", "broker_init", "activation", "run", "model_input_preparation"}
_ERROR_TYPES = {"Exception", "ValueError", "RuntimeError", "PermissionError", "TimeoutError",
                "OperationalError", "IntegrityError", "DatabaseError", "LockBusy"}


def run_once(conn, broker, *, execute=False, protect_only=False,
             snapshot=collect_snapshot, proposal=propose, calculated_review=False, entry_contract_enabled=False):
    with run_capture(conn):
        return _run_once(conn, broker, execute=execute, protect_only=protect_only,
                         snapshot=snapshot, proposal=proposal, calculated_review=calculated_review,
                         entry_contract_enabled=entry_contract_enabled)


def _run_once(conn, broker, *, execute=False, protect_only=False,
              snapshot=collect_snapshot, proposal=propose, calculated_review=False, entry_contract_enabled=False):
    if type(entry_contract_enabled) is not bool:
        raise ValueError("entry_contract_flag_must_be_bool")
    if entry_contract_enabled and (not execute or protect_only):
        return {"status":"blocked","reason":"entry_contract_requires_execute","model_called":False}
    if type(calculated_review) is not bool:
        raise ValueError("calculated_review_flag_must_be_bool")
    if calculated_review and (not execute or protect_only):
        return {"status":"blocked","reason":"calculated_review_requires_execute","model_called":False}
    if getattr(broker,"environment",None)!="demo" or getattr(broker,"lane",None)!="MAIN":
        return {"status":"blocked","reason":"demo_main_required"}
    if protect_only:
        try:
            with mutation_lock(conn):
                evidence=broker.reconcile()
                from live.scenario_outbox import enqueue
                notices=evidence.get("notices",[]) if isinstance(evidence,dict) else []
                if isinstance(notices,list):
                    for notice in notices[:100]:
                        conn.execute("SAVEPOINT protection_notice")
                        try:
                            enqueue(conn,notice["event_id"],notice)
                            conn.execute("RELEASE protection_notice")
                        except Exception:
                            conn.execute("ROLLBACK TO protection_notice")
                            conn.execute("RELEASE protection_notice")
                    conn.commit()
            if not isinstance(evidence,dict) or evidence.get("protection_confirmed") is not True:
                return {"status":"blocked","reason":"protection_not_verified_by_scenario_adapter","model_called":False}
            observation = evidence.get("observation")
            verified_flat = (isinstance(observation, dict)
                and observation.get("exchange_flat") is True
                and observation.get("open_orders") == []
                and observation.get("legacy_fenced") is False
                and evidence.get("protection_status") == "verified_flat_no_exposure")
            return {"status":"protection_checked","model_called":False,
                    "verified_flat":verified_flat}
        except LockBusy:
            return {"status":"lock_busy","model_called":False}
        except Exception:
            return {"status":"blocked","reason":"protection_unconfirmed","model_called":False}
    if not execute:
        return {"status":"execution_disabled","reason":"explicit_activation_required"}
    runtime=ScenarioRuntime(conn,broker,
        lambda snap,ctx:proposal(snap,ctx,response_contract(ctx)),snapshot,
        recovery_enabled=True,automatic_normalization_enabled=True,
        calculated_review_enabled=calculated_review,
        entry_contract_enabled=entry_contract_enabled,
        entry_goal_enabled=entry_contract_enabled and calculated_review,
        pending_renewal_enabled=entry_contract_enabled and calculated_review,
        calculated_review_propose=operational_calculated_propose if calculated_review else None,
        research_review_mode=CALCULATED_REVIEW_MODE if calculated_review else "legacy")
    return runtime.tick()


def observe_pending_market(result, snapshot, *, clock=time.time):
    """Collect outside trading locks; never authorize a model call or an order."""
    from live.scenario_runtime import snapshot_input_time
    result = dict(result, market_observation_only=True, model_called=False)
    try:
        observed = snapshot()
        if observed.get('valid') is not True or not 0 <= clock()-snapshot_input_time(observed) <= 120:
            raise ValueError('pending_market_snapshot_invalid')
        result['market_observation_status'] = 'observed'
    except Exception:
        result['market_observation_status'] = 'failed'
    return result


def record_health(conn,result,*,snapshot=None,loop=None,clock=time.time):
    """Keep existing BTC liveness monitoring, without forging legacy bar cursors."""
    from live import tracking
    from live.scenario_runtime import snapshot_input_time
    status=result.get("status","unknown")
    # Separate latest outcomes: protection success must not overwrite a failed
    # decision loop, and a legacy heartbeat is never this evidence.
    if loop in {"decision", "protection"}:
        from live.scenario_incident_epoch import historical
        excluded = historical(conn)
        management = {}
        authority = result.get('management_quarantine')
        if (loop == 'decision' and status == 'wait' and result.get('market_observation_only') is not True
                and isinstance(authority, dict) and authority.get('status') == 'AUTHORIZED'
                and authority.get('purpose') == 'ADMIN_AUTHORIZATION_NOT_EXCHANGE_EVIDENCE'
                and authority.get('allowed_actions') == ['WAIT', 'EXIT']
                and isinstance(authority.get('approval_id'), str) and authority['approval_id']
                and isinstance(authority.get('approval_sha256'), str) and len(authority['approval_sha256']) == 64
                and type(authority.get('expires_at')) in (int, float)
                and math.isfinite(authority['expires_at']) and clock() < authority['expires_at']):
            management = dict(management_only=True, management_approval_id=authority['approval_id'],
                management_approval_sha256=authority['approval_sha256'], management_expires_at=authority['expires_at'])
        observation = ({"market_observation_only": True, "model_called": False,
                        "market_observation_status": result.get('market_observation_status')}
                       if result.get('market_observation_only') is True else {})
        tracking.set_meta(conn,"scenario_health_"+loop,json.dumps({
            "at":clock(),"status":status,"reason":result.get("reason"),
            "historical_unsettled":excluded,
            "verified_flat_halt":result.get("verified_flat_halt") is True,
            "verified_flat":result.get("verified_flat") is True, **observation, **management},allow_nan=False),"demo")
    if (snapshot is not None and snapshot.get("valid") is True
            and result.get('market_observation_status') != 'failed'):
        observed_ms = snapshot_input_time(snapshot)*1000
        tracking.set_meta(conn,"scenario_market_observed_ms",observed_ms,"demo")
        if result.get('market_observation_only') is not True:
            tracking.set_meta(conn,"scenario_input_asof_ms",observed_ms,"demo")
    if status=="blocked" and result.get("reason") not in {
            'new_risk_halted', 'quarantine_release_required', 'flat_quarantined_unsettled'}:
        detail="scenario tick blocked: "+str(result.get("reason","unknown"))
        if result.get("failure_stage") in _FAILURE_STAGES:
            detail+="; failure_stage="+result["failure_stage"]
        if result.get("reason") == "llm_input_preparation_failed" and result.get("failure_code") in {
                "snapshot_unavailable", "snapshot_stale", "invalid_contract_context", "invalid_input", "input_size"}:
            detail += "; failure_code=" + result["failure_code"]
        if result.get("error_type") in _ERROR_TYPES:
            detail+="; error_type="+result["error_type"]
        tracking.log_event(conn,"error",detail,level="error",mode="demo")
    tracking.log_event(conn,"heartbeat","scenario tick: "+status,mode="demo")


def _record_health_best_effort(conn, result, *, snapshot=None, loop=None):
    try:
        record_health(conn, result, snapshot=snapshot, loop=loop)
    except Exception:
        result["health_recording"] = "failed"


def notify_runtime_status(conn,result,*,clock=time.time):
    """Deduplicated operator alert for host/model/protection failures, never trade proof."""
    from live.scenario_outbox import enqueue
    with mutation_lock(conn):
        # Model failures are not exchange uncertainty. Keep their recovery
        # independent from the once-per-minute protection heartbeat.
        conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_model_incident (id INTEGER PRIMARY KEY CHECK(id=1),event_id TEXT,opened REAL,active INTEGER)")
        model_row=conn.execute("SELECT event_id,opened,active FROM llm_scenario_model_incident WHERE id=1").fetchone()
        review_failed = (result.get("status") == "blocked"
            and result.get("reason") == "reconciliation_or_proposal_failed"
            and result.get("failure_code") in {
                "review_contract_failure", "review_tape_failure", "review_deadline_failure"})
        model_failed=result.get("status")=="blocked" and (result.get("reason") in {
            "llm_output_contract_failed", "llm_call_failed", "llm_input_preparation_failed"}
            or review_failed)
        model_validated=result.get("status")=="wait" or (
            result.get("status")=="intent_pending" and result.get("reason")=="awaiting_exact_evidence")
        if model_failed and (not model_row or not model_row[2]):
            identity="model-error-"+uuid.uuid4().hex
            stamp=clock()
            enqueue(conn,identity,{"kind":"MODEL_ERROR","timestamp":stamp,
                "reason_code":"calculated_review_failed" if review_failed else result["reason"],
                "failure_code": "input_size" if result.get("reason") == "llm_input_preparation_failed"
                    and result.get("failure_code") == "input_size" else None})
            conn.execute("INSERT OR REPLACE INTO llm_scenario_model_incident VALUES(1,?,?,1)",(identity,stamp))
        elif model_validated and model_row and model_row[2]:
            enqueue(conn,model_row[0]+"-resolved",{"kind":"MODEL_RECOVERED","timestamp":clock(),
                "model_validation_confirmed":True})
            conn.execute("UPDATE llm_scenario_model_incident SET active=0 WHERE id=1")
        conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_runner_incident (id INTEGER PRIMARY KEY CHECK(id=1),event_id TEXT,opened REAL,active INTEGER)")
        row=conn.execute("SELECT event_id,opened,active FROM llm_scenario_runner_incident WHERE id=1").fetchone()
        failed=result.get("status")=="blocked" and result.get("reason")!="new_risk_halted" and not model_failed
        if failed and (not row or not row[2]):
            identity="runner-error-"+uuid.uuid4().hex
            stamp=clock()
            enqueue(conn,identity,{"kind":"PENDING","timestamp":stamp})
            conn.execute("INSERT OR REPLACE INTO llm_scenario_runner_incident VALUES(1,?,?,1)",(identity,stamp))
        elif result.get("status")=="protection_checked" and row and row[2]:
            enqueue(conn,row[0]+"-resolved",{"kind":"RESOLVED","timestamp":clock(),
                "resolution_confirmed":True,"resolution":"PROTECTION_QUERY_RECOVERED"})
            conn.execute("UPDATE llm_scenario_runner_incident SET active=0 WHERE id=1")
        conn.commit()


def deliver_notices(conn,result):
    try:
        notify_runtime_status(conn,result)
        from live.scenario_outbox import flush
        result["notice_delivery"]=flush(conn)
    except Exception:
        result["notice_delivery"]={"status":"unconfirmed"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument("--execute",action="store_true")
    modes.add_argument("--protect-only",action="store_true")
    modes.add_argument("--activate",action="store_true",help="verify both old accounts flat and activate approved demo handoff; no orders")
    parser.add_argument("--calculated-review", action="store_true",
                        help="opt in to two-pass, host-sized DEMO MAIN decisions; requires --execute")
    parser.add_argument("--entry-contract", action="store_true",
                        help="opt in to immutable entry premises and sampled failure EXIT; requires --execute")
    parser.add_argument("--root-db",type=Path,
                        default=Path(__file__).resolve().parents[2]/"stock_tracking_db.sqlite")
    args=parser.parse_args()
    if args.calculated_review and not args.execute:
        parser.error("--calculated-review requires --execute and cannot be used for activation/protection")
    if args.entry_contract and not args.execute:
        parser.error("--entry-contract requires --execute")
    started=time.monotonic()
    loop="protection" if args.protect_only else "decision"
    if not args.execute and not args.protect_only and not args.activate:
        print(json.dumps({"status":"execution_disabled","reason":"explicit_activation_required"}))
        return 0
    # Fail without creating a different DB when a production path is wrong.
    if not args.root_db.is_file():
        print(json.dumps({"status":"blocked","reason":"existing_root_database_required"}))
        return 1
    conn=None
    failure_stage="database_init"
    try:
        conn=sqlite3.connect(f"file:{args.root_db}?mode=rw",uri=True,timeout=10)
        conn.row_factory=sqlite3.Row
        failure_stage="control"
        from live.scenario_control import read_control,existing_account_bindings
        control=read_control(conn)
        main_uid,swing_uid=existing_account_bindings(conn)
        if control is not None and control["main_uid"]!=main_uid:
            raise ValueError("scenario_binding_changed")
        if not args.activate and (control is None or control["state"] not in {"active","paused"}):
            print(json.dumps({"status":"execution_disabled","reason":"verified_handoff_required"}))
            return 1
        failure_stage="broker_init"
        from live.scenario_broker import ScenarioDemoBroker
        broker=ScenarioDemoBroker(conn,expected_main_uid=main_uid,execution_enabled=True)
        if args.activate:
            failure_stage="activation"
            from live.scenario_control import begin_transition,activate,flat_handoff_probe
            from live.swing import _make_swing_session
            from live.scenario_runtime import REQUIRED_CAPABILITIES
            if not REQUIRED_CAPABILITIES <= set(broker.capabilities):
                raise ValueError("execution_not_implemented")
            swing,error=_make_swing_session()
            if swing is None or error:
                raise ValueError("swing_handoff_unavailable")
            with mutation_lock(conn):
                def proof():
                    return flat_handoff_probe(conn,broker,swing,swing_uid)
                # Do not disable legacy entries when prerequisite observation fails.
                evidence=proof()
                if not all(evidence[k] for k in ("main_flat","swing_flat","all_orders_terminal","legacy_clear","broker_ready")):
                    raise ValueError("handoff_not_flat")
                begin_transition(conn,main_uid)
                activate(conn,proof=proof)
            print(json.dumps({"status":"active","orders_submitted":0,"schedule_changed":False}))
            return 0
        snapshots=[]
        def snapshot():
            value=collect_snapshot()
            snapshots.append(value)
            return value
        failure_stage="run"
        result=run_once(conn,broker,execute=args.execute,protect_only=args.protect_only,
                        snapshot=snapshot,proposal=operational_propose,
                        calculated_review=args.calculated_review, entry_contract_enabled=args.entry_contract)
        pending_observation = (result.get('status') == 'intent_pending' or
            (result.get('status') == 'blocked' and result.get('reason') in {
                'quarantine_release_required', 'flat_quarantined_unsettled'}))
        if args.execute and pending_observation and not snapshots:
            # Runtime returned before snapshot collection and released its locks.
            # Keep observation alive without bypassing the unresolved-order fence.
            result = observe_pending_market(result, snapshot)
        _record_health_best_effort(conn,result,snapshot=snapshots[-1] if snapshots else None,
                                   loop="protection" if args.protect_only else "decision")
        # Disabled adapters cannot produce trade notices. Delivery occurs only
        # after the runtime released the trading lock and only for queued proofs.
        if result.get("status")!="lock_busy":
            deliver_notices(conn,result)
        forward_run(result,loop=loop,started=started)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result["status"] in {"blocked","execution_disabled"} else 0
    except LockBusy:
        # Expected contention is not trade uncertainty or verified recovery.
        # In particular, do not flush old notices or touch incident state here.
        result={"status":"lock_busy","failure_stage":failure_stage,
                "error_type":"LockBusy"}
        if failure_stage in {"control", "broker_init", "activation"}:
            result["model_called"]=False
        _record_health_best_effort(conn,result,loop="protection" if args.protect_only else "decision")
        forward_run(result,loop=loop,started=started)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        error_type=type(exc).__name__
        result={"status":"blocked","reason":"scenario_runtime_unavailable",
                "failure_stage":failure_stage,
                "error_type":error_type if error_type in _ERROR_TYPES else "Exception"}
        _record_health_best_effort(conn,result,loop="protection" if args.protect_only else "decision")
        if conn is not None:
            deliver_notices(conn,result)
        forward_run(result,loop=loop,started=started)
        print(json.dumps(result))
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__=="__main__":
    raise SystemExit(main())
