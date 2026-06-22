# buy_quality.py

"""
O'Neil buy-quality vision gate — Phase 6 S3 (SHADOW by default, log-only).

Produces a CAN SLIM "base" analysis from a chart image and computes a
per-regime pass/fail verdict. In SHADOW mode (PRISM_VISION_SHADOW=true, the
default) the verdict is logged but NEVER fed into trading decisions.

Public API::

    analysis = await analyze_base(chart_image, numeric_pivot=..., current_price=...)
    # Returns None when vision is unavailable (off / no key) or on error.

    verdict = gate_verdict(analysis, regime)   # pure function, no side effects
    # {would_buy, regime, threshold, quality_score, reason}

Design constraints (mirror S1/S2):
- analyze_image is imported at module level but is itself cheap to import
  (its heavy deps are lazy-loaded only when vision_available() is True).
- vision_available() is checked first; if False, returns None immediately.
- Never raises to caller. All errors return None silently.
- BaseAnalysis is strict-JSON-schema compatible (extra="forbid", every field
  required) for OpenAI json_schema strict mode.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cores.llm.capabilities import vision_available
from cores.llm.features.vision import ImageInput, analyze_image

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Structured vision output schema (§2 of tasks/phase6_vision_oneil.md)         #
# --------------------------------------------------------------------------- #
class BaseAnalysis(BaseModel):
    """CAN SLIM base-structure analysis of a stock chart.

    Strict-JSON-schema compatible (OpenAI json_schema strict mode): every field
    is required and no extra properties are allowed.
    """

    model_config = ConfigDict(extra="forbid")

    base_type: Literal[
        "cup-handle",
        "flat",
        "double-bottom",
        "high-tight-flag",
        "ascending",
        "saucer",
        "none",
        "faulty",
    ]
    base_length_weeks: int
    depth_pct: float            # correction depth of the base
    handle_present: bool
    handle_in_upper_half: bool  # handle sits in the upper half of the base (proper)
    tightness: Literal["tight", "normal", "loose"]
    volume_dryup_in_handle: bool
    pivot_price: float
    dist_to_pivot_pct: float    # current price distance to pivot/buy point
    rs_line_new_high: bool      # RS line at a new high BEFORE price (O'Neil's strongest tell)
    proper_or_faulty: Literal["proper", "faulty"]
    quality_score: int          # 0-100
    confidence: int             # 0-100
    rationale: str


# --------------------------------------------------------------------------- #
# Per-regime quality_score pass floors (§1 of the plan).                       #
#                                                                              #
# TUNABLE PLACEHOLDERS — these are S3 starting values only. S4 backtest will   #
# tune them and they will move to features.yaml (vision.regime_thresholds).    #
# Behaviour encoded: lenient in bull regimes, strict in sideways, very strict  #
# in bear regimes, block-ish in parabolic (overheated chase defence).          #
#                                                                              #
# Keys cover the 6 regimes referenced by the buy matrix. The 5 deterministic   #
# strings emitted by _compute_kr_regime are strong_bull / moderate_bull /      #
# sideways / moderate_bear / strong_bear; "parabolic" is a derived activation  #
# regime in the buy prompt. "bull" is accepted as an alias for moderate_bull.  #
# --------------------------------------------------------------------------- #
REGIME_THRESHOLDS: dict[str, int] = {
    "strong_bull": 55,      # lenient — early/incomplete bases tolerated
    "moderate_bull": 60,    # lenient
    "bull": 60,             # alias for moderate_bull (plan §1 wording)
    "sideways": 75,         # strict — only tight proper bases
    "moderate_bear": 85,    # very strict — top-grade bases only
    "strong_bear": 90,      # very strict — almost no new buys
    "parabolic": 90,        # block-ish — suppress overheated chasing
}

# Default floor for any unrecognised regime: treat conservatively (strict).
_DEFAULT_THRESHOLD = 75

# Numeric cross-check tolerance: if the model's pivot deviates from the
# externally computed pivot by more than this fraction, penalise quality.
_PIVOT_TOLERANCE_PCT = 3.0
# Deterministic penalty applied to quality_score on a pivot mismatch.
_PIVOT_MISMATCH_PENALTY = 25


_BASE_PROMPT = """\
You are a CAN SLIM chart analyst trained in William O'Neil's methodology. Your \
ONLY job is to assess the BASE structure of the price chart for a potential \
buy point. Do NOT give buy/sell advice — only describe the base objectively.

Identify the base type. Proper O'Neil bases include:
- cup-with-handle, flat base, double-bottom, high-tight-flag, ascending base, saucer.
Mark base_type "none" if there is no constructive base, and "faulty" if the base \
is defective.

Apply these O'Neil rules to judge proper vs faulty:
- A PROPER base is reasonably tight, has constructive (declining/drying) volume, \
and (for cup-with-handle) a handle that drifts in the UPPER HALF of the base with \
volume dry-up.
- A FAULTY base is wide-and-loose, V-shaped (no real consolidation), has a handle \
in the lower half, or shows wedging/heavy volume in the handle.
- volume_dryup_in_handle: true only if volume visibly contracts through the handle.
- rs_line_new_high: true only if the Relative Strength line is making a NEW HIGH \
ahead of price (O'Neil's strongest confirmation).
- pivot_price: the buy point (top of the handle / breakout level).
- dist_to_pivot_pct: percent distance from the current price to the pivot \
(negative if price is already above the pivot).

Score the base:
- quality_score (0-100): overall O'Neil base quality. Tight proper cup-with-handle \
with RS new high and volume dry-up scores high; wide/loose/faulty/no-base scores low.
- confidence (0-100): your confidence in this assessment.
- rationale: one or two sentences justifying the scores.

Return a strict JSON object with exactly these fields: base_type, \
base_length_weeks, depth_pct, handle_present, handle_in_upper_half, tightness, \
volume_dryup_in_handle, pivot_price, dist_to_pivot_pct, rs_line_new_high, \
proper_or_faulty, quality_score, confidence, rationale.
"""


def _apply_pivot_cross_check(
    analysis: BaseAnalysis,
    numeric_pivot: float | None,
) -> BaseAnalysis:
    """Penalise quality_score when the model's pivot deviates from a numeric one.

    Deterministic hallucination guard. Mutates and returns *analysis*. When
    numeric_pivot is None or non-positive, or the model pivot is non-positive,
    this is a no-op (we cannot compute a meaningful deviation).
    """
    if numeric_pivot is None or numeric_pivot <= 0:
        return analysis
    if analysis.pivot_price <= 0:
        return analysis

    deviation_pct = abs(analysis.pivot_price - numeric_pivot) / numeric_pivot * 100.0
    if deviation_pct > _PIVOT_TOLERANCE_PCT:
        original = analysis.quality_score
        analysis.quality_score = max(0, original - _PIVOT_MISMATCH_PENALTY)
        analysis.rationale = (
            f"{analysis.rationale} "
            f"[pivot cross-check: model pivot {analysis.pivot_price:g} deviates "
            f"{deviation_pct:.1f}% from numeric pivot {numeric_pivot:g} "
            f"(> {_PIVOT_TOLERANCE_PCT:g}% tol); quality penalised "
            f"{original}->{analysis.quality_score}]"
        ).strip()
    return analysis


async def analyze_base(
    chart_image: ImageInput,
    *,
    numeric_pivot: float | None = None,
    current_price: float | None = None,
) -> BaseAnalysis | None:
    """Analyse the base structure of *chart_image* via vision (CAN SLIM).

    Args:
        chart_image:   Path/bytes of the chart image (same as analyze_image input).
        numeric_pivot: Optional externally-computed pivot for cross-check. When
                       the model's pivot deviates beyond tolerance, quality_score
                       is penalised deterministically.
        current_price: Optional current price (reserved for future numeric checks;
                       currently informational only).

    Returns:
        - ``None`` when vision is unavailable (off / no key) or on error.
        - :class:`BaseAnalysis` instance on success.

    Never raises.
    """
    if not vision_available():
        return None

    prompt = _BASE_PROMPT
    if numeric_pivot is not None or current_price is not None:
        hints = []
        if numeric_pivot is not None:
            hints.append(f"numeric pivot estimate = {numeric_pivot:g}")
        if current_price is not None:
            hints.append(f"current price = {current_price:g}")
        prompt = (
            f"{_BASE_PROMPT}\n"
            f"Reference values (for sanity-checking your pivot, not to copy "
            f"blindly): {', '.join(hints)}.\n"
        )

    try:
        result = await analyze_image(chart_image, prompt, schema=BaseAnalysis)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUY_QUALITY] error during analyze_image: %s", exc)
        return None

    if result is None:
        return None

    if not isinstance(result, BaseAnalysis):
        logger.warning(
            "[BUY_QUALITY] unexpected result type=%s", type(result).__name__
        )
        return None

    return _apply_pivot_cross_check(result, numeric_pivot)


def gate_verdict(analysis: BaseAnalysis, regime: str) -> dict:
    """Compute a per-regime buy-quality verdict. Pure function, no side effects.

    A faulty base is an automatic No-Entry regardless of score. Otherwise the
    base's quality_score is compared against the regime's pass floor.

    Returns a dict with keys:
        would_buy (bool), regime (str), threshold (int), quality_score (int),
        reason (str).
    """
    threshold = REGIME_THRESHOLDS.get(regime, _DEFAULT_THRESHOLD)
    quality_score = analysis.quality_score

    if analysis.proper_or_faulty == "faulty" or analysis.base_type == "faulty":
        return {
            "would_buy": False,
            "regime": regime,
            "threshold": threshold,
            "quality_score": quality_score,
            "reason": "faulty base — automatic No-Entry",
        }

    if analysis.base_type == "none":
        return {
            "would_buy": False,
            "regime": regime,
            "threshold": threshold,
            "quality_score": quality_score,
            "reason": "no constructive base detected",
        }

    would_buy = quality_score >= threshold
    if would_buy:
        reason = f"quality_score {quality_score} >= {regime} floor {threshold}"
    else:
        reason = f"quality_score {quality_score} < {regime} floor {threshold}"

    return {
        "would_buy": would_buy,
        "regime": regime,
        "threshold": threshold,
        "quality_score": quality_score,
        "reason": reason,
    }
