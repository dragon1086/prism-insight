"""Frozen intrabar sensitivity registry and streaming accounting evidence.

This is an offline research runner, not an optimizer or activation tool. A CLI
subset is permitted only for the already-labelled 2022 diagnostic cases.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess  # nosec B404 - fixed read-only git arguments, no shell
import time

import numpy as np
import pandas as pd

from analysis.intrabar_inputs import prepare_inputs
from backtest.intrabar_broker import Config, run_replay
from backtest.native_intrabar_strategy import NativeStrategies

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/BTC_INTRABAR_REVALIDATION_CONTRACT_2026-09-07_ko.md"
BAR_MS = 300_000
DAY_MS = 86_400_000
INITIAL = 10_000.
LABELS = tuple(f"{trend}_{vol}" for trend in ("up", "down", "range")
               for vol in ("normal", "shock")) + ("unknown",)
ARTIFACTS = ("events.jsonl", "decisions.jsonl", "closed_parents.json",
             "daily_nav.jsonl", "summary.json", "manifest.json")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_json(path, value):
    Path(path).write_text(canonical(value) + "\n", encoding="utf-8")


def registry():
    configs = (("main", "single"), ("main", "confirmed"), ("swing", "single"),
               ("joint", "single"), ("joint", "confirmed"))
    return [{"id": f"{phase}_{scope}_{mode}_{profile}_{path}", "phase": phase,
             "start_ms": start, "end_ms": end, "scope": scope, "main_mode": mode,
             "profile": profile, "path": path}
            for phase, start, end, profiles, paths in (
                ("diagnostic", 1_640_995_200_000, 1_672_531_200_000, ("BASE",), ("OHLC",)),
                ("evaluation", 1_672_531_200_000, 1_767_225_600_000, ("BASE", "STRESS"), ("OHLC", "OLHC")))
            for scope, mode in configs for profile in profiles for path in paths]


def execution_config(profile, path):
    if profile not in ("BASE", "STRESS"):
        raise ValueError("unknown execution profile")
    overrides = {} if profile == "BASE" else dict(entry_latency_ms=30_000, market_latency_ms=30_000,
        cancel_latency_ms=10_000, amend_latency_ms=10_000, maker_fee=.0004, taker_fee=.0011,
        slippage=.001, spread=.0002, participation=.001)
    return Config(path=path, **overrides)


def source_manifest():
    paths = [CONTRACT]
    for directory in ("core", "engine", "backtest"):
        paths.extend(sorted((ROOT / "prism-btc" / directory).rglob("*.py")))
    paths.extend(ROOT / "prism-btc/analysis" / name for name in
                 ("intrabar_inputs.py", "replay_data.py", "intrabar_revalidation.py",
                  "intrabar_evidence.py", "confidence_allocation_study.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _date(ts):
    return datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat()


class StreamingCollector:
    """Retain witnesses/daily cells, not millions of full valuation snapshots."""
    def __init__(self, bundle, bars, event_stream, initial_cash=INITIAL):
        self.bundle, self.event_stream, self.initial = bundle, event_stream, initial_cash
        self.cells = {label: {"coverage_bars": 0, "entry_fill_events": 0, "filled_parents": 0,
            "closed_parents": 0, "closed_parent_net_pnl": 0., "interval_nav_change": 0.,
            "fees": 0., "funding": 0., "interval_fees": 0., "interval_funding": 0.,
            "closed_wins": 0, "closed_losses": 0, "closed_flat": 0,
            "worst_closed_parent_net_pnl": None} for label in LABELS}
        self.labels = {}
        for bar in bars:
            stamp = bar["ts"]
            label = bundle.regime_at(stamp)["label"]
            if label not in self.cells:
                raise ValueError("unknown descriptive regime label")
            self.labels[stamp] = label
            self.cells[label]["coverage_bars"] += 1
        self.digest, self.count = hashlib.sha256(), 0
        self.previous_nav = self.peak = initial_cash
        self.last_ts = None
        self.peak_witness = {"ts": bars[0]["ts"], "nav": initial_cash, "phase": "initial"}
        self.drawdown_witness, self.max_drawdown = None, 0.
        self.max_held_notional, self.held_notional_witness = 0., None
        self.daily, self.parent_cells, self.children = {}, {}, {}
        self.event_counts, self.lane_fills = Counter(), {lane: Counter() for lane in ("main", "swing")}
        self.fills = {}

    def valuation(self, snapshot):
        # Never infer a period from endpoint ts: a close and next gap can share ts.
        stamp = snapshot["bar_open_ts"]
        if stamp not in self.labels:
            raise ValueError("valuation interval absent from selected data")
        if self.last_ts is not None and snapshot["ts"] < self.last_ts:
            raise ValueError("nonmonotonic valuation")
        cash = self.initial + snapshot["realized_price"] - snapshot["fees"] - snapshot["funding"]
        if not math.isclose(snapshot["cash"], cash, rel_tol=0, abs_tol=1e-7):
            raise ValueError("streaming cash identity")
        if not math.isclose(snapshot["nav"], snapshot["cash"] + snapshot["unrealized"], rel_tol=0, abs_tol=1e-7):
            raise ValueError("streaming NAV identity")
        self.digest.update((canonical(snapshot) + "\n").encode())
        self.count += 1
        nav = snapshot["nav"]
        self.cells[self.labels[stamp]]["interval_nav_change"] += nav - self.previous_nav
        self.previous_nav, self.last_ts = nav, snapshot["ts"]
        self.daily[stamp // DAY_MS * DAY_MS] = nav
        held_notional = snapshot.get("gross", 0.) - snapshot.get("reserved", 0.)
        if held_notional > self.max_held_notional:
            self.max_held_notional, self.held_notional_witness = held_notional, dict(snapshot)
        if nav > self.peak:
            self.peak, self.peak_witness = nav, dict(snapshot)
        drawdown = (self.peak - nav) / self.peak
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
            self.drawdown_witness = {"peak": self.peak_witness, "trough": dict(snapshot)}

    def event(self, row):
        self.event_stream.write(canonical(row) + "\n")
        self.event_counts[row["kind"]] += 1
        if row["kind"] in ("fill", "funding"):
            interval_label = self.labels[row["bar_open_ts"]]
            if row["kind"] == "fill":
                self.cells[interval_label]["interval_fees"] += row["fee"]
            else:
                self.cells[interval_label]["interval_funding"] += row["amount"]
        if row["kind"] == "fill":
            parent, lane = row["parent_id"], row["lane"]
            totals = self.fills.setdefault(parent, {"entry": 0, "exit": 0})
            key = "entry" if row["entry"] else "exit"
            totals[key] += row["lots"]
            if totals["exit"] > totals["entry"]:
                raise ValueError("streaming oversold lots")
            self.lane_fills[lane][key + "_lots"] += row["lots"]
            self.lane_fills[lane][key + "_fill_events"] += 1
            if row["entry"]:
                if parent not in self.parent_cells:
                    # Entry cohort uses first fill information, not order decision metadata.
                    label = self.bundle.regime_at(row["ts"])["label"]
                    self.parent_cells[parent] = label
                    self.cells[label]["filled_parents"] += 1
                label = self.parent_cells[parent]
                self.cells[label]["entry_fill_events"] += 1
                self.children.setdefault(parent, set()).add(row["child"])

    def finish(self, broker):
        final = broker.snapshot()
        if not self.count or not math.isclose(final["nav"], self.previous_nav, rel_tol=0, abs_tol=1e-7):
            raise ValueError("final valuation missing or inconsistent")
        interval_total = math.fsum(cell["interval_nav_change"] for cell in self.cells.values())
        if not math.isclose(interval_total, final["nav"] - self.initial, rel_tol=0, abs_tol=1e-6):
            raise ValueError("regime interval NAV conservation")
        closed = []
        lane_closed = {lane: {"closed_parents": 0, "closed_parent_net_pnl": 0., "fees": 0., "funding": 0.}
                       for lane in ("main", "swing")}
        for parent in broker.closed_trades:
            pid = parent["parent_id"]
            if self.fills[pid] != {"entry": parent["filled_lots"], "exit": parent["exited_lots"]}:
                raise ValueError("closed parent lot receipt mismatch")
            if parent["filled_lots"] != parent["exited_lots"]:
                raise ValueError("closed parent residual lots")
            label = self.parent_cells[pid]
            closed.append({**parent, "entry_regime": label})
            cell = self.cells[label]
            outcome = "closed_wins" if parent["net_pnl"] > 0 else "closed_losses" if parent["net_pnl"] < 0 else "closed_flat"
            cell[outcome] += 1
            worst = cell["worst_closed_parent_net_pnl"]
            cell["worst_closed_parent_net_pnl"] = parent["net_pnl"] if worst is None else min(worst, parent["net_pnl"])
            for target in (self.cells[label], lane_closed[parent["lane"]]):
                target["closed_parents"] += 1
                target["closed_parent_net_pnl"] += parent["net_pnl"]
                target["fees"] += parent["fees"]
                target["funding"] += parent["funding"]
        for cell in self.cells.values():
            cell["coverage_hours"] = cell["coverage_bars"] * BAR_MS / 3_600_000
            cell["sample_status"] = "INSUFFICIENT_LT30" if cell["closed_parents"] < 30 else "N_GE30_NOT_INDEPENDENCE_PROOF"
        daily, previous, yearly = [], self.initial, {}
        for stamp, nav in sorted(self.daily.items()):
            if previous <= 0:
                raise ValueError("nonpositive daily return denominator")
            daily.append({"date": _date(stamp), "timestamp_ms": stamp + DAY_MS,
                          "nav": nav, "return": nav / previous - 1})
            year = _date(stamp)[:4]
            if year not in yearly:
                yearly[year] = {"start_nav": previous, "end_nav": nav}
            yearly[year]["end_nav"] = nav
            previous = nav
        positions = [asdict(pos) for pos in broker.positions()]
        pending = broker.pending_entries()
        held = {pos["id"]: pos["lots"] for pos in positions}
        if any(values["entry"] - values["exit"] != held.get(pid, 0) for pid, values in self.fills.items()):
            raise ValueError("open parent lot conservation")
        summary = {"initial_cash": self.initial, "net_return": final["nav"] / self.initial - 1,
            "max_drawdown": self.max_drawdown, "final": final, "final_mark": broker.mark,
            "yearly_returns": {year: row["end_nav"] / row["start_nav"] - 1 for year, row in yearly.items()},
            "yearly_nav": yearly, "open_positions": positions,
            "open_at_end": {"disposition": "OPEN_AT_END", "position_count": len(positions),
                "held_lots": sum(held.values()), "pending_parent_count": len(pending),
                "pending_lots": sum(p["lots"] for p in pending),
                "pending_reduction_lots": sum(broker.pending_reductions(pos["id"]) for pos in positions),
                "raw_timer_heap_count": len(broker._timers),
                "active_parent_timer_count": sum(timer[3] in broker.active_parents for timer in broker._timers)},
            "execution": {"entry_fill_events": sum(x["entry_fill_events"] for x in self.lane_fills.values()),
                "entry_lots": sum(x["entry_lots"] for x in self.lane_fills.values()),
                "exit_lots": sum(x["exit_lots"] for x in self.lane_fills.values()),
                "filled_parents": len(self.fills), "closed_parents": len(closed),
                "multi_child_parents": sum(len(children) > 1 for children in self.children.values()),
                "event_counts": dict(self.event_counts), "lot_conservation": "PASS"},
            "per_lane": {lane: {**dict(self.lane_fills[lane]), **lane_closed[lane]} for lane in lane_closed},
            "regimes": self.cells, "regime_interval_nav_total": interval_total,
            "valuation": {"count": self.count, "sha256": self.digest.hexdigest(),
                "peak_witness": self.peak_witness, "drawdown_witness": self.drawdown_witness,
                "max_held_notional": self.max_held_notional, "held_notional_witness": self.held_notional_witness},
            "regime_cost_semantics": "fees/funding are closed-parent first-fill cohorts; interval_fees/interval_funding use event bar_open_ts",
            "profitability_status": "INSUFFICIENT", "auto_activate": False,
            "interpretation": "Path/profile sensitivity, not tick reconstruction, DEMO parity, or guaranteed bounds"}
        return summary, daily, closed


def run_case(bundle, case, outdir):
    output = Path(outdir)
    output.mkdir(parents=True, exist_ok=False)
    start, end = case["start_ms"], case["end_ms"]
    bars = tuple(bar for bar in bundle.bars if start <= bar["ts"] < end)
    if not bars or len(bars) != (end - start) // BAR_MS or bars[0]["ts"] != start or bars[-1]["ts"] != end - BAR_MS:
        raise ValueError("case requires complete selected trade coverage")
    if bundle.mark_bars is None:
        raise ValueError("registered study requires actual mark input")
    marks = tuple(bar for bar in bundle.mark_bars if start <= bar["ts"] < end)
    config = execution_config(case["profile"], case["path"])
    manifest = {"case": case, "config": asdict(config), "inputs": bundle.manifest,
                "input_manifest_sha256": hashlib.sha256(canonical(bundle.manifest).encode()).hexdigest(),
                "registry_sha256": hashlib.sha256(canonical(registry()).encode()).hexdigest(),
                "sources": source_manifest(), "allocation": "graded", "main_first": True,
                "source_profiles": ["RESEARCH_MAIN_NOT_DEMO", "NATIVE_SWING"]}
    write_json(output / "manifest.json", manifest)
    with (output / "events.jsonl").open("x", encoding="utf-8") as events, \
            (output / "decisions.jsonl").open("x", encoding="utf-8") as decisions:
        collector = StreamingCollector(bundle, bars, events)
        strategy = NativeStrategies(bundle, scope=case["scope"], main_mode=case["main_mode"],
            allocation="graded", sink=lambda row: decisions.write(canonical(row) + "\n"))
        broker = run_replay(bars, bundle.funding, marks, config, strategy.on_boundary,
            sink=collector.event, valuation_sink=collector.valuation,
            confirm_callback=strategy.confirm_callback, initial_cash=INITIAL)
        summary, daily, closed = collector.finish(broker)
    summary["case"] = case
    write_json(output / "closed_parents.json", closed)
    (output / "daily_nav.jsonl").write_text("".join(canonical(row) + "\n" for row in daily), encoding="utf-8")
    write_json(output / "summary.json", summary)
    return summary


def _clean_source():
    result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True,
                            capture_output=True, text=True)  # nosec B603 B607
    if result.stdout.strip():
        raise ValueError("historical run requires clean frozen source")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()  # nosec B603 B607


def run_study(bundle, output, selected=None):
    started = time.monotonic()
    commit = _clean_source()
    cases = registry()
    if selected is not None:
        requested = set(selected)
        if not requested or any(case_id not in {c["id"] for c in cases if c["phase"] == "diagnostic"} for case_id in requested):
            raise ValueError("subsets are restricted to labelled diagnostic cases")
        cases = [case for case in cases if case["id"] in requested]
    root = Path(output).resolve()
    if root.is_relative_to(ROOT):
        raise ValueError("historical output must be outside frozen source checkout")
    root.mkdir(parents=True, exist_ok=False)
    sources = source_manifest()
    write_json(root / "registry.json", {"source_commit": commit, "cases": cases,
        "full_registry": registry(), "sources": sources, "inputs": bundle.manifest,
        "registry_sha256": hashlib.sha256(canonical(registry()).encode()).hexdigest(),
        "input_manifest_sha256": hashlib.sha256(canonical(bundle.manifest).encode()).hexdigest(),
        "runtime": {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__}})
    progress = {"status": "running", "completed": [], "active": None,
                "completion_scope": "REPLAY_ONLY; independent ledger/daily/repeat verification required",
                "profitability_status": "INSUFFICIENT", "auto_activate": False,
                "started_at_utc": datetime.now(timezone.utc).isoformat()}

    def update_progress():
        progress.update(updated_at_utc=datetime.now(timezone.utc).isoformat(),
                        elapsed_seconds=round(time.monotonic() - started, 3))
        write_json(root / "progress.json", progress)

    update_progress()
    try:
        for case in cases:
            progress["active"] = case["id"]
            if source_manifest() != sources:
                raise ValueError("frozen source changed during study")
            update_progress()
            print(canonical({"case": case["id"], "status": "running"}), flush=True)
            run_case(bundle, case, root / case["id"])
            progress["completed"].append(case["id"])
            update_progress()
        if source_manifest() != sources:
            raise ValueError("frozen source changed during study")
        progress.update(status="complete", active=None)
    except Exception as exc:
        progress.update(status="failed", error_type=type(exc).__name__)
        update_progress()
        raise
    update_progress()
    return progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--trade-db", required=True)
    parser.add_argument("--mark-db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", nargs="+")
    args = parser.parse_args()
    _clean_source()
    bundle = prepare_inputs(args.market_db, args.trade_db, args.mark_db)
    run_study(bundle, args.output, args.cases)


if __name__ == "__main__":
    main()
