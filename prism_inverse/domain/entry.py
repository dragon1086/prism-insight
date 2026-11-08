"""④ Entry on the underlying: the day the gate turns on, or a weak rally up to a moving average."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from prism_inverse.domain.indicators import atr, closes, sma
from prism_inverse.domain.models import Bar
from prism_inverse.domain.policy import InversePolicy


@dataclass(frozen=True)
class EntrySignal:
    reason: str                       # ENTRY_* when it fires, otherwise a NO_*/MISSING_* code
    stop: Optional[float] = None      # underlying level; exit when a close reaches it
    target: Optional[float] = None

    @property
    def fired(self) -> bool:
        return self.reason.startswith("ENTRY_")


def entry_signal(bars: Sequence[Bar], gate_turned_on_today: bool, policy: InversePolicy) -> EntrySignal:
    if not bars:
        return EntrySignal("MISSING_BARS")
    a = atr(bars, policy.atr_days)
    if a is None:
        return EntrySignal("MISSING_ATR")
    close = bars[-1].close
    target = close - policy.target_atr * a if policy.target_atr is not None else None

    if policy.entry_mode == "signal":
        if not gate_turned_on_today:
            return EntrySignal("NO_NEW_SIGNAL")
        return EntrySignal("ENTRY_SIGNAL_DAY", close + policy.stop_atr * a, target)

    if policy.entry_mode == "rally":
        ma = sma(closes(bars), policy.rally_ma)
        if ma is None:
            return EntrySignal("MISSING_MA")
        touched = bars[-1].high >= ma * (1 - policy.rally_band)
        if not (touched and close < ma):
            return EntrySignal("NO_RALLY_TO_MA")
        return EntrySignal(f"ENTRY_RALLY_MA{policy.rally_ma}", max(ma, close + policy.stop_atr * a), target)

    raise ValueError(f"unknown entry_mode {policy.entry_mode!r}")
