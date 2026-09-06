"""Preregistered MA-convergence transition research, never strategy activation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np

from analysis.adaptive_signals import build_signal_tape
from analysis.mixture_statistics import bootstrap_max_error, summarise_nav
from analysis.strategy_mixture import (BAR, DAY, START, TRAIN, OOS, END, Budget,
    LedgerSink, ResourceLimit, canonical, digest, file_hash, load_inputs,
    recheck_inputs, validate_environment, write_json)
from analysis.transition_signals import build_transition_tapes
from backtest.adaptive_replay import AdaptiveConfig, run_adaptive

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT.parent/"docs/BTC_MA_TRANSITION_CONTRACT_2026-09-06_ko.md"
POLICIES = ("X0_F", "X1_F", "X1A_F", "X2_F", "X3_F", "X1_H", "X1_K", "X1_U",
            "X3_H", "X3_K", "X3_U", "S_F", "J_U")
PROFILES = ("F", "H", "K", "U")
PATHS = ("OHLC", "OLHC")
CELLS = tuple((c,d,p) for c in (1,2) for d in (5,30) for p in PATHS)
COMPARISONS = (("X1_F","X0_F"),("X1A_F","X1_F"),("X2_F","X1_F"),("X3_F","X2_F"),
               ("X1_K","X1_H"),("X1_U","X1_K"),("X3_K","X3_H"),("X3_U","X3_K"),
               ("X1_H","X1_F"),("X3_H","X3_F"),("X3_U","X0_F"),("J_U","S_F"))
SOURCES = ("analysis/transition_retest.py","analysis/transition_signals.py",
           "backtest/adaptive_replay.py","analysis/adaptive_signals.py",
           "analysis/strategy_mixture.py","analysis/mixture_statistics.py",
           "analysis/replay_data.py","core/scalp.py","core/swing.py",
           "engine/indicators.py","engine/config.py")


def planned_registry():
    rows=[]
    for phase,names,cells in (("TRAIN",POLICIES,tuple((1,5,p) for p in PATHS)),
                              ("OOS",POLICIES,CELLS),("PARTIAL",("X3_U","J_U"),CELLS)):
        for name in names:
            for cost,delay,path in cells:
                rows.append(dict(id=f"{phase}/{name}/c{cost}/d{delay}/{path}",phase=phase,
                                 name=name,cost=cost,delay=delay,path=path,status="PLANNED"))
    if len(rows)!=146 or len({r["id"] for r in rows})!=146:
        raise RuntimeError("invalid146 trial registry")
    return rows


def make_contract():
    return dict(version="ma-transition-v1",source_hashes={n:file_hash(ROOT/n) for n in SOURCES},
        specification_sha256=file_hash(DOC),portfolio_registry=planned_registry(),
        policies=POLICIES,comparisons=COMPARISONS,primary=["X3_U","X0_F"],
        periods=dict(start=START,train=TRAIN,oos=OOS,end_exclusive=END),
        paired=dict(variants=["X1","X3"],profiles=PROFILES,paths=PATHS,fixed_lots=20,risk_share=1),
        bootstrap=dict(columns=24,draws=2000,seed=20260906,block_days=30,method="joint centered-mean max-error"),
        resources=dict(seconds=7200,rss_bytes=2*1024**3,artifact_bytes=512*1024**2),
        selection="NONE",profitability_status="INSUFFICIENT",auto_activate=False)


def preregister(output):
    contract=make_contract()
    envelope=dict(contract=contract,contract_hash=digest(contract))
    write_json(Path(output)/"contract.json",envelope,immutable=True)
    write_json(Path(output)/"planned_registry.json",contract["portfolio_registry"],immutable=True)
    return envelope


def verify_contract(output):
    saved=json.loads((Path(output)/"contract.json").read_text())
    if digest(saved["contract"])!=saved["contract_hash"] or canonical(saved["contract"])!=canonical(make_contract()):
        raise ValueError("frozen contract or source mismatch")
    return saved


def context_hash(contexts):
    h=hashlib.sha256()
    for ts in sorted(contexts):
        h.update((canonical([int(ts),contexts[ts]])+"\n").encode())
    return h.hexdigest()


def prepare_tapes(bars):
    built=build_transition_tapes(bars)
    variants=built["variants"]
    expected={"X0","X1","X1A","X2","X3"}
    if set(variants)!=expected:
        raise ValueError("variant inventory mismatch")
    def entry_identity(signals):
        return digest([{k:v for k,v in s.items() if k!="risk_share"} for s in signals])
    if entry_identity(variants["X1"]["signals"])!=entry_identity(variants["X2"]["signals"]):
        raise ValueError("X1/X2 differ beyond risk_share")
    cache={}
    metadata={}
    for name,tape in variants.items():
        key=id(tape["contexts"])
        if key not in cache:
            cache[key]=context_hash(tape["contexts"])
        metadata[name]=dict(signal_count=len(tape["signals"]),signal_hash=digest(tape["signals"]),context_hash=cache[key])
    if metadata["X1"]["context_hash"]!=metadata["X2"]["context_hash"]:
        raise ValueError("X1/X2 context mismatch")
    older=build_signal_tape(bars)
    swing=dict(signals=[s for s in older["signals"] if s["lane"]=="S"],
               contexts={t:{"S":v["S"]} for t,v in older["contexts"].items() if "S" in v})
    joint_context={t:dict(v) for t,v in variants["X3"]["contexts"].items()}
    for ts,ctx in swing["contexts"].items():
        joint_context.setdefault(ts,{}).update(ctx)
    joint=dict(signals=sorted(swing["signals"]+variants["X3"]["signals"],
                             key=lambda s:(s["available_at"],s["lane"]!="S")),contexts=joint_context)
    return {**variants,"S":swing,"J":joint},dict(**built["metadata"],variants=metadata),built["episodes"]


def policy_config(row):
    variant,profile=row["name"].split("_")
    result=dict(start_ms=TRAIN if row["phase"]=="TRAIN" else OOS,
                end_ms=OOS if row["phase"]=="TRAIN" else END,
                lanes=("S",) if variant=="S" else ("S","C") if variant=="J" else ("C",),
                profile=profile,allocation="flex" if variant=="J" else "fixed",
                cost_multiple=row["cost"],timing_ms=row["delay"]*60_000,path=row["path"],
                partial=row["phase"]=="PARTIAL")
    if variant=="J":
        result.update(profile="F",lane_profiles=(("S","F"),("C","U")))
    return variant,result


def summarize(raw):
    if raw["counters"].get("policy_errors",0):
        raise ValueError("missing U policy inputs; retain evidence, do not treat as normal U")
    m=raw["metrics"]
    stats=summarise_nav([dict(timestamp_ms=int(t),nav=float(n)) for t,n in raw["daily_nav"]],
        start_ms=m["start_ms"],end_ms=m["end_ms"],initial_nav=m["initial_nav"],mtm_mdd=m["mtm_mdd"],
        completed_campaigns=m["completed_campaigns"],turnover=m["turnover"])
    if stats["status"]!="OK":
        raise ValueError("invalid daily statistics: "+str(stats))
    return {**raw,"statistics":stats,"status":"OK"}


def joint_bounds(results):
    columns={}
    dates=list(range(OOS+DAY,END+1,DAY))
    for path in PATHS:
        for a,b in COMPARISONS:
            lhs,rhs=(results[f"OOS/{name}/c1/d5/{path}"] for name in (a,b))
            if any([t for t,v in r["daily_nav"]]!=dates for r in (lhs,rhs)):
                raise ValueError("exact1096-day comparison calendar required")
            columns[f"{path}/{a}-{b}"]=(np.asarray(lhs["statistics"]["daily_returns"])-np.asarray(rhs["statistics"]["daily_returns"])).tolist()
    if len(columns)!=24:
        raise ValueError("entire24-column family required")
    ci=bootstrap_max_error({p:columns for p in PATHS},dates,require_full_period=False)
    if ci["status"]!="OK":
        raise ValueError("bootstrap failure")
    return dict(**ci["paths"]["OHLC"],columns=sorted(columns),draws=2000,seed=20260906,block_days=30,
                method="joint24_column_centered_mean_max_error")


def evaluate_gates(results,bounds):
    checks,growth,joint={},{},{}
    for path in PATHS:
        a,b=(results[f"OOS/{n}/c1/d5/{path}"]["statistics"] for n in ("X3_U","X0_F"))
        checks[path+":positive"]=a["total_return"]>0
        checks[path+":net_increase"]=a["total_return"]>b["total_return"]
        checks[path+":mdd_nonworse"]=a["mtm_mdd"]<=b["mtm_mdd"]
        checks[path+":campaigns60"]=a["completed_campaigns"]>=60
        checks[path+":adjusted_lower"]=bounds["lower_bounds"][path+"/X3_U-X0_F"]>0
        growth[path+":cagr20"]=a["cagr"]>=.2
        growth[path+":mdd20"]=a["mtm_mdd"]<=.2
        growth[path+":months55"]=a["positive_month_fraction"]>=.55
        growth[path+":three_years"]=len(a["yearly_returns"])==3 and all(v>0 for v in a["yearly_returns"].values())
        growth[path+":top5_removed"]=a["top5_removed_return"]>0
        growth[path+":top5_share"]=a["top5_share"] is not None and a["top5_share"]<=.5
        j,s=(results[f"OOS/{n}/c1/d5/{path}"]["statistics"] for n in ("J_U","S_F"))
        joint[path+":net_increase"]=j["total_return"]>s["total_return"]
        joint[path+":mdd_nonworse"]=j["mtm_mdd"]<=s["mtm_mdd"]
        joint[path+":adjusted_lower"]=bounds["lower_bounds"][path+"/J_U-S_F"]>0
    for cost,delay,path in CELLS:
        cell=f"c{cost}/d{delay}/{path}"
        full=results[f"OOS/X3_U/{cell}"]["statistics"]
        partial=results[f"PARTIAL/X3_U/{cell}"]["statistics"]
        checks[cell+":full_positive"]=full["total_return"]>0
        checks[cell+":full_mdd25"]=full["mtm_mdd"]<=.25
        checks[cell+":two_positive_years"]=sum(v>0 for v in full["yearly_returns"].values())>=2
        checks[cell+":partial_positive"]=partial["total_return"]>0
        checks[cell+":partial_mdd25"]=partial["mtm_mdd"]<=.25
    return dict(improvement_status="HISTORICAL_IMPROVEMENT" if all(checks.values()) else "NOT_PROVEN",
        high_growth_status="HISTORICAL_TARGET_MET" if all(growth.values()) else "NOT_MET",
        joint_status="HISTORICAL_IMPROVEMENT" if all(joint.values()) else "NOT_PROVEN",
        improvement_checks=checks,high_growth_checks=growth,joint_checks=joint,
        failed_checks=[k for k,v in checks.items() if not v],failed_growth_checks=[k for k,v in growth.items() if not v])


def paired_summary(records):
    by={}
    for r in records:
        key=(r["variant"],r["signal_id"],r["path"])
        if r["profile"] in by.setdefault(key,{}):
            raise ValueError("duplicate paired execution")
        by[key][r["profile"]]=r
    if any(set(v)!=set(PROFILES) for v in by.values()):
        raise ValueError("incomplete paired profiles")
    groups={}
    for variant in ("X1","X3"):
        for path in PATHS:
            for year in (2022,2023,2024,2025):
                cohorts=[v for (x,_,p),v in by.items() if x==variant and p==path and v["F"]["year"]==year]
                mismatches=sum(len({r["entry_identity"] for r in v.values()})!=1 for v in cohorts)
                matched=[v for v in cohorts if len({r["entry_identity"] for r in v.values()})==1 and v["F"]["entered_lots"]==20]
                groups[f"{variant}/{path}/{year}"]=dict(raw=len(cohorts),filled20=len(matched),mismatches=mismatches,
                    nofill=sum(v["F"]["entered_lots"]==0 for v in cohorts),
                    mean_net_r={p:float(np.mean([v[p]["net_r"] for v in matched])) if matched else None for p in PROFILES},
                    mean_delta={a+"-"+b:float(np.mean([v[a]["net_r"]-v[b]["net_r"] for v in matched])) if matched else None
                                for a,b in (("K","H"),("U","K"),("U","F"))})
    if any(g["mismatches"] for g in groups.values()):
        raise ValueError("paired initial fills differ")
    return dict(groups=groups,records=len(records),uncertainty="DESCRIPTIVE_ONLY_OVERLAPPING_COHORTS")


def run_experiment(output,market_db,execution_db):
    output=Path(output)
    envelope=verify_contract(output)
    validate_environment()
    if (output/"run_state.json").exists():
        raise ValueError("preserve existing attempt; use fresh output directory")
    profile=json.loads((output/"synthetic_profile.json").read_text())
    if (profile.get("kind")!="SYNTHETIC_NOT_MARKET" or profile.get("source_hashes")!=envelope["contract"]["source_hashes"]
            or not isinstance(profile.get("seconds"),(int,float)) or not math.isfinite(profile["seconds"]) or profile["seconds"]<=0):
        raise ValueError("matching synthetic profile required")
    budget=Budget(output,previous=profile["seconds"],limits=envelope["contract"]["resources"])
    rows,results,cohorts,paired=planned_registry(),{},[],[]
    state=dict(status="PREPARING",contract_hash=envelope["contract_hash"])
    try:
        bars,funding,data=load_inputs(market_db,execution_db)
        tapes,metadata,episodes=prepare_tapes(bars)
        manifest=dict(**data,transition=metadata)
        write_json(output/"data_manifest.json",manifest,immutable=True)
        write_json(output/"signals.json",{v:t["signals"] for v,t in tapes.items()},immutable=True)
        write_json(output/"episodes.json",episodes,immutable=True)
        for variant in ("X1","X3"):
            cohorts.extend(dict(variant=variant,signal=s,status="PLANNED") for s in tapes[variant]["signals"] if TRAIN<=s["available_at"]<END)
        write_json(output/"planned_cohorts.json",cohorts,immutable=True)
        state["data_hash"]=digest(manifest)
        write_json(output/"run_state.json",{**state,**budget.state()})
        for row in rows:
            budget(dict(trial=row["id"]))
            row["status"]="RUNNING"
            write_json(output/"registry.json",rows)
            directory=output/"trials"/row["id"]
            directory.mkdir(parents=True,exist_ok=False)
            variant,config=policy_config(row)
            sink=LedgerSink(directory/"events.jsonl.gz")
            try:
                def portfolio_guard(detail=None):
                    budget({**(detail or {}),"trial":row["id"]})
                raw=run_adaptive(bars,funding,tapes[variant]["signals"],tapes[variant]["contexts"],
                                 AdaptiveConfig(**config,resource_check=portfolio_guard),sink)
                result=summarize(raw)
            finally:
                sink.close()
            with (directory/"daily_nav.csv").open("w") as f:
                f.write("timestamp_ms,nav\n")
                for ts,value in result["daily_nav"]:
                    f.write(f"{int(ts)},{value:.17g}\n")
            row.update(status="OK",result_hash=digest(result),artifact_hashes={p.name:file_hash(p) for p in
                (directory/"daily_nav.csv",directory/"events.jsonl.gz")})
            write_json(directory/"result.json",result,immutable=True)
            results[row["id"]]={k:result[k] for k in ("metrics","counters","daily_nav","statistics","hashes")}
            write_json(output/"registry.json",rows)
            write_json(output/"run_state.json",{**state,**budget.state(),"status":"PORTFOLIO","completed":len(results)})
            print(canonical(dict(stage="portfolio",completed=len(results),total=len(rows))),flush=True)
        sink=LedgerSink(output/"paired_records.jsonl.gz")
        ctx_times={v:np.asarray(sorted(tapes[v]["contexts"]),dtype=np.int64) for v in ("X1","X3")}
        try:
            for i,cohort in enumerate(cohorts):
                cohort["status"]="RUNNING"
                variant=cohort["variant"]
                signal=dict(cohort["signal"],risk_share=1.0)
                start=signal["available_at"]
                end=min(END,start+signal["max_hold_ms"]+3*BAR)
                lo=max(0,int(np.searchsorted(bars[:,0],start))-1)
                hi=int(np.searchsorted(bars[:,0],end))
                fl,fh=np.searchsorted(funding[:,0],[start,end])
                times=ctx_times[variant]
                cl=max(0,int(np.searchsorted(times,start))-1)
                ch=int(np.searchsorted(times,end))
                ctx={int(t):tapes[variant]["contexts"][int(t)] for t in times[cl:ch]}
                for path in PATHS:
                    for profile_name in PROFILES:
                        checkpoint=dict(cohort=signal["signal_id"],variant=variant,path=path,profile=profile_name)
                        budget(checkpoint)
                        def cohort_guard(detail=None):
                            budget({**(detail or {}),**checkpoint})
                        raw=run_adaptive(bars[lo:hi],funding[fl:fh],[signal],ctx,AdaptiveConfig(start,end,lanes=("C",),
                            profile=profile_name,path=path,fixed_lots=20,resource_check=cohort_guard))
                        if raw["counters"].get("policy_errors",0):
                            raise ValueError("paired U missing inputs")
                        m=raw["metrics"]
                        record=dict(**checkpoint,signal_id=signal["signal_id"],year=time.gmtime(start/1000).tm_year,
                            available_at=start,net_pnl=m["final_nav"]-m["initial_nav"],
                            net_r=(m["final_nav"]-m["initial_nav"])/(.02*signal["stop_distance"]),
                            fees=m["fees"],slippage=m["slippage"],funding=m["funding"],
                            entry_identity=digest(raw["entry_fills"]),entered_lots=sum(f["lots"] for f in raw["entry_fills"]),
                            entry_fills=raw["entry_fills"],remaining_lots=sum(p["lots"] for p in raw["final_positions"].values()),hashes=raw["hashes"])
                        paired.append(record)
                        sink(record)
                cohort["status"]="OK"
                if (i+1)%100==0 or i+1==len(cohorts):
                    write_json(output/"cohort_registry.json",cohorts)
                    write_json(output/"run_state.json",{**state,**budget.state(),"status":"PAIRED","completed_cohorts":i+1})
                    print(canonical(dict(stage="paired",completed=i+1,total=len(cohorts))),flush=True)
        finally:
            sink.close()
        paired_stats=paired_summary(paired)
        bounds=joint_bounds(results)
        report=dict(status="COMPLETE",**evaluate_gates(results,bounds),bootstrap=bounds,paired=paired_stats,
            trial_summaries={k:dict(metrics=v["metrics"],counters=v["counters"],hashes=v["hashes"],
                statistics={a:b for a,b in v["statistics"].items() if a!="daily_returns"}) for k,v in results.items()},
            contract_hash=state["contract_hash"],data_hash=state["data_hash"],
            selection="NONE",profitability_status="INSUFFICIENT",auto_activate=False)
        recheck_inputs(market_db,execution_db,data)
        verify_contract(output)
        write_json(output/"paired_summary.json",paired_stats,immutable=True)
        write_json(output/"cohort_registry.json",cohorts)
        write_json(output/"report.json",report,immutable=True)
        write_json(output/"run_state.json",{**state,**budget.state(),"status":"COMPLETE"})
        budget.last_disk_check=-float("inf")
        budget()
        write_json(output/"run_state.json",{**state,**budget.state(),"status":"COMPLETE"})
        budget.last_disk_check=-float("inf")
        budget()
        return report
    except (Exception,KeyboardInterrupt) as exc:
        status="INCOMPLETE" if isinstance(exc,(ResourceLimit,KeyboardInterrupt)) else "INVALID_RESEARCH"
        for row in rows+cohorts:
            if row["status"]=="RUNNING":
                row.update(status=status,error=str(exc))
        failure=dict(status=status,error=type(exc).__name__+": "+str(exc),checkpoint=budget.checkpoint,
                     failure_state=getattr(exc,"state",None),profitability_status="INSUFFICIENT",auto_activate=False)
        write_json(output/"registry.json",rows)
        write_json(output/"cohort_registry.json",cohorts)
        write_json(output/"failure.json",failure)
        if "report" in locals():
            write_json(output/"report.json",{**report,**failure})
        write_json(output/"run_state.json",{**state,**budget.state(),**failure})
        raise


def synthetic_profile(output):
    begin=time.monotonic()
    ts=np.arange(START,TRAIN+DAY,BAR)
    x=30000+500*np.sin(np.arange(len(ts))/100)
    bars=np.column_stack((ts,x,x+30,x-30,x))
    tapes,metadata,_=prepare_tapes(bars)
    preparation=time.monotonic()-begin
    start=time.monotonic()
    raw=run_adaptive(bars,[],tapes["X3"]["signals"],tapes["X3"]["contexts"],AdaptiveConfig(TRAIN,TRAIN+DAY,lanes=("C",),profile="U"))
    record=dict(kind="SYNTHETIC_NOT_MARKET",seconds=time.monotonic()-begin,preparation_seconds=preparation,
        one_day_replay_seconds=time.monotonic()-start,metadata=metadata,hashes=raw["hashes"],source_hashes=make_contract()["source_hashes"])
    write_json(Path(output)/"synthetic_profile.json",record,immutable=True)
    return record


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--preregister-only",action="store_true")
    parser.add_argument("--profile-only",action="store_true")
    parser.add_argument("--market-db",type=Path)
    parser.add_argument("--execution-db",type=Path)
    args=parser.parse_args(argv)
    if args.preregister_only and args.profile_only:
        parser.error("separate preregistration and profile stages")
    if args.preregister_only:
        e=preregister(args.output_dir)
        print(canonical(dict(status="PREREGISTERED",contract_hash=e["contract_hash"],pnl_computed=False)))
    elif args.profile_only:
        validate_environment()
        print(canonical(synthetic_profile(args.output_dir)))
    else:
        if not args.market_db or not args.execution_db:
            parser.error("both read-only input DBs required")
        report=run_experiment(args.output_dir,args.market_db,args.execution_db)
        print(canonical(dict(status=report["status"],improvement=report["improvement_status"],growth=report["high_growth_status"])))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
