"""⑤ Exit on the underlying. Order matters: the stop is checked first."""

from __future__ import annotations

from typing import Optional, Sequence

from prism_inverse.domain.models import Bar, Position
from prism_inverse.domain.policy import InversePolicy


def exit_reason(pos: Position, bars: Sequence[Bar], pulse_state: Optional[str], policy: InversePolicy) -> Optional[str]:
    """``None`` = keep holding. A missing pulse never forces an exit by itself."""
    if not bars:
        return None
    close = bars[-1].close
    if close >= pos.stop_underlying:
        return "STOP"
    if pulse_state == "UPTREND":
        return "PULSE_UPTREND"           # follow-through day or recovery above the prior peak
    if pos.target_underlying is not None and close <= pos.target_underlying:
        return "TARGET"
    held = sum(1 for b in bars if b.day > pos.opened)
    if policy.time_stop_days is not None and held >= policy.time_stop_days:
        progress = (pos.entry_underlying - close) / pos.entry_underlying
        if progress < policy.time_stop_min_move:
            return "TIME_STOP"
    if held >= policy.max_hold_days:
        return "MAX_HOLD"
    return None
