"""POST-HOC execution-assumption sensitivity; never overrides the main study."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis.strategy_mixture import (Budget, LedgerSink, ResourceLimit, canonical,
    digest, file_hash, load_inputs, recheck_inputs, validate_environment, write_json)
from analysis.transition_retest import ROOT, SOURCES, prepare_tapes, policy_config, summarize
from backtest.adaptive_replay import AdaptiveConfig, run_adaptive

NAMES=("X0_F","X3_U","X4_U","J4_U")
PATHS=("OHLC","OLHC")
DOC=ROOT.parent/"docs/BTC_MA_ENTRY_LATENCY_DIAGNOSTIC_2026-09-06_ko.md"
REFERENCE_CONTRACT_HASH="f808b0188d0ef7d61a11b253af5de1787f5e50d7d23baf4469282eba6748e2ca"


def registry():
    return [dict(id=f"{name}/c{cost}/{path}/entry{delay}",name=name,cost=cost,path=path,
                 entry_delay=delay,status="PLANNED")
            for name in NAMES for cost in (1,2) for path in PATHS for delay in (5,0)]


def verify_reference(reference):
    reference=Path(reference)
    envelope=json.loads((reference/"contract.json").read_text())
    report=json.loads((reference/"report.json").read_text())
    state=json.loads((reference/"run_state.json").read_text())
    manifest=json.loads((reference/"data_manifest.json").read_text())
    rows=json.loads((reference/"registry.json").read_text())
    if (envelope["contract_hash"]!=REFERENCE_CONTRACT_HASH or digest(envelope["contract"])!=envelope["contract_hash"]
            or report.get("status")!="COMPLETE" or state.get("status")!="COMPLETE"
            or report.get("contract_hash")!=envelope["contract_hash"] or state.get("contract_hash")!=envelope["contract_hash"]
            or report.get("data_hash")!=digest(manifest) or state.get("data_hash")!=digest(manifest)):
        raise ValueError("original completed study linkage invalid")
    if len(rows)!=192 or len({r["id"] for r in rows})!=192 or any(r["status"]!="OK" for r in rows):
        raise ValueError("original registry inventory/status invalid")
    by_id={r["id"]:r for r in rows}
    controls={}
    for r in registry():
        if r["entry_delay"]!=5:
            continue
        key=f"OOS/{r['name']}/c{r['cost']}/d5/{r['path']}"
        original=json.loads((reference/f"trials/{key}/result.json").read_text())
        if key not in by_id or digest(original)!=by_id[key]["result_hash"]:
            raise ValueError("original control result hash mismatch")
        controls[key]=by_id[key]["result_hash"]
    return manifest,controls


def contract(reference):
    reference=Path(reference)
    verify_reference(reference)
    return dict(kind="POST_HOC_EXECUTION_ASSUMPTION_SENSITIVITY",version=1,
        source_hashes={name:file_hash(ROOT/name) for name in (*SOURCES,"analysis/entry_latency_diagnostic.py")},
        specification_sha256=file_hash(DOC),
        reference_hashes={name:file_hash(reference/name) for name in ("contract.json","registry.json","report.json","data_manifest.json","signals.json")},
        reference_source="3542b40fd21088fc017ea38afe16bf3c9c4079cd",registry=registry(),
        resources=dict(seconds=1800,rss_bytes=2*1024**3,artifact_bytes=128*1024**2),
        other_timing_minutes=5,entry_assumption="ideal next-open, not observed ticks or a return upper bound",
        selection="NONE",use_for_promotion=False,auto_activate=False,profitability_status="INSUFFICIENT")


def preregister(output,reference):
    value=contract(reference)
    saved=dict(contract=value,contract_hash=digest(value))
    write_json(Path(output)/"contract.json",saved,immutable=True)
    write_json(Path(output)/"planned_registry.json",registry(),immutable=True)
    return saved


def run(output,reference,market_db,execution_db):
    output,reference=Path(output),Path(reference)
    saved=json.loads((output/"contract.json").read_text())
    if saved["contract_hash"]!=digest(saved["contract"]) or canonical(saved["contract"])!=canonical(contract(reference)):
        raise ValueError("diagnostic source/reference changed")
    if (output/"run_state.json").exists():
        raise ValueError("fresh diagnostic directory required")
    validate_environment()
    budget=Budget(output,limits=saved["contract"]["resources"])
    rows,results=registry(),{}
    try:
        original_manifest,controls=verify_reference(reference)
        bars,funding,data=load_inputs(market_db,execution_db)
        tapes,metadata,_=prepare_tapes(bars)
        manifest=dict(**data,transition=metadata)
        if digest(manifest)!=digest(original_manifest):
            raise ValueError("data/indicator identity differs from original study")
        original_signals=json.loads((reference/"signals.json").read_text())
        for name,tape in tapes.items():
            if digest(tape["signals"])!=digest(original_signals[name]):
                raise ValueError("diagnostic changed input signals")
        write_json(output/"data_manifest.json",manifest,immutable=True)
        write_json(output/"run_state.json",dict(status="RUNNING",**budget.state()))
        for row in rows:
            row["status"]="RUNNING"
            write_json(output/"registry.json",rows)
            budget(dict(trial=row["id"]))
            variant,cfg=policy_config(dict(phase="OOS",name=row["name"],cost=row["cost"],delay=5,path=row["path"]))
            if row["entry_delay"]==0:
                cfg["entry_latency_ms"]=0
            directory=output/"trials"/row["id"]
            directory.mkdir(parents=True,exist_ok=False)
            sink=LedgerSink(directory/"events.jsonl.gz")
            try:
                def diagnostic_guard(detail=None):
                    budget({**(detail or {}),"trial":row["id"]})
                raw=run_adaptive(bars,funding,tapes[variant]["signals"],tapes[variant]["contexts"],
                    AdaptiveConfig(**cfg,resource_check=diagnostic_guard),sink)
                result=summarize(raw)
            finally:
                sink.close()
            if row["entry_delay"]==5:
                key=f"OOS/{row['name']}/c{row['cost']}/d5/{row['path']}"
                if digest(result)!=controls[key]:
                    raise ValueError("baseline financial result changed")
            write_json(directory/"result.json",result,immutable=True)
            with (directory/"daily_nav.csv").open("w") as stream:
                stream.write("timestamp_ms,nav\n")
                for ts,nav in result["daily_nav"]:
                    stream.write(f"{int(ts)},{nav:.17g}\n")
            row.update(status="OK",result_hash=digest(result),artifact_hashes={p.name:file_hash(p) for p in
                (directory/"daily_nav.csv",directory/"events.jsonl.gz")})
            results[row["id"]]=dict(metrics=result["metrics"],counters=result["counters"],hashes=result["hashes"],
                statistics={k:v for k,v in result["statistics"].items() if k!="daily_returns"})
            write_json(output/"registry.json",rows)
            write_json(output/"run_state.json",dict(status="RUNNING",completed=len(results),**budget.state()))
            print(canonical(dict(stage="post_hoc_latency",completed=len(results),total=32)),flush=True)
        recheck_inputs(market_db,execution_db,data)
        if canonical(saved["contract"])!=canonical(contract(reference)):
            raise ValueError("diagnostic source/reference changed during execution")
        report=dict(status="COMPLETE",kind=saved["contract"]["kind"],contract_hash=saved["contract_hash"],
                    baseline_matches=16,ideal_entry_cases=16,trial_summaries=results,
                    main_verdict_unchanged=True,selection="NONE",use_for_promotion=False,auto_activate=False,
                    profitability_status="INSUFFICIENT")
        write_json(output/"report.json",report,immutable=True)
        write_json(output/"run_state.json",dict(status="COMPLETE",**budget.state()))
        budget.last_disk_check=-float("inf")
        budget()
        write_json(output/"run_state.json",dict(status="COMPLETE",**budget.state()))
        budget.last_disk_check=-float("inf")
        budget()
        return report
    except (Exception,KeyboardInterrupt) as exc:
        status="INCOMPLETE" if isinstance(exc,(ResourceLimit,KeyboardInterrupt)) else "INVALID_DIAGNOSTIC"
        for row in rows:
            if row["status"]=="RUNNING":
                row.update(status=status,error=str(exc))
        failure=dict(status=status,error=type(exc).__name__+": "+str(exc),checkpoint=budget.checkpoint,
                     failure_state=getattr(exc,"state",None),use_for_promotion=False,auto_activate=False,
                     profitability_status="INSUFFICIENT")
        write_json(output/"registry.json",rows)
        write_json(output/"failure.json",failure)
        if "report" in locals():
            write_json(output/"report.json",{**report,**failure})
        write_json(output/"run_state.json",dict(**failure,**budget.state()))
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--reference-dir",type=Path,required=True)
    parser.add_argument("--preregister-only",action="store_true")
    parser.add_argument("--market-db",type=Path)
    parser.add_argument("--execution-db",type=Path)
    args=parser.parse_args(argv)
    if args.preregister_only:
        saved=preregister(args.output_dir,args.reference_dir)
        print(canonical(dict(status="REGISTERED_POST_HOC_DIAGNOSTIC",contract_hash=saved["contract_hash"],pnl_computed=False)))
    else:
        if not args.market_db or not args.execution_db:
            parser.error("both source databases required")
        result=run(args.output_dir,args.reference_dir,args.market_db,args.execution_db)
        print(canonical(dict(status=result["status"],kind=result["kind"],use_for_promotion=False)))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
