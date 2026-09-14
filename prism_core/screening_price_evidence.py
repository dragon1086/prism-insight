"""Descriptive screening evidence, not a target model, score, or buy gate.

The supplied highs may contain a partial session. Do not claim point-in-time
validity, confirmed resistance, or setup classification from this summary.
"""
from math import isfinite


def _positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def build_screening_price_evidence(reference_price, highs, assumed_stop_fraction, requested_date):
    """Summarize an already fetched window without inventing +15% upside."""
    price = _positive(reference_price)
    width = _positive(assumed_stop_fraction)
    if width is not None and width >= 1:
        width = None
    values = [_positive(value) for value in highs]
    result = {
        "schema_version": 1,
        "status": "MISSING",
        "requested_trade_date": str(requested_date),
        "bar_finality": "UNKNOWN",
        "reference_price": price,
        "window_row_count": len(values),
        "valid_high_count": sum(value is not None for value in values),
        "observed_window_high": None,
        "headroom_pct": None,
        "assumed_stop_fraction": width,
        "stop_basis": "LEGACY_TRIGGER_FIXED_WIDTH_NOT_STRUCTURAL_SUPPORT",
        "headroom_to_assumed_risk_ratio": None,
        "scenario_risk_reward_ratio": None,
        "setup_type": "UNCLASSIFIED",
        "legacy_score_basis": "MIN_15PCT_TARGET_FIXED_STOP_NOT_BUY_RR",
    }
    if price is None or width is None or len(values) < 3 or any(v is None for v in values):
        return result
    high = max(values)
    headroom = (high - price) / price
    if not all(isfinite(v) for v in (headroom, headroom * 100, headroom / width)):
        return result
    result.update(observed_window_high=high, headroom_pct=headroom * 100)
    if high <= price:
        result["status"] = "NO_OBSERVED_HIGH_ABOVE_REFERENCE"
    else:
        result["status"] = "OBSERVED_HIGH_ABOVE_REFERENCE"
        result["headroom_to_assumed_risk_ratio"] = headroom / width
    return result
