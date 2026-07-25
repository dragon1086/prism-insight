"""Pure deterministic liquidity-feature calculations."""

from decimal import Decimal
from typing import Sequence


def average_dollar_volume(
    closes: Sequence[Decimal], volumes: Sequence[Decimal], periods: int = 20
) -> Decimal:
    """Return the trailing arithmetic mean of close times volume."""
    if len(closes) != len(volumes) or len(closes) < periods:
        raise ValueError(f"at least {periods} aligned price and volume values are required")
    notionals = (
        close * volume
        for close, volume in zip(closes[-periods:], volumes[-periods:])
    )
    return sum(notionals, Decimal("0")) / Decimal(periods)
