"""Frozen offline retest: paired exits and a separate joint-capital experiment.

No optimizer, network, production state, or activation path. Historical data has
already been observed. Execution and mark-price assumptions remain explicit.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time

import numpy as np

from analysis.adaptive_signals import build_signal_tape
from analysis.mixture_statistics import bootstrap_max_error, summarise_nav
from analysis.strategy_mixture import (
    BAR, DAY, START, TRAIN, OOS, END, Budget, LedgerSink, ResourceLimit,
    canonical, digest, file_hash, load_inputs, recheck_inputs, validate_environment,
    write_json,
)
from backtest.adaptive_replay import AdaptiveConfig, run_adaptive

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("F", "P", "Q", "H")
PATHS = ("OHLC", "OLHC")
POLICIES = tuple(f"{lane}_{profile}" for lane in ("S", "C", "J") for profile in PROFILES) + ("J_H_FLEX",)
CELLS = tuple((cost, timing, path) for cost in (1, 2) for timing in (5, 30) for path in PATHS)
COMPARISONS = tuple((f"{lane}_{a}", f"{lane}_{b}") for lane in ("S", "C", "J")
                    for a, b in (("P", "F"), ("Q", "P"), ("H", "Q"))) + (
                        ("J_H_FLEX", "J_H"), ("J_H_FLEX", "J_F"))
SOURCES = ("analysis/adaptive_retest.py", "analysis/adaptive_signals.py",
           "backtest/adaptive_replay.py", "analysis/strategy_mixture.py",
           "analysis/mixture_statistics.py", "analysis/replay_data.py",
           "core/scalp.py", "core/swing.py", "engine/config.py", "engine/indicators.py")
DOCUMENT = ROOT.parent / "docs/BTC_ADAPTIVE_RETEST_CONTRACT_2026-09-06_ko.md"


def planned_registry():
    records = []
    for phase, names, cells in (("TRAIN", POLICIES, ((1, 5, p) for p in PATHS)),
                               ("OOS", POLICIES, CELLS),
                               ("PARTIAL", ("J_F", "J_H_FLEX"), CELLS)):
        cells = tuple(cells)
        for name in names:
            for cost, timing, path in cells:
                records.append(dict(id=f"{phase}/{name}/c{cost}/d{timing}/{path}",
                    phase=phase, name=name, cost=cost, timing=timing, path=path, status="PLANNED"))
    if len(records) != 146 or len({r["id"] for r in records}) != 146:
        raise RuntimeError("registry must contain exactly146 unique IDs")
    return records


def make_contract():
    return dict(version="adaptive-retest-v1", source_hashes={p: file_hash(ROOT/p) for p in SOURCES},
        specification_sha256=file_hash(DOCUMENT), policies=POLICIES, portfolio_registry=planned_registry(),
        periods=dict(data_start=START, train_start=TRAIN, oos_start=OOS, end_exclusive=END),
        paired=dict(profiles=PROFILES, paths=PATHS, cost=1, timing_minutes=5, fixed_lots=20,
                    cohort="all raw S/C intents >=TRAIN and <END; same backend, no shared-capital aggregation"),
        comparisons=COMPARISONS, primary=["J_H_FLEX", "J_F"], selection="NONE_NO_AFTER_RESULT_TUNING",
        bootstrap=dict(columns=22, draws=2000, seed=20260906, block_days=30,
                       method="joint-path centered-mean max-error, within-year circular blocks, linear q95"),
        resources=dict(seconds=7200, rss_bytes=2*1024**3, artifact_bytes=512*1024**2),
        profitability_status="INSUFFICIENT", auto_activate=False)


def preregister(output):
    contract = make_contract()
    envelope = dict(contract=contract, contract_hash=digest(contract))
    write_json(Path(output)/"contract.json", envelope, immutable=True)
    write_json(Path(output)/"planned_registry.json", contract["portfolio_registry"], immutable=True)
    return envelope


def verify_contract(output):
    saved = json.loads((Path(output)/"contract.json").read_text())
    if digest(saved["contract"]) != saved["contract_hash"] or canonical(saved["contract"]) != canonical(make_contract()):
        raise ValueError("contract/source changed; preserve prior attempts and freeze a new output directory")
    return saved


def policy_config(row):
    prefix, profile, *_ = row["name"].split("_")
    return dict(start_ms=TRAIN if row["phase"] == "TRAIN" else OOS,
                end_ms=OOS if row["phase"] == "TRAIN" else END,
                lanes=("S", "C") if prefix == "J" else (prefix,), profile=profile,
                allocation="flex" if row["name"].endswith("FLEX") else "fixed",
                cost_multiple=row["cost"], timing_ms=row["timing"]*60_000,
                path=row["path"], partial=row["phase"] == "PARTIAL")


def summarize(result):
    m = result["metrics"]
    stats = summarise_nav([dict(timestamp_ms=int(t), nav=float(v)) for t, v in result["daily_nav"]],
                          start_ms=m["start_ms"], end_ms=m["end_ms"], initial_nav=m["initial_nav"],
                          mtm_mdd=m["mtm_mdd"], completed_campaigns=m["completed_campaigns"],
                          turnover=m["turnover"])
    if stats["status"] != "OK":
        raise ValueError("invalid daily statistics: " + str(stats["reason_codes"]))
    return {**result, "statistics": stats, "status": "OK"}


def joint_bounds(results):
    columns, timestamps = {}, None
    for path in PATHS:
        for a, b in COMPARISONS:
            left, right = (results[f"OOS/{n}/c1/d5/{path}"] for n in (a, b))
            lt, rt = ([x[0] for x in r["daily_nav"]] for r in (left, right))
            if lt != rt or (timestamps is not None and lt != timestamps):
                raise ValueError("asymmetric comparison calendars")
            if lt != list(range(OOS+DAY, END+1, DAY)):
                raise ValueError("bootstrap requires exact1096-day OOS calendar")
            timestamps = lt
            columns[f"{path}/{a}-{b}"] = (
                np.asarray(left["statistics"]["daily_returns"])-np.asarray(right["statistics"]["daily_returns"])).tolist()
    if len(columns) != 22:
        raise ValueError("must adjust the entire22-column preregistered family")
    # Both old API paths receive the same JOINT matrix: its maximum covers
    # all22 columns, not two independent11-column corrections.
    raw = bootstrap_max_error({path: columns for path in PATHS}, timestamps,
                              draws=2000, seed=20260906, block_days=30, require_full_period=False)
    if raw["status"] != "OK":
        raise ValueError("bootstrap failed: " + str(raw))
    return dict(**raw["paths"]["OHLC"], columns=sorted(columns), draws=2000,
                seed=20260906, block_days=30, method="joint22_column_centered_mean_max_error")


def evaluate_gates(results, bounds):
    checks, growth = {}, {}
    for path in PATHS:
        a, b = (results[f"OOS/{n}/c1/d5/{path}"]["statistics"] for n in ("J_H_FLEX", "J_F"))
        checks[path+":net_increase"] = a["total_return"] > b["total_return"]
        checks[path+":mdd_nonworse"] = a["mtm_mdd"] <= b["mtm_mdd"]
        checks[path+":campaigns60"] = a["completed_campaigns"] >= 60
        checks[path+":adjusted_lower_positive"] = bounds["lower_bounds"][path+"/J_H_FLEX-J_F"] > 0
        growth[path+":cagr20"] = a["cagr"] >= .20
        growth[path+":mdd20"] = a["mtm_mdd"] <= .20
        growth[path+":months55"] = a["positive_month_fraction"] >= .55
        growth[path+":all_years_positive"] = len(a["yearly_returns"]) == 3 and all(v>0 for v in a["yearly_returns"].values())
        growth[path+":top5_removed_positive"] = a["top5_removed_return"] > 0
        growth[path+":top5_share50"] = a["top5_share"] is not None and a["top5_share"] <= .5
    for cost, timing, path in CELLS:
        cell = f"c{cost}/d{timing}/{path}"
        full = results[f"OOS/J_H_FLEX/{cell}"]["statistics"]
        checks[cell+":full_positive"] = full["total_return"] > 0
        checks[cell+":full_mdd25"] = full["mtm_mdd"] <= .25
        checks[cell+":full_two_positive_years"] = sum(v>0 for v in full["yearly_returns"].values()) >= 2
        partial, base = (results[f"PARTIAL/{n}/{cell}"]["statistics"] for n in ("J_H_FLEX", "J_F"))
        checks[cell+":partial_positive"] = partial["total_return"] > 0
        checks[cell+":partial_mdd25"] = partial["mtm_mdd"] <= .25
        checks[cell+":partial_net_increase"] = partial["total_return"] > base["total_return"]
        checks[cell+":partial_mdd_nonworse"] = partial["mtm_mdd"] <= base["mtm_mdd"]
    return dict(improvement_status="HISTORICAL_IMPROVEMENT" if all(checks.values()) else "NOT_PROVEN",
                high_growth_status="HISTORICAL_TARGET_MET" if all(growth.values()) else "NOT_MET",
                improvement_checks=checks, high_growth_checks=growth,
                failed_checks=[k for k, v in checks.items() if not v],
                failed_growth_checks=[k for k, v in growth.items() if not v])


def cohort_plan(signals, contexts):
    exit_times = {direction: np.asarray([t for t, lanes in sorted(contexts.items())
                    if lanes.get("S", {}).get("exit_long" if direction == 1 else "exit_short")], dtype=np.int64)
                  for direction in (-1, 1)}
    result = []
    for signal in signals:
        ts = signal["available_at"]
        if not TRAIN <= ts < END:
            continue
        if signal["lane"] == "S":
            times = exit_times[signal["direction"]]
            index = int(np.searchsorted(times, ts, side="right"))
            end = int(times[index])+2*BAR if index < len(times) else END
        else:
            end = ts+signal["max_hold_ms"]+3*BAR  # entrydelay + closingdelay + terminalbar
        result.append(dict(signal=signal, end_ms=min(end, END), status="PLANNED"))
    return result


def cohort_inputs(bars, funding, contexts, signal, end_ms, context_times=None):
    ts = signal["available_at"]
    lo = max(0, int(np.searchsorted(bars[:,0], ts))-1)
    hi = int(np.searchsorted(bars[:,0], end_ms))
    f_lo, f_hi = np.searchsorted(funding[:,0], [ts, end_ms])
    # Include one preceding context to initialize current background, then all
    # updates in the horizon. Both lane contexts are emitted at each30m edge.
    times = np.asarray(sorted(contexts), dtype=np.int64) if context_times is None else context_times
    c_lo = max(0, int(np.searchsorted(times, ts))-1)
    c_hi = int(np.searchsorted(times, end_ms))
    selected = {int(t): contexts[int(t)] for t in times[c_lo:c_hi]}
    return bars[lo:hi], funding[f_lo:f_hi], selected


def paired_record(result, signal, profile, path):
    m = result["metrics"]
    return dict(signal_id=signal["signal_id"], lane=signal["lane"], available_at=signal["available_at"],
                profile=profile, path=path, year=time.gmtime(signal["available_at"]/1000).tm_year,
                net_pnl=m["final_nav"]-m["initial_nav"],
                net_r=(m["final_nav"]-m["initial_nav"])/(20*.001*signal["stop_distance"]),
                fees=m["fees"], funding=m["funding"], slippage=m["slippage"],
                completed_campaigns=m["completed_campaigns"],
                remaining_lots=sum(p["lots"] for p in result["final_positions"].values()),
                entry_identity=digest(result["entry_fills"]), entry_fills=result["entry_fills"],
                hashes=result["hashes"])


def paired_summary(records):
    groups = {}
    seen = set()
    for row in records:
        key = row["signal_id"], row["profile"], row["path"]
        if key in seen:
            raise ValueError("duplicate paired record")
        if row["profile"] not in PROFILES or row["path"] not in PATHS or row["lane"] not in ("S", "C") or row["year"] not in (2022, 2023, 2024, 2025):
            raise ValueError("invalid paired record grouping")
        seen.add(key)
    for lane in ("S", "C"):
        for path in PATHS:
            for year in (2022, 2023, 2024, 2025):
                selected = [r for r in records if r["lane"] == lane and r["path"] == path and r["year"] == year]
                by_id = {}
                for row in selected:
                    by_id.setdefault(row["signal_id"], {})[row["profile"]] = row
                if any(set(v) != set(PROFILES) for v in by_id.values()):
                    raise ValueError("incomplete paired cohort")
                matched = [v for v in by_id.values() if len({r["entry_identity"] for r in v.values()}) == 1]
                # A no-fill cohort is not evidence that the exit policies match.
                filled = [v for v in matched if sum(f["lots"] for f in v["F"]["entry_fills"]) == 20]
                groups[f"{lane}/{path}/{year}"] = dict(
                    raw_cohorts=len(by_id), matched_entries=len(matched), filled20=len(filled),
                    entry_mismatches=len(by_id)-len(matched),
                    mean_net_r={p:float(np.mean([v[p]["net_r"] for v in filled])) if filled else None for p in PROFILES},
                    mean_paired_delta={a+"-"+b:float(np.mean([v[a]["net_r"]-v[b]["net_r"] for v in filled])) if filled else None
                                       for a,b in (("P","F"),("Q","P"),("H","Q"),("H","F"))})
    return dict(groups=groups, records=len(records), uncertainty="DESCRIPTIVE_ONLY_OVERLAPPING_COHORTS",
                interpretation="Same entry/quantity conditional exits, not portfolio CAGR or independent samples")


def run_experiment(output, market_db, execution_db):
    output = Path(output)
    contract = verify_contract(output)
    validate_environment()
    if (output/"run_state.json").exists():
        raise ValueError("fresh run directory required; interrupted artifacts must be preserved")
    profile = json.loads((output/"synthetic_profile.json").read_text())
    if (profile.get("kind") != "SYNTHETIC_NOT_MARKET" or profile.get("source_hashes") != contract["contract"]["source_hashes"]
            or not isinstance(profile.get("seconds"), (int, float)) or not math.isfinite(profile["seconds"]) or profile["seconds"] <= 0):
        raise ValueError("run matching synthetic profile before historical PnL")
    budget = Budget(output, previous=profile["seconds"], limits=contract["contract"]["resources"])
    rows, results, paired = planned_registry(), {}, []
    state = dict(status="PREPARING", contract_hash=contract["contract_hash"])
    try:
        bars, funding, manifest = load_inputs(market_db, execution_db)
        tape = build_signal_tape(bars)
        manifest["signal_tape"] = dict(**tape["metadata"], signals_hash=digest(tape["signals"]),
                                        contexts_hash=digest(tape["contexts"]))
        write_json(output/"data_manifest.json", manifest, immutable=True)
        write_json(output/"signals.json", tape["signals"], immutable=True)
        cohorts = cohort_plan(tape["signals"], tape["contexts"])
        write_json(output/"planned_cohorts.json", cohorts, immutable=True)
        state["data_hash"] = digest(manifest)
        write_json(output/"run_state.json", {**state, **budget.state()})
        for row in rows:
            budget(dict(trial=row["id"]))
            row["status"] = "RUNNING"
            write_json(output/"registry.json", rows)
            directory = output/"trials"/row["id"]
            directory.mkdir(parents=True, exist_ok=False)
            sink = LedgerSink(directory/"events.jsonl.gz")
            try:
                def guard(checkpoint=None):
                    budget({**(checkpoint or {}), "trial":row["id"]})
                config = AdaptiveConfig(**policy_config(row), resource_check=guard)
                raw = run_adaptive(bars, funding, tape["signals"], tape["contexts"], config, sink)
                result = summarize(raw)
            finally:
                sink.close()
            with (directory/"daily_nav.csv").open("w") as stream:
                stream.write("timestamp_ms,nav\n")
                for ts, value in result["daily_nav"]:
                    stream.write(f"{int(ts)},{value:.17g}\n")
            hashes = {p.name:file_hash(p) for p in (directory/"events.jsonl.gz", directory/"daily_nav.csv")}
            row.update(status="OK", result_hash=digest(result), artifact_hashes=hashes)
            write_json(directory/"result.json", result, immutable=True)
            results[row["id"]] = {k:result[k] for k in ("metrics", "counters", "daily_nav", "statistics", "hashes")}
            write_json(output/"registry.json", rows)
            write_json(output/"run_state.json", {**state, **budget.state(), "status":"PORTFOLIO", "completed":len(results)})
            print(canonical(dict(progress="portfolio", completed=len(results), total=len(rows), trial=row["id"])), flush=True)
        sink = LedgerSink(output/"paired_records.jsonl.gz")
        context_times = np.asarray(sorted(tape["contexts"]), dtype=np.int64)
        try:
            for i, cohort in enumerate(cohorts):
                cohort["status"] = "RUNNING"
                signal, end_ms = cohort["signal"], cohort["end_ms"]
                budget(dict(cohort=signal["signal_id"]))
                bs, fs, ctx = cohort_inputs(bars, funding, tape["contexts"], signal, end_ms, context_times)
                for path in PATHS:
                    for profile in PROFILES:
                        checkpoint = dict(cohort=signal["signal_id"], path=path, profile=profile)
                        budget(checkpoint)
                        def guard(detail=None):
                            budget({**(detail or {}), **checkpoint})
                        config = AdaptiveConfig(signal["available_at"], end_ms, lanes=(signal["lane"],),
                                                profile=profile, path=path, fixed_lots=20, resource_check=guard)
                        raw = run_adaptive(bs, fs, [signal], ctx, config)
                        record = paired_record(raw, signal, profile, path)
                        paired.append(record)
                        sink(record)
                cohort["status"] = "OK"
                if (i+1) % 100 == 0 or i+1 == len(cohorts):
                    write_json(output/"cohort_registry.json", cohorts)
                    write_json(output/"run_state.json", {**state, **budget.state(), "status":"PAIRED", "completed_cohorts":i+1})
                    print(canonical(dict(progress="paired", completed=i+1, total=len(cohorts))), flush=True)
        finally:
            sink.close()
        matched = paired_summary(paired)
        write_json(output/"paired_summary.json", matched, immutable=True)
        if any(g["entry_mismatches"] for g in matched["groups"].values()):
            raise ValueError("paired entry identities differ; do not interpret exit deltas")
        bounds = joint_bounds(results)
        stats = {k:dict(metrics=v["metrics"], counters=v["counters"], hashes=v["hashes"],
                       statistics={a:b for a,b in v["statistics"].items() if a!="daily_returns"}) for k,v in results.items()}
        report = dict(status="COMPLETE", **evaluate_gates(results, bounds), bootstrap=bounds,
                      trial_summaries=stats, paired=matched, selection="NONE",
                      contract_hash=state["contract_hash"], data_hash=state["data_hash"],
                      profitability_status="INSUFFICIENT", auto_activate=False)
        recheck_inputs(market_db, execution_db, {k:v for k,v in manifest.items() if k != "signal_tape"})
        verify_contract(output)
        budget.last_disk_check = -float("inf")
        budget()
        write_json(output/"report.json", report, immutable=True)
        write_json(output/"cohort_registry.json", cohorts)
        write_json(output/"run_state.json", {**state, **budget.state(), "status":"COMPLETE"})
        budget.last_disk_check = -float("inf")
        budget()
        write_json(output/"run_state.json", {**state, **budget.state(), "status":"COMPLETE"})
        budget.last_disk_check = -float("inf")
        budget()  # Include the final metadata write; no writes after this check.
        return report
    except (Exception, KeyboardInterrupt) as exc:
        status = "INCOMPLETE" if isinstance(exc, (ResourceLimit, KeyboardInterrupt)) else "INVALID_RESEARCH"
        running = [r for r in rows if r["status"] == "RUNNING"]
        for row in running:
            row.update(status=status, error=str(exc))
        write_json(output/"registry.json", rows)
        if "cohorts" in locals():
            for cohort in cohorts:
                if cohort["status"] == "RUNNING":
                    cohort.update(status=status, error=str(exc))
            write_json(output/"cohort_registry.json", cohorts)
        failure = dict(status=status, error=type(exc).__name__+": "+str(exc),
                       failure_state=getattr(exc,"state",None), checkpoint=budget.checkpoint,
                       profitability_status="INSUFFICIENT", auto_activate=False)
        write_json(output/"failure.json", failure)
        if "report" in locals():
            write_json(output/"report.json", {**report, **failure})
        write_json(output/"run_state.json", {**state, **budget.state(), **failure})
        raise


def synthetic_profile(output):
    """Fixed synthetic30-day resource smoke; no market/history performance."""
    ts = np.arange(TRAIN, TRAIN+30*DAY, BAR)
    x = 30_000+500*np.sin(np.arange(len(ts))/100)
    bars = np.column_stack((ts,x,x+30,x-30,x))
    signal = dict(signal_id="SYNTHETIC", lane="C", available_at=TRAIN, direction=1,
                  reference_price=30000., stop_distance=300., strong=True, max_hold_ms=7_200_000)
    begin = time.monotonic()
    result = run_adaptive(bars, [], [signal], {}, AdaptiveConfig(TRAIN, TRAIN+30*DAY, profile="H", allocation="flex"))
    elapsed = time.monotonic()-begin
    record = dict(kind="SYNTHETIC_NOT_MARKET", seconds=elapsed, hashes=result["hashes"],
                  source_hashes=make_contract()["source_hashes"],
                  portfolio_projected_seconds=elapsed*(26*275+120*1096)/30,
                  projection_excludes_signal_prep_and_paired=True)
    write_json(Path(output)/"synthetic_profile.json", record, immutable=True)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--preregister-only", action="store_true")
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--market-db", type=Path)
    parser.add_argument("--execution-db", type=Path)
    args = parser.parse_args(argv)
    if args.preregister_only and args.profile_only:
        parser.error("preregister/profile are separate stages")
    if args.preregister_only:
        envelope = preregister(args.output_dir)
        print(canonical(dict(status="PREREGISTERED", contract_hash=envelope["contract_hash"], pnl_computed=False)))
    elif args.profile_only:
        validate_environment()
        print(canonical(synthetic_profile(args.output_dir)))
    else:
        if not args.market_db or not args.execution_db:
            parser.error("both read-only source databases required")
        report = run_experiment(args.output_dir, args.market_db, args.execution_db)
        print(canonical(dict(status=report["status"], improvement=report["improvement_status"], high_growth=report["high_growth_status"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
