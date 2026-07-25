"""Pure deterministic fundamental-feature calculations."""

from decimal import Decimal


def earnings_trend(current: Decimal, previous: Decimal) -> Decimal:
    """Return point-in-time earnings growth in percentage terms."""
    if previous == 0:
        raise ValueError("previous earnings value must be non-zero")
    return (current / abs(previous) - Decimal("1")) * Decimal("100")


def catalyst_recency(sessions: Decimal) -> Decimal:
    """Validate and preserve a deterministically counted session distance."""
    if sessions < 0 or sessions != sessions.to_integral_value():
        raise ValueError("catalyst recency must be a non-negative whole session count")
    return sessions


def industry_leadership(score: Decimal) -> Decimal:
    """Validate a code-computed bounded industry-leadership score."""
    if score < 0 or score > 100:
        raise ValueError("industry leadership must be between 0 and 100")
    return score
