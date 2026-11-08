"""Minimal indicators over ``Bar`` lists (oldest first). ``None`` when history is short."""

from __future__ import annotations

from typing import Optional, Sequence

from prism_inverse.domain.models import Bar


def sma(values: Sequence[float], n: int) -> Optional[float]:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def closes(bars: Sequence[Bar]) -> list[float]:
    return [b.close for b in bars]


def atr(bars: Sequence[Bar], n: int) -> Optional[float]:
    """Simple mean of the last ``n`` true ranges."""
    if n <= 0 or len(bars) < n + 1:
        return None
    trs = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[-n - 1:-1], bars[-n:])
    ]
    return sum(trs) / n


def volume_ratio(bars: Sequence[Bar], n: int) -> Optional[float]:
    """Today's volume over the prior ``n``-day mean; ``None`` if any volume is unknown."""
    if len(bars) < n + 1:
        return None
    window = [b.volume for b in bars[-n - 1:-1]]
    today = bars[-1].volume
    if today is None or any(v is None for v in window):
        return None
    mean = sum(window) / n
    return today / mean if mean > 0 else None
