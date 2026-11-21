"""Past replay on daily bars with the same ``decide`` as the live cycle.

Decisions made on day d fill at the ETF's next-session open, paying half a tick each way.
Leader states are evaluated on each day's history only (no look-ahead).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Optional, Sequence

from prism_inverse.app.inputs import build_inputs, snapshot_from_bars, upto
from prism_inverse.app.pulse import pulse_series
from prism_inverse.domain.broken_leaders import leader_states
from prism_inverse.domain.engine import decide
from prism_inverse.domain.models import ENTER, EXIT, LANE_STOCK, Bar, Decision, EtfSpec, Position
from prism_inverse.domain.policy import InversePolicy
from prism_inverse.domain.ticks import etf_tick


@dataclass(frozen=True)
class Trade:
    position_id: str
    lane: str
    etf: str
    attempt_no: int
    entry_day: date
    entry_price: float
    exit_day: Optional[date]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    qty: int

    @property
    def return_pct(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return self.exit_price / self.entry_price - 1


@dataclass
class ReplayResult:
    trades: list[Trade] = field(default_factory=list)
    decisions: dict[date, list[Decision]] = field(default_factory=dict)


def _next_open(bars: Sequence[Bar], day: date) -> Optional[Bar]:
    later = bars[len(upto(bars, day)):]
    return later[0] if later else None


def replay(
    market: str, specs: Sequence[EtfSpec], days: Sequence[date],
    underlying_bars: Mapping[str, Sequence[Bar]], etf_bars: Mapping[str, Sequence[Bar]],
    policy: InversePolicy, equity: float = 100_000_000.0,
    long_lane_by_day: Optional[Mapping[date, frozenset]] = None,
) -> ReplayResult:
    pulses = {code: pulse_series(bars) for code, bars in underlying_bars.items()}
    stock_codes = {s.underlying.code for s in specs if s.lane == LANE_STOCK}
    leader_by_day = {
        code: {bars[i].day: st for i, st in enumerate(leader_states(code, bars, policy))}
        for code, bars in underlying_bars.items() if code in stock_codes
    }
    spec_by_code = {s.code: s for s in specs}
    result = ReplayResult()
    open_pos: dict[str, tuple[Position, date]] = {}
    attempts: dict[str, int] = {}

    for day in days:
        snaps = {s.code: snapshot_from_bars(s, upto(etf_bars.get(s.code, ()), day)) for s in specs}
        leaders = {code: states[day] for code, states in leader_by_day.items() if day in states}
        inputs = build_inputs(
            day, market, specs, underlying_bars, snaps, pulses, leaders,
            [p for p, _ in open_pos.values()], attempts, equity,
            long_lane=(long_lane_by_day or {}).get(day, frozenset()),
        )
        decisions = decide(inputs, policy)
        result.decisions[day] = decisions
        for d in decisions:
            if d.action == EXIT and d.position_id in open_pos:
                pos, opened = open_pos.pop(d.position_id)
                fill = _next_open(etf_bars.get(pos.etf, ()), day)
                price = fill.open - etf_tick(market, fill.open) / 2 if fill else None
                result.trades.append(Trade(pos.position_id, pos.lane, pos.etf, pos.attempt_no, opened,
                                           pos.entry_price, fill.day if fill else None, price, d.reason, pos.qty))
            elif d.action == ENTER:
                spec = spec_by_code[d.etf]
                fill = _next_open(etf_bars.get(d.etf, ()), day)
                if fill is None:
                    continue  # no next session in the data: the order would not have filled
                price = fill.open + etf_tick(market, fill.open) / 2
                attempt = d.inputs.get("attempt_no", 1)
                attempts[spec.underlying.code] = attempt
                open_pos[d.position_id] = (Position(
                    d.position_id, d.etf, d.lane, spec.multiplier, d.qty, price, d.underlying_close,
                    d.stop_underlying, d.target_underlying, day, attempt, d.underlying), fill.day)
    for pos, opened in open_pos.values():
        result.trades.append(Trade(pos.position_id, pos.lane, pos.etf, pos.attempt_no, opened,
                                   pos.entry_price, None, None, None, pos.qty))
    return result
