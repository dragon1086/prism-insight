"""Offline conditional exit experiment, NOT scalp entry/portfolio performance.

The v1 fixture was registered in docs/BTC_EXECUTION_REPLAY_2026-09-06_ko.md.
OHLC node times/paths, unlimited liquidity and last-price valuation are model
assumptions, not observed executions, mark prices or strict return bounds.
The command reads input DBs only and prints JSON to stdout; it has no network I/O.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from math import fsum
from pathlib import Path
from statistics import mean

import pandas as pd

from analysis.replay_data import load_bars, load_funding, TIMEFRAME_MS, MONDAY_ANCHOR_MS
from backtest.execution_replay import (
    EntryRequest, FundingEvent, PriceEvent, ReplayConfig, run_replay,
)
from core.scalp import Target
from engine.indicators import add_indicators
from engine.regime import build_snapshot, build_tf_state
from engine.signal import generate_signal
from engine.sizing import compute_sl_price

VERSION = "scalp-exit-conditional-v1"
TFS = ("30m", "1h", "4h", "12h", "1d", "1w")
BAR_MS = TIMEFRAME_MS["5m"]
HORIZON_MS = 125 * 60_000
FIXTURE = dict(initial_cash=10_000., lot_size=.001, lots=20,
               max_gross_notional=10_000., maker_fee=.0002, taker_fee=.00055,
               market_slippage_bps=5., entry_latency_ms=0, amend_latency_ms=0,
               check_interval_ms=300_000, max_hold_seconds=7200.,
               tp1_r=.5, tp2_r=1., early_r=.5, runner_r=1.5)


def canonical_hash(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def utc_ms(value: str) -> int:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def grid_floor(ts: int, tf: str) -> int:
    anchor = MONDAY_ANCHOR_MS if tf == "1w" else 0
    return (ts - anchor) // TIMEFRAME_MS[tf] * TIMEFRAME_MS[tf] + anchor


def closed_frame(frames: dict, tf: str, ts: int):
    """Never expose a higher-TF candle before its right-hand boundary."""
    frame = frames[tf]
    cutoff = pd.Timestamp(ts - TIMEFRAME_MS[tf], unit="ms", tz="UTC")
    return frame.iloc[:int(frame.index.searchsorted(cutoff, side="right"))]


def entry_context(frames: dict, ts: int) -> dict | None:
    closed = {tf: closed_frame(frames, tf, ts) for tf in TFS}
    if any(len(frame) < 50 for frame in closed.values()):
        return None
    snap = build_snapshot(closed, evaluated_at=pd.Timestamp(ts, unit="ms", tz="UTC").to_pydatetime())
    if set(snap.tf_states) != set(TFS):
        raise ValueError("incomplete signal snapshot")
    signal = generate_signal(snap)
    if signal.side == "none":
        return None
    reference = float(closed["30m"].iloc[-1]["close"])
    hourly = closed["1h"]
    swing = float(hourly["low"].iloc[-10:].min() if signal.side == "long"
                  else hourly["high"].iloc[-10:].max())
    stop = compute_sl_price(reference, signal.side, swing,
                            float(hourly.iloc[-1]["atr14"]), float(hourly.iloc[-1]["ma35"]))
    return dict(ts_ms=ts, side=signal.side, reference_price=reference,
                initial_stop=stop, risk_distance=abs(reference - stop),
                signal_strength=signal.strength)


def background_permission(frames: dict, side: str, ts: int) -> bool:
    direction = "up" if side == "long" else "down"
    return all(build_tf_state(closed_frame(frames, tf, ts)).trend == direction
               for tf in ("1h", "4h"))


def ohlc_path_events(rows: tuple | list, path: str, permissions: list[bool]):
    """Four hypothetical ordered nodes; offsets DO NOT recover actual ticks."""
    if path not in ("OHLC", "OLHC") or len(rows) != len(permissions):
        raise ValueError("path or permission length")
    names = ("open", "high", "low", "close") if path == "OHLC" else (
        "open", "low", "high", "close")
    events = []
    for row, permission in zip(rows, permissions):
        for offset, name in zip((0, 100_000, 200_000, 299_999), names):
            events.append(PriceEvent(row["open_time"] + offset, row[name], None, permission))
    return events


def campaign_summary(result, episode: dict, profile: str, path: str, cost: int) -> dict:
    risk = episode["risk_distance"] * FIXTURE["lots"] * FIXTURE["lot_size"]
    if risk <= 0:
        raise ValueError("positive initial reference risk required")
    net = result.nav_final - FIXTURE["initial_cash"]
    simulated_tp1_lots = sum(fill.lots for fill in result.fills if fill.reason == "tp1")
    return dict(episode_id=episode["episode_id"], ts_ms=episode["ts_ms"],
                year=datetime.fromtimestamp(episode["ts_ms"] / 1000, timezone.utc).year,
                side=episode["side"], profile=profile, path=path, cost_multiple=cost,
                net_r=net / risk, net_pnl=net, cash=result.cash,
                unrealized_pnl=result.unrealized_pnl, nav_final=result.nav_final,
                fees=result.total_fees, funding=result.total_funding,
                held_funding_events=sum(charge.lots > 0 for charge in result.funding),
                entered_lots=result.entered_lots, remaining_lots=result.remaining_lots,
                tp1_complete=simulated_tp1_lots >= FIXTURE["lots"] // 2,
                max_campaign_drawdown=result.max_drawdown, status=result.status,
                flags=list(result.flags), trace_sha256=canonical_hash(asdict(result)))


def aggregate(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    pairs = defaultdict(dict)
    for row in rows:
        groups[(row["year"], row["side"], row["profile"], row["path"], row["cost_multiple"])].append(row)
        pair = pairs[(row["episode_id"], row["path"], row["cost_multiple"])]
        if row["profile"] in pair:
            raise ValueError("duplicate paired campaign")
        pair[row["profile"]] = row
    summaries = []
    for (year, side, profile, path, cost), members in sorted(groups.items()):
        n = len(members)
        summaries.append(dict(year=year, side=side, profile=profile, path=path, cost_multiple=cost,
                              campaigns=n, mean_net_r=mean(x["net_r"] for x in members),
                              positive_nav_fraction=sum(x["net_pnl"] > 0 for x in members) / n,
                              tp1_complete_fraction=sum(x["tp1_complete"] for x in members) / n,
                              residual_campaigns=sum(x["remaining_lots"] > 0 for x in members),
                              rejected_or_unfilled=sum(x["entered_lots"] == 0 for x in members),
                              held_funding_events=sum(x["held_funding_events"] for x in members),
                              max_campaign_drawdown=max(x["max_campaign_drawdown"] for x in members)))
    differences = defaultdict(list)
    for (_, path, cost), members in pairs.items():
        if set(members) != {"tight_remainder", "trend_runner"}:
            raise ValueError("unpaired campaign comparison")
        before, after = members["tight_remainder"], members["trend_runner"]
        differences[(before["year"], path, cost)].append(after["net_r"] - before["net_r"])
    paired = [dict(year=year, path=path, cost_multiple=cost, pairs=len(values),
                   mean_delta_net_r=fsum(values) / len(values))
              for (year, path, cost), values in sorted(differences.items())]
    return dict(groups=summaries, paired_runner_minus_tight=paired)


def funding_cashflow_probe(execution_rows, funding_rows) -> dict:
    """Separate constant-exposure ledger audit, NOT a trading performance run.

    Price coverage here is deliberately only two endpoints. Funding accounting
    is compared against an independent closed-form sum, never used to evaluate
    stops, liquidation, market-path drawdown or the registered scalp profiles.
    """
    first, last = execution_rows[0], execution_rows[-1]
    start, end = first["open_time"], last["open_time"] + BAR_MS - 1
    prices = {row["open_time"]: row["open"] for row in execution_rows}
    events = [FundingEvent(row["funding_time"], row["rate"], prices[row["funding_time"]])
              for row in funding_rows]
    qty = FIXTURE["lots"] * FIXTURE["lot_size"]
    audits = []
    for side, sign in (("long", 1), ("short", -1)):
        config = ReplayConfig(10000, FIXTURE["lot_size"], 0, 0, 0, 0, 0, BAR_MS,
                              10000, 1e10, 1e10, (end - start) / 1000 + 1)
        request = EntryRequest(start, side, FIXTURE["lots"],
                               1e-9 if sign == 1 else 1e10,
                               1e10 if sign == 1 else 1e-9, start + BAR_MS)
        result = run_replay([PriceEvent(start, first["open"]), PriceEvent(end, last["close"])],
                            request, config, events)
        expected = fsum(sign * qty * e.mark_price * e.rate for e in events if e.ts_ms > start)
        if abs(result.total_funding - expected) > 1e-8 * max(1., abs(expected)):
            raise AssertionError("historical funding cashflow mismatch")
        if result.entered_lots != FIXTURE["lots"]:
            raise AssertionError("funding audit exposure rejected")
        audits.append(dict(side=side, held_events=sum(c.lots > 0 for c in result.funding),
                           nonzero_charges=sum(c.amount != 0 for c in result.funding),
                           simulated_funding=result.total_funding, formula_funding=expected,
                           absolute_error=abs(result.total_funding - expected)))
    return dict(kind="ACCOUNTING_ONLY_CONSTANT_EXPOSURE_NO_TRADING_PERFORMANCE",
                price_source="FUNDING_LAST_PRICE_PROXY", quantity=qty, results=audits)


def run_benchmark(market_db, execution_db, start_ms: int, end_ms: int) -> dict:
    if start_ms % TIMEFRAME_MS["1d"] or end_ms % TIMEFRAME_MS["1d"]:
        raise ValueError("experiment bounds must be UTC midnight")
    execution = load_bars(execution_db, "5m", start_ms, end_ms)
    funding = load_funding(market_db, start_ms, end_ms, 8 * 3_600_000)
    manifests = dict(execution=execution.manifest, funding=funding.manifest)
    frames = {}
    for tf in TFS:
        start = grid_floor(start_ms, tf) - 60 * TIMEFRAME_MS[tf]
        end = grid_floor(end_ms, tf)
        loaded = load_bars(market_db, tf, start, end)
        frame = pd.DataFrame(loaded.rows)
        frame.index = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
        frames[tf] = add_indicators(frame)
        manifests[tf] = loaded.manifest

    episodes, rows, examples = [], [], []
    excluded_tail = 0
    funding_by_time = {row["funding_time"]: row["rate"] for row in funding.rows}
    for ts in range(start_ms, end_ms, TIMEFRAME_MS["4h"]):
        episode = entry_context(frames, ts)
        if episode is None:
            continue
        if ts + HORIZON_MS > end_ms:
            excluded_tail += 1
            continue
        episode["episode_id"] = canonical_hash(episode)[:24]
        episodes.append(episode)
        index = (ts - start_ms) // BAR_MS
        window = execution.rows[index:index + HORIZON_MS // BAR_MS]
        permissions = [background_permission(frames, episode["side"], row["open_time"])
                       for row in window]
        funding_events = [FundingEvent(row["open_time"], funding_by_time[row["open_time"]], row["open"])
                          for row in window if row["open_time"] in funding_by_time]
        sign = 1 if episode["side"] == "long" else -1
        risk = episode["risk_distance"]
        request = EntryRequest(submitted_ms=ts, side=episode["side"], lots=FIXTURE["lots"],
                               stop_price=episode["initial_stop"],
                               tp1_price=episode["reference_price"] + sign * FIXTURE["tp1_r"] * risk,
                               expiry_ms=ts + BAR_MS,
                               further_targets=(Target(episode["reference_price"] + sign * risk,
                                                       FIXTURE["lots"] // 4),))
        for path in ("OHLC", "OLHC"):
            events = ohlc_path_events(window, path, permissions)
            for cost in (1, 2):
                for profile in ("tight_remainder", "trend_runner"):
                    config = ReplayConfig(
                        initial_cash=FIXTURE["initial_cash"], lot_size=FIXTURE["lot_size"],
                        maker_fee=FIXTURE["maker_fee"] * cost, taker_fee=FIXTURE["taker_fee"] * cost,
                        market_slippage_bps=FIXTURE["market_slippage_bps"] * cost,
                        entry_latency_ms=FIXTURE["entry_latency_ms"],
                        amend_latency_ms=FIXTURE["amend_latency_ms"],
                        check_interval_ms=FIXTURE["check_interval_ms"],
                        max_gross_notional=FIXTURE["max_gross_notional"],
                        early_distance=risk * FIXTURE["early_r"],
                        runner_distance=risk * (FIXTURE["runner_r"] if profile == "trend_runner"
                                                else FIXTURE["early_r"]),
                        max_hold_seconds=FIXTURE["max_hold_seconds"])
                    result = run_replay(events, request, config, funding_events)
                    rows.append(campaign_summary(result, episode, profile, path, cost))
                    if len(episodes) == 1:
                        examples.append(dict(episode_id=episode["episode_id"], path=path,
                                             profile=profile, cost_multiple=cost, replay=asdict(result)))
    code_paths = ("analysis/scalp_replay_benchmark.py", "analysis/replay_data.py",
                  "backtest/execution_replay.py", "core/scalp.py", "engine/signal.py",
                  "engine/regime.py", "engine/indicators.py", "engine/sizing.py", "engine/config.py")
    base = Path(__file__).resolve().parents[1]
    packet = dict(contract_version=VERSION, verdict="RESEARCH_ONLY_INSUFFICIENT",
                  auto_activate=False, actual_execution_samples=0,
                  start_ms=start_ms, end_ms_exclusive=end_ms, fixture=FIXTURE,
                  manifests=manifests,
                  code_sha256={p:hashlib.sha256((base / p).read_bytes()).hexdigest() for p in code_paths},
                  episode_count=len(episodes), scenario_count=len(rows), excluded_tail=excluded_tail,
                  episodes=episodes, results=rows, summary=aggregate(rows), sample_traces=examples,
                  funding_cashflow_audit=funding_cashflow_probe(execution.rows, funding.rows),
                  limitations=["EXISTING_SIGNAL_CONDITIONAL_NOT_SCALP_ENTRY_STRATEGY",
                               "INDEPENDENT_CAMPAIGNS_NOT_PORTFOLIO_OR_COMPOUND_RETURN",
                               "HYPOTHETICAL_OHLC_PATHS_AND_NODE_TIMES_NOT_STRICT_BOUNDS",
                               "UNLIMITED_MODEL_LIQUIDITY_NOT_OBSERVED_FILLS",
                               "FUNDING_MARK_AND_NAV_LAST_PRICE_PROXY",
                               "SCALP_2H_FIXTURE_HAS_NO_POST_ENTRY_FUNDING_BOUNDARY",
                               "60_BAR_ATR_SEED_NOT_FULL_HISTORY_MAIN_SIGNAL_PARITY",
                               "SUB_5M_CADENCE_NOT_VALIDATED", "NO_MARGIN_LIQUIDATION_MODEL",
                               "HISTORICAL_DATA_PREVIOUSLY_SEEN_NOT_UNTOUCHED_HOLDOUT"])
    packet["packet_sha256"] = canonical_hash(packet)
    return packet


def compact_packet(packet: dict) -> dict:
    """Small delivery artifact retaining exact fingerprints of omitted rows."""
    compact = {key: value for key, value in packet.items()
               if key not in ("episodes", "results", "sample_traces", "packet_sha256")}
    compact.update(full_packet_sha256=packet["packet_sha256"],
                   episodes_sha256=canonical_hash(packet["episodes"]),
                   result_rows_sha256=canonical_hash(packet["results"]),
                   sample_traces_sha256=canonical_hash(packet["sample_traces"]),
                   result_examples=packet["results"][:8],
                   omitted_rows_reproducible_with="same command without --summary-only")
    compact["artifact_sha256"] = canonical_hash(compact)
    return compact


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)
    packet = run_benchmark(args.market_db, args.execution_db, utc_ms(args.start), utc_ms(args.end))
    if args.summary_only:
        packet = compact_packet(packet)
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
