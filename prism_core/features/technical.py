"""Pure deterministic technical-feature calculations."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Sequence


HUNDRED = Decimal("100")


def percent_change(values: Sequence[Decimal], periods: int) -> Decimal:
    """Return percentage change over an exact trailing-session window."""
    if periods <= 0 or len(values) <= periods:
        raise ValueError(f"at least {periods + 1} values are required")
    start = values[-(periods + 1)]
    if start <= 0:
        raise ValueError("price values must be positive")
    with localcontext() as context:
        context.prec = 40
        return (values[-1] / start - Decimal("1")) * HUNDRED


def relative_strength(
    closes: Sequence[Decimal], benchmark_closes: Sequence[Decimal], periods: int
) -> Decimal:
    """Return security return minus benchmark return in percentage points."""
    return percent_change(closes, periods) - percent_change(benchmark_closes, periods)


def volume_expansion(volumes: Sequence[Decimal], periods: int = 20) -> Decimal:
    """Return latest volume versus the preceding-window mean as a percentage."""
    if len(volumes) <= periods:
        raise ValueError(f"at least {periods + 1} volume values are required")
    history = volumes[-(periods + 1) : -1]
    average = sum(history, Decimal("0")) / Decimal(periods)
    if average <= 0:
        raise ValueError("historical average volume must be positive")
    return volumes[-1] / average * HUNDRED


def atr_percent(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    periods: int = 14,
) -> Decimal:
    """Return simple trailing true range as a percentage of latest close."""
    if not (len(highs) == len(lows) == len(closes)) or len(closes) <= periods:
        raise ValueError(f"at least {periods + 1} aligned OHLC values are required")
    ranges = []
    start = len(closes) - periods
    for index in range(start, len(closes)):
        ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    average = sum(ranges, Decimal("0")) / Decimal(periods)
    if closes[-1] <= 0:
        raise ValueError("latest close must be positive")
    return average / closes[-1] * HUNDRED


def price_above_average(closes: Sequence[Decimal], periods: int) -> Decimal:
    """Return latest close's percentage distance above a trailing mean."""
    if len(closes) < periods:
        raise ValueError(f"at least {periods} close values are required")
    average = sum(closes[-periods:], Decimal("0")) / Decimal(periods)
    if average <= 0:
        raise ValueError("moving average must be positive")
    return (closes[-1] / average - Decimal("1")) * HUNDRED


def moving_average_alignment(
    closes: Sequence[Decimal], short_periods: int = 50, long_periods: int = 200
) -> Decimal:
    """Return short moving average's percentage distance above the long mean."""
    if short_periods >= long_periods or len(closes) < long_periods:
        raise ValueError("aligned moving averages require short < long and enough data")
    short = sum(closes[-short_periods:], Decimal("0")) / Decimal(short_periods)
    long = sum(closes[-long_periods:], Decimal("0")) / Decimal(long_periods)
    if long <= 0:
        raise ValueError("long moving average must be positive")
    return (short / long - Decimal("1")) * HUNDRED
