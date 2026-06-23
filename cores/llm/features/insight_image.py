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

Layout (subscriber-ready, S6 polish): the incoming mplfinance figure has three
stacked panels (price / volume / RS). We grow the figure taller and re-stack
those panels into clean, non-overlapping bands using explicit fractions
(price : volume : RS ≈ 6 : 1.5 : 2.5), then add a dedicated CAPTION BAND at the
bottom (its own ``axis('off')`` axes) carrying a tidy KOREAN summary + the full
(wrapped) rationale + a disclaimer. The verdict text therefore never overlaps
the candles, and S/R price labels sit INSIDE the plot near the left edge.

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
import textwrap

from cores.llm.capabilities import vision_available
from cores.llm.features.buy_quality import BaseAnalysis, validate_levels

logger = logging.getLogger(__name__)

# --- Premium dark theme palette (subscriber-facing "card" styling) --------- #
_BG = "#0b0e14"          # figure background (near-black navy)
_PANEL = "#0f1320"       # axes panel background
_GRID = "#1e2636"        # subtle gridlines
_TXT = "#d6deeb"         # primary text (light)
_TXT_DIM = "#8b97b0"     # secondary/dim text
_GOLD = "#e5c07b"        # premium accent (pivot + base box = the hero)

# Annotation colours (deterministic; we draw, never the LLM).
_COLOR_SUPPORT = "#3fb950"      # green
_COLOR_RESISTANCE = "#f85149"   # red
_COLOR_BUY = _GOLD              # gold pivot (hero)
_COLOR_STOP = "#d29922"         # amber
_COLOR_BASEBOX = _GOLD          # base highlight box

_DISCLAIMER = "※ 분석 의견이며 투자 조언이 아닙니다."

# Korean labels for the enum fields (numeric/enum stay as data; we map here for
# display only). Anything unmapped falls back to the raw value.
_BASE_TYPE_KO = {
    "cup-handle": "컵앤핸들",
    "cup-with-handle": "컵앤핸들",
    "flat": "플랫 베이스",
    "double-bottom": "더블바텀",
    "high-tight-flag": "하이트 플래그",
    "ascending": "상승 베이스",
    "saucer": "소서 베이스",
    "none": "베이스 없음",
    "faulty": "부적합/결함",
}
_VERDICT_KO = {
    "proper": "적합",
    "faulty": "부적합/결함",
}


def _format_price(value: float, *, symbol: str = "₩", decimals: int = 0) -> str:
    """Format a price with the given currency symbol/precision.

    KR uses ``₩72,500`` (no decimals); US uses ``$189.76`` (2 decimals).
    """
    try:
        return f"{symbol}{value:,.{decimals}f}"
    except Exception:  # noqa: BLE001
        return f"{symbol}{value}"


def _format_won(value: float) -> str:
    """Backward-compatible KR (won) formatter."""
    return _format_price(value, symbol="₩", decimals=0)


def _ko_base_type(value: str) -> str:
    return _BASE_TYPE_KO.get(str(value), str(value))


def _ko_verdict(value: str) -> str:
    return _VERDICT_KO.get(str(value), str(value))


def _draw_level(ax, price: float, color: str, label: str, *, linestyle: str,
                linewidth: float, font_prop) -> None:
    """Draw one horizontal price line + an IN-PLOT text label near the left edge.

    The label is anchored just inside the left axis edge (x in axes-fraction via
    a blended transform; y in data coords) so it never collides with the legend
    (upper-left) nor gets clipped at the right edge.
    """
    ax.axhline(price, color=color, linestyle=linestyle, linewidth=linewidth,
               alpha=0.85, zorder=4)
    txt_kw = dict(color=color, fontsize=9, fontweight="bold", va="bottom",
                  ha="left", transform=ax.get_yaxis_transform(), zorder=6,
                  bbox=dict(boxstyle="round,pad=0.25", facecolor=_PANEL,
                            edgecolor=color, linewidth=0.8, alpha=0.92))
    if font_prop is not None:
        txt_kw["fontproperties"] = font_prop
    # x=0.015 axes-fraction (left), nudged up off the line; y in data coords.
    ax.text(0.015, price, label, **txt_kw)


def _classify_axes(fig):
    """Best-effort split of an mplfinance O'Neil fig into (price, volume, rs).

    The O'Neil daily fig is candles+volume via ``mpf.plot(panel_ratios=(4,1))``
    (so ``axes[0]``=price, a later axes=volume) plus an RS axes added LAST by
    ``_add_rs_panel`` as a thin bottom strip. We pick:
      - price  = axes[0]
      - rs     = the LAST axes (added after mpf), if there are >= 3 axes
      - volume = the shortest remaining axes (the 1-ratio volume panel)
    Returns (price_ax, volume_ax_or_None, rs_ax_or_None). Robust to the simple
    single-axes test figures (returns price only).
    """
    axes = [a for a in getattr(fig, "axes", []) if a is not None]
    if not axes:
        return None, None, None
    price_ax = axes[0]
    rs_ax = None
    volume_ax = None
    others = axes[1:]
    if len(axes) >= 3:
        rs_ax = axes[-1]
        others = axes[1:-1]
    if others:
        # The volume panel is the shortest of the remaining axes.
        def _h(a):
            try:
                return a.get_position().height
            except Exception:  # noqa: BLE001
                return 1.0
        volume_ax = min(others, key=_h)
    return price_ax, volume_ax, rs_ax


def _build_caption(analysis: BaseAnalysis, *, ticker: str,
                   company_name: str | None,
                   supports, resistances, buy, stop,
                   currency_symbol="₩", price_decimals=0) -> str:
    """Compose the KOREAN caption-band text (tidy summary + wrapped rationale).

    Numeric/enum fields are mapped to Korean labels; the rationale is shown as
    returned by the model (the prompt now requests Korean) and WRAPPED with
    textwrap so nothing is truncated.
    """
    header = f"{company_name or ''} ({ticker})".strip()
    rs_status = "예" if analysis.rs_line_new_high else "아니오"

    def _won_list(levels):
        return " · ".join(
            _format_price(v, symbol=currency_symbol, decimals=price_decimals)
            for v in levels
        ) if levels else "-"

    buy_txt = _format_price(buy[0], symbol=currency_symbol, decimals=price_decimals) if buy else "-"
    stop_txt = _format_price(stop[0], symbol=currency_symbol, decimals=price_decimals) if stop else "-"

    lines = [
        f"{header}",
        f"베이스 유형: {_ko_base_type(analysis.base_type)}    "
        f"품질점수: {analysis.quality_score}/100    "
        f"판정: {_ko_verdict(analysis.proper_or_faulty)}",
        f"RS 신고가: {rs_status}    매수 피벗: {buy_txt}    손절: {stop_txt}",
        f"지지: {_won_list(supports)}    저항: {_won_list(resistances)}",
    ]

    rationale = (analysis.rationale or "").strip().replace("\n", " ")
    if rationale:
        # Wrap to a readable width; never truncate.
        wrapped = textwrap.fill(f"분석: {rationale}", width=58)
        lines.append("")
        lines.append(wrapped)

    # Plain-language glossary so non-experts can read the O'Neil jargon used
    # in the labels/tags above (베이스/N주/매수 피벗/RS 신고가/적합·부적합 …).
    glossary_terms = [
        "베이스=주가가 다지는 조정·횡보 구간('N주'=그 베이스가 형성된 기간)",
        "매수 피벗=베이스 상단을 돌파할 때의 매수 기준가",
        "RS 신고가=시장(지수) 대비 상대강도가 신고가 → 강세 신호",
        "지지/저항=하락을 받쳐주는/상승을 막는 가격대",
        "적합·부적합=오닐 기준 매수자리로 적절/부적절한 베이스 형태",
    ]
    glossary = "ℹ️ 용어 안내 — " + " · ".join(glossary_terms)
    lines.append("")
    lines.append(textwrap.fill(glossary, width=58))

    lines.append("")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def _apply_premium_theme(fig, axes_list, *, font_prop=None) -> None:
    """Restyle the (light) mplfinance figure into a premium dark 'card' in-place.

    Candle/MA colours set at plot time read well on the dark panel; we only
    repaint backgrounds, spines, ticks and gridlines. Never raises.
    """
    try:
        fig.patch.set_facecolor(_BG)
        for ax in axes_list:
            if ax is None:
                continue
            ax.set_facecolor(_PANEL)
            ax.tick_params(colors=_TXT_DIM, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(_GRID)
            ax.grid(True, color=_GRID, alpha=0.55, linewidth=0.6)
            try:
                ax.yaxis.label.set_color(_TXT_DIM)
                ax.xaxis.label.set_color(_TXT_DIM)
            except Exception:  # noqa: BLE001
                pass
            if ax.get_title():
                ax.title.set_color(_TXT)
        try:
            if getattr(fig, "_suptitle", None) is not None:
                fig._suptitle.set_color(_TXT)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] theme failed: %s", exc)


def _draw_base_box(price_ax, analysis, *, price_min, price_max,
                   font_prop=None) -> None:
    """Highlight the most-recent base region with a box + a pattern tag.

    X is mplfinance candle-index space (0..N-1); N is read from the axis xlim,
    and the box spans the last ``base_length_weeks*5`` trading days. Y spans the
    base from a support/stop up to the pivot/resistance. Best-effort; on any
    issue it is skipped silently.
    """
    try:
        from matplotlib.patches import Rectangle

        xmin, xmax = price_ax.get_xlim()
        weeks = int(getattr(analysis, "base_length_weeks", 0) or 0)
        base_days = weeks * 5
        if base_days < 8:
            base_days = max(20, int((xmax - xmin) * 0.12))
        x_end = xmax - 2.0
        x_start = max(xmin + 1.0, x_end - base_days)

        sup = validate_levels(list(analysis.support_levels), price_min, price_max)
        res = validate_levels(list(analysis.resistance_levels), price_min, price_max)
        buy = validate_levels([analysis.buy_point], price_min, price_max)
        stop = validate_levels([analysis.stop_loss], price_min, price_max)
        tops = (res or []) + (buy or [])
        bottoms = (sup or []) + (stop or [])
        if tops and bottoms:
            top, bottom = max(tops), min(bottoms)
        else:
            span = price_max - price_min
            top, bottom = price_max - span * 0.18, price_min + span * 0.20
        if top <= bottom:
            return
        pad = (top - bottom) * 0.08
        bottom -= pad
        top += pad

        # Soft fill + crisp gold border = the "hero" pattern highlight.
        price_ax.add_patch(Rectangle(
            (x_start, bottom), x_end - x_start, top - bottom,
            linewidth=0, facecolor=_COLOR_BASEBOX, alpha=0.12, zorder=2.4))
        price_ax.add_patch(Rectangle(
            (x_start, bottom), x_end - x_start, top - bottom,
            linewidth=2.0, edgecolor=_COLOR_BASEBOX, facecolor="none",
            alpha=0.95, zorder=3.0))

        tag = (f"{_ko_base_type(analysis.base_type)} · {weeks}주"
               if weeks else _ko_base_type(analysis.base_type))
        txt_kw = dict(color="#0b0e14", fontsize=10, fontweight="bold",
                      va="center", ha="left", zorder=7,
                      bbox=dict(boxstyle="round,pad=0.35", facecolor=_COLOR_BASEBOX,
                                edgecolor="none", alpha=0.97))
        if font_prop is not None:
            txt_kw["fontproperties"] = font_prop
        price_ax.text(x_start + 0.6, top, tag, **txt_kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] base box failed: %s", exc)


def _draw_pivot_marker(price_ax, analysis, *, price_min, price_max,
                       font_prop=None, currency_symbol="₩", price_decimals=0) -> None:
    """Circle the buy pivot near the right edge + an arrow callout. Best-effort."""
    try:
        from matplotlib.patches import Ellipse

        buy = validate_levels([analysis.buy_point], price_min, price_max)
        if not buy:
            return
        pivot = buy[0]
        xmin, xmax = price_ax.get_xlim()
        cx = xmax - 6.0
        w = max(6.0, (xmax - xmin) * 0.045)
        h = (price_max - price_min) * 0.055
        price_ax.add_patch(Ellipse(
            (cx, pivot), width=w, height=h, fill=False, edgecolor=_GOLD,
            linewidth=2.4, zorder=6))
        txt_kw = dict(color="#0b0e14", fontsize=10, fontweight="bold", zorder=8,
                      ha="left", va="center",
                      bbox=dict(boxstyle="round,pad=0.35", facecolor=_GOLD,
                                edgecolor="none", alpha=0.97))
        if font_prop is not None:
            txt_kw["fontproperties"] = font_prop
        price_ax.annotate(
            f"매수 피벗 {_format_price(pivot, symbol=currency_symbol, decimals=price_decimals)}",
            xy=(cx, pivot), xycoords="data",
            xytext=(xmin + (xmax - xmin) * 0.50,
                    price_max - (price_max - price_min) * 0.08),
            textcoords="data",
            arrowprops=dict(arrowstyle="-|>", color=_GOLD, linewidth=2.0),
            **txt_kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] pivot marker failed: %s", exc)


def render_insight_image(
    daily_fig,
    analysis: BaseAnalysis,
    *,
    ticker: str,
    company_name: str | None = None,
    price_min: float,
    price_max: float,
    currency_symbol: str = "₩",
    price_decimals: int = 0,
) -> bytes | None:
    """Overlay deterministic O'Neil annotations on the DAILY chart and add a
    clean Korean caption band below it.

    Draws (on the price axis, ``daily_fig.axes[0]``):
      - support lines (green dashed + in-plot ``지지 ₩X`` label),
      - resistance lines (red dashed + in-plot ``저항 ₩X`` label),
      - buy_point / pivot (blue solid + in-plot ``매수 피벗 ₩X`` label),
      - stop_loss (orange dashed + in-plot ``손절 ₩X`` label).
    All levels are passed through :func:`validate_levels` first, so absurd /
    out-of-band values are dropped. The verdict/analysis summary lives in a
    dedicated CAPTION BAND below the chart (its own ``axis('off')`` axes), in
    Korean, with the full rationale wrapped (no truncation).

    Args:
        daily_fig:    A matplotlib figure from create_oneil_daily_chart (or a
                      compatible fig whose ``axes[0]`` is the price axis).
        analysis:     The BaseAnalysis carrying the structured price levels.
        ticker:       Stock ticker (used in the caption header).
        company_name: Company name (used in the caption header).
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

        price_ax, volume_ax, rs_ax = _classify_axes(daily_fig)
        if price_ax is None:
            return None

        # Reuse stock_chart's Korean font setup so labels render correctly.
        try:
            from cores.stock_chart import KOREAN_FONT_PROP as font_prop
        except Exception:  # noqa: BLE001
            font_prop = None

        # --- Premium dark theme FIRST (patches then sit on dark panels) ------
        _apply_premium_theme(daily_fig, [price_ax, volume_ax, rs_ax],
                             font_prop=font_prop)

        # --- Validate price levels (deterministic; we draw, not the LLM) -----
        supports = validate_levels(
            list(analysis.support_levels), price_min, price_max
        )
        resistances = validate_levels(
            list(analysis.resistance_levels), price_min, price_max
        )
        buy = validate_levels([analysis.buy_point], price_min, price_max)
        stop = validate_levels([analysis.stop_loss], price_min, price_max)

        def _fmt(v):
            return _format_price(v, symbol=currency_symbol, decimals=price_decimals)

        # --- HERO annotations: pattern box + pivot circle/callout ------------
        _draw_base_box(price_ax, analysis, price_min=price_min,
                       price_max=price_max, font_prop=font_prop)
        _draw_pivot_marker(price_ax, analysis, price_min=price_min,
                           price_max=price_max, font_prop=font_prop,
                           currency_symbol=currency_symbol, price_decimals=price_decimals)

        # --- Supporting levels (de-emphasised dashed lines + labels) ---------
        for lv in supports:
            _draw_level(price_ax, lv, _COLOR_SUPPORT, f"지지 {_fmt(lv)}",
                        linestyle=(0, (4, 3)), linewidth=1.1, font_prop=font_prop)
        for lv in resistances:
            _draw_level(price_ax, lv, _COLOR_RESISTANCE, f"저항 {_fmt(lv)}",
                        linestyle=(0, (4, 3)), linewidth=1.1, font_prop=font_prop)
        if stop:
            _draw_level(price_ax, stop[0], _COLOR_STOP,
                        f"손절 {_fmt(stop[0])}", linestyle="--",
                        linewidth=1.2, font_prop=font_prop)
        # buy pivot is rendered by _draw_pivot_marker (circle + callout) above

        # --- Re-stack the panels into clean bands + add a caption band ---
        _relayout_with_caption(
            daily_fig, price_ax, volume_ax, rs_ax,
            caption=_build_caption(
                analysis, ticker=ticker, company_name=company_name,
                supports=supports, resistances=resistances, buy=buy, stop=stop,
                currency_symbol=currency_symbol, price_decimals=price_decimals,
            ),
            font_prop=font_prop,
        )

        return _fig_to_jpeg(daily_fig)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] render failed for %s: %s", ticker, exc)
        return None


def _relayout_with_caption(fig, price_ax, volume_ax, rs_ax, *, caption,
                           font_prop) -> None:
    """Grow the figure taller and re-stack panels into clean, spaced bands.

    Band layout (figure fraction, top→bottom), x-span 0.09..0.95:
      price  : 0.620 .. 0.955   (h 0.335)  — ratio ~6
      volume : 0.520 .. 0.605   (h 0.085)  — ratio ~1.5
      rs     : 0.395 .. 0.490   (h 0.095)  — ratio ~2.5
      caption: 0.020 .. 0.355   (own axis('off'))

    Missing panels (e.g. the tiny single-axes test fig) simply expand the price
    band downward. Never raises; on error the figure is left as-is.
    """
    try:
        # Taller, balanced aspect for subscriber publishing.
        try:
            fig.set_size_inches(12, 14, forward=True)
        except Exception:  # noqa: BLE001
            pass

        left, width = 0.09, 0.86

        have_vol = volume_ax is not None
        have_rs = rs_ax is not None

        if have_vol and have_rs:
            price_box = (left, 0.620, width, 0.335)
            vol_box = (left, 0.520, width, 0.085)
            rs_box = (left, 0.395, width, 0.095)
            cap_top = 0.355
        elif have_vol:
            price_box = (left, 0.560, width, 0.395)
            vol_box = (left, 0.430, width, 0.110)
            rs_box = None
            cap_top = 0.380
        else:
            # Minimal fig (tests): big price band, large caption.
            price_box = (left, 0.480, width, 0.475)
            vol_box = None
            rs_box = None
            cap_top = 0.430

        price_ax.set_position(price_box)
        if have_vol and vol_box is not None:
            volume_ax.set_position(vol_box)
        if have_rs and rs_box is not None:
            rs_ax.set_position(rs_box)
            # Clean, compact Korean-ish label; no title overlapping neighbours.
            try:
                rs_ax.set_title("")  # drop any prior title text
            except Exception:  # noqa: BLE001
                pass
            ylbl_kw = dict(fontsize=8)
            if font_prop is not None:
                ylbl_kw["fontproperties"] = font_prop
            try:
                rs_ax.set_ylabel("RS (상대강도)", **ylbl_kw)
            except Exception:  # noqa: BLE001
                pass

        # Caption band: dedicated, borderless axes carrying the Korean summary.
        cap_ax = fig.add_axes([left, 0.020, width, cap_top - 0.020])
        cap_ax.set_facecolor(_PANEL)
        for _sp in cap_ax.spines.values():
            _sp.set_color(_GRID)
        cap_ax.set_xticks([])
        cap_ax.set_yticks([])
        txt_kw = dict(fontsize=11, va="top", ha="left", linespacing=1.5,
                      color=_TXT)
        if font_prop is not None:
            txt_kw["fontproperties"] = font_prop
        cap_ax.text(0.0, 1.0, caption, transform=cap_ax.transAxes, **txt_kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] relayout failed: %s", exc)


def _fig_to_jpeg(fig, *, dpi: int = 110) -> bytes | None:
    """Render a matplotlib figure to JPEG bytes; closes the figure. None on error."""
    try:
        from io import BytesIO

        import matplotlib.pyplot as plt

        buffer = BytesIO()
        # No bbox_inches='tight' here: our explicit band positions ARE the
        # layout, and 'tight' would re-crop/undo the careful spacing.
        fig.savefig(buffer, format="jpg", dpi=dpi)
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

        _is_us = isinstance(market, str) and market.strip().lower() in (
            "us", "usa", "united states"
        )
        # Escape "$" as "\\$": matplotlib treats a bare "$" as mathtext, which
        # corrupts adjacent Korean glyphs (renders them via the math 'rm' font).
        _currency_symbol, _price_decimals = ("\\$", 2) if _is_us else ("₩", 0)
        return render_insight_image(
            daily_fig,
            analysis,
            ticker=ticker,
            company_name=company_name,
            price_min=price_min,
            price_max=price_max,
            currency_symbol=_currency_symbol,
            price_decimals=_price_decimals,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] build failed for %s: %s", ticker, exc)
        return None
