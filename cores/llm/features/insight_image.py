# insight_image.py

"""
Annotated insight-image renderer — Phase 6 S6 (DISPLAY-ONLY, OFF-gated).

This is a subscriber-facing PUBLISHING feature, fully separate from trading
decisions. It takes an :class:`~cores.llm.features.buy_quality.BaseAnalysis`
(which carries structured price levels) and the O'Neil DAILY chart, then draws
the annotations DETERMINISTICALLY with matplotlib at the model's price levels —
AFTER validating those numbers against the chart's actual visible price range.

Design principle (critical): the vision model must NEVER draw on the image. It
returns STRUCTURED DATA (price levels + verdict); WE draw lines/labels at those
exact y-coordinates, dropping any level that falls outside a plausible price
band (validate_levels). This avoids hallucinated annotations on a financial
image.

Public API::

    img = render_insight_image(daily_fig, analysis, ticker=..., company_name=...,
                               price_min=..., price_max=...)   # -> bytes | None

    img = await build_insight_image_for(ticker, company_name)  # -> bytes | None
    # Returns None when vision is unavailable; reuses ONE vision call.

Constraints (mirror S1/S2/S3):
- vision_available() gates build_insight_image_for; OFF -> None, no work.
- Never raises to caller. Any failure returns None silently.
- Korean font reuses cores.stock_chart's font setup (KOREAN_FONT_PROP).
"""

from __future__ import annotations

import logging

from cores.llm.capabilities import vision_available
from cores.llm.features.buy_quality import BaseAnalysis, validate_levels

logger = logging.getLogger(__name__)

# Annotation colours (deterministic, subscriber-facing styling).
_COLOR_SUPPORT = "#27ae60"      # green
_COLOR_RESISTANCE = "#c0392b"   # red
_COLOR_BUY = "#1f6feb"          # blue
_COLOR_STOP = "#e67e22"         # orange

_DISCLAIMER = "분석 의견 · 투자 조언 아님"


def _format_won(value: float) -> str:
    """Format a WON price level compactly (e.g. ``₩72,500``)."""
    try:
        return f"₩{value:,.0f}"
    except Exception:  # noqa: BLE001
        return f"₩{value}"


def _draw_level(ax, price: float, color: str, label: str, *, linestyle: str,
                font_prop) -> None:
    """Draw one horizontal price line + right-edge text label on *ax*."""
    ax.axhline(price, color=color, linestyle=linestyle, linewidth=1.3, alpha=0.9)
    txt_kw = dict(color=color, fontsize=8, va="center", ha="right",
                  transform=ax.get_yaxis_transform())
    if font_prop is not None:
        txt_kw["fontproperties"] = font_prop
    # x=0.995 in axes-fraction (via get_yaxis_transform), y in data coords.
    ax.text(0.995, price, label, **txt_kw)


def render_insight_image(
    daily_fig,
    analysis: BaseAnalysis,
    *,
    ticker: str,
    company_name: str | None = None,
    price_min: float,
    price_max: float,
) -> bytes | None:
    """Overlay deterministic O'Neil annotations on the DAILY chart.

    Draws (on the price axis, ``daily_fig.axes[0]``):
      - support lines (green dashed + ``₩X`` label),
      - resistance lines (red dashed + ``₩X`` label),
      - buy_point / pivot (blue solid + ``BUY PIVOT ₩X`` + arrow),
      - stop_loss (orange dashed + ``STOP ₩X``).
    All levels are passed through :func:`validate_levels` first, so absurd /
    out-of-band values are dropped. Then adds a right-margin TEXT PANEL
    summarising base_type, quality_score/100, proper-or-faulty, RS new-high
    status, and a truncated takeaway, plus a small disclaimer line.

    Args:
        daily_fig:    A matplotlib figure from create_oneil_daily_chart (or a
                      compatible fig whose ``axes[0]`` is the price axis).
        analysis:     The BaseAnalysis carrying the structured price levels.
        ticker:       Stock ticker (used in the text-panel header).
        company_name: Company name (used in the text-panel header).
        price_min:    The chart's actual visible minimum price.
        price_max:    The chart's actual visible maximum price.

    Returns:
        JPEG bytes on success, or ``None`` on any failure. Never raises.
    """
    try:
        import matplotlib.pyplot as plt  # noqa: F401  (ensure backend import)

        if daily_fig is None or not getattr(daily_fig, "axes", None):
            logger.warning("[INSIGHT_IMAGE] no price axis on figure for %s", ticker)
            return None
        price_ax = daily_fig.axes[0]

        # Reuse stock_chart's Korean font setup so labels render correctly.
        try:
            from cores.stock_chart import KOREAN_FONT_PROP as font_prop
        except Exception:  # noqa: BLE001
            font_prop = None

        # --- Validate then draw price levels (deterministic; we draw, not LLM) ---
        supports = validate_levels(
            list(analysis.support_levels), price_min, price_max
        )
        resistances = validate_levels(
            list(analysis.resistance_levels), price_min, price_max
        )
        buy = validate_levels([analysis.buy_point], price_min, price_max)
        stop = validate_levels([analysis.stop_loss], price_min, price_max)

        for lv in supports:
            _draw_level(price_ax, lv, _COLOR_SUPPORT, f"S {_format_won(lv)}",
                        linestyle="--", font_prop=font_prop)
        for lv in resistances:
            _draw_level(price_ax, lv, _COLOR_RESISTANCE, f"R {_format_won(lv)}",
                        linestyle="--", font_prop=font_prop)
        if stop:
            _draw_level(price_ax, stop[0], _COLOR_STOP,
                        f"STOP {_format_won(stop[0])}", linestyle="--",
                        font_prop=font_prop)
        if buy:
            bp = buy[0]
            price_ax.axhline(bp, color=_COLOR_BUY, linestyle="-", linewidth=1.8,
                             alpha=0.95)
            ann_kw = dict(color=_COLOR_BUY, fontsize=9, fontweight="bold",
                          va="bottom", ha="left",
                          xycoords=price_ax.get_yaxis_transform(),
                          arrowprops=dict(arrowstyle="->", color=_COLOR_BUY))
            if font_prop is not None:
                ann_kw["fontproperties"] = font_prop
            # Arrow pointing to the pivot line from slightly inside the axis.
            price_ax.annotate(
                f"BUY PIVOT {_format_won(bp)}",
                xy=(0.02, bp), xytext=(0.18, bp),
                **ann_kw,
            )

        # --- Right-margin text panel summarising the verdict ---
        rs_status = "RS new high: YES" if analysis.rs_line_new_high else "RS new high: no"
        takeaway = (analysis.rationale or "").strip().replace("\n", " ")
        if len(takeaway) > 140:
            takeaway = takeaway[:137] + "..."
        header = f"{company_name or ''} ({ticker})".strip()
        panel_lines = [
            header,
            f"Base: {analysis.base_type}",
            f"Quality: {analysis.quality_score}/100",
            f"Verdict: {analysis.proper_or_faulty}",
            rs_status,
            "",
            takeaway,
        ]
        panel_text = "\n".join(line for line in panel_lines if line is not None)

        # Make room on the right for the panel, then place it in figure coords.
        try:
            daily_fig.subplots_adjust(right=0.80)
        except Exception:  # noqa: BLE001
            pass
        panel_kw = dict(fontsize=8, va="top", ha="left",
                        bbox=dict(boxstyle="round", facecolor="#f7f7f7",
                                  edgecolor="#cccccc", alpha=0.95))
        if font_prop is not None:
            panel_kw["fontproperties"] = font_prop
        daily_fig.text(0.815, 0.88, panel_text, **panel_kw)

        # Small disclaimer line (subscriber-facing, not investment advice).
        disc_kw = dict(fontsize=7, color="#888888", ha="left", va="bottom")
        if font_prop is not None:
            disc_kw["fontproperties"] = font_prop
        daily_fig.text(0.815, 0.05, _DISCLAIMER, **disc_kw)

        return _fig_to_jpeg(daily_fig)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] render failed for %s: %s", ticker, exc)
        return None


def _fig_to_jpeg(fig, *, dpi: int = 90) -> bytes | None:
    """Render a matplotlib figure to JPEG bytes; closes the figure. None on error."""
    try:
        from io import BytesIO

        import matplotlib.pyplot as plt

        buffer = BytesIO()
        fig.savefig(buffer, format="jpg", bbox_inches="tight", dpi=dpi)
        plt.close(fig)
        buffer.seek(0)
        data = buffer.getvalue()
        return data if data else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] fig->jpeg failed: %s", exc)
        try:
            import matplotlib.pyplot as plt

            plt.close(fig)
        except Exception:  # noqa: BLE001
            pass
        return None


async def build_insight_image_for(
    ticker: str,
    company_name: str | None = None,
    *,
    market: str | None = None,
) -> bytes | None:
    """Convenience: produce an annotated insight image for *ticker*.

    Reuses ONE vision call: runs analyze_base_oneil to obtain the BaseAnalysis,
    then regenerates the DAILY chart and renders annotations on it. Gated on
    vision_available(); if vision is off, returns None and does NO work. Any
    failure returns None — never raises.

    Note: this does NOT make a second vision call. The same analysis that gates
    buy-quality is reused to drive the annotation.
    """
    if not vision_available():
        return None

    try:
        from cores.llm.features.buy_quality import analyze_base_oneil

        analysis = await analyze_base_oneil(
            ticker, company_name=company_name, market=market
        )
        if analysis is None:
            return None

        from cores.stock_chart import create_oneil_daily_chart

        daily_fig = create_oneil_daily_chart(
            ticker, company_name=company_name, market=market
        )
        if daily_fig is None:
            return None

        # Derive the visible price band from the price axis.
        price_ax = daily_fig.axes[0] if getattr(daily_fig, "axes", None) else None
        if price_ax is None:
            return None
        price_min, price_max = price_ax.get_ylim()

        return render_insight_image(
            daily_fig,
            analysis,
            ticker=ticker,
            company_name=company_name,
            price_min=price_min,
            price_max=price_max,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] build failed for %s: %s", ticker, exc)
        return None
