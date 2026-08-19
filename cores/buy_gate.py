"""Deterministic new-entry gate shared by the KR and US simulators.

The LLM still supplies the thesis and qualitative judgement.  This module is
the final, market-independent underwriting check for a *new* entry.  It never
places orders and it never changes an existing position or a sell decision.

The gate deliberately validates fields that are available in both current and
legacy scenario records.  Optional newer fields (fundamental/momentum counts)
are checked when present, while missing legacy fields do not make historical
scenarios impossible to replay.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping


REGIME_RULES: dict[str, dict[str, float | int]] = {
    "parabolic": {"min_score": 4, "rr_floor": 0.7, "max_loss_pct": 7.0, "min_momentum": 1, "min_confirmation": 0},
    "strong_bull": {"min_score": 4, "rr_floor": 1.0, "max_loss_pct": 7.0, "min_momentum": 1, "min_confirmation": 0},
    "moderate_bull": {"min_score": 4, "rr_floor": 1.2, "max_loss_pct": 7.0, "min_momentum": 1, "min_confirmation": 0},
    "sideways": {"min_score": 5, "rr_floor": 1.3, "max_loss_pct": 6.0, "min_momentum": 1, "min_confirmation": 0},
    "moderate_bear": {"min_score": 5, "rr_floor": 1.5, "max_loss_pct": 5.0, "min_momentum": 2, "min_confirmation": 1},
    "strong_bear": {"min_score": 6, "rr_floor": 1.8, "max_loss_pct": 5.0, "min_momentum": 2, "min_confirmation": 1},
}

_REGIME_RE = re.compile(
    r"\b(parabolic|strong_bull|moderate_bull|sideways|moderate_bear|strong_bear)\b",
    re.IGNORECASE,
)
_T1_RE = re.compile(r"T1_hit[^:]*:\s*(true|false)", re.IGNORECASE)
_T2_RE = re.compile(r"T2_hit[^:]*:\s*(true|false)", re.IGNORECASE)
_DIST_RE = re.compile(r"distribution\s*days[^:]*:\s*(\d+)", re.IGNORECASE)
_ATR_RE = re.compile(r"ATR20\s*[:=]\s*([0-9.]+)", re.IGNORECASE)
_ADR_RE = re.compile(r"ADR20\s*[:=]\s*([0-9.]+)", re.IGNORECASE)

_DISTRIBUTION_CAUTION = 6
_REGIME_STEP_DOWN = {
    "parabolic": "strong_bull",
    "strong_bull": "moderate_bull",
    "moderate_bull": "sideways",
    "sideways": "moderate_bear",
    "moderate_bear": "strong_bear",
    "strong_bear": "strong_bear",
}
_STRICT_SCORE_FLOORS = {
    "sideways": 8,
    "moderate_bear": 8,
    "strong_bear": 9,
}


def _strict_score_floor_enabled() -> bool:
    """Legacy rollback flag; the final gate is ON unless explicitly disabled."""
    return os.getenv("REGIME_MIN_SCORE_FLOOR", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(str(value).replace(",", "").replace("%", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_regime(value: Any) -> str | None:
    match = _REGIME_RE.search(str(value or ""))
    return match.group(1).lower() if match else None


def _bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"true", "pass", "passed", "통과", "yes", "1"}:
            return True
        if raw in {"false", "fail", "failed", "미달", "no", "0"}:
            return False
    return None


def _finding(code: str, message: str, *, hard: bool = True) -> dict[str, Any]:
    return {"code": code, "message": message, "hard": hard}


def _distribution_from_text(text: str) -> int | None:
    match = _DIST_RE.search(text or "")
    return int(match.group(1)) if match else None


def _volatility_from_text(text: str) -> tuple[float | None, float | None]:
    atr = _ATR_RE.search(text or "")
    adr = _ADR_RE.search(text or "")
    return (
        _number(atr.group(1)) if atr else None,
        _number(adr.group(1)) if adr else None,
    )


def effective_buy_regime(regime: Any, distribution_days: int | None) -> tuple[str | None, bool]:
    """Return the computed regime after the deterministic distribution caution."""
    base = normalize_regime(regime)
    if base is None:
        return None, False
    caution = distribution_days is not None and distribution_days >= _DISTRIBUTION_CAUTION
    return (_REGIME_STEP_DOWN[base] if caution else base), caution


def evaluate_production_buy_gate(
    scenario: Mapping[str, Any] | None,
    *,
    current_price: float | None,
    market_regime: Any,
    score_override: float | None = None,
    trend_facts: str = "",
    distribution_days: int | None = None,
    is_add: bool = False,
) -> dict[str, Any]:
    """Evaluate one candidate immediately before the simulator buy call.

    ``market_regime`` must be the programmatically computed regime.  A missing
    regime is a hard stop for a new buy: a data outage may reduce opportunity,
    but must not silently remove the market-risk filter.
    """
    data = dict(scenario or {})
    facts = trend_facts or str(data.get("_deterministic_trend_facts") or "")
    if distribution_days is None:
        distribution_days = _distribution_from_text(facts)
    atr20_pct, adr20_pct = _volatility_from_text(facts)

    findings: list[dict[str, Any]] = []
    computed_regime = normalize_regime(market_regime)
    scenario_regime = normalize_regime(data.get("market_condition"))
    effective_regime, distribution_caution = effective_buy_regime(computed_regime, distribution_days)

    if computed_regime is None:
        findings.append(_finding("missing_computed_regime", "programmatic market regime unavailable"))
    rule = REGIME_RULES.get(effective_regime or "")
    if rule is None:
        findings.append(_finding("missing_regime_rule", "no deterministic rule is available for the market regime"))

    score = _number(score_override)
    if score is None:
        score = _number(data.get("effective_score"))
    if score is None:
        buy_score = _number(data.get("buy_score"))
        macro = _number(data.get("macro_adjustment")) or 0.0
        score = (buy_score + macro) if buy_score is not None else None
    if score is None:
        findings.append(_finding("missing_score", "effective buy score is unavailable"))
    elif rule is not None:
        scenario_min = _number(data.get("min_score")) or 0.0
        required_score = max(float(rule["min_score"]), scenario_min)
        if _strict_score_floor_enabled():
            required_score = max(
                required_score,
                float(_STRICT_SCORE_FLOORS.get(effective_regime or "", 0)),
            )
        if score < required_score:
            findings.append(_finding(
                "score_below_floor",
                f"effective score {score:g} < required {required_score:g} ({effective_regime})",
            ))

    fundamental = data.get("fundamental_check")
    fundamental_passed = _bool_value(fundamental.get("all_passed")) if isinstance(fundamental, Mapping) else None
    if fundamental_passed is False and effective_regime in {"sideways", "moderate_bear", "strong_bear"}:
        findings.append(_finding("fundamental_gate_failed", "fundamental_check.all_passed=false in a cautious regime"))

    momentum = _number(data.get("momentum_signal_count"))
    confirmation = _number(data.get("additional_confirmation_count"))
    if rule is not None and momentum is not None and momentum < float(rule["min_momentum"]):
        findings.append(_finding("momentum_count_below_floor", f"momentum signals {momentum:g} < {rule['min_momentum']}"))
    if rule is not None and confirmation is not None and confirmation < float(rule["min_confirmation"]):
        findings.append(_finding("confirmation_count_below_floor", f"confirmations {confirmation:g} < {rule['min_confirmation']}"))

    price = _number(current_price)
    target = _number(data.get("target_price"))
    stop = _number(data.get("stop_loss"))
    recomputed_rr = None
    volatility_shadow: dict[str, Any] | None = None
    if price is None or price <= 0:
        findings.append(_finding("invalid_current_price", "current price is unavailable or non-positive"))
    else:
        if target is None or target <= 0:
            findings.append(_finding("missing_target", "target_price is unavailable"))
        elif target <= price:
            findings.append(_finding("invalid_target", "target_price must be above current price"))
        if stop is None or stop <= 0:
            findings.append(_finding("missing_stop", "stop_loss is unavailable"))
        elif stop >= price:
            findings.append(_finding("invalid_stop", "stop_loss must be below current price"))

        expected_return = ((target - price) / price * 100.0) if target is not None and target > price else None
        expected_loss = ((price - stop) / price * 100.0) if stop is not None and 0 < stop < price else None
        if expected_loss is not None:
            volatility_values = [v for v in (atr20_pct, adr20_pct) if v is not None and v > 0]
            if volatility_values:
                noise_floor = max(volatility_values) * 0.5
                if expected_loss < noise_floor:
                    volatility_shadow = {
                        "code": "stop_below_volatility_noise_floor",
                        "message": (
                            f"stop width {expected_loss:.2f}% < 0.5×max(ATR20/ADR20) "
                            f"{noise_floor:.2f}% (ATR20={atr20_pct}, ADR20={adr20_pct})"
                        ),
                        "hard": False,
                    }
                    findings.append(volatility_shadow)
        if expected_return is not None and expected_loss and expected_loss > 0:
            recomputed_rr = expected_return / expected_loss
            if rule is not None and recomputed_rr < float(rule["rr_floor"]):
                findings.append(_finding("rr_below_floor", f"recomputed R/R {recomputed_rr:.2f} < {rule['rr_floor']:.2f}"))
            if rule is not None and expected_loss > float(rule["max_loss_pct"]) + 0.25:
                findings.append(_finding("stop_exceeds_regime_limit", f"loss width {expected_loss:.2f}% > {rule['max_loss_pct']:.2f}%"))

            reported_rr = _number(data.get("risk_reward_ratio"))
            if reported_rr is not None:
                tolerance = max(0.15, abs(recomputed_rr) * 0.10)
                if abs(reported_rr - recomputed_rr) > tolerance:
                    findings.append(_finding("rr_arithmetic_mismatch", f"reported R/R {reported_rr:.2f} != {recomputed_rr:.2f}"))

            for field, expected in (("expected_return_pct", expected_return), ("expected_loss_pct", expected_loss)):
                reported = _number(data.get(field))
                if reported is not None and abs(reported - expected) > max(0.25, abs(expected) * 0.10):
                    findings.append(_finding("risk_arithmetic_mismatch", f"{field} {reported:.2f} != {expected:.2f}"))

    t1 = _T1_RE.search(facts)
    t2 = _T2_RE.search(facts)
    if t1 and t1.group(1).lower() == "true":
        findings.append(_finding("individual_trend_t1", "T1 individual trend gate is hit"))
    if t2 and t2.group(1).lower() == "true":
        findings.append(_finding("individual_trend_t2", "T2 individual trend gate is hit"))

    if distribution_caution:
        findings.append(_finding(
            "distribution_day_caution",
            f"distribution days={distribution_days}; effective regime {computed_regime}->{effective_regime}",
            hard=False,
        ))

    hard_findings = [item for item in findings if item["hard"]]
    shadow_findings = [item for item in findings if not item["hard"]]
    return {
        "allowed": not hard_findings,
        "would_block": bool(hard_findings),
        "is_add": bool(is_add),
        "computed_regime": computed_regime,
        "scenario_regime": scenario_regime,
        "effective_regime": effective_regime,
        "distribution_days": distribution_days,
        "distribution_caution": distribution_caution,
        "recomputed_rr": recomputed_rr,
        "findings": findings,
        "hard_findings": hard_findings,
        "shadow_findings": shadow_findings,
        "atr20_pct": atr20_pct,
        "adr20_pct": adr20_pct,
        "volatility_noise_floor_pct": (
            max(v for v in (atr20_pct, adr20_pct) if v is not None and v > 0) * 0.5
            if any(v is not None and v > 0 for v in (atr20_pct, adr20_pct))
            else None
        ),
        "reason": "; ".join(item["message"] for item in hard_findings),
    }


__all__ = [
    "REGIME_RULES",
    "effective_buy_regime",
    "evaluate_production_buy_gate",
    "normalize_regime",
]
