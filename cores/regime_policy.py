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

Policy rationale (US two-batch follow-up to
tasks/market_pulse/00_VALIDATION_PLAN.md §7 Rev.5):
    The V2 trade-sample audit REJECTED the original "CORRECTION = full stop"
    policy — CORRECTION-window buys had a scary 38% stop-out rate but a NET
    +25.3% P&L (the post-crash rebound monsters live in this window). So the
    revised policy does NOT stop buying during a correction; it merely REDUCES
    the agent to a single daily batch window, cutting exposure to the two noisiest
    micro-structure windows while keeping one shot at the rebound:

        * KR (morning / afternoon): CORRECTION -> afternoon only.
        * US (morning / afternoon): CORRECTION -> afternoon only.

    US now has the same two analysis windows as KR. The 10-minute hardstop and
    trend-exit loops own intraday downside response, so a third analysis batch
    is no longer needed. UNDER_PRESSURE therefore keeps both US windows:
    otherwise it would have the same one-batch result as CORRECTION.

    Remaining states — UPTREND and None (unknown / fail-open) — run every batch
    normally, as does KR under UNDER_PRESSURE. Exit/sell loops are NEVER affected
    by this policy; only the new-analysis agents rest.

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

# Table: batches that REST during CORRECTION, per market. Any batch NOT listed
# here still runs during CORRECTION (the retained daily window).
_CORRECTION_REST_BATCHES = {
    # Both markets retain the close-confirmation afternoon window and rest the
    # morning one. This keeps correction behavior consistent across KR and US.
    "kr": frozenset({"morning"}),
    "us": frozenset({"morning"}),
}

# UNDER_PRESSURE has no batch rests. With two scheduled US windows, resting its
# morning batch would make it operationally identical to CORRECTION.
_UNDER_PRESSURE_REST_BATCHES: dict[str, frozenset[str]] = {}

# Module-level cache: pulse state is computed once per (short-lived) process and
# reused by the orchestrator hook and by per-ticker trend-fact injection so we do
# not re-fetch the index for every ticker. A None result is cached too, so a
# failed/network-less run does not retry on every call.
_STATE_CACHE: dict = {}

# Item 4: post-FTD pilot re-exposure cache (per-process). True/False memoized so
# the ~400d index replay runs at most once per market per process. Fail-open False.
_PILOT_CACHE: dict = {}

# Item 5: Market Pulse detail cache — state + distribution_days + window, per-process.
_DETAIL_CACHE: dict = {}


@dataclass(frozen=True)
class MarketPulseDetail:
    """Snapshot of the Market Pulse state machine after the latest replay.

    Attributes:
        state:             Final state string (UPTREND / UNDER_PRESSURE / CORRECTION).
        distribution_days: Live distribution-day count from the rolling window.
        window:            Rolling-window length in sessions (mirrors DISTRIBUTION_WINDOW).
    """

    state: str
    distribution_days: int
    window: int


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
        batch_mode:  KR/US: "morning" or "afternoon".
                     ("both" or any unknown mode fails open -> run.)
        pulse_state: UPTREND / UNDER_PRESSURE / CORRECTION / None.

    Rationale: CORRECTION is not a buy stop; it reduces both markets to the
    afternoon close-confirmation window while keeping one shot at the post-crash
    rebound. UNDER_PRESSURE keeps the normal morning + afternoon schedule. Exit
    loops are unaffected. Any state/mode not in a rest table runs (fail-open on
    None/unknown).
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

    if pulse_state == UNDER_PRESSURE:
        rest_batches = _UNDER_PRESSURE_REST_BATCHES.get(m, frozenset())
        if mode in rest_batches:
            return BatchPolicy(
                run_batch=False,
                reason=(
                    f"UNDER_PRESSURE: {m or '?'} '{mode or '?'}' batch rests "
                    "(exit loops unaffected)"
                ),
                pulse_state=pulse_state,
            )
        return BatchPolicy(
            run_batch=True,
            reason=(
                f"UNDER_PRESSURE: {m or '?'} '{mode or '?'}' batch runs "
                "(normal two-batch schedule)"
            ),
            pulse_state=pulse_state,
        )

    # UPTREND / None(unknown) -> run everything (fail-open).
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
# Regime-adaptive hard min_score floor (env-gated, default OFF)               #
# --------------------------------------------------------------------------- #
# 7월 -42%p 손실 재발 방지책의 하나: 매수 임계(min_score)는 지금 LLM이 시나리오마다
# 자유롭게 정한다(약세장에서 낮아질 수 있음). 아래 표는 시장 레짐별 "절대 하한선"을
# 강제해, 약세장에서는 LLM이 무슨 값을 주더라도 그 밑으로는 못 사게 한다(안전 게이트).
# 기본 OFF(REGIME_MIN_SCORE_FLOOR 미설정) = 현행 유지(LLM 값 그대로).
_REGIME_MIN_SCORE_FLOORS = {
    "strong_bear": 9,
    "moderate_bear": 8,
    "sideways": 8,
    "moderate_bull": 0,
    "strong_bull": 0,
    "unknown": 0,
}


def regime_min_score_floor_enabled() -> bool:
    """Return True when REGIME_MIN_SCORE_FLOOR is truthy (1/true/yes/on). Default OFF.

    Same truthy parsing as trigger_batch's REGIME_WEAK_NO_TOPDOWN gate.
    """
    return os.getenv("REGIME_MIN_SCORE_FLOOR", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )


def min_score_floor(market_regime: Optional[str]) -> int:
    """Hard buy-score floor for ``market_regime`` (0 for bullish / unknown regimes).

    Tolerant of decorated labels (e.g. ``"strong_bear (하락)"`` -> ``strong_bear``):
    only the leading whitespace-delimited token is matched. Unmapped -> 0.
    """
    raw = (market_regime or "").strip().lower()
    key = raw.split()[0] if raw else "unknown"
    return _REGIME_MIN_SCORE_FLOORS.get(key, 0)


def effective_min_score(llm_min_score, market_regime: Optional[str]) -> int:
    """Return ``max(llm_min_score, regime_floor)`` when the flag is ON; else the
    LLM value unchanged.

    NEVER lowers the LLM threshold (floor is a one-way raise). Pure, no I/O beyond
    the single env read. ``llm_min_score`` non-int/None is treated as 0.
    """
    try:
        base = int(llm_min_score or 0)
    except (TypeError, ValueError):
        base = 0
    if not regime_min_score_floor_enabled():
        return base
    return max(base, min_score_floor(market_regime))


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

    import sys

    spec = importlib.util.spec_from_file_location(f"prism_root_cores_{name}", target)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec_module so a top-level frozen @dataclass in the loaded
    # module (e.g. market_pulse.DailyBar) can resolve sys.modules[cls.__module__]
    # .__dict__ during class creation. Without this, the by-path fallback — hit
    # whenever cores/ is shadowed (e.g. the prism-us runtime) — raises
    # AttributeError('NoneType' object has no attribute '__dict__') and
    # get_market_pulse_state/detail silently fail-open to None. Mirrors the same
    # fix in us_stock_tracking_agent._import_from_main_cores.
    sys.modules[spec.name] = mod
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
    """Test/utility hook: clear the memoized pulse-state + pilot + detail caches."""
    _STATE_CACHE.clear()
    _PILOT_CACHE.clear()
    _DETAIL_CACHE.clear()


def get_market_pulse_detail(market: str, use_cache: bool = True) -> Optional[MarketPulseDetail]:
    """Compute Market Pulse state AND distribution-day count for ``market``.

    Replays :class:`cores.market_pulse.MarketPulse` over ~400 calendar days of
    index bars and returns a :class:`MarketPulseDetail` with ``state``,
    ``distribution_days``, and ``window``.  Per-process memoized (:data:`_DETAIL_CACHE`).

    Fail-open: ANY exception (network, auth, missing data, import) is logged as a
    warning and returns ``None``, so this never raises into the buy path.
    The existing :func:`get_market_pulse_state` signature/return contract is unchanged.
    """
    m = (market or "").strip().lower()
    if use_cache and m in _DETAIL_CACHE:
        return _DETAIL_CACHE[m]

    try:
        mp_mod = _load_root_cores("market_pulse")
        MarketPulse = mp_mod.MarketPulse
        DailyBar = mp_mod.DailyBar
        dd_window = getattr(mp_mod, "DISTRIBUTION_WINDOW", 25)

        if m == "kr":
            bars = _fetch_kr_bars(DailyBar)
        elif m == "us":
            bars = _fetch_us_bars(DailyBar)
        else:
            logger.warning("[MARKET_PULSE_DETAIL] unknown market %r -> None", market)
            _DETAIL_CACHE[m] = None
            return None

        if not bars or len(bars) < 30:
            raise RuntimeError(f"insufficient index bars: {len(bars) if bars else 0}")

        mp = MarketPulse()
        state: Optional[str] = None
        for bar in bars:
            state = mp.feed(bar)

        if state is None:
            _DETAIL_CACHE[m] = None
            return None

        detail = MarketPulseDetail(
            state=state,
            distribution_days=int(mp.distribution_days),
            window=int(dd_window),
        )
        _DETAIL_CACHE[m] = detail
        return detail
    except Exception as e:  # noqa: BLE001 - fail-open, never raise
        logger.warning(
            "[MARKET_PULSE_DETAIL] detail compute failed for %s, fail-open None: %s",
            m or "?", e,
        )
        _DETAIL_CACHE[m] = None
        return None


# --------------------------------------------------------------------------- #
# Post-FTD progressive re-exposure — pilot new-entry throttle (env-gated, OFF)  #
# --------------------------------------------------------------------------- #
# After a CORRECTION ends (FTD or price-recovery exit), the first buys back into
# the market are the riskiest (early re-entry can be a bull trap). PULSE_PILOT_REEXPOSURE
# ON => for the first PULSE_PILOT_WINDOW_SESSIONS trading sessions after the
# CORRECTION -> (UPTREND/UNDER_PRESSURE) transition, THROTTLE THE NUMBER of new
# entries (배치당 신규 진입 1종목 + 중복매수 동결) at the decision layer shared by the
# simulator and real orders. 금액은 항상 100% 정상 (all-in/all-out per position 계약을
# 지키기 위해 fractional sizing 은 절대 사용하지 않는다 — sim/real parity). Default OFF =
# 현행 유지. Fail-open: any error -> 원래 동작(정상 진입).
PULSE_PILOT_WINDOW_SESSIONS: int = 5


def pilot_reexposure_enabled() -> bool:
    """Return True when PULSE_PILOT_REEXPOSURE is truthy (1/true/yes/on). Default OFF."""
    return os.getenv("PULSE_PILOT_REEXPOSURE", "false").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _sessions_since_correction_exit(states) -> Optional[int]:
    """Pure. Given a chronological list of pulse-state strings, return the number
    of trading sessions since the most recent CORRECTION -> non-CORRECTION
    transition (0 on the exit session itself, 1 the next session, ...).

    Returns ``None`` when the series currently ends in CORRECTION (no re-exposure
    window active yet) or no such transition exists in the series.
    """
    if not states:
        return None
    if states[-1] == CORRECTION:
        return None
    exit_idx = None
    for i in range(1, len(states)):
        if states[i - 1] == CORRECTION and states[i] != CORRECTION:
            exit_idx = i
    if exit_idx is None:
        return None
    return (len(states) - 1) - exit_idx


def is_pilot_window(sessions_ago: Optional[int], flag_on: Optional[bool] = None) -> bool:
    """Pure. True iff the flag is ON and ``0 <= sessions_ago < PULSE_PILOT_WINDOW_SESSIONS``.

    ``flag_on`` defaults to :func:`pilot_reexposure_enabled` when not supplied
    (injectable for tests). ``sessions_ago is None`` -> False (full size).
    """
    if flag_on is None:
        flag_on = pilot_reexposure_enabled()
    if not flag_on or sessions_ago is None:
        return False
    return 0 <= sessions_ago < PULSE_PILOT_WINDOW_SESSIONS


def pilot_reexposure_active(market: str, use_cache: bool = True) -> bool:
    """Return True when the pilot new-entry throttle applies for ``market`` ("kr" | "us").

    Flag OFF -> False (no replay, zero cost). Otherwise replays MarketPulse over
    ~400d of index bars, finds the last CORRECTION exit, and checks the window.
    Memoized per process (:data:`_PILOT_CACHE`). Fail-open: ANY error -> False
    (정상 진입), so this never raises into a production buy path.
    """
    if not pilot_reexposure_enabled():
        return False
    m = (market or "").strip().lower()
    if use_cache and m in _PILOT_CACHE:
        return _PILOT_CACHE[m]
    try:
        mp_mod = _load_root_cores("market_pulse")
        MarketPulse = mp_mod.MarketPulse
        DailyBar = mp_mod.DailyBar

        if m == "kr":
            bars = _fetch_kr_bars(DailyBar)
        elif m == "us":
            bars = _fetch_us_bars(DailyBar)
        else:
            logger.warning("[PULSE_PILOT] unknown market %r -> full size", market)
            _PILOT_CACHE[m] = False
            return False

        if not bars or len(bars) < 30:
            raise RuntimeError(f"insufficient index bars: {len(bars) if bars else 0}")

        mp = MarketPulse()
        states = [mp.feed(bar) for bar in bars]
        ago = _sessions_since_correction_exit(states)
        active = is_pilot_window(ago, flag_on=True)
        _PILOT_CACHE[m] = active
        return active
    except Exception as e:  # noqa: BLE001 - fail-open, never raise
        logger.warning("[PULSE_PILOT] active-check failed for %s, fail-open full-size: %s",
                       m or "?", e)
        _PILOT_CACHE[m] = False
        return False
