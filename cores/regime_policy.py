"""Regime policy — single source of truth for Market Pulse batch/rest decisions.

This module glues the pure :mod:`cores.market_pulse` state machine to the
production orchestrators. It answers three questions, and nothing else:

  1. :func:`decide_batch_policy` — given (market, batch_mode, pulse_state), should
     THIS analysis batch run, or rest? Pure, table-driven, no I/O, no env reads.
  2. :func:`get_market_pulse_state` — compute the CURRENT pulse state by replaying
     :class:`cores.market_pulse.MarketPulse` over the last ~400 calendar days of
     index bars. Fail-open: ANY error returns ``None`` (never raises).
  3. :func:`market_pulse_mode` — read the ``MARKET_PULSE_MODE`` env flag
     (``shadow`` | ``live`` | ``off``; default ``shadow``).

Policy rationale (tasks/market_pulse/00_VALIDATION_PLAN.md §7 Rev.3):
    The V2 trade-sample audit REJECTED the original "CORRECTION = full stop"
    policy — CORRECTION-window buys had a scary 38% stop-out rate but a NET
    +25.3% P&L (the post-crash rebound monsters live in this window). So the
    revised policy does NOT stop buying during a correction; it merely REDUCES
    the agent to a single daily batch window, cutting exposure to the two noisiest
    micro-structure windows while keeping one shot at the rebound:

        * KR (morning / afternoon): CORRECTION -> afternoon rests, morning runs.
        * US (morning / midday / afternoon): CORRECTION -> only midday runs;
          morning (open-hour noise is maximal) and afternoon (overnight gap risk
          on a late buy) both rest.

    Non-CORRECTION states — UPTREND, UNDER_PRESSURE, and None (unknown / fail-open)
    — run every batch normally. Exit/sell loops are NEVER affected by this policy;
    only the new-analysis agents rest.

Import safety: this module performs NO heavy imports at module load. All data
fetching and market_pulse/stock_chart imports are lazy (inside functions) and
resolved via :func:`_load_root_cores`, which loads the ROOT ``cores/`` sibling by
file path even when ``sys.path`` shadowing (prism-us/cores) would otherwise win.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# PulseState string values (mirror cores.market_pulse; kept local so
# decide_batch_policy stays a pure function with zero imports of the state
# machine — the strings are the contract).
UPTREND: str = "UPTREND"
UNDER_PRESSURE: str = "UNDER_PRESSURE"
CORRECTION: str = "CORRECTION"

# Valid MARKET_PULSE_MODE values and default.
_VALID_MODES = ("shadow", "live", "off")
_DEFAULT_MODE = "shadow"

# Table (§7 Rev.3): batches that REST during CORRECTION, per market. Any batch
# NOT listed here still runs during CORRECTION (the retained daily window).
_CORRECTION_REST_BATCHES = {
    # §7 Rev.4: KR keeps the AFTERNOON (14:50, close-confirmation) window and
    # rests the morning one — in corrections, morning gap-strength fades
    # intraday (distribution into early hope); buy what HELD through the day,
    # not what looks like it will rise. Same open-noise principle as US.
    "kr": frozenset({"morning"}),                 # afternoon runs, morning rests
    "us": frozenset({"morning", "afternoon"}),    # only midday runs
}

# Module-level cache: pulse state is computed once per (short-lived) process and
# reused by the orchestrator hook and by per-ticker trend-fact injection so we do
# not re-fetch the index for every ticker. A None result is cached too, so a
# failed/network-less run does not retry on every call.
_STATE_CACHE: dict = {}


@dataclass(frozen=True)
class BatchPolicy:
    """Decision for a single analysis batch.

    Attributes:
        run_batch:   True => run this batch normally; False => this batch rests.
        reason:      Human-readable explanation (goes to logs).
        pulse_state: The pulse state the decision was based on (may be None).
    """

    run_batch: bool
    reason: str
    pulse_state: Optional[str]


def decide_batch_policy(
    market: str, batch_mode: str, pulse_state: Optional[str]
) -> BatchPolicy:
    """Decide whether an analysis batch should run, given the pulse state.

    Pure function — no env reads, no I/O, table-driven (:data:`_CORRECTION_REST_BATCHES`).

    Args:
        market:      "kr" or "us" (case-insensitive).
        batch_mode:  KR: "morning"/"afternoon"; US: "morning"/"midday"/"afternoon".
                     ("both" or any unknown mode fails open -> run.)
        pulse_state: UPTREND / UNDER_PRESSURE / CORRECTION / None.

    Rationale (§7 Rev.3, batch choice revised by Rev.4): CORRECTION is not a buy
    stop; it reduces the agent to a single daily window (KR afternoon-only =
    close-confirmation entry, US midday-only) to dodge the open-hour noise where
    gap-strength fades intraday in weak markets, while keeping one shot at the
    post-crash rebound. Exit loops are unaffected. Any non-CORRECTION or unknown
    state runs everything (fail-open).
    """
    m = (market or "").strip().lower()
    mode = (batch_mode or "").strip().lower()

    if pulse_state == CORRECTION:
        rest_batches = _CORRECTION_REST_BATCHES.get(m, frozenset())
        if mode in rest_batches:
            return BatchPolicy(
                run_batch=False,
                reason=(
                    f"CORRECTION: {m or '?'} '{mode or '?'}' batch rests "
                    "(reduce to one daily window; exit loops unaffected)"
                ),
                pulse_state=pulse_state,
            )
        return BatchPolicy(
            run_batch=True,
            reason=(
                f"CORRECTION: {m or '?'} '{mode or '?'}' batch runs "
                "(retained daily window)"
            ),
            pulse_state=pulse_state,
        )

    # UPTREND / UNDER_PRESSURE / None(unknown) -> run everything (fail-open).
    return BatchPolicy(
        run_batch=True,
        reason=f"{pulse_state or 'UNKNOWN'}: run all batches",
        pulse_state=pulse_state,
    )


def market_pulse_mode() -> str:
    """Return the MARKET_PULSE_MODE env flag: 'shadow' (default) | 'live' | 'off'.

    Unknown/empty values fall back to 'shadow' (the safe, log-only default).
    """
    raw = (os.getenv("MARKET_PULSE_MODE") or "").strip().lower()
    return raw if raw in _VALID_MODES else _DEFAULT_MODE


# --------------------------------------------------------------------------- #
# Pulse-state computation (lazy, fail-open, shadow-safe imports)               #
# --------------------------------------------------------------------------- #
def _load_root_cores(name: str):
    """Import ``cores.<name>`` from the ROOT cores/ dir, defeating sys.path shadowing.

    The US orchestrator/agent runs with prism-us/ ahead of PROJECT_ROOT on
    sys.path, so a plain ``import cores.<name>`` may resolve to prism-us/cores/.
    This module lives in the ROOT cores/ dir, so its siblings (market_pulse.py,
    stock_chart.py) are addressable by file path relative to ``__file__`` — always
    the correct root module. We try the normal import first (cheap when it already
    points at the right file, e.g. in the KR process) and only fall back to a
    by-path load when it is missing or shadowed.
    """
    import importlib
    import importlib.util
    import pathlib

    target = pathlib.Path(__file__).with_name(f"{name}.py").resolve()
    try:
        mod = importlib.import_module(f"cores.{name}")
        mf = getattr(mod, "__file__", None)
        if mf and pathlib.Path(mf).resolve() == target:
            return mod
    except Exception:  # noqa: BLE001 - shadowed/missing => fall through to by-path
        pass

    spec = importlib.util.spec_from_file_location(f"prism_root_cores_{name}", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _df_to_bars(df, close_col: str, vol_col: Optional[str], DailyBar):
    """Convert an OHLCV frame to a chronological list of ``DailyBar``."""
    import pandas as pd

    bars = []
    for idx, row in df.iterrows():
        c = float(row[close_col])
        if c <= 0:
            continue
        v: Optional[float] = None
        if vol_col is not None:
            raw = row[vol_col]
            if raw is not None and not pd.isna(raw):
                v = float(raw)
                if v <= 0:
                    v = None
        bars.append(DailyBar(date=idx.strftime("%Y-%m-%d"), close=c, volume=v))
    return bars


def _fetch_kr_bars(DailyBar):
    """KOSPI index (1001) ~400d daily OHLCV via the authenticated KRX client.

    Mirrors tools/market_pulse_backtest.py:fetch_kr_bars but with a 400-day window
    (~2 yearly chunks; the KRX API rejects a 6y single request with INVALIDPERIOD2,
    so we fetch per calendar year and concat). Volume is required for DD detection.
    """
    import pandas as pd
    from datetime import datetime, timedelta

    sc = _load_root_cores("stock_chart")
    get_index_ohlcv_by_date = sc.get_index_ohlcv_by_date

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=400)
    chunks = []
    y = start_dt.year
    while y <= end_dt.year:
        s = max(start_dt, datetime(y, 1, 1)).strftime("%Y%m%d")
        e = min(end_dt, datetime(y, 12, 31)).strftime("%Y%m%d")
        cdf = get_index_ohlcv_by_date(s, e, "1001")
        if cdf is not None and len(cdf):
            chunks.append(cdf)
        y += 1
    if not chunks:
        raise RuntimeError("KOSPI(1001) KRX fetch returned empty for all chunks")
    df = pd.concat(chunks)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    close_col = "종가" if "종가" in df.columns else "Close"
    vol_col = (
        "거래량" if "거래량" in df.columns
        else ("Volume" if "Volume" in df.columns else None)
    )
    if vol_col is None:
        raise RuntimeError("KOSPI(1001) frame has no volume column")
    return _df_to_bars(df, close_col, vol_col, DailyBar)


def _fetch_us_bars(DailyBar):
    """S&P 500 (^GSPC) daily via yfinance (period=2y ~ the 400d window)."""
    import pandas as pd
    import yfinance as yf

    df = yf.download("^GSPC", period="2y", interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(how="all")
    if df is None or len(df) == 0:
        raise RuntimeError("^GSPC fetch returned empty")
    vol_col = "Volume" if "Volume" in df.columns else None
    return _df_to_bars(df.sort_index(), "Close", vol_col, DailyBar)


def get_market_pulse_state(market: str, use_cache: bool = True) -> Optional[str]:
    """Compute the current Market Pulse state for ``market`` ("kr" | "us").

    Replays :class:`cores.market_pulse.MarketPulse` over ~400 calendar days of
    index bars and returns the final state string (UPTREND / UNDER_PRESSURE /
    CORRECTION). Memoized per process (:data:`_STATE_CACHE`).

    NOTE: 400 days is enough for current-state purposes (the rolling peak / DD
    window reference stays inside this window). A state read near the window edge
    can differ slightly from a full 6-year replay — acceptable for policy use.

    Fail-open: ANY exception (network, auth, missing data, import) is logged as a
    warning and returns ``None`` (cached), so this never raises into a production
    batch or buy path.
    """
    m = (market or "").strip().lower()
    if use_cache and m in _STATE_CACHE:
        return _STATE_CACHE[m]

    try:
        mp_mod = _load_root_cores("market_pulse")
        MarketPulse = mp_mod.MarketPulse
        DailyBar = mp_mod.DailyBar

        if m == "kr":
            bars = _fetch_kr_bars(DailyBar)
        elif m == "us":
            bars = _fetch_us_bars(DailyBar)
        else:
            logger.warning("[MARKET_PULSE] unknown market %r -> None", market)
            _STATE_CACHE[m] = None
            return None

        if not bars or len(bars) < 30:
            raise RuntimeError(f"insufficient index bars: {len(bars) if bars else 0}")

        mp = MarketPulse()
        state: Optional[str] = None
        for bar in bars:
            state = mp.feed(bar)
        _STATE_CACHE[m] = state
        return state
    except Exception as e:  # noqa: BLE001 - fail-open, never raise
        logger.warning("[MARKET_PULSE] state compute failed for %s, fail-open None: %s",
                       m or "?", e)
        _STATE_CACHE[m] = None
        return None


def _reset_state_cache() -> None:
    """Test/utility hook: clear the memoized pulse-state cache."""
    _STATE_CACHE.clear()
