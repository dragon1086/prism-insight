"""Assemble ``LaneInputs`` for one day from full histories. Used by both cycle and replay."""

from __future__ import annotations

import statistics
from bisect import bisect_right
from datetime import date
from typing import Mapping, Optional, Sequence

from prism_inverse.app.pulse import today_and_yesterday
from prism_inverse.domain.engine import LaneInputs
from prism_inverse.domain.models import Bar, EtfSnapshot, EtfSpec, LeaderState, Position
from prism_inverse.domain.ticks import etf_tick


def upto(bars: Sequence[Bar], day: date) -> Sequence[Bar]:
    """Bars dated on or before ``day`` (no look-ahead)."""
    return bars[:bisect_right([b.day for b in bars], day)]


def snapshot_from_bars(spec: EtfSpec, bars: Sequence[Bar]) -> EtfSnapshot:
    """Price, tick and 20-day median traded value from the ETF's own daily bars.

    NAV premium and tracking error are not derivable from bars, so they stay ``None``.
    """
    if not bars:
        return EtfSnapshot()
    last20 = bars[-20:]
    values = [b.close * b.volume for b in last20 if b.volume is not None]
    value_20d = statistics.median(values) if len(last20) == 20 and len(values) == 20 else None
    price = bars[-1].close
    return EtfSnapshot(price=price, tick=etf_tick(spec.market, price), value_20d=value_20d)


def build_inputs(
    day: date, market: str, specs: Sequence[EtfSpec],
    underlying_bars: Mapping[str, Sequence[Bar]],
    snapshots: Mapping[str, EtfSnapshot],
    pulses: Mapping[str, dict],
    leaders: Mapping[str, LeaderState],
    positions: Sequence[Position], attempts: Mapping[str, int], equity: Optional[float],
    overlap: Optional[Mapping[str, Optional[float]]] = None, long_lane: Optional[frozenset] = frozenset(),
) -> LaneInputs:
    return LaneInputs(
        day=day, market=market,
        etfs=[(s, snapshots[s.code]) for s in specs if s.code in snapshots],
        bars={code: upto(bars, day) for code, bars in underlying_bars.items()},
        pulse={code: today_and_yesterday(series, day) for code, series in pulses.items()},
        leaders=dict(leaders), overlap=dict(overlap or {}), long_lane=long_lane,
        positions=list(positions), attempts=dict(attempts), equity=equity,
    )
