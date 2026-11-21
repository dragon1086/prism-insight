"""One decision step, shared by the live cycle and the replay so both decide identically.

Index/sector and single-stock lanes are evaluated and recorded separately (user 2026-11-09).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Sequence

from prism_inverse.domain.entry import entry_signal
from prism_inverse.domain.etf_selector import best_per_underlying, choose_index, prefer_multiplier, reject_reason
from prism_inverse.domain.exit import exit_reason
from prism_inverse.domain.market_gate import gate
from prism_inverse.domain.models import (
    ENTER, EXIT, HOLD, LANE_INDEX, LANE_STOCK, SKIP, Bar, Decision, EtfSnapshot, EtfSpec, LeaderState, Position,
)
from prism_inverse.domain.policy import InversePolicy
from prism_inverse.domain.sizing import size, worst_up_gap


@dataclass(frozen=True)
class LaneInputs:
    day: date
    market: str
    etfs: Sequence[tuple[EtfSpec, EtfSnapshot]]
    bars: Mapping[str, Sequence[Bar]]                 # underlying code -> bars up to and including ``day``
    pulse: Mapping[str, tuple[Optional[str], Optional[str]]]  # underlying code -> (today, yesterday)
    leaders: Mapping[str, LeaderState] = field(default_factory=dict)
    overlap: Mapping[str, Optional[float]] = field(default_factory=dict)  # underlying -> broken weight
    long_lane: Optional[frozenset] = frozenset()      # tickers the long lane holds; None = unknown
    positions: Sequence[Position] = ()
    attempts: Mapping[str, int] = field(default_factory=dict)  # underlying -> prior entries this bear
    equity: Optional[float] = None


def _pulse_now(inputs: LaneInputs, code: str) -> Optional[str]:
    return inputs.pulse.get(code, (None, None))[0]


def _turned_on(inputs: LaneInputs, code: str, policy: InversePolicy) -> bool:
    today, yesterday = inputs.pulse.get(code, (None, None))
    return today in policy.gate_states and yesterday is not None and yesterday not in policy.gate_states


def _enter(inputs: LaneInputs, spec: EtfSpec, snap: EtfSnapshot, lane: str, policy: InversePolicy,
           base: dict, gate_code: str) -> Decision:
    code = spec.underlying.code
    bars = inputs.bars.get(code) or []
    sig = entry_signal(bars, _turned_on(inputs, gate_code, policy), policy)
    close = bars[-1].close if bars else None
    common = dict(lane=lane, etf=spec.code, underlying=code, underlying_close=close)
    if not sig.fired:
        return Decision(SKIP, sig.reason, inputs=base, **common)
    stock = lane == LANE_STOCK
    gap = worst_up_gap(bars, policy.gap_lookback) if stock else None
    qty, why = size(inputs.equity, snap.price, spec.multiplier, close, sig.stop, policy, gap=gap, need_gap=stock)
    if qty <= 0:
        return Decision(SKIP, why, stop_underlying=sig.stop, target_underlying=sig.target, inputs=base, **common)
    attempt = inputs.attempts.get(code, 0) + 1
    return Decision(
        ENTER, sig.reason, stop_underlying=sig.stop, target_underlying=sig.target, qty=qty,
        position_id=f"{inputs.market}-{spec.code}-{inputs.day.isoformat()}",
        inputs={**base, "attempt_no": attempt, "worst_up_gap": gap, "etf_price": snap.price}, **common,
    )


def decide(inputs: LaneInputs, policy: InversePolicy) -> list[Decision]:
    out: list[Decision] = []
    main = policy.main_underlying(inputs.market)
    held_underlyings = set()
    exited_lanes = set()

    # Exits first. Index positions read their own underlying's pulse; stock positions the main index.
    spec_by_code = {s.code: s for s, _ in inputs.etfs}
    for pos in inputs.positions:
        spec = spec_by_code.get(pos.etf)
        code = spec.underlying.code if spec else None
        bars = (inputs.bars.get(code) or []) if code else []
        if not bars:
            # Unknown is not an exit signal: keep holding and say why.
            out.append(Decision(HOLD, "MISSING_BARS", pos.lane, pos.etf, code, position_id=pos.position_id))
            held_underlyings.add(code)
            continue
        pulse_code = main if pos.lane == LANE_STOCK else code
        why = exit_reason(pos, bars, _pulse_now(inputs, pulse_code), policy)
        if why:
            out.append(Decision(EXIT, why, pos.lane, pos.etf, code, bars[-1].close, position_id=pos.position_id))
            exited_lanes.add(pos.lane)
        else:
            out.append(Decision(HOLD, "HOLD", pos.lane, pos.etf, code, bars[-1].close, position_id=pos.position_id))
            held_underlyings.add(code)

    # Eligibility, recorded per ETF.
    eligible = []
    for spec, snap in inputs.etfs:
        reason = reject_reason(spec, snap, inputs.day, policy)
        if reason:
            out.append(Decision(SKIP, reason, spec.lane, spec.code, spec.underlying.code))
        else:
            eligible.append((spec, snap))
    eligible = best_per_underlying(eligible)
    snaps = {s.code: snap for s, snap in eligible}

    # Index/sector lane: one position at a time, on the gated underlying with the most broken leaders.
    open_lanes = {p.lane for p in inputs.positions}
    if LANE_INDEX not in open_lanes and LANE_INDEX not in exited_lanes:
        index_specs = [s for s, _ in eligible if s.lane == LANE_INDEX and s.multiplier == policy.index_multiplier]
        gated = []
        for spec in index_specs:
            ok, why = gate(_pulse_now(inputs, spec.underlying.code), inputs.overlap.get(spec.underlying.code), policy)
            if ok:
                gated.append(spec)
            else:
                out.append(Decision(SKIP, why, LANE_INDEX, spec.code, spec.underlying.code))
        chosen, why = choose_index(gated, inputs.overlap, main, policy)
        if chosen is None:
            if index_specs:
                out.append(Decision(SKIP, why, LANE_INDEX))
        else:
            base = {"choice": why, "overlap": inputs.overlap.get(chosen.underlying.code)}
            out.append(_enter(inputs, chosen, snaps[chosen.code], LANE_INDEX, policy, base, chosen.underlying.code))

    # Single-stock lane: the main index gates the whole lane (market first), then broken leaders only.
    if LANE_STOCK not in exited_lanes:
        room = policy.max_stock_positions - sum(1 for p in inputs.positions if p.lane == LANE_STOCK)
        ok, gate_why = gate(_pulse_now(inputs, main), None, policy)
        stock_specs = prefer_multiplier(
            (s for s, _ in eligible if s.lane == LANE_STOCK), tuple(policy.stock_multiplier_pref))
        for code, spec in sorted(stock_specs.items()):
            if code in held_underlyings:
                continue
            if not ok:
                out.append(Decision(SKIP, gate_why, LANE_STOCK, spec.code, code))
                continue
            if inputs.long_lane is None:
                out.append(Decision(SKIP, "MISSING_LONG_LANE", LANE_STOCK, spec.code, code))
                continue
            if code in inputs.long_lane:
                out.append(Decision(SKIP, "LONG_LANE_HOLDS", LANE_STOCK, spec.code, code))
                continue
            leader = inputs.leaders.get(code)
            if leader is None or not leader.is_broken:
                out.append(Decision(SKIP, "NOT_BROKEN_LEADER", LANE_STOCK, spec.code, code))
                continue
            if room <= 0:
                out.append(Decision(SKIP, "STOCK_SLOTS_FULL", LANE_STOCK, spec.code, code))
                continue
            base = {"leader_status": leader.status, "leader_since": str(leader.since)}
            dec = _enter(inputs, spec, snaps[spec.code], LANE_STOCK, policy, base, main)
            out.append(dec)
            room -= dec.action == ENTER
    return out
