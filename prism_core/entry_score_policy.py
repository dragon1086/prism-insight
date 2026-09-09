"""Shared KR/US entry-score contract; no market-data or broker imports.

Pulse and pilot budget permission must come from runtime callers, never model
scenario annotations. Distribution caution is resolved by the final buy gate.
"""
from __future__ import annotations

import math
import os


_REGIME_MIN_SCORE_FLOORS = {
    "strong_bear": 9, "moderate_bear": 8, "sideways": 8,
    "moderate_bull": 0, "strong_bull": 0, "unknown": 0,
}


def regime_min_score_floor_enabled() -> bool:
    return os.getenv("REGIME_MIN_SCORE_FLOOR", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _regime(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return raw.split()[0] if raw else "unknown"


def _finite_number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def min_score_floor(market_regime: str | None, pulse_state: str | None = None) -> int:
    """Strict regime floor; only sideways with current UPTREND is eased to 7."""
    key = _regime(market_regime)
    if key == "sideways" and (pulse_state or "").strip().upper() == "UPTREND":
        return 7
    return _REGIME_MIN_SCORE_FLOORS.get(key, 0)


def effective_min_score(llm_min_score, market_regime: str | None,
                        pulse_state: str | None = None) -> int:
    """Compatibility API: preserve the historical integer LLM threshold."""
    try:
        base = int(llm_min_score or 0)
    except (TypeError, ValueError, OverflowError):
        base = 0
    if not regime_min_score_floor_enabled():
        return base
    return max(base, min_score_floor(market_regime, pulse_state))


def is_rebound_pilot_entry(buy_score, llm_min_score, market_regime: str | None,
                           pulse_state: str | None, decision: str | None) -> bool:
    """Candidate eligibility only; execution additionally requires a cash cap."""
    score = _finite_number(buy_score)
    base = _finite_number(llm_min_score)
    return (
        regime_min_score_floor_enabled()
        and _regime(market_regime) == "sideways"
        and (pulse_state or "").strip().upper() == "UPTREND"
        and (decision or "").strip().lower() in {"enter", "entry"}
        and score == 6
        and base is not None and base <= 6
    )


def evaluate_entry_score_policy(
    buy_score, llm_min_score, market_regime: str | None,
    pulse_state: str | None = None, decision: str | None = None, *,
    effective_regime: str | None = None, distribution_caution: bool = False,
    pilot_budget_available: bool = False, is_add: bool = False,
    rule_min_score: float = 0,
) -> dict:
    """Resolve a single score threshold without bypassing independent gates.

    A distribution downgrade never inherits the sideways recovery exception,
    even when its *result* is sideways. Missing budget permission fails closed
    for a pilot. The regular rollback flag disables only the strict overlay.
    """
    regime = effective_regime or market_regime
    pulse = None if distribution_caution else pulse_state
    enabled = regime_min_score_floor_enabled()
    floor = min_score_floor(regime, pulse) if enabled else 0
    base = _finite_number(llm_min_score)
    pilot = (
        pilot_budget_available is True and not is_add and not distribution_caution
        and _regime(regime) == _regime(market_regime)
        and is_rebound_pilot_entry(buy_score, llm_min_score, market_regime, pulse, decision)
    )
    required = max(float(rule_min_score), base or 0.0, 6.0 if pilot else float(floor))
    return {
        "required_score": required,
        "strict_floor": floor,
        "strict_enabled": enabled,
        "rebound_pilot": pilot,
        "position_fraction": 0.5 if pilot else 1.0,
    }
