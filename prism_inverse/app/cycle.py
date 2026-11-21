"""One live run: collect inputs -> decide -> record -> (observe) virtual position book.

There is no order path. ``mode`` is ``dry-run`` (compute and return only) or ``observe``
(write the lane DB and events, keep virtual positions). ``live`` arrives with S5.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence

from prism_inverse.app.inputs import build_inputs
from prism_inverse.app.pulse import pulse_series, today_and_yesterday
from prism_inverse.domain.broken_leaders import leader_state
from prism_inverse.domain.engine import decide
from prism_inverse.domain.models import ENTER, EXIT, LANE_STOCK, Decision, EtfSnapshot, EtfSpec, Position
from prism_inverse.domain.policy import POLICY_VERSION, InversePolicy
from prism_inverse.ports import EtfInfo, EventSink, LongLaneBook, MarketData, Store, Unavailable, Unsupported

MODES = ("dry-run", "observe")
HISTORY_DAYS = 800  # calendar days: 250-session lookback + 200-day line, with margin


def trace_id(market: str, decision_id: str) -> str:
    """Same rule as the rest of PRISM so ``tools/clickstack_query.py trace`` links the lane."""
    return hashlib.sha256(f"trade-trace|{market.upper()}|{decision_id}".encode()).hexdigest()[:32]


@dataclass
class CycleResult:
    run_id: str
    decisions: list[Decision] = field(default_factory=list)
    data_errors: dict[str, str] = field(default_factory=dict)


def run_cycle(
    market: str, day: date, specs: Sequence[EtfSpec], data: MarketData, info: EtfInfo,
    store: Store, events: EventSink, long_lane: LongLaneBook, policy: InversePolicy,
    equity: Optional[float], mode: str = "dry-run",
) -> CycleResult:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}; live orders do not exist before S5")
    write = mode == "observe"
    run_id = f"inv-{market}-{day.isoformat()}-{uuid.uuid4().hex[:8]}"
    result = CycleResult(run_id)
    if write:
        store.start_run(run_id, market, day, mode, POLICY_VERSION, policy.config_hash())
    try:
        specs = [s for s in specs if s.market == market]
        main = policy.main_underlying(market)
        underlyings = {s.underlying.code: s.underlying for s in specs}
        bars, pulses, leaders, snaps = {}, {}, {}, {}
        for code, inst in underlyings.items():
            try:
                bars[code] = data.daily_bars(inst, day - timedelta(days=HISTORY_DAYS), day)
            except (Unavailable, Unsupported) as exc:
                result.data_errors[code] = f"{type(exc).__name__}: {exc}"
        if main not in bars and main not in result.data_errors:
            result.data_errors[main] = "MAIN_UNDERLYING_NOT_IN_REGISTRY"
        for code, series in bars.items():
            pulses[code] = pulse_series(series)
            stock = any(s.underlying.code == code and s.lane == LANE_STOCK for s in specs)
            leaders[code] = leader_state(code, series, policy) if stock else None
            if write:
                today, yesterday = today_and_yesterday(pulses[code], day)
                store.record_market(run_id, code, today, yesterday, leaders[code])
        for spec in specs:
            try:
                snaps[spec.code] = info.snapshot(spec, day)
                error = None
            except (Unavailable, Unsupported) as exc:
                snaps[spec.code] = EtfSnapshot()  # all None -> rejects as MISSING_*, never as cheap
                error = f"{type(exc).__name__}: {exc}"
                result.data_errors[spec.code] = error
            if write:
                store.record_snapshot(run_id, spec, snaps[spec.code], error)

        try:
            held_long = long_lane.tickers(market)
        except (Unavailable, Unsupported) as exc:
            held_long = None  # unknown: the stock lane skips rather than assume "holds nothing"
            result.data_errors["long_lane"] = f"{type(exc).__name__}: {exc}"
        positions = store.open_positions(market) if write else []
        inputs = build_inputs(
            day, market, specs, bars, snaps, pulses,
            {code: st for code, st in leaders.items() if st is not None},
            positions, store.attempts(market) if write else {}, equity,
            long_lane=held_long,
        )
        result.decisions = decide(inputs, policy)
        by_position = {p.position_id: p for p in positions}
        for i, d in enumerate(result.decisions):
            decision_id = f"{run_id}-{i}"
            tid = trace_id(market, d.position_id) if d.position_id else None
            events.emit(_event_type(d), market=market, ticker=d.etf, trace_id=tid,
                        decision_id=decision_id, position_id=d.position_id,
                        attributes={"lane": d.lane, "reason": d.reason, "underlying": d.underlying,
                                    "underlying_close": d.underlying_close, "stop": d.stop_underlying,
                                    "target": d.target_underlying, "qty": d.qty, "mode": mode,
                                    "policy_version": POLICY_VERSION, "config_hash": policy.config_hash()})
            if not write:
                continue
            store.record_decision(run_id, decision_id, tid, d)
            if d.action == ENTER:
                spec = next(s for s in specs if s.code == d.etf)
                store.open_position(market, Position(
                    d.position_id, d.etf, d.lane, spec.multiplier, d.qty, snaps[d.etf].price,
                    d.underlying_close, d.stop_underlying, d.target_underlying, day,
                    d.inputs.get("attempt_no", 1), d.underlying), tid)
            elif d.action == EXIT and d.position_id in by_position:
                etf = by_position[d.position_id].etf
                store.close_position(d.position_id, day, snaps.get(etf, EtfSnapshot()).price, d.reason)
        if write:
            store.finish_run(run_id, "ok")
    except Exception as exc:
        if write:
            store.finish_run(run_id, "error", f"{type(exc).__name__}: {exc}")
        raise
    return result


def _event_type(d: Decision) -> str:
    if d.action == ENTER:
        return "inverse.entry_decided"
    if d.action == EXIT:
        return "inverse.exit_decided"
    if d.action == "HOLD":
        return "inverse.holding_evaluated"
    return "inverse.entry_skipped"
