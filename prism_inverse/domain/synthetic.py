"""Daily-reset inverse price built from an underlying, for periods before a product existed.

Used in S2's development period for single-stock inverse (products listed 2022+).
It is a product-structure model, not evidence: fees, financing and fills differ.
"""

from __future__ import annotations

from typing import Sequence

from prism_inverse.domain.models import Bar


def inverse_bars(bars: Sequence[Bar], multiplier: int, fee_annual: float = 0.01, start: float = 100.0) -> list[Bar]:
    """Close-to-close daily reset. Open/high/low follow the same day's move from the prior close."""
    if not bars:
        return []
    fee = fee_annual / 252
    out = [Bar(bars[0].day, start, start, start, start, None)]
    prev_px = start
    for prev, cur in zip(bars, bars[1:]):
        def at(level: float) -> float:
            return max(0.0, prev_px * (1 + multiplier * (level / prev.close - 1) - fee))
        levels = [at(cur.open), at(cur.close), at(cur.low), at(cur.high)]
        close = levels[1]
        out.append(Bar(cur.day, levels[0], max(levels), min(levels), close, None))
        prev_px = close
    return out
