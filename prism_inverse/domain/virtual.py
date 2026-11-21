"""Virtual (paper) trade for one signal family. Ported from s18_payoff.sim (2026-11-10).

Enter at the next session's open; judge each underlying close from the entry day on; exit at the
next session's open. The job runs after the close, so each "next open" is filled on a later run.
A missing bar keeps the trade waiting with ``error`` set; no price is invented.

Exit rules (``Rules``): first stop ``stop_atr`` above entry; target ``target_atr`` below; trailing
stop ``trail_atr`` above the lowest close since entry; stop moved to entry after ``be_atr`` in
favour; ``max_held`` sessions; and the no-progress exit (still at or above entry on session 3), the
lever that took both markets to payoff >= 2 (S18).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Mapping, Optional, Sequence

from prism_inverse.domain.models import Bar

PENDING_ENTRY, OPEN, PENDING_EXIT, CLOSED = "PENDING_ENTRY", "OPEN", "PENDING_EXIT", "CLOSED"
NOPROG_DAY = 3


@dataclass(frozen=True)
class Rules:
    stop_atr: float
    target_atr: float
    max_held: int
    trail_atr: Optional[float] = None
    be_atr: Optional[float] = None
    synthetic_mult: Optional[int] = None   # None: real ETF open-to-open; 2: daily-reset -2x on the underlying
    cost: float = 0.001


KR_RULES = Rules(stop_atr=1.5, target_atr=3.0, max_held=10, trail_atr=3.0, be_atr=1.5)   # S18 KR pooled: payoff 2.51
US_RULES = Rules(stop_atr=2.0, target_atr=3.0, max_held=20, synthetic_mult=2, cost=0.0015)  # S18 US 28 sectors: payoff 2.11


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
    entry_open: Optional[float] = None        # inverse ETF open (underlying open when synthetic)
    entry_underlying: Optional[float] = None  # underlying open, same day
    stop: Optional[float] = None              # first stop; the breakeven move and trail are recomputed from bars
    target: Optional[float] = None
    exit_signal_date: Optional[date] = None
    exit_date: Optional[date] = None
    exit_open: Optional[float] = None
    exit_reason: Optional[str] = None
    ret: Optional[float] = None
    error: Optional[str] = None               # why the trade could not advance this run (MISSING_*)


def _next_session(calendar: Sequence[date], day: date) -> Optional[date]:
    return next((d for d in calendar if d > day), None)


def _synthetic_ret(t: VirtualTrade, und: Mapping[date, Bar], days: Sequence[date], exit_open: float, r: Rules) -> float:
    """Daily-reset -m× on the underlying: entry open -> closes -> exit open."""
    m, v, prev = r.synthetic_mult, 1.0, t.entry_underlying
    for d in days:
        v *= 1 - m * (und[d].close / prev - 1)
        prev = und[d].close
    v *= 1 - m * (exit_open / prev - 1)
    return v - 1 - r.cost


def advance(t: VirtualTrade, calendar: Sequence[date], etf: Mapping[date, Bar], und: Mapping[date, Bar],
            rules: Rules = KR_RULES) -> VirtualTrade:
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
                    stop=e + rules.stop_atr * t.atr, target=e - rules.target_atr * t.atr)
    if t.status == OPEN:
        e, at, held, low, stop = t.entry_underlying, t.atr, 0, None, t.stop
        for day in (d for d in calendar if d >= t.entry_date):
            if day not in und:
                return replace(t, error=f"MISSING_UNDERLYING_BAR {day.isoformat()}")
            held += 1
            c = und[day].close
            low = c if low is None else min(low, c)
            if rules.be_atr and low <= e - rules.be_atr * at:
                stop = min(stop, e)
            reason = ("STOP" if c >= stop else "TARGET" if c <= t.target else "TIME" if held >= rules.max_held
                      else "NOPROG" if held == NOPROG_DAY and c >= e
                      else "TRAIL" if rules.trail_atr and c >= low + rules.trail_atr * at else None)
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
        if rules.synthetic_mult:
            days = [d for d in calendar if t.entry_date <= d <= t.exit_signal_date]
            gap = next((d for d in days if d not in und), None)
            if gap is not None:
                return replace(t, error=f"MISSING_UNDERLYING_BAR {gap.isoformat()}")
            ret = _synthetic_ret(t, und, days, x, rules)
        else:
            ret = x / t.entry_open - 1 - rules.cost
        t = replace(t, status=CLOSED, exit_date=day, exit_open=x, ret=ret)
    return t
