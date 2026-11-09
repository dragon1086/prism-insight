"""Virtual (paper) trade for one signal family. Ported from s4_track.sim (stop atr2, target 3, 20 days).

Enter at the next session's open of the inverse ETF; judge each underlying close from the entry
day on; exit at the next session's open. The job runs after the close, so each "next open" is
filled on a later run. A missing bar keeps the trade waiting with ``error`` set; no price is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Mapping, Optional, Sequence

from prism_inverse.domain.models import Bar

PENDING_ENTRY, OPEN, PENDING_EXIT, CLOSED = "PENDING_ENTRY", "OPEN", "PENDING_EXIT", "CLOSED"
STOP_ATR, TARGET_ATR, MAX_HELD, COST = 2.0, 3.0, 20, 0.001


@dataclass(frozen=True)
class VirtualTrade:
    trade_id: str
    family: str
    etf: str
    underlying: str
    signal_date: date
    atr: float                                # underlying ATR20 on the signal day
    status: str = PENDING_ENTRY
    entry_date: Optional[date] = None
    entry_open: Optional[float] = None        # inverse ETF open
    entry_underlying: Optional[float] = None  # underlying open, same day
    stop: Optional[float] = None
    target: Optional[float] = None
    exit_signal_date: Optional[date] = None
    exit_date: Optional[date] = None
    exit_open: Optional[float] = None
    exit_reason: Optional[str] = None
    ret: Optional[float] = None
    error: Optional[str] = None               # why the trade could not advance this run (MISSING_*)


def _next_session(calendar: Sequence[date], day: date) -> Optional[date]:
    return next((d for d in calendar if d > day), None)


def advance(t: VirtualTrade, calendar: Sequence[date], etf: Mapping[date, Bar],
            und: Mapping[date, Bar]) -> VirtualTrade:
    """Move the trade as far as the bars allow. ``calendar``: known sessions up to today."""
    t = replace(t, error=None)
    if t.status == PENDING_ENTRY:
        day = _next_session(calendar, t.signal_date)
        if day is None:
            return t
        if day not in etf or day not in und:
            return replace(t, error=f"MISSING_ENTRY_BAR {day.isoformat()}")
        e = und[day].open
        t = replace(t, status=OPEN, entry_date=day, entry_open=etf[day].open, entry_underlying=e,
                    stop=e + STOP_ATR * t.atr, target=e - TARGET_ATR * t.atr)
    if t.status == OPEN:
        held = 0
        for day in (d for d in calendar if d >= t.entry_date):
            if day not in und:
                return replace(t, error=f"MISSING_UNDERLYING_BAR {day.isoformat()}")
            held += 1
            c = und[day].close
            reason = "STOP" if c >= t.stop else "TARGET" if c <= t.target else "TIME" if held >= MAX_HELD else None
            if reason:
                t = replace(t, status=PENDING_EXIT, exit_signal_date=day, exit_reason=reason)
                break
    if t.status == PENDING_EXIT:
        day = _next_session(calendar, t.exit_signal_date)
        if day is None:
            return t
        if day not in etf:
            return replace(t, error=f"MISSING_EXIT_BAR {day.isoformat()}")
        x = etf[day].open
        t = replace(t, status=CLOSED, exit_date=day, exit_open=x, ret=x / t.entry_open - 1 - COST)
    return t
