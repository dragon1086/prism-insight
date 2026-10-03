"""Explicit opt-in DEMO MAIN scenario entrypoint. Never edits cron/legacy policy.

Activation requires a verified broker adapter and a separately verified legacy
handoff. No implicit fallback to the old strategy or to real-money credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from live.scenario_llm import propose
from live.scenario_preview import collect_snapshot, response_contract
from live.scenario_runtime import ScenarioRuntime
from live.shared_entry_coordinator import mutation_lock


def run_once(conn, broker, *, execute=False, protect_only=False,
             snapshot=collect_snapshot, proposal=propose):
    if getattr(broker,"environment",None)!="demo" or getattr(broker,"lane",None)!="MAIN":
        return {"status":"blocked","reason":"demo_main_required"}
    if protect_only:
        try:
            with mutation_lock(conn):
                evidence=broker.reconcile()
            if not isinstance(evidence,dict) or evidence.get("protection_confirmed") is not True:
                return {"status":"blocked","reason":"protection_not_verified_by_scenario_adapter","model_called":False}
            return {"status":"protection_checked","model_called":False}
        except Exception:
            return {"status":"blocked","reason":"protection_unconfirmed","model_called":False}
    if not execute:
        return {"status":"execution_disabled","reason":"explicit_activation_required"}
    runtime=ScenarioRuntime(conn,broker,
        lambda snap,ctx:proposal(snap,ctx,response_contract(ctx)),snapshot)
    return runtime.tick()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument("--execute",action="store_true")
    modes.add_argument("--protect-only",action="store_true")
    parser.add_argument("--root-db",type=Path,
                        default=Path(__file__).resolve().parents[2]/"stock_tracking_db.sqlite")
    args=parser.parse_args()
    if not args.execute and not args.protect_only:
        print(json.dumps({"status":"execution_disabled","reason":"explicit_activation_required"}))
        return 0
    identity=os.environ.get("PRISM_BTC_MAIN_UID","")
    if not identity.isascii() or not identity.isdigit() or int(identity)<=0:
        print(json.dumps({"status":"blocked","reason":"verified_main_binding_required"}))
        return 1
    # Fail without creating a different DB when a production path is wrong.
    if not args.root_db.is_file():
        print(json.dumps({"status":"blocked","reason":"existing_root_database_required"}))
        return 1
    conn=sqlite3.connect(f"file:{args.root_db}?mode=rw",uri=True,timeout=10)
    conn.row_factory=sqlite3.Row
    try:
        from live.scenario_broker import ScenarioDemoBroker
        broker=ScenarioDemoBroker(conn,expected_main_uid=identity)
        result=run_once(conn,broker,execute=args.execute,protect_only=args.protect_only)
        # Disabled adapters cannot produce trade notices. Delivery occurs only
        # after the runtime released the trading lock and only for queued proofs.
        if result["status"] not in {"blocked","execution_disabled"}:
            from live.scenario_outbox import flush
            result["notice_delivery"]=flush(conn)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result["status"] in {"blocked","execution_disabled"} else 0
    except Exception:
        print(json.dumps({"status":"blocked","reason":"scenario_runtime_unavailable"}))
        return 1
    finally:
        conn.close()


if __name__=="__main__":
    raise SystemExit(main())
