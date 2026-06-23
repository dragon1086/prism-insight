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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import GenerateJsonSchema

from cores.llm.capabilities import vision_available
from cores.llm.features.vision import ImageInput, analyze_image

logger = logging.getLogger(__name__)


class _AllFieldsRequired(GenerateJsonSchema):
    """JSON-schema generator that forces EVERY property into ``required``.

    OpenAI's ``strict`` json_schema mode requires every property to be listed
    in ``required`` even when the Pydantic field carries a default. We add
    coordinate-bearing fields (support/resistance/buy/stop) with empty-ish
    defaults so existing callers that don't supply them keep working, while
    this generator keeps the emitted schema strict-compatible
    (``required == properties``).
    """

    def generate(self, schema: Any, mode: str = "validation") -> dict:
        js = super().generate(schema, mode=mode)
        if js.get("type") == "object" and "properties" in js:
            js["required"] = list(js["properties"].keys())
        return js


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

    # --- Coordinate-bearing annotation fields (Phase 6 S6, display-only). ---
    # All in WON price units (KR), consistent with the chart's y-axis. These
    # let us DRAW annotations deterministically (we never let the model draw on
    # the image). Empty list / 0.0 means "none". Defaults keep older callers
    # working; _AllFieldsRequired keeps the emitted JSON schema strict-safe.
    support_levels: list[float] = []      # price levels of support
    resistance_levels: list[float] = []   # price levels of resistance
    buy_point: float = 0.0                # entry/buy-pivot price (0.0 = none)
    stop_loss: float = 0.0                # protective stop price (0.0 = none)

    @classmethod
    def model_json_schema(cls, *args, **kwargs):  # type: ignore[override]
        """Emit a strict-compatible schema (every property in ``required``).

        Routes through :class:`_AllFieldsRequired` so fields carrying defaults
        (the S6 annotation fields) are still marked required, satisfying
        OpenAI's strict json_schema mode and the existing strictness tests.
        """
        kwargs.setdefault("schema_generator", _AllFieldsRequired)
        return super().model_json_schema(*args, **kwargs)


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
- rationale: one or two sentences justifying the scores. WRITE THE rationale IN \
KOREAN (한국어), in a natural, concise tone suitable for retail subscribers. All \
other fields stay as specified (numbers, booleans, and the fixed enum strings \
for base_type / tightness / proper_or_faulty remain in English exactly as listed).

Also return KEY PRICE LEVELS so they can be drawn on the chart. Report ALL of \
these in WON (₩) price units, consistent with the chart's y-axis (NOT \
percentages, NOT chart pixel coordinates):
- support_levels: a list of price levels (₩) acting as support (empty list if none).
- resistance_levels: a list of price levels (₩) acting as resistance (empty list if none).
- buy_point: the entry/buy-pivot price (₩); use 0.0 if there is no valid buy point.
- stop_loss: a sensible protective stop price (₩) below the base; use 0.0 if none.
Keep each list short (typically 1-3 levels) and only include levels you can read \
off the chart's price axis.

Return a strict JSON object with exactly these fields: base_type, \
base_length_weeks, depth_pct, handle_present, handle_in_upper_half, tightness, \
volume_dryup_in_handle, pivot_price, dist_to_pivot_pct, rs_line_new_high, \
proper_or_faulty, quality_score, confidence, rationale, support_levels, \
resistance_levels, buy_point, stop_loss.
"""


# Two-timeframe (daily + weekly) variant of the prompt. Used by
# analyze_base_oneil, which sends two labeled images in one message.
_BASE_PROMPT_TWO_TF = (
    _BASE_PROMPT
    + """

IMPORTANT — you are given TWO images of the SAME stock, in this order:
- IMAGE 1 = DAILY chart (candles + MA5/20/60/120 + volume + an RS line panel \
labeled "RS vs <index>").
- IMAGE 2 = WEEKLY chart (weekly candles + 10-week and 40-week MAs + weekly \
volume + the same RS line, weekly).

How to read them:
- Read the BASE and HANDLE structure PRIMARILY on the WEEKLY chart (image 2): \
base_type, base_length_weeks, depth_pct, handle position, tightness, and \
weekly volume behavior are best judged there.
- Read the PIVOT / breakout level and the ENTRY TIMING (dist_to_pivot_pct) \
PRIMARILY on the DAILY chart (image 1).
- Judge rs_line_new_high from the LABELED RS line panel: it is true only if \
the RS line is making a NEW HIGH (a green ^ marker / RS at its running max), \
ideally BEFORE price confirms (O'Neil's strongest tell). Do NOT infer RS from \
price alone — use the dedicated RS panel.
"""
)


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


def validate_levels(
    levels: list[float],
    price_min: float,
    price_max: float,
    pad: float = 0.25,
) -> list[float]:
    """Keep only price levels that fall within a plausible band of the chart.

    A deterministic guard so we never DRAW an absurd (hallucinated) line on a
    subscriber-facing image. Levels outside
    ``[price_min*(1-pad), price_max*(1+pad)]`` are dropped. Non-positive levels
    are always dropped. Logs how many were dropped. Never raises.

    Args:
        levels:    Candidate price levels (WON), as returned by the vision model.
        price_min: The chart's actual visible minimum price.
        price_max: The chart's actual visible maximum price.
        pad:       Fractional padding around the visible band (default 0.25).

    Returns:
        The in-band subset of *levels*, order preserved.
    """
    if not levels:
        return []
    try:
        lo, hi = float(price_min), float(price_max)
        if lo > hi:
            lo, hi = hi, lo
        lower = lo * (1.0 - pad)
        upper = hi * (1.0 + pad)
        kept = [lv for lv in levels if lv > 0 and lower <= lv <= upper]
        dropped = len(levels) - len(kept)
        if dropped:
            logger.info(
                "[INSIGHT_IMAGE] validate_levels dropped %d/%d out-of-band level(s) "
                "(band=[%.4g, %.4g])",
                dropped,
                len(levels),
                lower,
                upper,
            )
        return kept
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] validate_levels failed: %s", exc)
        return []


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


def _fig_to_bytes(fig, *, image_format: str = "jpg", dpi: int = 80) -> bytes | None:
    """Render a matplotlib figure to raw image bytes (JPEG/PNG).

    Mirrors the buffer/compression approach in
    stock_chart.get_chart_as_base64_html, but returns raw bytes (not HTML).
    Closes the figure to avoid leaks. Returns None on error.
    """
    try:
        from io import BytesIO

        import matplotlib.pyplot as plt

        buffer = BytesIO()
        fig.savefig(buffer, format=image_format, bbox_inches="tight", dpi=dpi)
        plt.close(fig)
        buffer.seek(0)

        if image_format.lower() in ("jpg", "jpeg"):
            try:
                from PIL import Image

                img = Image.open(buffer)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                new_buffer = BytesIO()
                img.save(new_buffer, format="JPEG", quality=85, optimize=True)
                return new_buffer.getvalue()
            except ImportError:
                return buffer.getvalue()
        return buffer.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUY_QUALITY] fig->bytes failed: %s", exc)
        return None


async def analyze_base_oneil(
    ticker: str,
    company_name: str | None = None,
    *,
    regime: str | None = None,
    numeric_pivot: float | None = None,
    current_price: float | None = None,
    market: str | None = None,
    extra_context: str | None = None,
) -> BaseAnalysis | None:
    """Two-timeframe O'Neil base analysis: generate DAILY + WEEKLY charts (with
    RS line) for *ticker* and analyse both in a single multi-image vision call.

    *extra_context* (optional) is appended to the prompt as additional grounding
    text — e.g. a concise summary of the ticker's PAST trades for the insight
    image. Purely informational; never changes the structured output schema.

    Grounds BaseAnalysis.rs_line_new_high (RS line panel) and the weekly base
    reading instead of relying on a single daily image.

    Args:
        ticker:        Stock ticker symbol.
        company_name:  Company name for chart titles (auto-fetched if None).
        regime:        Optional market regime (informational; not used here —
                        gate_verdict applies the regime threshold downstream).
        numeric_pivot: Optional externally-computed pivot for cross-check.
        current_price: Optional current price (informational).
        market:        Optional 'KOSPI'/'KOSDAQ' hint for index selection.

    Returns:
        - ``None`` when vision is unavailable, chart generation fails, or on
          any error.
        - :class:`BaseAnalysis` instance on success.

    Never raises.
    """
    if not vision_available():
        return None

    # Lazy import — chart generation pulls in matplotlib/pykrx; only do it when
    # vision is actually on.
    try:
        from cores.stock_chart import (
            create_oneil_daily_chart,
            create_oneil_weekly_chart,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUY_QUALITY] oneil chart import failed: %s", exc)
        return None

    try:
        daily_fig = create_oneil_daily_chart(
            ticker, company_name=company_name, market=market
        )
        weekly_fig = create_oneil_weekly_chart(
            ticker, company_name=company_name, market=market
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUY_QUALITY] oneil chart generation failed: %s", exc)
        return None

    if daily_fig is None or weekly_fig is None:
        logger.warning("[BUY_QUALITY] oneil chart(s) unavailable for %s", ticker)
        return None

    daily_bytes = _fig_to_bytes(daily_fig)
    weekly_bytes = _fig_to_bytes(weekly_fig)
    if daily_bytes is None or weekly_bytes is None:
        return None

    prompt = _BASE_PROMPT_TWO_TF
    if numeric_pivot is not None or current_price is not None:
        hints = []
        if numeric_pivot is not None:
            hints.append(f"numeric pivot estimate = {numeric_pivot:g}")
        if current_price is not None:
            hints.append(f"current price = {current_price:g}")
        prompt = (
            f"{_BASE_PROMPT_TWO_TF}\n"
            f"Reference values (for sanity-checking your pivot, not to copy "
            f"blindly): {', '.join(hints)}.\n"
        )

    if extra_context:
        prompt = (
            f"{prompt}\n"
            f"추가 참고 정보 (구조화 출력에는 영향 없음, 분석 근거로만 활용):\n"
            f"{extra_context}\n"
        )

    try:
        result = await analyze_image(
            [daily_bytes, weekly_bytes], prompt, schema=BaseAnalysis
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[BUY_QUALITY] error during oneil analyze_image: %s", exc)
        return None

    if result is None:
        return None

    if not isinstance(result, BaseAnalysis):
        logger.warning(
            "[BUY_QUALITY] unexpected oneil result type=%s", type(result).__name__
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
