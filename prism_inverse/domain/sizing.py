"""Quantity from risk: the same loss at the stop whatever the multiplier (2x only saves cash)."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from prism_inverse.domain.models import Bar
from prism_inverse.domain.policy import InversePolicy


def worst_up_gap(bars: Sequence[Bar], lookback: int) -> Optional[float]:
    """Largest open-over-previous-close rise in the window; the overnight risk a stop cannot cut."""
    if len(bars) < lookback + 1:
        return None
    window = bars[-lookback - 1:]
    return max(cur.open / prev.close - 1 for prev, cur in zip(window, window[1:]))


def size(
    equity: Optional[float], etf_price: Optional[float], multiplier: int,
    underlying_close: float, stop_underlying: Optional[float], policy: InversePolicy,
    gap: Optional[float] = None, need_gap: bool = False,
) -> tuple[int, str]:
    if not equity or not etf_price or equity <= 0 or etf_price <= 0 or stop_underlying is None:
        return 0, "MISSING_SIZING_INPUT"
    dist = (stop_underlying - underlying_close) / underlying_close
    if dist <= 0:
        return 0, "BAD_STOP"
    qty = math.floor(equity * policy.risk_per_trade / (etf_price * abs(multiplier) * dist))
    if need_gap:
        if gap is None:
            return 0, "MISSING_GAP_HISTORY"
        gap_loss = etf_price * min(1.0, abs(multiplier) * max(gap, 0.0))
        if gap_loss > 0:
            qty = min(qty, math.floor(equity * policy.gap_risk_per_trade / gap_loss))
    return (qty, "OK") if qty > 0 else (0, "QTY_ZERO")
