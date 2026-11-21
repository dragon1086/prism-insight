"""Dr. K market-direction model (Morales & Kacher, ch.7) as a SHADOW baseline — pure, no I/O.

Research comparison baseline only: nothing in the gate, batch, prompt or order paths imports this
module (``tests/test_drk_market_direction.py`` pins that). Production direction stays
``cores/market_pulse.py``.

Book rules (owner notes ``drk-market-direction-model.md``) mapped to code:

* State ``INVESTED`` (after a buy signal) / ``CASH`` (after a sell signal). Starts ``INVESTED``.
* Distribution day, volume up vs the prior session required:
  (a) close <= -0.1% and the close in the lower half of the day's range
      (a close in the upper half — long lower wick — is not distribution);
  (b) close < +0.1% and the close in the bottom 25% of the range (long upper wick);
  (c) churning — ASSUMPTION (the book gives no numbers): gain in [0, +0.2%), volume
      >= +20% and the close in the lower half;
  (d) hold on index-event days — ASSUMPTION for the confirmation rule: a candidate on an
      event day (quad witching, Russell, MSCI; KR K200 change, quarterly expiry, MSCI) counts
      only if the next close is lower than the event-day close.
  Expiry: the 20-session window only (no +5% recovery expiry).
* Sell: the day a distribution day brings the 20-session count to >= 5 is the ready day; a later
  low under the ready-day low is the sell signal. A later high over the ready-day high voids it.
  When both happen on the same bar (intrabar order unknown) the close decides: close under the
  ready low = sell, otherwise void.
* Rally attempt (CASH): day 1 = first close above the prior close after a new low, or the new-low
  day itself when its close is in the upper half of its range. A low under the rally low restarts.
* Follow-through (buy): rally day >= ``ftd_min_day`` (4; O'Neil "day 4 or later"), gain >= the
  threshold fixed on rally day 1, volume up. The book's typical "day 4~7" cap is the
  ``ftd_max_day`` option, off by default: with it, a rally whose low is never undercut can never
  start again, and NASDAQ replay stays in CASH from 1985 to 2026. Day-3 exceptions are not
  implemented (09-29 P2 rejected them).
* Buy-signal invalidation (back to CASH, rally inactive, low anchor kept): the next session's low
  under the signal-day low; or, after >= 5 new intraday highs since the signal, a down close under
  the signal-day close. The book's gap-up rule ("the gap day's close under the prior close") cannot
  fire when the signal itself is a close up by the threshold, so it is not implemented.
* After a buy signal the count is NOT reset; only the first distribution day after the signal is
  dropped, unless that day closes under the signal-day low.

Missing high/low (or high == low): candle rules cannot be applied, so only rule (a) without the half-range filter
runs and the record says ``candle_unknown``. Missing volume: no distribution day, no FTD.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence

INVESTED = "INVESTED"
CASH = "CASH"

SIGMA_LOOKBACK = 60


def _sd(returns: Sequence[float]) -> Optional[float]:
    return statistics.stdev(returns) if len(returns) >= SIGMA_LOOKBACK else None


def _mad_sd(returns: Sequence[float]) -> Optional[float]:
    if len(returns) < SIGMA_LOOKBACK:
        return None
    med = statistics.median(returns)
    return 1.4826 * statistics.median(abs(r - med) for r in returns)


def _linear(sigma: Optional[float]) -> Optional[float]:
    # Line through the book's six era thresholds vs NASDAQ daily sigma (1974-2010); not fitted to trades.
    return None if sigma is None else 0.85 + 0.43 * sigma


# FTD threshold (%) from the last 60 daily % returns up to rally day 1. Fixed at five; do not add more.
FTD_THRESHOLDS: Dict[str, Callable[[Sequence[float]], Optional[float]]] = {
    "fixed_1.25": lambda r: 1.25,
    "fixed_1.5": lambda r: 1.5,
    "lin_sd60": lambda r: _linear(_sd(r)),
    "lin_mad60": lambda r: _linear(_mad_sd(r)),
    "prop_sd60": lambda r: None if _sd(r) is None else 1.1 * _sd(r),
}


@dataclass(frozen=True)
class DrkConfig:
    ftd_threshold: str = "fixed_1.5"
    ftd_min_day: int = 4
    ftd_max_day: Optional[int] = None     # option: book's typical day-7 cap (locks out, see docstring)
    low_day_is_day1: bool = False        # option: count the new-low day as rally day 1
    dd_drop_pct: float = 0.1
    dd_window: int = 20
    sell_ready_count: int = 5
    churn: bool = True                   # ASSUMPTION thresholds below (book gives none)
    churn_max_gain_pct: float = 0.2
    churn_min_volume_jump_pct: float = 20.0
    event_dates: FrozenSet[str] = field(default_factory=frozenset)
    start_state: str = INVESTED


def event_dates(dates: Iterable[str], market: str) -> FrozenSet[str]:
    """Index-event sessions (approximate calendar rules; a holiday moves to the prior session).

    KR: 2nd Thursday of Mar/Jun/Sep/Dec (quarterly expiry; Jun/Dec also KOSPI200 change) and the
    last session of Feb/May/Aug/Nov (MSCI). US: 3rd Friday of Mar/Jun/Sep/Dec (quad witching +
    S&P rebalance), last Friday of June (Russell), last session of Feb/May/Aug/Nov (MSCI).
    Monthly option expiry is excluded: it showed no distribution clustering (2012-2026 check).
    """
    days = sorted({_date.fromisoformat(d) for d in dates})
    by_month: Dict[tuple, List[_date]] = {}
    for d in days:
        by_month.setdefault((d.year, d.month), []).append(d)
    weekday, nth = (3, 2) if market.lower() == "kr" else (4, 3)
    out = set()
    for (y, m), sessions in by_month.items():
        cal = [_date(y, m, k) for k in range(1, 32) if _valid(y, m, k)]
        targets = []
        if m in (2, 5, 8, 11):
            targets.append([d for d in cal if d.weekday() < 5][-1])
        if m in (3, 6, 9, 12):
            targets.append([d for d in cal if d.weekday() == weekday][nth - 1])
        if market.lower() != "kr" and m == 6:
            targets.append([d for d in cal if d.weekday() == 4][-1])
        for target in targets:
            prior = [d for d in sessions if d <= target]
            if prior and target <= days[-1]:   # an unfinished month has no event yet
                out.add(prior[-1])
    return frozenset(d.isoformat() for d in out)


def _valid(y: int, m: int, k: int) -> bool:
    try:
        _date(y, m, k)
        return True
    except ValueError:
        return False


class DrkDirection:
    """Feed bars (``cores.market_pulse.DailyBar``: date, close, volume, high, low) in order."""

    def __init__(self, config: DrkConfig = DrkConfig()) -> None:
        if config.ftd_threshold not in FTD_THRESHOLDS:
            raise ValueError(f"unknown ftd_threshold {config.ftd_threshold!r}")
        self.cfg = config
        self.state = config.start_state
        self._bars: list = []
        self._returns: List[float] = []
        self._dd_idx: List[int] = []          # counted distribution-day indices
        self._pending_hold: Optional[int] = None
        self._ready: Optional[dict] = None
        self._signal: Optional[dict] = None   # last buy signal (index, low, close, new_highs, max_high)
        self._first_dd_pending = False
        self._anchor_low: Optional[float] = None
        self._anchor_idx = 0
        self._rally_day = 0
        self._rally_low: Optional[float] = None
        self._threshold: Optional[float] = None

    # ------------------------------------------------------------------ helpers
    def dd_count(self) -> int:
        i = len(self._bars) - 1
        return sum(1 for k in self._dd_idx if k > i - self.cfg.dd_window)

    def _classify(self, bar, prev) -> tuple:
        """Return (kind or None, candle_unknown)."""
        if bar.volume is None or prev.volume is None or not bar.volume > prev.volume or prev.close <= 0:
            return None, False
        pct = (bar.close - prev.close) / prev.close * 100.0
        if bar.high is None or bar.low is None or not bar.high > bar.low:
            # No range (missing, or a flat close-only bar as in pre-1985 NASDAQ data).
            return ("down" if pct <= -self.cfg.dd_drop_pct else None), True
        pos = (bar.close - bar.low) / (bar.high - bar.low)
        if pct <= -self.cfg.dd_drop_pct and pos < 0.5:
            return "down", False
        if pct < 0.1 and pos <= 0.25:
            return "upper_wick", False
        jump = (bar.volume / prev.volume - 1.0) * 100.0
        if (self.cfg.churn and 0 <= pct < self.cfg.churn_max_gain_pct
                and jump >= self.cfg.churn_min_volume_jump_pct and pos < 0.5):
            return "churn", False
        return None, False

    def _count_dd(self, idx: int, events: List[str]) -> None:
        bar = self._bars[idx]
        if self._first_dd_pending and self._signal is not None:
            self._first_dd_pending = False
            if bar.close >= (self._signal["low"] if self._signal["low"] is not None else self._signal["close"]):
                events.append("dd_first_after_buy_excluded")
                return
        self._dd_idx.append(idx)
        events.append("dd_counted")
        if (self.state == INVESTED and self._ready is None
                and self.dd_count() >= self.cfg.sell_ready_count):
            self._ready = {"idx": idx, "high": bar.high, "low": bar.low}
            events.append("sell_ready")

    # ------------------------------------------------------------------ main
    def feed(self, bar) -> dict:
        prev = self._bars[-1] if self._bars else None
        self._bars.append(bar)
        n = len(self._bars) - 1
        events: List[str] = []
        kind, candle_unknown = (None, False)
        if prev is not None and prev.close > 0:
            self._returns.append((bar.close - prev.close) / prev.close * 100.0)
            self._returns = self._returns[-SIGMA_LOOKBACK:]

        # (d) resolve yesterday's held event-day candidate first.
        if self._pending_hold is not None:
            held = self._pending_hold
            self._pending_hold = None
            if bar.close < self._bars[held].close:
                events.append("dd_hold_confirmed")
                self._count_dd(held, events)
            else:
                events.append("dd_hold_dropped")

        if prev is not None:
            kind, candle_unknown = self._classify(bar, prev)
            if kind is not None:
                if bar.date in self.cfg.event_dates:
                    self._pending_hold = n
                    events.append(f"dd_held:{kind}")
                else:
                    events.append(f"dd:{kind}")
                    self._count_dd(n, events)

        if self.state == INVESTED:
            self._invested_step(bar, prev, n, events)
        else:
            self._cash_step(bar, prev, n, events)

        return {
            "date": bar.date, "state": self.state, "dd_count": self.dd_count(), "dd_kind": kind,
            "candle_unknown": candle_unknown, "rally_day": self._rally_day if self.state == CASH else 0,
            "ftd_threshold": self._threshold if self.state == CASH and self._rally_day else None,
            "events": events,
        }

    def _invested_step(self, bar, prev, n, events) -> None:
        sig = self._signal
        if sig is not None and n > sig["idx"]:
            if n == sig["idx"] + 1 and bar.low is not None and sig["low"] is not None and bar.low < sig["low"]:
                return self._invalidate(events, "buy_invalid_next_day_low")
            if bar.high is not None and bar.high > sig["max_high"]:
                sig["max_high"] = bar.high
                sig["new_highs"] += 1
            if sig["new_highs"] >= 5 and prev is not None and bar.close < prev.close and bar.close < sig["close"]:
                return self._invalidate(events, "buy_invalid_after_new_highs")
        ready = self._ready
        if ready is None or n <= ready["idx"] or ready["low"] is None:
            return
        broke_low = bar.low is not None and bar.low < ready["low"]
        over_high = ready["high"] is not None and bar.high is not None and bar.high > ready["high"]
        if broke_low and over_high:
            if bar.close < ready["low"]:
                return self._sell(bar, events, "sell_signal_same_bar_close")
            self._ready = None
            events.append("sell_ready_void_same_bar_close")
        elif broke_low:
            self._sell(bar, events, "sell_signal")
        elif over_high:
            self._ready = None
            events.append("sell_ready_void_high")

    def _sell(self, bar, events, label) -> None:
        events.append(label)
        self.state = CASH
        self._ready = None
        self._signal = None
        self._first_dd_pending = False
        self._anchor_low = bar.low if bar.low is not None else bar.close
        self._anchor_idx = len(self._bars) - 1
        self._rally_day = 0
        self._rally_low = None

    def _invalidate(self, events, label) -> None:
        events.append(label)
        self.state = CASH
        self._signal = None
        self._first_dd_pending = False
        self._rally_day = 0
        self._rally_low = None

    def _start_rally(self, bar, low, events, day: int) -> None:
        self._rally_day = day
        self._rally_low = low
        self._threshold = FTD_THRESHOLDS[self.cfg.ftd_threshold](self._returns)
        events.append(f"rally_day{day}")

    def _cash_step(self, bar, prev, n, events) -> None:
        low = bar.low if bar.low is not None else bar.close
        pos = None
        if bar.high is not None and bar.low is not None and bar.high > bar.low:
            pos = (bar.close - bar.low) / (bar.high - bar.low)
        if self._rally_day and self._rally_low is not None and low < self._rally_low:
            events.append("rally_fail")
            self._rally_day = 0
            self._rally_low = None
        new_low = self._anchor_low is None or low < self._anchor_low
        if new_low:
            self._anchor_low, self._anchor_idx = low, n
        if not self._rally_day:
            if new_low:
                if pos is not None and pos >= 0.5:
                    self._start_rally(bar, low, events, 1)
            elif prev is not None and bar.close > prev.close:
                day = n - self._anchor_idx + 1 if self.cfg.low_day_is_day1 else 1
                self._start_rally(bar, self._anchor_low, events, day)
            return
        self._rally_day += 1
        max_day = self.cfg.ftd_max_day
        if self._rally_day < self.cfg.ftd_min_day or (max_day is not None and self._rally_day > max_day):
            return
        if self._threshold is None:   # too little history on day 1: fix it on the first known day
            self._threshold = FTD_THRESHOLDS[self.cfg.ftd_threshold](self._returns)
        if self._threshold is None or prev is None or prev.volume is None or bar.volume is None:
            return
        gain = (bar.close - prev.close) / prev.close * 100.0
        if gain >= self._threshold and bar.volume > prev.volume:
            events.append("follow_through_buy")
            self.state = INVESTED
            self._signal = {"idx": n, "low": bar.low, "close": bar.close, "new_highs": 0,
                            "max_high": bar.high if bar.high is not None else bar.close}
            self._first_dd_pending = True
            self._ready = None
            self._rally_day = 0
            self._rally_low = None


def replay(bars, config: DrkConfig = DrkConfig()) -> List[dict]:
    machine = DrkDirection(config)
    return [machine.feed(bar) for bar in bars]
