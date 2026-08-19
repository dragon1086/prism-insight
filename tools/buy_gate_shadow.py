#!/usr/bin/env python3
"""Deterministic buy-gate shadow evaluator.

This module is intentionally not wired into the production buy path yet.
It evaluates the fields that the trading prompt already requires and reports
which entries would be rejected by a deterministic post-LLM check.

The first rollout target is measurement, not prediction: the evaluator must
show would-block counts, winner preservation, and missing/contradictory fields
before it is allowed to become a live veto.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class RegimeRule:
    min_score: int
    rr_floor: float
    max_loss_pct: float
    min_momentum: int
    min_confirmation: int


REGIME_RULES: dict[str, RegimeRule] = {
    "parabolic": RegimeRule(4, 0.7, 7.0, 1, 0),
    "strong_bull": RegimeRule(4, 1.0, 7.0, 1, 0),
    "moderate_bull": RegimeRule(4, 1.2, 7.0, 1, 0),
    "sideways": RegimeRule(5, 1.3, 6.0, 1, 0),
    "moderate_bear": RegimeRule(5, 1.5, 5.0, 2, 1),
    "strong_bear": RegimeRule(6, 1.8, 5.0, 2, 1),
}

_REGIME_RE = re.compile(
    r"\b(parabolic|strong_bull|moderate_bull|sideways|moderate_bear|strong_bear)\b",
    re.IGNORECASE,
)
_T1_RE = re.compile(r"T1_hit[^:]*:\s*(true|false)", re.IGNORECASE)
_T2_RE = re.compile(r"T2_hit[^:]*:\s*(true|false)", re.IGNORECASE)
_DIST_RE = re.compile(r"distribution\s*days[^:]*:\s*(\d+)", re.IGNORECASE)


def _number(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def normalize_regime(value: Any) -> str | None:
    """Extract a known regime token from a decorated market-condition value."""
    match = _REGIME_RE.search(str(value or ""))
    return match.group(1).lower() if match else None


def _finding(code: str, message: str, *, hard: bool = True) -> dict[str, Any]:
    return {"code": code, "message": message, "hard": hard}


def _column(frame: Any, *names: str) -> Any:
    for name in names:
        if name in frame:
            return frame[name]
    return None


def build_asof_features(
    ohlcv: Any,
    *,
    entry_price: float | None = None,
    regime: str | None = None,
    distribution_days: int | None = None,
    trigger: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build independent as-of features from OHLCV ending at the entry date.

    This function deliberately does not call an LLM or read scenario fields.
    It accepts pandas-like frames so both the KR chart client and the US
    yfinance client can feed the same gate contract.
    """
    close = _column(ohlcv, "Close", "close")
    high = _column(ohlcv, "High", "high")
    low = _column(ohlcv, "Low", "low")
    if close is None or len(close) < 20:
        raise ValueError("as-of OHLCV needs at least 20 closes")

    close = close.astype(float)
    price = _number(entry_price) or float(close.iloc[-1])
    ma20_s = close.rolling(20).mean()
    ma50_s = close.rolling(50).mean()
    ma60_s = close.rolling(60).mean()
    ma200_s = close.rolling(200).mean()
    ma20 = _number(ma20_s.iloc[-1])
    ma50 = _number(ma50_s.iloc[-1])
    ma60 = _number(ma60_s.iloc[-1])
    ma200 = _number(ma200_s.iloc[-1])
    ma20_prev = _number(ma20_s.iloc[-6]) if len(close) >= 25 else None

    mid = ma50 if ma50 is not None else ma60
    t1_hit = bool(mid is not None and price < mid)
    t2_hit = bool(
        ma20 is not None
        and ma20_prev is not None
        and ma20 < ma20_prev
        and price <= ma20 * 0.95
    )

    adr20 = None
    atr20 = None
    if high is not None and low is not None:
        high = high.astype(float)
        low = low.astype(float)
        valid = (high > 0) & (low > 0)
        adr20 = _number((((high / low - 1.0) * 100.0)[valid]).tail(20).mean())
        prev_close = close.shift(1)
        true_range = pd_max(high - low, (high - prev_close).abs(), (low - prev_close).abs())
        atr20 = _number((true_range.tail(20).mean() / price) * 100.0)

    trigger = dict(trigger or {})
    trend_facts = (
        f"- T1_hit(종가<MA50): {str(t1_hit)} / "
        f"T2_hit(MA20 하락 and 종가 MA20 대비 -5%↓): {str(t2_hit)}"
    )
    if distribution_days is not None:
        trend_facts += (
            f"\n- Market Pulse: distribution days, 최근 25세션: {int(distribution_days)}"
        )

    return {
        "price": price,
        "ma20": ma20,
        "ma50": ma50,
        "ma60": ma60,
        "ma200": ma200,
        "t1_hit": t1_hit,
        "t2_hit": t2_hit,
        "adr20_pct": adr20,
        "atr20_pct": atr20,
        "extension_ma20_pct": ((price / ma20 - 1.0) * 100.0) if ma20 else None,
        "regime": regime,
        "distribution_days": distribution_days,
        "gap_pct": _number(trigger.get("gap_rate")),
        "intraday_pct": _number(trigger.get("intraday_change")),
        "trend_facts": trend_facts,
    }


def pd_max(*series: Any) -> Any:
    """Small pandas max helper kept local to avoid adding a numpy dependency."""
    import pandas as pd

    return pd.concat(series, axis=1).max(axis=1)


def validate_scenario(
    scenario: Mapping[str, Any],
    *,
    current_price: float | None = None,
    regime: str | None = None,
    trend_facts: str = "",
    distribution_days: int | None = None,
    asof_features: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate an LLM scenario without changing production state.

    ``regime`` is expected to be the programmatically computed regime. When it
    is absent, the scenario's market_condition is only used as a labelled
    fallback and the result marks that provenance as weak.
    """
    data = dict(scenario or {})
    features = dict(asof_features or {})
    current_price = features.get("price", current_price)
    regime = features.get("regime", regime)
    trend_facts = features.get("trend_facts", trend_facts)
    distribution_days = features.get("distribution_days", distribution_days)
    if distribution_days is None:
        match = _DIST_RE.search(trend_facts or "")
        if match:
            distribution_days = int(match.group(1))
    findings: list[dict[str, Any]] = []
    authoritative_regime = (regime or "").strip().lower() or None
    scenario_regime = normalize_regime(data.get("market_condition"))
    effective_regime = authoritative_regime or scenario_regime

    if authoritative_regime is None:
        findings.append(_finding(
            "regime_provenance_weak",
            "programmatic regime was not supplied; scenario market_condition is advisory",
            hard=False,
        ))
    rule = REGIME_RULES.get(effective_regime or "")
    if rule is None:
        findings.append(_finding("missing_regime", "no recognized regime is available"))

    score = _number(data.get("effective_score"))
    if score is None:
        buy_score = _number(data.get("buy_score"))
        macro_adjustment = _number(data.get("macro_adjustment")) or 0.0
        score = (buy_score or 0.0) + macro_adjustment

    if rule is not None and score < rule.min_score:
        findings.append(_finding(
            "score_below_regime_floor",
            f"effective score {score:g} < regime floor {rule.min_score}",
        ))

    fundamental = data.get("fundamental_check")
    if rule is not None and effective_regime in {"sideways", "moderate_bear", "strong_bear"}:
        if isinstance(fundamental, Mapping) and fundamental.get("all_passed") is False:
            findings.append(_finding(
                "fundamental_gate_failed",
                "fundamental_check.all_passed=false in a non-bull regime",
            ))

    momentum = _number(data.get("momentum_signal_count"))
    confirmation = _number(data.get("additional_confirmation_count"))
    if rule is not None and momentum is not None and momentum < rule.min_momentum:
        findings.append(_finding(
            "momentum_count_below_floor",
            f"momentum signals {momentum:g} < required {rule.min_momentum}",
        ))
    if rule is not None and confirmation is not None and confirmation < rule.min_confirmation:
        findings.append(_finding(
            "confirmation_count_below_floor",
            f"additional confirmations {confirmation:g} < required {rule.min_confirmation}",
        ))

    price = _number(current_price)
    target = _number(data.get("target_price"))
    stop = _number(data.get("stop_loss"))
    if price is not None and price > 0:
        if target is None:
            findings.append(_finding("missing_target", "target_price is missing"))
        elif target <= price:
            findings.append(_finding("invalid_target", "target_price is not above current price"))
        if stop is None:
            findings.append(_finding("missing_stop", "stop_loss is missing"))
        elif stop >= price:
            findings.append(_finding("invalid_stop", "stop_loss is not below current price"))

        expected_return = ((target - price) / price * 100.0) if target is not None else None
        expected_loss = ((price - stop) / price * 100.0) if stop is not None else None
        recomputed_rr = (
            expected_return / expected_loss
            if expected_return is not None and expected_loss and expected_loss > 0
            else None
        )
        reported_rr = _number(data.get("risk_reward_ratio"))
        if rule is not None and recomputed_rr is not None and recomputed_rr < rule.rr_floor:
            findings.append(_finding(
                "rr_below_regime_floor",
                f"recomputed R/R {recomputed_rr:.2f} < regime floor {rule.rr_floor:.2f}",
            ))
        if rule is not None and expected_loss is not None and expected_loss > rule.max_loss_pct + 0.25:
            findings.append(_finding(
                "stop_exceeds_regime_limit",
                f"recomputed loss {expected_loss:.2f}% > regime max {rule.max_loss_pct:.2f}%",
            ))
        if reported_rr is not None and recomputed_rr is not None:
            tolerance = max(0.15, abs(recomputed_rr) * 0.10)
            if abs(reported_rr - recomputed_rr) > tolerance:
                findings.append(_finding(
                    "rr_arithmetic_mismatch",
                    f"reported R/R {reported_rr:.2f} != recomputed {recomputed_rr:.2f}",
                ))

    t1 = _T1_RE.search(trend_facts or "")
    t2 = _T2_RE.search(trend_facts or "")
    if t1 and t1.group(1).lower() == "true":
        findings.append(_finding("individual_trend_t1", "T1 individual trend gate is hit"))
    if t2 and t2.group(1).lower() == "true":
        findings.append(_finding("individual_trend_t2", "T2 individual trend gate is hit"))

    if distribution_days is not None and distribution_days >= 6:
        findings.append(_finding(
            "distribution_day_caution",
            f"elevated distribution days={distribution_days}; new-buy regime should be one step more conservative",
            hard=False,
        ))

    hard_findings = [f for f in findings if f["hard"]]
    return {
        "would_block": bool(hard_findings),
        "regime": effective_regime,
        "regime_source": "computed" if authoritative_regime else "scenario",
        "findings": findings,
        "hard_findings": hard_findings,
    }


__all__ = ["REGIME_RULES", "normalize_regime", "validate_scenario"]
