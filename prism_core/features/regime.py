"""Pure deterministic market-regime feature validation."""

from decimal import Decimal


def regime_compatibility(score: Decimal) -> Decimal:
    """Preserve a bounded deterministic regime-policy compatibility score."""
    if score < 0 or score > 100:
        raise ValueError("regime compatibility must be between 0 and 100")
    return score
