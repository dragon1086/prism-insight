"""Explicit opt-in DEMO MAIN scenario entrypoint. Never edits cron/legacy policy.

Activation requires a verified broker adapter and a separately verified legacy
handoff. No implicit fallback to the old strategy or to real-money credentials.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from pathlib import Path

from live.scenario_llm import propose
from live.scenario_preview import collect_snapshot, response_contract
from live.scenario_runtime import ScenarioRuntime
from live.shared_entry_coordinator import mutation_lock
from live.entry_reservations import LockBusy


_FAILURE_STAGES = {"database_init", "control", "broker_init", "activation", "run"}
_ERROR_TYPES = {"Exception", "ValueError", "RuntimeError", "PermissionError", "TimeoutError",
                "OperationalError", "IntegrityError", "DatabaseError", "LockBusy"}


def run_once(conn, broker, *, execute=False, protect_only=False,
             snapshot=collect_snapshot, proposal=propose):
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
            return {"status":"protection_checked","model_called":False}
        except LockBusy:
            return {"status":"lock_busy","model_called":False}
        except Exception:
            return {"status":"blocked","reason":"protection_unconfirmed","model_called":False}
    if not execute:
        return {"status":"execution_disabled","reason":"explicit_activation_required"}
    runtime=ScenarioRuntime(conn,broker,
        lambda snap,ctx:proposal(snap,ctx,response_contract(ctx)),snapshot)
    return runtime.tick()


def record_health(conn,result,*,snapshot=None):
    """Keep existing BTC liveness monitoring, without forging legacy bar cursors."""
    from live import tracking
    from live.scenario_runtime import snapshot_input_time
    if snapshot is not None and snapshot.get("valid") is True:
        tracking.set_meta(conn,"scenario_input_asof_ms",snapshot_input_time(snapshot)*1000,"demo")
    status=result.get("status","unknown")
    if status=="blocked" and result.get("reason")!="new_risk_halted":
        detail="scenario tick blocked: "+str(result.get("reason","unknown"))
        if result.get("failure_stage") in _FAILURE_STAGES:
            detail+="; failure_stage="+result["failure_stage"]
        if result.get("error_type") in _ERROR_TYPES:
            detail+="; error_type="+result["error_type"]
        tracking.log_event(conn,"error",detail,level="error",mode="demo")
    tracking.log_event(conn,"heartbeat","scenario tick: "+status,mode="demo")


def _record_health_best_effort(conn, result, *, snapshot=None):
    try:
        record_health(conn, result, snapshot=snapshot)
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
        model_failed=result.get("status")=="blocked" and result.get("reason") in {
            "llm_output_contract_failed", "llm_call_failed"}
        model_validated=result.get("status")=="wait" or (
            result.get("status")=="intent_pending" and result.get("reason")=="awaiting_exact_evidence")
        if model_failed and (not model_row or not model_row[2]):
            identity="model-error-"+uuid.uuid4().hex
            stamp=clock()
            enqueue(conn,identity,{"kind":"MODEL_ERROR","timestamp":stamp,
                "reason_code":result["reason"]})
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
    parser.add_argument("--root-db",type=Path,
                        default=Path(__file__).resolve().parents[2]/"stock_tracking_db.sqlite")
    args=parser.parse_args()
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
                proof=lambda:flat_handoff_probe(conn,broker,swing,swing_uid)
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
        result=run_once(conn,broker,execute=args.execute,protect_only=args.protect_only,snapshot=snapshot)
        _record_health_best_effort(conn,result,snapshot=snapshots[-1] if snapshots else None)
        # Disabled adapters cannot produce trade notices. Delivery occurs only
        # after the runtime released the trading lock and only for queued proofs.
        if result.get("status")!="lock_busy":
            deliver_notices(conn,result)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result["status"] in {"blocked","execution_disabled"} else 0
    except LockBusy:
        # Expected contention is not trade uncertainty or verified recovery.
        # In particular, do not flush old notices or touch incident state here.
        result={"status":"lock_busy","failure_stage":failure_stage,
                "error_type":"LockBusy"}
        if failure_stage in {"control", "broker_init", "activation"}:
            result["model_called"]=False
        _record_health_best_effort(conn,result)
        print(json.dumps(result))
        return 0
    except Exception as exc:
        error_type=type(exc).__name__
        result={"status":"blocked","reason":"scenario_runtime_unavailable",
                "failure_stage":failure_stage,
                "error_type":error_type if error_type in _ERROR_TYPES else "Exception"}
        _record_health_best_effort(conn,result)
        if conn is not None:
            deliver_notices(conn,result)
        print(json.dumps(result))
        return 1
    finally:
        if conn is not None:
            conn.close()


if __name__=="__main__":
    raise SystemExit(main())
