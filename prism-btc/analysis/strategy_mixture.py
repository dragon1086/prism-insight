"""Preregistered, bounded historical research; never a live strategy or promotion.

One shared collateral account, fixed T/B/R policies, actual-state decisions.
Historical OOS has already been observed. No TP/trailing or production parity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import sqlite3
import sys
import time
from types import MappingProxyType

import numpy as np
import pandas as pd

from analysis.replay_data import load_bars, load_funding
from backtest.portfolio_replay import DailyFeatures, Decision, PortfolioPolicy, run_portfolio

DAY = 86_400_000
BAR = 300_000
PATHS = ("OHLC", "OLHC")
MIXES = MappingProxyType({
    "M01": (.5, .5, 0.), "M02": (.5, 0., .5), "M03": (0., .5, .5),
    "M04": (1/3, 1/3, 1/3), "M05": (.5, .25, .25),
    "M06": (.25, .5, .25), "M07": (.25, .25, .5),
})
POLICIES = MappingProxyType({**MIXES, "T": (1., 0., 0.), "B": (0., 1., 0.),
                            "R": (0., 0., 1.), "BH": (1., 0., 0.), "cash": (0., 0., 0.)})
CELLS = tuple((cost, delay, path) for cost in (1, 2) for delay in (5, 30) for path in PATHS)
BASE_CELLS = tuple((1, 5, path) for path in PATHS)
SOURCE_FILES = ("analysis/strategy_mixture.py", "analysis/mixture_statistics.py",
                "analysis/replay_data.py", "backtest/portfolio_replay.py")


def utc_ms(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp() * 1000)


START, TRAIN, OOS, END = map(utc_ms, ("2022-01-01", "2022-04-01", "2023-01-01", "2026-01-01"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value, *, immutable=False):
    """Runtime artifact writer; immutable files may only be reused byte-equivalently."""
    path = Path(path)
    data = canonical(value) + "\n"
    if immutable and path.exists():
        if path.read_text() != data:
            raise ValueError(f"immutable artifact mismatch: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        with path.open("x") as stream:
            stream.write(data)
    else:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data)
        tmp.replace(path)


def planned_registry():
    rows = []
    for phase, names, cells in (("TRAIN", POLICIES, BASE_CELLS), ("OOS_RAW", POLICIES, CELLS),
                               ("OOS_MATCHED", [f"{m}-{r}" for m in MIXES for r in ("single", "BH")], CELLS),
                               ("OOS_PARTIAL", ("FROZEN",), CELLS)):
        for name in names:
            for cost, delay, path in cells:
                fill = "partial50" if phase == "OOS_PARTIAL" else "full"
                trial_id = f"{phase}/{name}/c{cost}/d{delay}/{path}/{fill}"
                rows.append(dict(id=trial_id, phase=phase, name=name, cost=cost, delay=delay,
                                 path=path, fill=fill, status="PLANNED"))
    if len(rows) != 240 or len({r["id"] for r in rows}) != 240:
        raise RuntimeError("registered trial matrix must contain 240 unique IDs")
    return rows


def make_contract():
    root = Path(__file__).resolve().parents[1]
    source = {name: file_hash(root / name) for name in SOURCE_FILES}
    return dict(version="strategy-mixture-v2", pinned_base="47a821ab260f63b8881995c2c754bd0ebcef7ac3",
                pinned_base_tree="10299df0bf8560a18b7a4cb3ab6961ed25bfbe67", source_hashes=source,
                catalogue={k: list(v) for k, v in POLICIES.items()},
                periods=dict(warmup_start=START, train_start=TRAIN, oos_start=OOS, end_exclusive=END),
                indicators=dict(warmup_days=90, sma=[20, 60], atr_simple=14, std_ddof=0,
                                breakout_prior=[20, 10], range_atr=1, entry_z=1.5),
                execution=dict(initial_nav=10000., gross_cap=1., heat_cap=.02, lot=.001,
                               stop_atr=2, fee=.00055, slippage=.0005, cells=CELLS,
                               partial_entries_only=.5, partial_ttl_ms=1_800_000,
                               bh="gross-capped, stop/heat-exempt; no voluntary rebalance"),
                selection="2022 only: worst path log growth - 2*MDD; positive net and >=12 campaigns both paths; ID tie",
                matching="min(1, train sample-vol ratio OHLC, train sample-vol ratio OLHC); quantity replay",
                bootstrap=dict(draws=2000, seed=20260906, block_days=30, columns_per_path=14,
                               method="unstudentized centered-mean max-error", quantile=.95),
                gates=dict(cagr=.20, mdd=.20, positive_month_fraction=.55, campaigns=60,
                           positive_years=3, stress_mdd=.25, stress_positive_years=2,
                           top5_share=.5, adjusted_lower_strictly_positive=True),
                resources=dict(seconds=7200, rss_bytes=2*1024**3, artifact_bytes=512*1024**2,
                               two_pass_seconds=14400),
                trace_days=["2022-04-02", "2023-01-02", "2025-01-02"], trace_rows_per_day=5000,
                limitations=["ALREADY_OBSERVED_HISTORY", "NO_M5_FORWARD", "NO_PRODUCTION_PARITY",
                             "NO_TP_OR_TRAILING", "LAST_TRADE_NOT_MARK", "HYPOTHETICAL_OHLC_PATH",
                             "UNLIMITED_LIQUIDITY_MODEL", "NOT_USER_LIVE_RISK_CONSENT"],
                profitability_status="INSUFFICIENT", auto_activate=False,
                environment=dict(python="3.12", pandas="2.2.3", numpy="2.2.6"),
                registry=planned_registry())


def preregister(output):
    contract = make_contract()
    envelope = dict(contract=contract, contract_hash=digest(contract))
    write_json(Path(output) / "contract.json", envelope, immutable=True)
    write_json(Path(output) / "planned_registry.json", contract["registry"], immutable=True)
    return envelope


def verify_contract(path):
    envelope = json.loads(Path(path).read_text())
    expected = make_contract()
    if envelope.get("contract_hash") != digest(envelope.get("contract")) or canonical(envelope.get("contract")) != canonical(expected):
        raise ValueError("contract/source hash mismatch; preregister before computing PnL")
    return envelope


def build_daily_features(bars):
    """Complete UTC 5m arrays only; features become available at the right edge.

    No virtual held state or targets are stored. Previous-channel windows exclude
    the current daily candle. The first decision follows 90 completed days.
    """
    data = np.asarray(bars, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != 5 or not len(data) or len(data) % 288:
        raise ValueError("incomplete daily OHLC coverage")
    if not np.isfinite(data).all() or data[0, 0] % DAY or np.any(np.diff(data[:, 0]) != BAR):
        raise ValueError("nonfinite or noncontiguous UTC 5m bars")
    if np.any(data[:, 1:] <= 0) or np.any(data[:, 3] > data[:, 1]) or np.any(data[:, 3] > data[:, 4]) or np.any(data[:, 2] < data[:, 1]) or np.any(data[:, 2] < data[:, 4]):
        raise ValueError("invalid OHLC range")
    days = data.reshape(-1, 288, 5)
    high, low, close = days[:, :, 2].max(1), days[:, :, 3].min(1), days[:, -1, 4]
    previous = np.r_[days[0, 0, 1], close[:-1]]
    tr = np.maximum(high-low, np.maximum(abs(high-previous), abs(low-previous)))
    result = {}
    for i in range(89, len(days)):
        available = int(days[i, 0, 0]) + DAY
        result[available] = DailyFeatures(available_at=available,
            sma20=float(np.mean(close[i-19:i+1])), sma60=float(np.mean(close[i-59:i+1])),
            atr14=float(np.mean(tr[i-13:i+1])), std20=float(np.std(close[i-19:i+1], ddof=0)),
            prior_high20=float(max(high[i-20:i])), prior_low20=float(min(low[i-20:i])),
            prior_high10=float(max(high[i-10:i])), prior_low10=float(min(low[i-10:i])), close=float(close[i]))
    return result


def decide_T(actual_direction, f):
    return int(f.sma20 > f.sma60) - int(f.sma20 < f.sma60)


def decide_B(actual_direction, f):
    if f.close > f.prior_high20:
        return 1
    if f.close < f.prior_low20:
        return -1
    if actual_direction == 1 and f.close < f.prior_low10 or actual_direction == -1 and f.close > f.prior_high10:
        return 0
    return actual_direction


def decide_R(actual_direction, f):
    if f.std20 == 0 or abs(f.sma20-f.sma60) > f.atr14:
        return 0
    z = (f.close-f.sma20)/f.std20
    if actual_direction:
        return 0 if actual_direction*z >= 0 else actual_direction
    return 1 if z <= -1.5 else -1 if z >= 1.5 else 0


def callbacks(policy_id):
    return {name: (lambda held, features, fn=fn, w=w: Decision(fn(held, features), w))
            for name, fn, w in zip(("T", "B", "R"), (decide_T, decide_B, decide_R), POLICIES[policy_id]) if w}


def select_train_candidate(train_results):
    """Accept exactly the 24 TRAIN records. OOS cannot enter the selector API."""
    expected = {r["id"] for r in planned_registry() if r["phase"] == "TRAIN"}
    if set(train_results) != expected:
        raise ValueError("selector requires exactly TRAIN24, never OOS results")
    scores, eligible = {}, []
    for name in POLICIES:
        pair = [train_results[f"TRAIN/{name}/c1/d5/{path}/full"] for path in PATHS]
        if not all(r.get("status") == "OK" for r in pair):
            continue
        metrics = [r["metrics"] for r in pair]
        if not all(math.isfinite(m["final_nav"]) and m["final_nav"] > 0 and math.isfinite(m["mtm_mdd"]) for m in metrics):
            continue
        scores[name] = min(math.log(m["final_nav"]/m["initial_nav"]) - 2*m["mtm_mdd"] for m in metrics)
        if name in MIXES and all(m["final_nav"] > m["initial_nav"] and m["completed_campaigns"] >= 12 for m in metrics):
            eligible.append(name)
    def rank(names):
        return min(names, key=lambda n: (-scores[n], n)) if names else None
    selected = rank(eligible)
    diagnostic = selected or rank([n for n in MIXES if n in scores]) or min(MIXES)
    return dict(selected_id=selected, diagnostic_id=diagnostic,
                single_id=rank([n for n in ("T", "B", "R") if n in scores]), scores=scores,
                eligible_ids=sorted(eligible), selection_period=[TRAIN, OOS])


def validate_environment():
    if sys.version_info[:2] != (3, 12) or pd.__version__ != "2.2.3" or np.__version__ != "2.2.6":
        raise ValueError("use documented .venv-bt Python3.12/pandas2.2.3/numpy2.2.6")
    from analysis.scalp_replay_benchmark import checked_indicators
    x = np.arange(40000, dtype=float) + 100.
    checked_indicators(pd.DataFrame(dict(open=x, high=x+1, low=x-1, close=x, volume=x*0)))
    return dict(python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__)


def load_inputs(market_db, execution_db, *, provenance=None):
    """Identity is the selected normalized history, never a live DB's whole file."""
    before = {str(Path(p).resolve()): file_hash(p) for p in (market_db, execution_db)}
    loaded = load_bars(execution_db, "5m", START, END)
    bars = np.asarray([(r["open_time"], r["open"], r["high"], r["low"], r["close"]) for r in loaded.rows])
    bar_manifest = loaded.manifest
    del loaded
    loaded = load_funding(market_db, START, END, 8*60*60_000)
    funding = np.asarray([(r["funding_time"], r["rate"]) for r in loaded.rows])
    fund_manifest = loaded.manifest
    del loaded
    if provenance is not None:
        provenance.update(source_files_before_load=before,
                          source_files_after_load={p: file_hash(p) for p in before})
    return bars, funding, dict(bars=bar_manifest, funding=fund_manifest)


def recheck_inputs(market_db, execution_db, expected):
    """Re-read the same strict slices and release each loader's large rows at once."""
    observed = dict(bars=load_bars(execution_db, "5m", START, END).manifest,
                    funding=load_funding(market_db, START, END, 8*60*60_000).manifest)
    if digest(observed) != digest(expected):
        raise ValueError("SELECTED_HISTORY_CHANGED_DURING_RUN")
    return digest(observed)


class ResourceLimit(RuntimeError):
    pass


class Budget:
    def __init__(self, output, previous=0., limits=None):
        self.output, self.previous, self.started = Path(output), previous, time.monotonic()
        self.limits = limits or dict(seconds=7200, rss_bytes=2*1024**3, artifact_bytes=512*1024**2)
        self.last_disk_check, self.disk_bytes = -math.inf, 0
        self.checkpoint = {}

    def state(self):
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return dict(elapsed_seconds=self.previous + time.monotonic()-self.started,
                    peak_rss_bytes=int(rss if sys.platform == "darwin" else rss*1024), artifact_bytes=self.disk_bytes)

    def __call__(self, checkpoint=None):
        self.checkpoint = checkpoint or self.checkpoint
        now = time.monotonic()
        if now-self.last_disk_check > 2:
            self.disk_bytes = sum(p.stat().st_size for p in self.output.rglob("*") if p.is_file())
            self.last_disk_check = now
        state = self.state()
        if state["elapsed_seconds"] >= self.limits["seconds"] or state["peak_rss_bytes"] >= self.limits["rss_bytes"] or state["artifact_bytes"] >= self.limits["artifact_bytes"]:
            raise ResourceLimit("pass budget exceeded")


class LedgerSink:
    def __init__(self, path):
        self.raw = Path(path).open("wb")
        self.stream = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)

    def __call__(self, event):
        self.stream.write((canonical(event)+"\n").encode())

    def close(self):
        self.stream.close()
        self.raw.close()


def finalize_recheck(output, report, manifest, market_db, execution_db, provenance, *, limits=None):
    state_path = Path(output)/"run_state.json"
    state = json.loads(state_path.read_text())
    budget = Budget(output, state["elapsed_seconds"], limits)
    try:
        budget()
        provenance["rechecked_data_hash"] = recheck_inputs(market_db, execution_db, manifest)
        before = provenance.get("source_files_before_load", {})
        after = {p: file_hash(p) for p in before}
        provenance.update(source_files_after_run=after, whole_files_changed=before != after)
        write_json(Path(output)/"provenance.json", provenance)
        budget.last_disk_check = -math.inf
        budget()
    except (ValueError, sqlite3.Error, OSError) as exc:
        report.update(status="INVALID_RESEARCH", reason_codes=[str(exc)])
    except ResourceLimit:
        report.update(status="INCOMPLETE", reason_codes=["PASS_RESOURCE_LIMIT_DURING_RECHECK"])
    write_json(Path(output)/"report.json", report)
    write_json(state_path, {**state, **budget.state(), "status": report["status"]})
    return report


def summarize(summary, start, end):
    from analysis.mixture_statistics import summarise_nav
    raw = asdict(summary)
    m = raw["metrics"]
    statistics = summarise_nav([dict(timestamp_ms=int(t), nav=float(n)) for t, n in raw["daily_nav"]],
        initial_nav=m["initial_nav"], start_ms=start, end_ms=end, mtm_mdd=m["mtm_mdd"],
        completed_campaigns=m["completed_campaigns"], turnover=m["turnover"],
        underwater_days=m.get("underwater_ms", 0)/DAY)
    for field in ("profit_factor", "payoff_ratio"):
        if field in m:
            statistics[field] = m[field]
    return dict(status="OK" if statistics["status"] == "OK" else "INVALID", **raw, statistics=statistics)


def freeze_train(results, identity):
    from analysis.mixture_statistics import train_reference_scale
    freeze = {**select_train_candidate(results), **identity, "scales": {}}
    for mix in MIXES:
        for ref, policy in (("single", freeze["single_id"]), ("BH", "BH")):
            def vols(name):
                return {p: results.get(f"TRAIN/{name}/c1/d5/{p}/full", {}).get("statistics", {}).get("daily_volatility") for p in PATHS}
            freeze["scales"][f"{mix}-{ref}"] = train_reference_scale(vols(mix), vols(policy))
    return freeze


def resolved_trial(row, freeze):
    policy_id, scale = row["name"], 1.
    if row["phase"] == "OOS_MATCHED":
        reference = policy_id.split("-")[1]
        item = freeze["scales"][policy_id]
        if item["status"] != "OK" or not isinstance(item.get("scale"), (int, float)) or not 0 < item["scale"] <= 1:
            raise ValueError("invalid train-only matched reference scale")
        scale, policy_id = item["scale"], freeze["single_id"] if reference == "single" else "BH"
    elif row["phase"] == "OOS_PARTIAL":
        policy_id = freeze["diagnostic_id"]
        if policy_id not in freeze["scores"]:
            raise ValueError("invalid diagnostic train dependency")
    start, end = (TRAIN, OOS) if row["phase"] == "TRAIN" else (OOS, END)
    policy = dict(start_ms=start, end_ms=end, fee_rate=.00055*row["cost"], slippage=.0005*row["cost"],
                  delay_ms=row["delay"]*60_000, path=row["path"], partial_entries=row["fill"] == "partial50",
                  scale=scale, mode="bh" if policy_id == "BH" else "cash" if policy_id == "cash" else "strategies")
    return policy_id, policy


def run_registry(output, contract, bars, funding, data_manifest, *, limits=None, replay=run_portfolio, preparation_seconds=0.):
    """Resume verified completed trials; interrupted trials restart, time is cumulative."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    identity = dict(contract_hash=contract["contract_hash"], data_hash=digest(data_manifest))
    write_json(output/"data_manifest.json", data_manifest, immutable=True)
    state_path = output/"run_state.json"
    previous = json.loads(state_path.read_text()) if state_path.exists() else {**identity, "elapsed_seconds": 0.}
    if any(previous.get(k) != v for k, v in identity.items()):
        raise ValueError("resume input/contract identity mismatch")
    registry_path = output/"registry.json"
    if registry_path.exists():
        prior_rows = json.loads(registry_path.read_text())
        if {r["id"] for r in prior_rows} != {r["id"] for r in planned_registry()} or len(prior_rows) != 240:
            raise RuntimeError("resume registry IDs mismatch")
        if any(r["status"] == "OK" and not (output/"trials"/r["id"]/"result.json").exists() for r in prior_rows):
            raise RuntimeError("completed trial result missing")
    budget = Budget(output, previous["elapsed_seconds"]+preparation_seconds, limits)
    features = build_daily_features(bars)
    rows, results, cache, freeze = planned_registry(), {}, {}, None
    stopped = False
    for row in rows:
        if row["phase"] != "TRAIN" and freeze is None:
            freeze = freeze_train(results, identity)
            write_json(output/"freeze.json", freeze, immutable=True)
        trial_dir = output/"trials"/row["id"]
        result_path = trial_dir/"result.json"
        key = None
        try:
            budget()
            policy_id, policy = resolved_trial(row, freeze)
            key = digest(dict(identity=identity, policy_id=policy_id, policy=policy))
            if result_path.exists():
                saved = json.loads(result_path.read_text())
                result = saved["result"]
                if saved["trial_hash"] != key or saved["result_hash"] != digest(result):
                    raise RuntimeError("completed trial hash mismatch")
                for name, sha in saved.get("artifact_hashes", {}).items():
                    if file_hash(output/name) != sha:
                        raise RuntimeError("completed trial artifact hash mismatch")
                row.update(status=result["status"], trial_hash=key, result_hash=saved["result_hash"], alias_of=saved.get("alias_of"))
            elif key in cache:
                source = cache[key]
                result = results[source]
                row.update(status=result["status"], trial_hash=key, result_hash=digest(result), alias_of=source)
                write_json(result_path, dict(trial_hash=key, result_hash=digest(result), result=result, alias_of=source), immutable=True)
            else:
                trial_dir.mkdir(parents=True, exist_ok=True)
                row.update(status="RUNNING", trial_hash=key)
                write_json(output/"registry.json", rows)
                sink = LedgerSink(trial_dir/"events.jsonl.gz")
                try:
                    summary = replay(bars, funding, features, callbacks(policy_id), PortfolioPolicy(**policy, resource_check=budget), sink)
                    result = summarize(summary, policy["start_ms"], policy["end_ms"])
                finally:
                    sink.close()
                daily_path = trial_dir/"daily_nav.csv"
                with daily_path.open("w") as stream:
                    stream.write("timestamp_ms,nav\n")
                    for timestamp, nav in result["daily_nav"]:
                        stream.write(f"{timestamp},{nav:.17g}\n")
                hashes = {str(p.relative_to(output)): file_hash(p) for p in (daily_path, trial_dir/"events.jsonl.gz")}
                row.update(status=result["status"], result_hash=digest(result))
                write_json(result_path, dict(trial_hash=key, result_hash=digest(result), result=result, artifact_hashes=hashes), immutable=True)
            results[row["id"]] = result
            if result.get("error"):
                row["error"] = result["error"]
            cache[key] = row.get("alias_of") or row["id"]
        except ResourceLimit as exc:
            row.update(status="INCOMPLETE", error=str(exc))
            stopped = True
        except RuntimeError:
            raise  # Corrupted resume evidence must not be silently replaced.
        except (ValueError, ArithmeticError) as exc:
            row.update(status="INVALID", error=str(exc))
            results[row["id"]] = dict(status="INVALID", error=str(exc), failure_state=getattr(exc, "state", None))
            if key is not None:
                failed = results[row["id"]]
                row.update(trial_hash=key, result_hash=digest(failed))
                write_json(result_path, dict(trial_hash=key, result_hash=digest(failed), result=failed), immutable=True)
        write_json(output/"registry.json", rows)
        write_json(state_path, {**identity, **budget.state(), "checkpoint": budget.checkpoint, "status": "INCOMPLETE"})
        if stopped:
            for remaining in rows:
                if remaining["status"] == "PLANNED":
                    remaining.update(status="INCOMPLETE", error="pass resource limit")
            break
    write_json(output/"registry.json", rows)
    report = build_report(rows, results, freeze) if not stopped else dict(status="INCOMPLETE")
    report.update(identity, registry_counts=dict(Counter(r["status"] for r in rows)),
                  profitability_status="INSUFFICIENT", auto_activate=False,
                  limitations=contract["contract"]["limitations"], research_round="INITIAL_BOUNDED")
    report["trial_summaries"] = {k: {a: b for a, b in v.items() if a != "daily_nav" and a != "statistics"} |
        {"statistics": {a: b for a, b in v.get("statistics", {}).items() if a != "daily_returns"}} for k, v in results.items()}
    report["failure_reasons"] = {r["id"]: r.get("error", results.get(r["id"], {}).get("statistics", {}).get("reason_codes")) for r in rows if r["status"] != "OK"}
    write_json(output/"report.json", report)
    try:
        budget.last_disk_check = -math.inf
        budget()
    except ResourceLimit:
        report.update(status="INCOMPLETE", reason_codes=["PASS_RESOURCE_LIMIT"])
        write_json(output/"report.json", report)
    write_json(state_path, {**identity, **budget.state(), "checkpoint": budget.checkpoint, "status": report["status"]})
    return report


def build_report(rows, results, freeze):
    from analysis.mixture_statistics import bootstrap_max_error, evaluate_research_gate
    if any(r["status"] != "OK" for r in rows):
        return dict(status="INVALID_RESEARCH", reason_codes=["INVALID_TRIAL_OR_DEPENDENCY"], freeze=freeze)
    def result(phase, name, cell):
        cost, delay, path = cell
        fill = "partial50" if phase == "OOS_PARTIAL" else "full"
        return results[f"{phase}/{name}/c{cost}/d{delay}/{path}/{fill}"]
    differences = {p: {} for p in PATHS}
    for p in PATHS:
        for mix in MIXES:
            raw = result("OOS_RAW", mix, (1, 5, p))
            for ref in ("single", "BH"):
                matched = result("OOS_MATCHED", f"{mix}-{ref}", (1, 5, p))
                if [t for t, n in raw["daily_nav"]] != [t for t, n in matched["daily_nav"]]:
                    return dict(status="INVALID_RESEARCH", reason_codes=["ASYMMETRIC_COMPARISON_DATES"])
                differences[p][f"{mix}/{ref}"] = (np.asarray(raw["statistics"]["daily_returns"])-np.asarray(matched["statistics"]["daily_returns"])).tolist()
    timestamps = [t for t, n in raw["daily_nav"]]
    ci = bootstrap_max_error(differences, timestamps)
    selected = freeze["selected_id"] or freeze["diagnostic_id"]
    base = {p: result("OOS_RAW", selected, (1, 5, p))["statistics"] for p in PATHS}
    refs = {p: {r: result("OOS_MATCHED", f"{selected}-{r}", (1, 5, p))["statistics"] for r in ("single", "BH")} for p in PATHS}
    lowers = {p: {r: ci.get("paths", {}).get(p, {}).get("lower_bounds", {}).get(f"{selected}/{r}") for r in ("single", "BH")} for p in PATHS}
    gate = evaluate_research_gate(freeze["selected_id"], base, refs, lowers,
        {f"c{c}/d{d}/{p}": result("OOS_RAW", selected, (c, d, p))["statistics"] for c, d, p in CELLS},
        {f"c{c}/d{d}/{p}": result("OOS_PARTIAL", "FROZEN", (c, d, p))["statistics"] for c, d, p in CELLS},
        train_eligible=freeze["selected_id"] is not None)
    correlations = {}
    for p in PATHS:
        series = {name: result("OOS_RAW", name, (1,5,p))["statistics"]["daily_returns"] for name in (*MIXES,"T","B","R")}
        correlations[p] = {f"{a}/{b}": float(np.corrcoef(x, series[b])[0,1]) if np.std(x) > 0 and np.std(series[b]) > 0 else None
                           for a, x in series.items() for b in series if a < b}
    return {**gate, "freeze": freeze, "bootstrap": ci, "base": base, "matched_references": refs, "daily_return_correlations": correlations,
            "trial_summaries": {k: {"metrics": v["metrics"], "statistics": {a: b for a, b in v["statistics"].items() if a != "daily_returns"}, "hashes": v["hashes"]} for k, v in results.items()}}


def synthetic_profile(output):
    """Fixed synthetic smoke/profile, never real-history candidate performance."""
    begin = time.monotonic()
    timestamps = np.arange(START, TRAIN+30*DAY, BAR)
    close = 30000 + 1000*np.sin(np.arange(len(timestamps))/1500)
    bars = np.column_stack((timestamps, close, close+30, close-30, close))
    funding = np.column_stack((np.arange(START, TRAIN+30*DAY, 8*3600_000), np.zeros(len(bars)//96)))
    features, records = build_daily_features(bars), {}
    for days in (1, 30):
        t = time.monotonic()
        summary = run_portfolio(bars, funding, features, callbacks("M04"), PortfolioPolicy(start_ms=TRAIN, end_ms=TRAIN+days*DAY))
        records[str(days)] = dict(seconds=time.monotonic()-t, hashes=summary.hashes, counters=summary.counters)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    record = dict(kind="SYNTHETIC_NOT_MARKET", seconds=time.monotonic()-begin, runs=records,
                  source_hashes=make_contract()["source_hashes"],
                  peak_rss_bytes=int(rss if sys.platform == "darwin" else rss*1024),
                  conservative_projected_seconds=records["30"]["seconds"]*(24*275+216*1096)/30)
    write_json(Path(output)/"synthetic_profile.json", record)
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregister-only", action="store_true")
    parser.add_argument("--profile-only", action="store_true")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--market-db", type=Path)
    parser.add_argument("--execution-db", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.preregister_only and args.profile_only:
        parser.error("preregister and synthetic profile are separate operations")
    if args.preregister_only:
        envelope = preregister(args.output_dir)
        print(canonical(dict(status="PREREGISTERED", contract_hash=envelope["contract_hash"], planned_trials=240, pnl_computed=False)))
        return 0
    if args.profile_only:
        validate_environment()
        print(canonical(synthetic_profile(args.output_dir)))
        return 0
    if not all((args.contract, args.market_db, args.execution_db)):
        parser.error("run requires --contract, --market-db and --execution-db")
    preparation_start = time.monotonic()
    contract = verify_contract(args.contract)
    environment = validate_environment()
    profile_path = args.output_dir/"synthetic_profile.json"
    if not profile_path.exists():
        parser.error("run --profile-only into this output directory before historical performance")
    profile = json.loads(profile_path.read_text())
    if profile.get("source_hashes") != contract["contract"]["source_hashes"] or profile.get("kind") != "SYNTHETIC_NOT_MARKET" or set(profile.get("runs", {})) != {"1", "30"} or not math.isfinite(profile.get("seconds", math.nan)) or profile["seconds"] <= 0:
        raise ValueError("synthetic profile evidence missing/mismatched")
    profile_seconds = profile["seconds"] if not (args.output_dir/"run_state.json").exists() else 0.
    source_provenance = {}
    try:
        bars, funding, manifest = load_inputs(args.market_db, args.execution_db, provenance=source_provenance)
    except ValueError as exc:
        rows = [{**r, "status": "INVALID", "error": str(exc)} for r in planned_registry()]
        write_json(args.output_dir/"registry.json", rows)
        write_json(args.output_dir/"report.json", dict(status="INVALID_RESEARCH", reason_codes=[str(exc)], profitability_status="INSUFFICIENT", auto_activate=False))
        print(canonical(dict(status="INVALID_RESEARCH", error=str(exc))))
        return 2
    # Content hashes bind the code actually validated by verify_contract. Git
    # commit/tree receipts belong to the external publication record; research
    # does not start child processes or trust an executable search path.
    provenance = dict(environment=environment,
                      source_context=dict(pinned_base=contract["contract"]["pinned_base"],
                                          source_hashes=contract["contract"]["source_hashes"]),
                      **source_provenance)
    write_json(args.output_dir/"provenance.json", provenance)
    report = run_registry(args.output_dir, contract, bars, funding, manifest,
                          preparation_seconds=profile_seconds+time.monotonic()-preparation_start)
    report = finalize_recheck(args.output_dir, report, manifest, args.market_db, args.execution_db, provenance)
    print(canonical(dict(status=report["status"], registry_counts=report["registry_counts"], profitability_status="INSUFFICIENT", auto_activate=False)))
    return 0 if report["status"] in ("RESEARCH_CANDIDATE", "NO_QUALIFYING_CANDIDATE") else 2


if __name__ == "__main__":
    raise SystemExit(main())
