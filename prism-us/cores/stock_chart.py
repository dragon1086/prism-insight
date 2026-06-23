"""
US O'Neil chart module (Phase 6 S6, vision-only) — shadows root ``cores.stock_chart``.

Under the US runtime, ``from cores.X`` resolves to ``prism-us/cores/X``. The
shared feature code (``cores.llm.features.buy_quality.analyze_base_oneil`` and
``cores.llm.features.insight_image.build_insight_image_for``) does::

    from cores.stock_chart import create_oneil_daily_chart, create_oneil_weekly_chart

so for US tickers those imports land HERE. The KR implementations (pykrx-based)
live only in the root ``cores/stock_chart.py`` and are intentionally NOT touched.

This module mirrors the KR contract for the O'Neil daily/weekly charts but
sources data from yfinance via :class:`prism_us.cores.us_data_client.USDataClient`:

  - ``create_oneil_daily_chart(ticker, company_name=None, market="us", ...)``
      candles + MA10/20/50/200 + volume + RS line vs a US index.
  - ``create_oneil_weekly_chart(ticker, company_name=None, market="us", ...)``
      weekly candles (daily resampled) + MA10/40 + volume + weekly RS line.

Both return a matplotlib ``Figure`` whose ``axes[0]`` is the PRICE axis — the
same contract the renderer (``render_insight_image``) and ``analyze_base_oneil``
rely on. Prices are in USD (the renderer's caption currency formatting is KR-won;
that is acceptable for now and the renderer is intentionally left unchanged).

Constraints (mirror KR / S6):
  - Never raise into the batch. On any data/render failure: log + return ``None``.
  - The empty-new-high guard (``len(is_new_high) > 0``) is preserved in the RS panel.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")  # Non-interactive backend (mirror KR module).

import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Korean font for the shared renderer. `render_insight_image` does
# `from cores.stock_chart import KOREAN_FONT_PROP`; under US shadowing that lands
# HERE, so it must be defined or US insight images render Korean as tofu boxes.
# Mirror the root module's font discovery (never raises; None falls back to
# matplotlib default).
import os as _os  # noqa: E402
import matplotlib.font_manager as _fm  # noqa: E402

KOREAN_FONT_PROP = None
for _fp in (
    "/usr/share/fonts/nanum/NanumGothicCoding.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
    "/usr/share/fonts/google-nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    _os.path.expanduser("~/Library/Fonts/NanumGothic.ttf"),
):
    try:
        if _os.path.exists(_fp):
            _fm.fontManager.addfont(_fp)
            KOREAN_FONT_PROP = _fm.FontProperties(fname=_fp)
            break
    except Exception:  # noqa: BLE001 — font setup must never crash callers
        pass
if KOREAN_FONT_PROP is None:
    for _family in ("AppleSDGothicNeo-Regular", "AppleGothic", "Malgun Gothic", "NanumGothic"):
        try:
            _path = _fm.findfont(_fm.FontProperties(family=_family), fallback_to_default=False)
            if _path:
                KOREAN_FONT_PROP = _fm.FontProperties(family=_family)
                break
        except Exception:  # noqa: BLE001
            pass

# US index symbols (yfinance). S&P 500 is the default RS benchmark; NASDAQ
# Composite is used for clearly-Nasdaq names.
_SP500_INDEX = "^GSPC"
_NASDAQ_INDEX = "^IXIC"

# A small, conservative set of obviously-Nasdaq mega/large caps. For anything
# not listed we default to the S&P 500 benchmark (broadest, safest choice).
_NASDAQ_TICKERS = {
    "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA", "AVGO",
    "PEP", "COST", "ADBE", "CSCO", "NFLX", "AMD", "INTC", "QCOM", "TXN",
    "AMAT", "MU", "PYPL", "SBUX", "INTU", "ISRG", "BKNG", "MRVL", "PANW",
    "MELI", "ASML", "LRCX", "ADI", "REGN", "VRTX", "KLAC", "SNPS", "CDNS",
    "MDLZ", "GILD", "PDD", "CRWD", "ABNB", "FTNT", "SMCI", "ON", "MRNA",
    "DDOG", "TEAM", "ZS", "SNOW", "DASH", "COIN", "PLTR", "ARM", "MSTR",
}


def create_mpf_style(base_mpl_style: str = "seaborn-v0_8-whitegrid"):
    """Generate an mplfinance style for US charts (TradingView-like colors).

    Mirrors the KR ``create_mpf_style`` color scheme but without the Korean-font
    rc tweaks (US charts use the default Latin font). Never raises.
    """
    try:
        plt.style.use(base_mpl_style)
    except Exception:  # noqa: BLE001
        pass

    rc_font = {
        "font.size": 10,
        "axes.unicode_minus": False,
    }

    mc = mpf.make_marketcolors(
        up="#089981",
        down="#F23645",
        edge="inherit",
        wick="inherit",
        volume={"up": "#a3f7b5", "down": "#ffa5a5"},
    )

    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle="-",
        gridcolor="#e6e6e6",
        gridaxis="both",
        rc=rc_font,
        facecolor="white",
    )
    return s


def _detect_index_ticker(ticker: str) -> str:
    """Return the US index benchmark symbol for *ticker*.

    Clearly-Nasdaq names map to ``^IXIC`` (NASDAQ Composite); everything else
    (and any uncertainty) defaults to ``^GSPC`` (S&P 500). Never raises.
    """
    try:
        if ticker and ticker.upper() in _NASDAQ_TICKERS:
            return _NASDAQ_INDEX
        return _SP500_INDEX
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"[ONEIL][US] index detection failed for {ticker}, "
            f"defaulting to S&P 500: {e}"
        )
        return _SP500_INDEX


def _get_client():
    """Lazily build a USDataClient. Returns None on import/init failure."""
    try:
        from cores.us_data_client import USDataClient

        return USDataClient()
    except Exception:  # noqa: BLE001
        try:
            # Fallback to the fully-qualified path if `cores` is not shadowed.
            from prism_us.cores.us_data_client import USDataClient  # type: ignore

            return USDataClient()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[ONEIL][US] USDataClient unavailable: {e}")
            return None


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    """Normalize a yfinance OHLCV frame (lowercased cols) to capitalized
    Open/High/Low/Close/Volume with a tz-naive DatetimeIndex. None if unusable.
    """
    if df is None or len(df) == 0:
        return None
    rename = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    # Tolerate either already-capitalized or lowercased columns.
    cols = {c.lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for low, cap in rename.items():
        src = cols.get(low)
        if src is None:
            return None
        out[cap] = df[src]
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index = pd.to_datetime(out.index)
    # yfinance often returns a tz-aware index; drop tz for clean resampling.
    try:
        if out.index.tz is not None:
            out.index = out.index.tz_localize(None)
    except (AttributeError, TypeError):
        pass
    out = out.sort_index()
    # Drop all-zero rows.
    ohlc = [c for c in ["Open", "High", "Low", "Close"] if c in out.columns]
    if ohlc:
        out = out[out[ohlc].sum(axis=1) > 0]
    return out if len(out) > 0 else None


def _fetch_index_close(index_ticker: str, days: int):
    """Fetch the US index daily close series via USDataClient/yfinance.

    Returns a pandas Series indexed by a tz-naive DatetimeIndex (close prices),
    or None on failure. Never raises.
    """
    try:
        client = _get_client()
        if client is None:
            return None
        period = _period_for_days(days)
        idf = client.get_index_data(index=index_ticker, period=period)
        idf = _normalize_ohlcv(idf)
        if idf is None:
            return None
        s = idf["Close"].sort_index()
        s = s[s > 0]
        return s if len(s) > 0 else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL][US] index fetch failed for {index_ticker}: {e}")
        return None


def _compute_rs_line(stock_close, index_close):
    """Compute the O'Neil RS line = (stock/index) normalized to 100 at window start.

    Aligns both series on the stock's dates, normalizes the ratio to 100 at the
    first common date, and returns a Series reindexed to the stock's dates — or
    None if alignment yields nothing usable. Never raises.
    """
    try:
        idx = index_close.reindex(stock_close.index).ffill().bfill()
        ratio = stock_close / idx
        ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
        if len(ratio) == 0:
            return None
        base = ratio.iloc[0]
        if base == 0:
            return None
        rs = ratio / base * 100.0
        return rs.reindex(stock_close.index)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL][US] RS line computation failed: {e}")
        return None


def _add_rs_panel(fig, rs_series, label):
    """Draw the RS line as a dedicated readable bottom subpanel on *fig*.

    Marks every RS new high (running-max) and emphasises the most recent one.
    Includes the empty-new-high guard (``len(is_new_high) > 0``) so a degenerate
    RS series cannot raise an index error. Never raises; on error the chart is
    left unchanged.
    """
    try:
        if rs_series is None:
            return
        rs_clean = rs_series.dropna()
        if len(rs_clean) == 0:
            return
        rs_ax = fig.add_axes([0.08, 0.02, 0.84, 0.18])
        x = list(range(len(rs_clean)))
        rs_vals = rs_clean.values
        rs_ax.plot(x, rs_vals, color="#8e44ad", linewidth=1.3)

        running_max = rs_clean.cummax()
        is_new_high = rs_vals >= running_max.values
        nh_x = [i for i, flag in enumerate(is_new_high) if bool(flag)]
        if nh_x:
            rs_ax.scatter(
                nh_x,
                [rs_vals[i] for i in nh_x],
                marker="^",
                color="#27ae60",
                s=18,
                zorder=3,
                label="RS new high",
            )
        # Guard against an empty new-high array (avoids "index -1 out of bounds
        # for axis 0 with size 0" when the RS series degenerates).
        if len(is_new_high) > 0 and bool(is_new_high[-1]):
            last_i = len(rs_vals) - 1
            rs_ax.scatter(
                [last_i],
                [rs_vals[last_i]],
                marker="^",
                color="#1e8449",
                s=70,
                zorder=4,
            )
            rs_ax.annotate(
                "RS new high",
                (last_i, rs_vals[last_i]),
                fontsize=7,
                color="#1e8449",
                ha="right",
                va="bottom",
            )

        rs_ax.set_xticks([])
        rs_ax.tick_params(axis="y", labelsize=7)
        rs_ax.margins(x=0.01)
        rs_ax.grid(True, axis="y", alpha=0.2)
        rs_ax.set_ylabel(f"RS vs {label}", fontsize=8)
        rs_ax.set_title(f"RS vs {label} (norm=100 at start)", fontsize=7)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL][US] RS panel draw failed: {e}")


def _period_for_days(days: int) -> str:
    """Map a calendar-day lookback to a yfinance ``period`` string (rounded up)."""
    if days <= 35:
        return "1mo"
    if days <= 95:
        return "3mo"
    if days <= 190:
        return "6mo"
    if days <= 370:
        return "1y"
    if days <= 740:
        return "2y"
    if days <= 1850:
        return "5y"
    return "10y"


def _resolve_index(ticker, market, index_ticker):
    """Resolve (index_symbol, index_label) for the RS benchmark."""
    if index_ticker is None:
        index_ticker = _detect_index_ticker(ticker)
    label = "NASDAQ" if index_ticker == _NASDAQ_INDEX else "S&P500"
    return index_ticker, label


def create_oneil_daily_chart(
    ticker,
    company_name=None,
    days=400,
    adjusted=True,
    save_path=None,
    market="us",
    index_ticker=None,
    return_df=False,
):
    """Generate an O'Neil DAILY chart for a US ticker (vision-only).

    Candles + MA10/20/50/200 (US O'Neil daily standard) + volume + an RS line
    vs a US index (``^GSPC`` by default, ``^IXIC`` for clearly-Nasdaq names).

    Args:
        ticker:       US stock ticker symbol (e.g. ``"AAPL"``).
        company_name: Company name for the title (auto-fetched if None).
        days:         Lookback window in calendar days (default 400).
        adjusted:     Accepted for KR-signature parity (yfinance auto-adjusts).
        save_path:    If given, save the figure there; otherwise just return it.
        market:       Market hint (default ``"us"``); informational here.
        index_ticker: Optional explicit index symbol override (e.g. ``"^IXIC"``).
        return_df:    If True, return ``(fig, ohlc_df)`` so callers can map
                      trade dates to mplfinance candle index positions.

    Returns:
        matplotlib Figure (``axes[0]`` is the price axis), or None on failure.
        When ``return_df=True``, returns ``(fig, ohlc_df)`` so callers can map
        trade dates to mplfinance candle index positions (position i <->
        ohlc_df.index[i]). Never raises.
    """
    try:
        client = _get_client()
        if client is None:
            return None

        if company_name is None:
            try:
                info = client.get_company_info(ticker)
                company_name = info.get("name") or ticker
            except Exception:  # noqa: BLE001
                company_name = ticker

        df = client.get_ohlcv(ticker, period=_period_for_days(days), interval="1d")
        df = _normalize_ohlcv(df)
        if df is None:
            logger.info(f"[ONEIL][US] No daily data for {ticker}.")
            return None

        df["MA10"] = df["Close"].rolling(window=10).mean()
        df["MA20"] = df["Close"].rolling(window=20).mean()
        df["MA50"] = df["Close"].rolling(window=50).mean()
        df["MA200"] = df["Close"].rolling(window=200).mean()

        ohlc_df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        s = create_mpf_style()

        index_ticker, index_label = _resolve_index(ticker, market, index_ticker)
        index_close = _fetch_index_close(index_ticker, days)
        rs_series = (
            _compute_rs_line(df["Close"], index_close)
            if index_close is not None
            else None
        )
        if rs_series is None:
            logger.warning(
                f"[ONEIL][US] RS line omitted for {ticker} (daily); "
                f"chart still rendered."
            )

        additional_plots = [
            mpf.make_addplot(df["MA10"], color="#00aa44", width=1),
            mpf.make_addplot(df["MA20"], color="#ff9500", width=1),
            mpf.make_addplot(df["MA50"], color="#0066cc", width=1.5),
            mpf.make_addplot(df["MA200"], color="#cc3300", width=1.5, linestyle="--"),
        ]

        if rs_series is not None:
            title = f"{company_name} ({ticker}) - O'Neil Daily (RS vs {index_label})"
        else:
            title = f"{company_name} ({ticker}) - O'Neil Daily"

        fig, axes = mpf.plot(
            ohlc_df,
            type="candle",
            style=s,
            title=title,
            ylabel="Price (USD)",
            volume=True,
            figsize=(12, 9),
            tight_layout=True,
            addplot=additional_plots,
            panel_ratios=(4, 1),
            returnfig=True,
        )

        ax1 = axes[0]
        ax1.legend(["MA10", "MA20", "MA50", "MA200"], loc="upper left")

        _add_rs_panel(fig, rs_series, index_label)

        fig.text(
            0.99, 0.005, "AI Stock Analysis", ha="right", va="bottom",
            color="#cccccc", fontsize=8,
        )

        if save_path:
            fig.savefig(save_path, bbox_inches="tight", dpi=80)
            logger.info(f"[ONEIL][US] daily chart saved: {save_path}")

        if return_df:
            return fig, ohlc_df
        return fig
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL][US] daily chart build failed for {ticker}: {e}")
        return None


def create_oneil_weekly_chart(
    ticker,
    company_name=None,
    weeks=104,
    adjusted=True,
    save_path=None,
    market="us",
    index_ticker=None,
):
    """Generate an O'Neil WEEKLY chart for a US ticker (vision-only).

    Fetches DAILY OHLCV ONCE and resamples to weekly (open=first, high=max,
    low=min, close=last, volume=sum), then draws weekly candles + 10-week and
    40-week MAs (O'Neil standard) + weekly volume + a weekly RS line vs the index.

    Args:
        ticker:       US stock ticker symbol.
        company_name: Company name for the title (auto-fetched if None).
        weeks:        Number of weeks to display (default 104 ~= 2 years).
        adjusted:     Accepted for KR-signature parity (yfinance auto-adjusts).
        save_path:    If given, save the figure there; otherwise just return it.
        market:       Market hint (default ``"us"``); informational here.
        index_ticker: Optional explicit index symbol override.

    Returns:
        matplotlib Figure (``axes[0]`` is the price axis), or None on failure.
        Never raises.
    """
    try:
        client = _get_client()
        if client is None:
            return None

        days = weeks * 7 + 40

        if company_name is None:
            try:
                info = client.get_company_info(ticker)
                company_name = info.get("name") or ticker
            except Exception:  # noqa: BLE001
                company_name = ticker

        daily = client.get_ohlcv(ticker, period=_period_for_days(days), interval="1d")
        daily = _normalize_ohlcv(daily)
        if daily is None:
            logger.info(f"[ONEIL][US] No daily data for {ticker} (weekly).")
            return None

        weekly = (
            daily.resample("W")
            .agg(
                {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum",
                }
            )
            .dropna(subset=["Open", "High", "Low", "Close"])
        )
        if len(weekly) == 0:
            logger.info(f"[ONEIL][US] Weekly resample empty for {ticker}.")
            return None

        weekly = weekly.tail(weeks).copy()

        weekly["MA10"] = weekly["Close"].rolling(window=10).mean()
        weekly["MA40"] = weekly["Close"].rolling(window=40).mean()

        ohlc_df = weekly[["Open", "High", "Low", "Close", "Volume"]].copy()
        s = create_mpf_style()

        index_ticker, index_label = _resolve_index(ticker, market, index_ticker)
        index_daily = _fetch_index_close(index_ticker, days)
        rs_series = None
        if index_daily is not None:
            try:
                index_weekly = index_daily.resample("W").last()
                rs_series = _compute_rs_line(weekly["Close"], index_weekly)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[ONEIL][US] weekly RS resample failed for {ticker}: {e}"
                )
        if rs_series is None:
            logger.warning(
                f"[ONEIL][US] RS line omitted for {ticker} (weekly); "
                f"chart still rendered."
            )

        additional_plots = [
            mpf.make_addplot(weekly["MA10"], color="#ff9500", width=1.2),
            mpf.make_addplot(weekly["MA40"], color="#cc3300", width=1.5, linestyle="--"),
        ]

        if rs_series is not None:
            title = f"{company_name} ({ticker}) - O'Neil Weekly (RS vs {index_label})"
        else:
            title = f"{company_name} ({ticker}) - O'Neil Weekly"

        fig, axes = mpf.plot(
            ohlc_df,
            type="candle",
            style=s,
            title=title,
            ylabel="Price (USD)",
            volume=True,
            figsize=(12, 9),
            tight_layout=True,
            addplot=additional_plots,
            panel_ratios=(4, 1),
            returnfig=True,
        )

        ax1 = axes[0]
        ax1.legend(["MA10 (10wk)", "MA40 (40wk)"], loc="upper left")

        _add_rs_panel(fig, rs_series, index_label)

        fig.text(
            0.99, 0.005, "AI Stock Analysis", ha="right", va="bottom",
            color="#cccccc", fontsize=8,
        )

        if save_path:
            fig.savefig(save_path, bbox_inches="tight", dpi=80)
            logger.info(f"[ONEIL][US] weekly chart saved: {save_path}")

        return fig
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ONEIL][US] weekly chart build failed for {ticker}: {e}")
        return None
