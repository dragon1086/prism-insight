#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file (required before krx_data_client import)

import datetime
import pandas as pd
import numpy as np
import logging
import os
import time
from typing import Optional
from cores.naver_market_snapshot import (
    MarketSnapshotBundle,
    fetch_naver_snapshot_bundle,
)
from cores.kis_market_snapshot import build_kis_openapi_snapshot_bundle
from cores.rs_rating import oneil_weighted_return, percentile_ratings
from krx_data_client import (
    _get_client,
    get_market_ohlcv_by_ticker,
    get_nearest_business_day_in_a_week,
    get_market_cap_by_ticker,
    get_market_ticker_name,
)

# pykrx compatibility wrapper (for existing code compatibility)
class stock_api:
    get_market_ohlcv_by_ticker = staticmethod(get_market_ohlcv_by_ticker)
    get_nearest_business_day_in_a_week = staticmethod(get_nearest_business_day_in_a_week)
    get_market_cap_by_ticker = staticmethod(get_market_cap_by_ticker)
    get_market_ticker_name = staticmethod(get_market_ticker_name)

# Logger configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)


_TICKER_NAME_CACHE: Optional[dict[str, str]] = None
SCREENING_MIN_TRADE_VALUE = 10_000_000_000
EMERGING_LIQUIDITY_MIN_TRADE_VALUE = 5_000_000_000
EMERGING_LIQUIDITY_MAX_CANDIDATES = 1


class MarketSnapshotUnavailableError(RuntimeError):
    """Raised when neither KRX nor the emergency Naver source is usable."""


def _normalize_ticker_code(ticker) -> str:
    ticker_code = str(ticker).strip()
    if ticker_code.isdigit():
        return ticker_code.zfill(6)
    return ticker_code


def _get_ticker_name_map() -> dict[str, str]:
    """Fetch all ticker names once per process to avoid repeated KRX calls."""
    global _TICKER_NAME_CACHE
    if _TICKER_NAME_CACHE is None:
        try:
            client = _get_client()
            names = client.get_market_ticker_name(market="ALL")
            _TICKER_NAME_CACHE = {_normalize_ticker_code(ticker): str(name) for ticker, name in names.items()}
        except Exception as e:
            logger.warning(f"KRX ticker name lookup failed; using ticker codes as names: {e}")
            _TICKER_NAME_CACHE = {}
    return _TICKER_NAME_CACHE


def _get_display_ticker_name(ticker, name_map: dict[str, str]) -> str:
    """Company name for display, falling back through the source chain.

    The bulk lookup above is a single KRX call, so when KRX blocks the host it
    returns an empty map and *every* name silently degrades to a bare code. That
    reached users on 2026-08-05: the afternoon signal alert read "009150 (009150)"
    instead of "삼성전기 (009150)".

    ``cores.market_data`` resolves names through the configured chain, where the
    FDR source answers from a KRX listing snapshot that has nothing to do with
    the blocked Data Marketplace session. Only selected candidates are ever
    rendered — a handful of tickers — and FDR caches the listing after the first
    call, so this stays cheap.

    Resolved names are written back into ``name_map`` (the process-wide cache),
    so each ticker costs at most one lookup per run.
    """
    ticker_code = _normalize_ticker_code(ticker)
    name = name_map.get(ticker_code)
    if name:
        return name

    try:
        from cores.market_data import get_market_ticker_name

        resolved = get_market_ticker_name(ticker_code)
    except Exception as e:  # noqa: BLE001 - a label is never worth failing over
        logger.debug(f"Ticker name chain lookup failed for {ticker_code}: {e}")
        return ticker_code

    # The chain returns the ticker itself when no source can answer.
    if resolved and resolved != ticker_code:
        name_map[ticker_code] = resolved
        return resolved
    return ticker_code


# --- Data collection and caching functions ---

# KRX snapshot retry: 2026-07-22 afternoon batch died on a single transient
# data.krx.co.kr read timeout (no retry → "No stocks selected" → whole KR
# window skipped). Transient KRX blips recover within seconds-to-minutes,
# so retry a few times with a flat backoff before giving up.
_SNAPSHOT_MAX_ATTEMPTS = 3
_SNAPSHOT_RETRY_WAIT_SEC = 20


def get_snapshot(trade_date: str) -> pd.DataFrame:
    """
    Return OHLCV snapshot for all stocks on specified trading date.
    Columns: "Open", "High", "Low", "Close", "Volume", "Amount"
    """
    logger.debug(f"get_snapshot called: {trade_date}")
    last_exc: Optional[Exception] = None
    for attempt in range(1, _SNAPSHOT_MAX_ATTEMPTS + 1):
        try:
            df = stock_api.get_market_ohlcv_by_ticker(trade_date)
            break
        except Exception as e:
            last_exc = e
            logger.warning(
                f"KRX snapshot fetch failed (attempt {attempt}/{_SNAPSHOT_MAX_ATTEMPTS}): {e}"
            )
            if attempt < _SNAPSHOT_MAX_ATTEMPTS:
                time.sleep(_SNAPSHOT_RETRY_WAIT_SEC)
    else:
        raise last_exc
    if df.empty:
        logger.error(f"No OHLCV data for {trade_date}.")
        raise ValueError(f"No OHLCV data for {trade_date}.")

    # Data verification
    logger.debug(f"Snapshot data sample: {df.head()}")
    logger.debug(f"Snapshot data columns: {df.columns}")

    return df

def get_previous_snapshot(trade_date: str) -> (pd.DataFrame, str):
    """
    Find the previous business day before specified trading date and return OHLCV snapshot with date.
    """
    # Convert to date object
    date_obj = datetime.datetime.strptime(trade_date, '%Y%m%d')

    # Move back one day
    prev_date_obj = date_obj - datetime.timedelta(days=1)

    # Convert to string for business day check
    prev_date_str = prev_date_obj.strftime('%Y%m%d')

    # Find previous business day
    prev_date = stock_api.get_nearest_business_day_in_a_week(prev_date_str, prev=True)

    logger.debug(f"Previous trading day check - Base date: {trade_date}, Day before: {prev_date_str}, Previous business day: {prev_date}")

    df = stock_api.get_market_ohlcv_by_ticker(prev_date)
    if df.empty:
        logger.error(f"No OHLCV data for {prev_date}.")
        raise ValueError(f"No OHLCV data for {prev_date}.")

    # Data verification
    logger.debug(f"Previous trading day data sample: {df.head()}")
    logger.debug(f"Previous trading day data columns: {df.columns}")

    return df, prev_date

# --- KRX request throttle ---------------------------------------------------
# 여기 있던 _krx_throttle 은 cores/market_data/krx_source.py 로 옮겼다.
# data.krx.co.kr 는 버스트 요청·장마감 트래픽에 응답이 느려져 Read timeout 을
# 내고, 지속되면 "비정상 대량 조회"로 IP 가 차단된다. 간격 유지는 호출자 한 곳이
# 아니라 **소스**의 책임이다 — 이 파일에 두었을 때는 get_multi_day_ohlcv 한
# 군데만 보호했고 체인을 타는 나머지 KRX 호출은 전부 무방비였다.
# 조절은 그대로 KRX_MIN_GAP_SEC (기본 0.4s, 0 이면 비활성).


def get_multi_day_ohlcv(ticker: str, end_date: str, days: int = 10) -> pd.DataFrame:
    """
    Query N-day OHLCV data for specific stock.

    Args:
        ticker: Stock code
        end_date: End date (YYYYMMDD)
        days: Number of business days to query (default: 10 days)

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume, Amount
        Index: Date
    """
    # Routed through the source chain (default kis -> fdr -> krx) rather than
    # calling krx_data_client directly.
    #
    # This is the hottest KRX path in the batch: the 2026-08-05 outage run made
    # 42 KRX attempts and **39 of them came from here**. Going direct meant the
    # chain order was irrelevant — KIS could be first and this still hammered
    # KRX, which is both what gets the host restricted and what Plan A exists to
    # remove.
    #
    # The chain returns an empty frame when every source is exhausted (it does
    # not raise), so the FDR retry below now only runs after kis/fdr/krx have all
    # declined. It is kept because it calls FinanceDataReader differently from
    # the chain's FDR source, so it is a genuine second chance rather than a
    # repeat of the same request.
    from cores.market_data import get_market_ohlcv_by_date

    # Calculate sufficient past date from end date (with margin for business days)
    end_dt = datetime.datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - datetime.timedelta(days=days * 2)  # 2x margin for business days
    start_date = start_dt.strftime('%Y%m%d')

    chain_failed = False
    try:
        df = get_market_ohlcv_by_date(start_date, end_date, ticker)
        if df.empty:
            logger.warning(
                f"No {days}-day data for {ticker} from any source; "
                f"attempting FinanceDataReader fallback."
            )
            chain_failed = True
        else:
            # Select only recent N days
            return df.tail(days)
    except Exception as e:
        logger.warning(f"Market data chain failed for {ticker}: {e}; attempting FinanceDataReader fallback.")
        chain_failed = True

    if chain_failed:
        try:
            import FinanceDataReader as fdr
            fdr_start = start_dt.strftime('%Y-%m-%d')
            fdr_end = end_dt.strftime('%Y-%m-%d')
            fdr_df = fdr.DataReader(ticker, fdr_start, fdr_end)
            if fdr_df.empty:
                logger.warning(f"FinanceDataReader also returned empty data for {ticker}.")
                return pd.DataFrame()
            # Normalize columns to KRX schema (English: Open/High/Low/Close/Volume)
            col_map = {
                'open': 'Open', 'high': 'High', 'low': 'Low',
                'close': 'Close', 'volume': 'Volume',
                '시가': 'Open', '고가': 'High', '저가': 'Low',
                '종가': 'Close', '거래량': 'Volume',
            }
            fdr_df = fdr_df.rename(columns={
                c: col_map[c.lower()] for c in fdr_df.columns
                if c.lower() in col_map
            })
            logger.warning(f"[FDR-FALLBACK] {ticker}: FinanceDataReader(네이버) used (KRX unavailable).")
            return fdr_df.tail(days)
        except Exception as fe:
            logger.error(f"FinanceDataReader fallback also failed for {ticker}: {fe}")
            return pd.DataFrame()

    return pd.DataFrame()


def get_market_cap_df(trade_date: str, market: str = "ALL") -> pd.DataFrame:
    """
    Return market cap data for all stocks on specified trading date as DataFrame.
    Index is stock code, includes market cap column.
    """
    logger.debug(f"get_market_cap_df called: {trade_date}, market={market}")
    cap_df = stock_api.get_market_cap_by_ticker(trade_date, market=market)
    if cap_df.empty:
        logger.error(f"No market cap data for {trade_date}.")
        raise ValueError(f"No market cap data for {trade_date}.")
    return cap_df


def load_market_snapshot_bundle(trade_date: str) -> MarketSnapshotBundle:
    """Load current KIS quotes plus previous OPEN API data, or Naver fallback."""
    try:
        bundle = build_kis_openapi_snapshot_bundle(trade_date)
        logger.info(
            "[MARKET-DATA] source=KIS+KRX_OPENAPI stocks=%d prev_date=%s cap_rows=%d",
            len(bundle.snapshot),
            bundle.prev_date,
            len(bundle.cap_df),
        )
        return bundle
    except Exception as primary_exc:
        logger.warning(
            "[MARKET-DATA] KIS+OPENAPI bundle failed; switching to Naver fallback: %s",
            primary_exc,
        )
        try:
            bundle = fetch_naver_snapshot_bundle(
                trade_date,
                detail_min_amount=EMERGING_LIQUIDITY_MIN_TRADE_VALUE,
            )
        except Exception as naver_exc:
            logger.error(
                "[MARKET-DATA] both KIS+OPENAPI and Naver snapshot sources failed: "
                "primary=%s naver=%s",
                primary_exc,
                naver_exc,
            )
            raise MarketSnapshotUnavailableError(
                f"Market snapshot unavailable from KIS+OPENAPI and Naver: {naver_exc}"
            ) from naver_exc

        logger.warning(
            "[MARKET-DATA] source=NAVER_FALLBACK stocks=%d prev_date=%s cap_rows=%d",
            len(bundle.snapshot),
            bundle.prev_date,
            len(bundle.cap_df),
        )
        return bundle

def filter_low_liquidity(df: pd.DataFrame, threshold: float = 0.2) -> pd.DataFrame:
    """
    Filter out stocks in bottom N% by volume (low liquidity filtering)
    """
    volume_cutoff = np.percentile(df['Volume'], threshold * 100)
    return df[df['Volume'] > volume_cutoff]

def apply_absolute_filters(df: pd.DataFrame, min_value: int = 500000000) -> pd.DataFrame:
    """
    Absolute criteria filtering:
    - Minimum trade value (500M KRW or more)
    - Sufficient liquidity
    """
    # Minimum trade value filter (500M KRW or more)
    filtered_df = df[df['Amount'] >= min_value]

    # Volume filter: at least 20% of market average
    avg_volume = df['Volume'].mean()
    min_volume = avg_volume * 0.2
    filtered_df = filtered_df[filtered_df['Volume'] >= min_volume]

    return filtered_df

def normalize_and_score(df: pd.DataFrame, ratio_col: str, abs_col: str,
                        ratio_weight: float = 0.6, abs_weight: float = 0.4,
                        ascending: bool = False) -> pd.DataFrame:
    """
    Calculate composite score by normalizing columns and applying weights.

    ratio_col: Relative ratio column (e.g., volume ratio)
    abs_col: Absolute value column (e.g., volume)
    ratio_weight: Weight for relative ratio (default: 0.6)
    abs_weight: Weight for absolute value (default: 0.4)
    ascending: Sort direction (default: False, descending)
    """
    if df.empty:
        return df

    # Calculate max/min values for normalization
    ratio_max = df[ratio_col].max()
    ratio_min = df[ratio_col].min()
    abs_max = df[abs_col].max()
    abs_min = df[abs_col].min()

    # Prevent division by zero
    ratio_range = ratio_max - ratio_min if ratio_max > ratio_min else 1
    abs_range = abs_max - abs_min if abs_max > abs_min else 1

    # Normalize each column (to 0-1 range)
    df[f"{ratio_col}_norm"] = (df[ratio_col] - ratio_min) / ratio_range
    df[f"{abs_col}_norm"] = (df[abs_col] - abs_min) / abs_range

    # Calculate composite score
    df["composite_score"] = (df[f"{ratio_col}_norm"] * ratio_weight) + (df[f"{abs_col}_norm"] * abs_weight)

    # Sort by composite score
    return df.sort_values("composite_score", ascending=ascending)

def enhance_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add additional information like stock name, sector to DataFrame
    """
    if not df.empty:
        df = df.copy()  # Explicitly create copy to prevent SettingWithCopyWarning
        name_map = _get_ticker_name_map()
        df["stock_name"] = df.index.map(lambda ticker: _get_display_ticker_name(ticker, name_map))
    return df


# v1.16.6: Agent criteria by trigger type (synchronized with trading_agents.py)
TRIGGER_CRITERIA = {
    "거래량 급증 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "갭 상승 모멘텀 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "일중 상승률 상위주": {"rr_target": 1.2, "sl_max": 0.05},
    "마감 강도 상위주": {"rr_target": 1.3, "sl_max": 0.05},
    "시총 대비 집중 자금 유입 상위주": {"rr_target": 1.3, "sl_max": 0.05},
    "거래량 증가 상위 횡보주": {"rr_target": 1.5, "sl_max": 0.07},
    "매크로 섹터 리더": {"rr_target": 1.3, "sl_max": 0.07},
    "역발상 가치주": {"rr_target": 1.5, "sl_max": 0.08},
    "default": {"rr_target": 1.5, "sl_max": 0.07}
}


def calculate_agent_fit_metrics(ticker: str, current_price: float, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> dict:
    """
    Calculate metrics that fit buy/sell agent criteria.

    v1.16.6: Changed to fixed stop-loss method (15% annual return system)
    - Core change: 10-day support level based → current price based fixed stop-loss
    - Reason: Improved to allow surge stocks to meet agent criteria
    - Risk-reward ratio: Maintain resistance level based, guarantee minimum +15%

    Criteria by trigger type (synchronized with trading_agents.py):
    - Volume surge/Gap up/Intraday rise: Risk-reward 1.2+, Stop-loss 5%
    - Closing strength/Fund inflow: Risk-reward 1.3+, Stop-loss 5%
    - Sideways: Risk-reward 1.5+, Stop-loss 7%

    Args:
        ticker: Stock code
        current_price: Current price
        trade_date: Reference trading date
        lookback_days: Number of past business days to query
        trigger_type: Trigger type (used for differentiated criteria)

    Returns:
        dict with keys: stop_loss_price, target_price, stop_loss_pct, risk_reward_ratio, agent_fit_score
    """
    result = {
        "stop_loss_price": 0,
        "target_price": 0,
        "stop_loss_pct": 1.0,  # Default: unfavorable value
        "risk_reward_ratio": 0,
        "agent_fit_score": 0,
    }

    if current_price <= 0:
        return result

    # v1.16.6: Query criteria by trigger type (query first)
    criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
    sl_max = criteria["sl_max"]
    rr_target = criteria["rr_target"]

    # v1.16.6 Core change: Apply fixed stop-loss method
    # Before: 10-day low based → 48%+ stop-loss on surge stocks → agent rejection
    # After: Current price based fixed ratio → always meets agent criteria
    stop_loss_price = current_price * (1 - sl_max)
    stop_loss_pct = sl_max  # Fixed value (5% or 7%)

    # Target price calculation: Maintain existing resistance level method
    multi_day_df = get_multi_day_ohlcv(ticker, trade_date, lookback_days)
    if multi_day_df.empty or len(multi_day_df) < 3:
        # Default to current price + 15% when data is insufficient
        target_price = current_price * 1.15
        logger.debug(f"{ticker}: Insufficient data, applying default target price ({target_price:.0f})")
    else:
        # Check column name (English/Korean compatibility)
        high_col = "High" if "High" in multi_day_df.columns else "고가"

        if high_col not in multi_day_df.columns:
            target_price = current_price * 1.15
            logger.debug(f"{ticker}: No high column, applying default target price")
        else:
            # Filter out 0 values (market holidays or data errors)
            valid_highs = multi_day_df[high_col][multi_day_df[high_col] > 0]
            if valid_highs.empty:
                target_price = current_price * 1.15
            else:
                # Resistance level (highest among recent N-day highs)
                target_price = valid_highs.max()

    # v1.16.6 Residual risk mitigation: Guarantee minimum +15% target
    min_target = current_price * 1.15
    if target_price <= current_price:
        target_price = min_target
        logger.debug(f"{ticker}: Target price below current price, applying minimum ({target_price:.0f})")
    elif target_price < min_target:
        # Raise to minimum if resistance is below +15%
        logger.debug(f"{ticker}: Target price {target_price:.0f} → raised to minimum {min_target:.0f}")
        target_price = min_target

    # Calculate risk-reward ratio
    potential_gain = target_price - current_price
    potential_loss = current_price - stop_loss_price

    if potential_loss > 0 and potential_gain > 0:
        risk_reward_ratio = potential_gain / potential_loss
    else:
        risk_reward_ratio = 0

    # v1.16.6: Calculate agent fit score (simplified)
    # sl_score = 1.0 since stop-loss is always within criteria
    rr_score = min(risk_reward_ratio / rr_target, 1.0) if risk_reward_ratio > 0 else 0
    sl_score = 1.0  # Always perfect score since stop-loss is fixed

    # Final score (risk-reward 60%, stop-loss 40%)
    agent_fit_score = rr_score * 0.6 + sl_score * 0.4

    result = {
        "stop_loss_price": stop_loss_price,
        "target_price": target_price,
        "stop_loss_pct": stop_loss_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "agent_fit_score": agent_fit_score,
    }

    logger.debug(f"{ticker}: Stop-loss={stop_loss_price:.0f}, Target={target_price:.0f}, "
                 f"Stop-loss%={stop_loss_pct*100:.1f}% (fixed), Risk-reward={risk_reward_ratio:.2f}, "
                 f"Agent score={agent_fit_score:.3f}")

    return result


def score_candidates_by_agent_criteria(candidates_df: pd.DataFrame, trade_date: str, lookback_days: int = 10, trigger_type: str = None) -> pd.DataFrame:
    """
    Calculate agent criteria scores for candidate stocks and add to DataFrame.

    v1.16.6: Apply differentiated criteria by trigger type

    Args:
        candidates_df: Candidate stocks DataFrame (index: stock code, Close column required)
        trade_date: Reference trading date
        lookback_days: Number of past business days to query
        trigger_type: Trigger type (used for differentiated criteria)

    Returns:
        DataFrame with agent criteria scores added
    """
    if candidates_df.empty:
        return candidates_df

    result_df = candidates_df.copy()

    # Initialize agent-related columns
    result_df["stop_loss_price"] = 0.0
    result_df["target_price"] = 0.0
    result_df["stop_loss_pct"] = 0.0
    result_df["risk_reward_ratio"] = 0.0
    result_df["agent_fit_score"] = 0.0

    for ticker in result_df.index:
        current_price = result_df.loc[ticker, "Close"]
        metrics = calculate_agent_fit_metrics(ticker, current_price, trade_date, lookback_days, trigger_type)

        result_df.loc[ticker, "stop_loss_price"] = metrics["stop_loss_price"]
        result_df.loc[ticker, "target_price"] = metrics["target_price"]
        result_df.loc[ticker, "stop_loss_pct"] = metrics["stop_loss_pct"]
        result_df.loc[ticker, "risk_reward_ratio"] = metrics["risk_reward_ratio"]
        result_df.loc[ticker, "agent_fit_score"] = metrics["agent_fit_score"]

    return result_df


# === #289: KR screening signals (O'Neil-style RS + extension), regime-aware blend ===
# Regime-aware weights for final_score: (composite/momentum, agent R/R, RS, extension).
# Each component is 0~1 and weights sum to 1.0 → final_score stays in 0~1.
# Kill switch / rollback: set RS and extension weights to 0 (then re-rank is composite+agent only).
REGIME_SCORE_WEIGHTS = {
    "strong_bull":   (0.20, 0.35, 0.30, 0.15),  # bull: emphasize RS (leaders), lighten extension (but not 0)
    "moderate_bull": (0.25, 0.35, 0.20, 0.20),
    "sideways":      (0.20, 0.35, 0.15, 0.30),  # calm: heavier extension penalty (chasing worst here)
    "moderate_bear": (0.15, 0.35, 0.15, 0.35),
    "strong_bear":   (0.15, 0.35, 0.15, 0.35),
}
_DEFAULT_SCORE_WEIGHTS = REGIME_SCORE_WEIGHTS["sideways"]

# Extension (overheating) thresholds, measured in ADR units above MA20.
EXTENSION_ADR_T_LOW = 2.0    # <= : healthy (near base) → no penalty (score 1.0)
EXTENSION_ADR_T_HIGH = 6.0   # >= : climax / extended → full penalty (score 0.0)

# Sideways trigger downtrend gate (#289 follow-up): a genuine consolidation base
# forms at/above the 20-day mean. A stock drifting BELOW MA20 on a quiet day is a
# downtrend, not a base, and must not be treated as a buyable "sideways" stock.
# Allow a support test within this fraction of MA20 (0.97 = down to -3% of MA20).
SIDEWAYS_MA20_SUPPORT_TOLERANCE = 0.97

# Multi-week relative-strength lookback (trading days, ~3 months).
SCREENING_SIGNAL_LOOKBACK_DAYS = 60
# O'Neil 다개월 RS Rating 히스토리 (252일 + 버퍼, Phase B SHADOW-gate).
RS_RATING_LOOKBACK_DAYS = 260


def _compute_extension_score(extension_in_adr: float) -> float:
    """#289: Map ADR-extension above MA20 to a 0~1 score.

    1.0 = healthy (price near its 20-day mean, i.e. near a base/pivot),
    0.0 = climax / extended (price far above the mean in volatility-normalized terms).
    Linear taper between EXTENSION_ADR_T_LOW and EXTENSION_ADR_T_HIGH.
    """
    if extension_in_adr <= EXTENSION_ADR_T_LOW:
        return 1.0
    if extension_in_adr >= EXTENSION_ADR_T_HIGH:
        return 0.0
    span = EXTENSION_ADR_T_HIGH - EXTENSION_ADR_T_LOW
    return float(max(0.0, min(1.0, 1.0 - (extension_in_adr - EXTENSION_ADR_T_LOW) / span)))


def calculate_screening_signals(ticker: str, current_price: float, trade_date: str,
                                lookback_days: int = SCREENING_SIGNAL_LOOKBACK_DAYS) -> dict:
    """#289: Compute O'Neil-style screening signals from a single multi-week OHLCV fetch.

    Intentionally independent of calculate_agent_fit_metrics so the agent's 10-day
    target-price (resistance) lookback — and therefore agent_fit_score — is NOT perturbed.

    Returns dict:
        - extension_in_adr: (Close - MA20)/MA20 expressed in units of ADR% (overheating proxy)
        - extension_score:  0~1 (1=healthy near base, 0=climax/extended)
        - return_nd:        N-day price return % (raw multi-week relative-strength input;
                            benchmark subtraction cancels under cross-candidate normalization)
    """
    result = {"extension_in_adr": 0.0, "extension_score": 1.0, "return_nd": 0.0, "oneil_raw": None}
    if current_price <= 0:
        return result

    # 최적화(B): 260일 1회 fetch 후 60일 슬라이싱 → fetch 2→1 절감.
    # df260.tail(lookback_days) == 독립 60일 fetch의 tail(60)과 동일한 마지막 행.
    df260 = get_multi_day_ohlcv(ticker, trade_date, RS_RATING_LOOKBACK_DAYS)
    if df260.empty:
        return result

    # O'Neil 다개월 RS Rating (Phase B SHADOW-gate): 260일 전체 종가 사용.
    c260 = "Close" if "Close" in df260.columns else "종가"
    if c260 in df260.columns:
        cl260 = df260[c260][df260[c260] > 0]
        result["oneil_raw"] = oneil_weighted_return(cl260)

    # 60일 구간 슬라이싱 (return_nd / MA20 / ADR extension 계산용).
    df = df260.tail(lookback_days)
    if len(df) < 5:
        return result

    high_col = "High" if "High" in df.columns else "고가"
    low_col = "Low" if "Low" in df.columns else "저가"
    close_col = "Close" if "Close" in df.columns else "종가"
    if close_col not in df.columns:
        return result

    closes = df[close_col][df[close_col] > 0]
    if closes.empty:
        return result

    # Multi-week return (relative-strength input).
    first_close = float(closes.iloc[0])
    if first_close > 0:
        result["return_nd"] = (current_price - first_close) / first_close * 100

    # MA20 + ADR(20d) extension.
    recent_closes = closes.tail(20)
    ma20 = float(recent_closes.mean()) if len(recent_closes) > 0 else 0.0
    if ma20 > 0 and high_col in df.columns and low_col in df.columns:
        hl = df.tail(20)
        valid = hl[(hl[high_col] > 0) & (hl[low_col] > 0)]
        if not valid.empty:
            adr_pct = float(((valid[high_col] / valid[low_col] - 1.0) * 100).mean())
            if adr_pct > 0:
                ext = ((current_price - ma20) / ma20 * 100) / adr_pct
                result["extension_in_adr"] = float(ext)
                result["extension_score"] = _compute_extension_score(ext)

    return result


def _compute_ma20(ticker: str, trade_date: str, lookback_days: int = 20) -> float:
    """#289 follow-up: 20-day moving average of close, for the sideways-trigger
    downtrend gate. Returns 0.0 when data is unavailable (the gate treats 0 as
    'unknown → pass' so a data blip never silently drops candidates)."""
    df = get_multi_day_ohlcv(ticker, trade_date, lookback_days)
    if df.empty:
        return 0.0
    close_col = "Close" if "Close" in df.columns else "종가"
    if close_col not in df.columns:
        return 0.0
    closes = df[close_col][df[close_col] > 0].tail(20)
    return float(closes.mean()) if len(closes) > 0 else 0.0


# --- Morning trigger functions (based on market open snapshot) ---
def trigger_morning_volume_surge(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 1] Top stocks with intraday volume surge
    - Absolute criteria: Minimum trade value 500M KRW + at least 20% of market average volume
    - Additional filter: Volume increase of 30% or more
    - Composite score: Volume increase rate (60%) + Absolute volume (40%)
    - Secondary filtering: Select only rising stocks (current price > opening price)
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_morning_volume_surge started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Debug information
    logger.debug(f"Previous day close data sample: {prev['Close'].head()}")
    logger.debug(f"Current day close data sample: {snap['Close'].head()}")

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    # Calculate volume ratio
    snap["volume_ratio"] = snap["Volume"] / prev["Volume"].replace(0, np.nan)
    # Calculate volume increase rate (percentage)
    snap["volume_increase_rate"] = (snap["volume_ratio"] - 1) * 100

    # Calculate two types of change rates
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Current vs opening price

    # Calculate change rate vs previous day - modified method
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100

    # #289: Change rate upper limit unified to 15% (was 20%) — align secondary triggers with primary
    snap = snap[snap["prev_day_change_rate"] <= 15.0]

    # Debug calculation process for first 5 stocks' change rate vs previous day
    for ticker in snap.index[:5]:
        try:
            today_close = snap.loc[ticker, "Close"]
            yesterday_close = prev.loc[ticker, "Close"]
            change_rate = ((today_close - yesterday_close) / yesterday_close) * 100
            logger.debug(f"Stock {ticker} - Today close: {today_close}, Yesterday close: {yesterday_close}, Change rate: {change_rate:.2f}%")
        except Exception as e:
            logger.debug(f"Error during debugging: {e}")

    snap["is_rising"] = snap["Close"] > snap["Open"]

    # Filter for volume increase rate 30% or more
    snap = snap[snap["volume_increase_rate"] >= 30.0]

    if snap.empty:
        logger.debug("trigger_morning_volume_surge: No stocks with volume increase")
        return pd.DataFrame()

    # Primary filtering: Select top stocks by composite score
    scored = normalize_and_score(snap, "volume_increase_rate", "Volume", 0.6, 0.4)
    candidates = scored.head(top_n)

    # Secondary filtering: Select only rising stocks
    result = candidates[candidates["is_rising"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_volume_surge: No stocks meeting criteria")
        return pd.DataFrame()

    logger.debug(f"Volume surge stocks detected: {len(result)}")
    return enhance_dataframe(result.sort_values("composite_score", ascending=False).head(10))

def trigger_morning_gap_up_momentum(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [Morning Trigger 2] Top gap-up momentum stocks
    - Absolute criteria: Minimum trade value 500M KRW or more
    - Composite score: Gap rate (50%) + Intraday rise (30%) + Trade value (20%)
    - Secondary filtering: Select only stocks with current price > opening price (sustained rise)
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_morning_gap_up_momentum started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    # Calculate gap rate
    snap["gap_up_rate"] = (snap["Open"] / prev["Close"] - 1) * 100
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Intraday change rate vs opening
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100  # Change rate vs previous close
    snap["sustained_rise"] = snap["Close"] > snap["Open"]

    # Primary filtering: Gap rate 1% or more, change rate 20% or less (v1.16.6: surge stocks can enter)
    snap = snap[(snap["gap_up_rate"] >= 1.0) & (snap["prev_day_change_rate"] <= 15.0)]

    # Score calculation (custom composite score)
    if not snap.empty:
        # Normalize each indicator
        for col in ["gap_up_rate", "intraday_change_rate", "Amount"]:
            col_max = snap[col].max()
            col_min = snap[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            snap[f"{col}_norm"] = (snap[col] - col_min) / col_range

        # Calculate composite score (apply weights)
        snap["composite_score"] = (
                snap["gap_up_rate_norm"] * 0.5 +
                snap["intraday_change_rate_norm"] * 0.3 +
                snap["Amount_norm"] * 0.2
        )

        # Select top stocks by score
        candidates = snap.sort_values("composite_score", ascending=False).head(top_n)
    else:
        candidates = snap

    # Secondary filtering: Select only stocks with sustained rise
    result = candidates[candidates["sustained_rise"] == True].copy()

    if result.empty:
        logger.debug("trigger_morning_gap_up_momentum: No stocks meeting criteria")
        return pd.DataFrame()

    # Calculate additional information
    result["total_momentum"] = result["gap_up_rate"] + result["intraday_change_rate"]

    logger.debug(f"Gap-up momentum stocks detected: {len(result)}")
    return enhance_dataframe(result.sort_values("composite_score", ascending=False).head(10))


def trigger_morning_value_to_cap_ratio(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """
    [Morning Trigger 3] Top stocks with concentrated fund inflow vs market cap
    - Absolute criteria: Minimum trade value 500M KRW or more
    - Composite score: Trade value ratio (50%) + Absolute trade value (30%) + Intraday change (20%)
    - Secondary filtering: Select only rising stocks (current price > opening price)
    """
    logger.info("Starting analysis of top stocks with concentrated fund inflow vs market cap")

    # Defense code 1: Input data validation
    if snapshot.empty:
        logger.error("snapshot data is empty")
        return pd.DataFrame()

    if prev_snapshot.empty:
        logger.error("prev_snapshot data is empty")
        return pd.DataFrame()

    if cap_df.empty:
        logger.error("cap_df data is empty")
        return pd.DataFrame()

    # Defense code 2: Check market cap column exists
    if '시가총액' not in cap_df.columns:
        logger.error(f"'market cap' column not found in cap_df. Actual columns: {list(cap_df.columns)}")
        return pd.DataFrame()

    logger.info(f"Input data validation complete - snapshot: {len(snapshot)} items, cap_df: {len(cap_df)} items")

    try:
        # Merge market cap and OHLCV data
        logger.debug("Starting market cap data merge")
        merged = snapshot.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner").copy()
        logger.info(f"Data merge complete: {len(merged)} stocks")

        # Defense code 3: Recheck market cap column after merge
        if '시가총액' not in merged.columns:
            logger.error(f"'market cap' column not found after merge. Post-merge columns: {list(merged.columns)}")
            return pd.DataFrame()

        # Merge with previous trading day data
        common = merged.index.intersection(prev_snapshot.index)
        if len(common) == 0:
            logger.error("No common stocks")
            return pd.DataFrame()

        if len(common) < 50:
            logger.warning(f"Low number of common stocks ({len(common)}). Result quality may be poor")

        merged = merged.loc[common].copy()
        prev = prev_snapshot.loc[common].copy()
        logger.debug(f"Previous day data merge complete - Common stocks: {len(common)}")

        # Apply absolute criteria (raised to 10B KRW trade value)
        logger.debug("Starting absolute criteria filtering")
        merged = apply_absolute_filters(merged, min_value=SCREENING_MIN_TRADE_VALUE)
        if merged.empty:
            logger.warning("No stocks after absolute criteria filtering")
            return pd.DataFrame()

        logger.info(f"Filtering complete: {len(merged)} stocks")

        # Defense code 4: Recheck required columns
        required_columns = ['Amount', '시가총액', 'Close', 'Open']
        missing_columns = [col for col in required_columns if col not in merged.columns]
        if missing_columns:
            logger.error(f"Missing required columns: {missing_columns}")
            return pd.DataFrame()

        # Calculate trade value / market cap ratio
        logger.debug("Starting trade value ratio calculation")
        merged["trade_value_ratio"] = (merged["Amount"] / merged["시가총액"]) * 100

        # Calculate two types of change rates
        merged["intraday_change_rate"] = (merged["Close"] / merged["Open"] - 1) * 100  # Current vs opening price
        merged["prev_day_change_rate"] = ((merged["Close"] - prev["Close"]) / prev["Close"]) * 100  # Same as brokerage app
        merged["is_rising"] = merged["Close"] > merged["Open"]

        # #289: Change rate upper limit unified to 15% (was 20%) — align secondary triggers with primary
        merged = merged[merged["prev_day_change_rate"] <= 15.0]
        if merged.empty:
            logger.warning("No stocks after change rate upper limit filtering")
            return pd.DataFrame()

        # Market cap filtering - minimum 500B KRW (v1.16.6: expanded opportunity pool)
        merged = merged[merged["시가총액"] >= 500000000000]
        if merged.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

        logger.debug(f"Market cap filtering complete - Remaining stocks: {len(merged)}")

        # Calculate composite score
        if not merged.empty:
            # Normalize each indicator
            for col in ["trade_value_ratio", "Amount", "intraday_change_rate"]:
                col_max = merged[col].max()
                col_min = merged[col].min()
                col_range = col_max - col_min if col_max > col_min else 1
                merged[f"{col}_norm"] = (merged[col] - col_min) / col_range

            # Calculate composite score
            merged["composite_score"] = (
                    merged["trade_value_ratio_norm"] * 0.5 +
                    merged["Amount_norm"] * 0.3 +
                    merged["intraday_change_rate_norm"] * 0.2
            )

            # Select top stocks
            candidates = merged.sort_values("composite_score", ascending=False).head(top_n)
        else:
            candidates = merged

        # Secondary filtering: Select only rising stocks
        result = candidates[candidates["is_rising"] == True].copy()

        if result.empty:
            logger.info("No stocks meeting criteria")
            return pd.DataFrame()

        logger.info(f"Analysis complete: {len(result)} stocks selected")
        return enhance_dataframe(result.sort_values("composite_score", ascending=False).head(10))

    except Exception as e:
        logger.error(f"Exception occurred during function execution: {e}")
        import traceback
        logger.debug(f"Detailed error:\n{traceback.format_exc()}")
        return pd.DataFrame()

# --- Afternoon trigger functions (based on market close snapshot) ---
def trigger_afternoon_daily_rise_top(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [Afternoon Trigger 1] Top intraday rise stocks
    - Absolute criteria: Minimum trade value 1B KRW or more
    - Composite score: Intraday rise (60%) + Trade value (40%)
    - Additional filter: Change rate 3% or more
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_afternoon_daily_rise_top started")

    # Connect previous trading day data
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Keep the original >=10B KRW path intact, but admit one separately-ranked
    # 5B~10B candidate. A global threshold reduction does not work because
    # absolute Amount normalization pushes this early-liquidity cohort out of
    # the shared top-N before the hybrid selector can evaluate it.
    snap = apply_absolute_filters(
        snap.copy(), min_value=EMERGING_LIQUIDITY_MIN_TRADE_VALUE
    )

    # Calculate two types of change rates
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Current vs opening price
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100  # Same as brokerage app
    snap["is_rising"] = snap["Close"] > snap["Open"]

    # Change rate filter: 3% or more, 20% or less (v1.16.6: surge stocks can enter)
    # Bullish candle required — prev_day_change_rate alone measures the move from
    # yesterday's close, so a stock that gapped up and then sold off all day still
    # clears +3% while printing a bearish candle. Mirrors us_trigger_batch.py.
    snap = snap[(snap["prev_day_change_rate"] >= 3.0)
                & (snap["prev_day_change_rate"] <= 15.0)
                & snap["is_rising"]]

    if snap.empty:
        logger.debug("trigger_afternoon_daily_rise_top: No stocks meeting criteria")
        return pd.DataFrame()

    standard = snap[snap["Amount"] >= SCREENING_MIN_TRADE_VALUE].copy()
    emerging = snap[
        (snap["Amount"] >= EMERGING_LIQUIDITY_MIN_TRADE_VALUE)
        & (snap["Amount"] < SCREENING_MIN_TRADE_VALUE)
    ].copy()
    emerging_eligible = len(emerging)

    if not standard.empty:
        standard = normalize_and_score(
            standard, "intraday_change_rate", "Amount", 0.6, 0.4
        ).head(top_n).head(10).copy()
        standard["liquidity_lane"] = "standard"
        standard["liquidity_lane_rank"] = range(1, len(standard) + 1)

    if not emerging.empty:
        emerging = normalize_and_score(
            emerging, "intraday_change_rate", "Amount", 0.6, 0.4
        ).head(EMERGING_LIQUIDITY_MAX_CANDIDATES).copy()
        # Scores are normalized inside each lane. With one surviving row the
        # generic normalizer yields zero; rank one must remain comparable with
        # the standard lane's rank-one candidate in the downstream blend.
        emerging["composite_score"] = 1.0
        emerging["liquidity_lane"] = "emerging"
        emerging["liquidity_lane_rank"] = range(1, len(emerging) + 1)

    result = pd.concat([standard, emerging])
    if result.empty:
        logger.debug("trigger_afternoon_daily_rise_top: No stocks in either liquidity lane")
        return pd.DataFrame()

    result["liquidity_floor"] = result["liquidity_lane"].map(
        {
            "standard": SCREENING_MIN_TRADE_VALUE,
            "emerging": EMERGING_LIQUIDITY_MIN_TRADE_VALUE,
        }
    )
    result["liquidity_ceiling"] = result["liquidity_lane"].map(
        {"standard": None, "emerging": SCREENING_MIN_TRADE_VALUE}
    )
    logger.info(
        "[LIQUIDITY-LANE] market=KR trigger=afternoon_daily_rise "
        "standard=%d emerging_eligible=%d emerging_added=%d",
        len(standard),
        emerging_eligible,
        len(emerging),
    )
    return enhance_dataframe(result)

def trigger_afternoon_closing_strength(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 15) -> pd.DataFrame:
    """
    [Afternoon Trigger 2] Top closing strength stocks
    - Absolute criteria: Minimum trade value 500M KRW + volume increase vs previous day
    - Composite score: Closing strength (50%) + Volume increase rate (30%) + Trade value (20%)
    - Secondary filtering: Select only rising stocks that recovered the previous close
      (close > open and close >= previous close)
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_afternoon_closing_strength started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    # Calculate closing strength (closer to high = closer to 1)
    snap["closing_strength"] = 0.0  # Set default value
    valid_range = (snap["High"] != snap["Low"])  # Prevent division by zero
    snap.loc[valid_range, "closing_strength"] = (snap.loc[valid_range, "Close"] - snap.loc[valid_range, "Low"]) / (snap.loc[valid_range, "High"] - snap.loc[valid_range, "Low"])

    # Calculate volume increase
    snap["volume_increase_rate"] = (snap["Volume"] / prev["Volume"].replace(0, np.nan) - 1) * 100

    # Calculate two types of change rates
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Current vs opening price
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100  # Same as brokerage app

    # #289: Change rate upper limit unified to 15% (was 20%) — exclude limit-up / align with primary
    snap = snap[snap["prev_day_change_rate"] <= 15.0]
    if snap.empty:
        logger.debug("trigger_afternoon_closing_strength: No stocks after change rate filtering")
        return pd.DataFrame()

    snap["volume_increased"] = (snap["Volume"] - prev["Volume"].replace(0, np.nan)) > 0
    snap["is_rising"] = snap["Close"] > snap["Open"]

    # A gap-down recovery can look like a strong bullish intraday candle while
    # still closing below the previous session.  That is not closing strength
    # in the swing-trend sense used by this trigger.  Keep this rule local to
    # closing-strength; reversal and capital-inflow triggers intentionally have
    # different contracts.
    downside_recovery = (
        snap["volume_increased"]
        & snap["is_rising"]
        & (snap["prev_day_change_rate"] < 0.0)
    )
    rejected_count = int(downside_recovery.sum())
    if rejected_count:
        rejected_sample = ",".join(
            str(ticker) for ticker in snap.index[downside_recovery][:20]
        )
        logger.info(
            "[SCREENING-FILTER] market=KR trigger=afternoon_closing_strength "
            "trade_date=%s reason=close_below_previous_close rejected=%d sample=%s",
            trade_date,
            rejected_count,
            rejected_sample,
        )
    snap = snap[snap["prev_day_change_rate"] >= 0.0]
    if snap.empty:
        logger.debug(
            "trigger_afternoon_closing_strength: No stocks after previous-close recovery filtering"
        )
        return pd.DataFrame()

    # Primary filtering: Select only stocks with volume increase
    candidates = snap[snap["volume_increased"] == True].copy()

    # Calculate composite score
    if not candidates.empty:
        # Normalize each indicator
        for col in ["closing_strength", "volume_increase_rate", "Amount"]:
            col_max = candidates[col].max()
            col_min = candidates[col].min()
            col_range = col_max - col_min if col_max > col_min else 1
            candidates[f"{col}_norm"] = (candidates[col] - col_min) / col_range

        # Calculate composite score
        candidates["composite_score"] = (
                candidates["closing_strength_norm"] * 0.5 +
                candidates["volume_increase_rate_norm"] * 0.3 +
                candidates["Amount_norm"] * 0.2
        )

        # Select top stocks by score
        candidates = candidates.sort_values("composite_score", ascending=False).head(top_n)

    # Secondary filtering: Select only rising stocks
    result = candidates[candidates["is_rising"] == True].copy()

    if result.empty:
        logger.debug("trigger_afternoon_closing_strength: No stocks meeting criteria")
        return pd.DataFrame()

    logger.debug(f"Closing strength top stocks detected: {len(result)}")
    return enhance_dataframe(result.sort_values("composite_score", ascending=False).head(10))

def trigger_afternoon_volume_surge_flat(trade_date: str, snapshot: pd.DataFrame, prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None, top_n: int = 20) -> pd.DataFrame:
    """
    [Afternoon Trigger 3] Top volume increase sideways stocks
    - Absolute criteria: Minimum trade value 500M KRW + volume vs market average
    - Composite score: Volume increase rate (60%) + Trade value (40%)
    - Secondary filtering: Select only sideways stocks with change rate within ±5%
    - Penny stock filter: Market cap 50B KRW or more
    """
    logger.debug("trigger_afternoon_volume_surge_flat started")
    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Merge and filter market cap data (v1.16.6: adjusted to 500B or more)
    if cap_df is not None and not cap_df.empty:
        snap = snap.merge(cap_df[["시가총액"]], left_index=True, right_index=True, how="inner")
        # Select stocks with market cap 500B KRW or more (v1.16.6: expanded opportunity pool, 518 stocks)
        snap = snap[snap["시가총액"] >= 500000000000]
        logger.debug(f"Stock count after market cap filtering: {len(snap)}")
        if snap.empty:
            logger.warning("No stocks after market cap filtering")
            return pd.DataFrame()

    # Apply absolute criteria (raised to 10B KRW trade value)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    # Calculate volume increase rate
    snap["volume_increase_rate"] = (snap["Volume"] / prev["Volume"].replace(0, np.nan) - 1) * 100

    # Calculate two types of change rates
    snap["intraday_change_rate"] = (snap["Close"] / snap["Open"] - 1) * 100  # Current vs opening price
    snap["prev_day_change_rate"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100  # Same as brokerage app

    # Determine sideways stocks (change rate within ±5%) - v1.16.6: Changed to previous day change rate basis
    snap["is_sideways"] = (snap["prev_day_change_rate"].abs() <= 5)
    snap["is_rising"] = snap["Close"] > snap["Open"]

    # Additional filter: Only stocks with 50% or more volume increase vs previous day
    snap = snap[snap["volume_increase_rate"] >= 50]

    if snap.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: No stocks meeting criteria")
        return pd.DataFrame()

    # Calculate composite score
    scored = normalize_and_score(snap, "volume_increase_rate", "Amount", 0.6, 0.4)

    # Primary filtering: Top stocks by composite score
    candidates = scored.head(top_n)

    # Secondary filtering: Select only sideways stocks, and only on a bullish candle.
    # A volume surge that closes below its own open is distribution, not the
    # accumulation base this trigger is meant to find — is_sideways alone (|move| <= 5%)
    # cannot tell the two apart.
    result = candidates[candidates["is_sideways"] & candidates["is_rising"]].copy()

    if result.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: No stocks meeting criteria")
        return pd.DataFrame()

    # #289 follow-up: downtrend gate. is_sideways only checks |today's move| <= 5%,
    # which mislabels a downtrending stock (below MA20, weak RS) as "sideways"
    # (e.g. 이노션 2026-05-29: -2.2%, below MA20). A real consolidation base sits
    # at/above the 20-day mean — exclude names clearly below MA20.
    kept_mask = []
    for ticker in result.index:
        ma20 = _compute_ma20(ticker, trade_date)
        close_price = float(result.loc[ticker, "Close"])
        # ma20 <= 0 means data unavailable → keep (don't drop on a data blip)
        kept_mask.append(ma20 <= 0 or close_price >= ma20 * SIDEWAYS_MA20_SUPPORT_TOLERANCE)
    excluded = len(result) - sum(kept_mask)
    result = result[pd.Series(kept_mask, index=result.index)].copy()
    if excluded:
        logger.debug(f"trigger_afternoon_volume_surge_flat: downtrend gate excluded "
                     f"{excluded} stock(s) below MA20")

    if result.empty:
        logger.debug("trigger_afternoon_volume_surge_flat: No stocks after downtrend gate")
        return pd.DataFrame()

    # Add debugging logs
    for ticker in result.index[:3]:
        logger.debug(f"Sideways stock debug - {ticker}: Volume increase {result.loc[ticker, 'volume_increase_rate']:.2f}%, "
                     f"Intraday change {result.loc[ticker, 'intraday_change_rate']:.2f}%, Previous day change {result.loc[ticker, 'prev_day_change_rate']:.2f}%, "
                     f"Volume {result.loc[ticker, 'Volume']:,} shares, Previous volume {prev.loc[ticker, 'Volume']:,} shares")

    logger.debug(f"Volume increase sideways stocks detected: {len(result)}")
    return enhance_dataframe(result.sort_values("composite_score", ascending=False).head(10))

def trigger_macro_sector_leader(trade_date: str, snapshot: pd.DataFrame,
                                 prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                                 macro_context: dict = None, top_n: int = 10) -> pd.DataFrame:
    """
    [New Trigger] Macro Sector Leader
    - Identifies stocks in macro-leading sectors with relative strength
    - Composite score: Relative strength (30%) + Trading amount (20%) + Sector confidence (30%) + Market cap proxy (20%)
    - Requires macro_context with leading_sectors and sector_map
    """
    logger.debug("trigger_macro_sector_leader started")

    if macro_context is None:
        logger.debug("trigger_macro_sector_leader: No macro_context provided")
        return pd.DataFrame()

    leading_sectors = macro_context.get("leading_sectors", [])
    if not leading_sectors:
        logger.debug("trigger_macro_sector_leader: No leading sectors in macro_context")
        return pd.DataFrame()

    # KR: sector_map is already available in macro_context (no external API needed)
    sector_map = macro_context.get("sector_map", {})

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Absolute filters (100억원)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    if snap.empty:
        logger.debug("trigger_macro_sector_leader: No stocks pass absolute filters")
        return pd.DataFrame()

    # Limit to top 100 by Amount
    top100 = snap.nlargest(100, "Amount")

    # Build sector confidence lookup and leading sector names
    sector_confidence = {}
    leading_names = set()
    for s in leading_sectors:
        name = s.get("sector", "")
        conf = s.get("confidence", 0.5)
        sector_confidence[name] = conf
        leading_names.add(name)

    # Filter stocks whose sector matches any leading sector (fuzzy substring match)
    matched_rows = []
    matched_confs = []
    for ticker in top100.index:
        stock_sector = sector_map.get(ticker, "")
        if not stock_sector:
            continue
        matched_sector = None
        if stock_sector in leading_names:
            matched_sector = stock_sector
        else:
            for l in leading_names:
                if stock_sector in l or l in stock_sector:
                    matched_sector = l
                    break
        if matched_sector:
            matched_rows.append(ticker)
            matched_confs.append(sector_confidence.get(matched_sector, 0.5))

    if not matched_rows:
        logger.debug("trigger_macro_sector_leader: No stocks matched leading sectors")
        return pd.DataFrame()

    snap_filtered = top100.loc[matched_rows].copy()
    snap_filtered["SectorConfidence"] = matched_confs

    # Calculate daily change for relative strength
    snap_filtered["DailyChange"] = ((snap_filtered["Close"] - prev.loc[matched_rows, "Close"]) /
                                     prev.loc[matched_rows, "Close"]) * 100
    # Expose as prev_day_change_rate so the downstream dispatch (which only reads
    # prev_day_change_rate) reports the real change rate instead of 0.
    snap_filtered["prev_day_change_rate"] = snap_filtered["DailyChange"]

    # Bullish candle required (Close > Open) — same rule the other triggers already apply.
    # A "sector leader" that closes below its own open is not showing leadership today;
    # relative strength alone can stay positive in a falling market.
    snap_filtered = snap_filtered[snap_filtered["Close"] > snap_filtered["Open"]]
    if snap_filtered.empty:
        logger.debug("trigger_macro_sector_leader: No leaders with a bullish candle")
        return pd.DataFrame()

    # Market average change for relative strength calculation
    market_avg_change = (
        ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100
    ).mean()
    snap_filtered["RelativeStrength"] = snap_filtered["DailyChange"] - market_avg_change

    # Normalize each component
    def _norm_col(series: pd.Series) -> pd.Series:
        col_min = series.min()
        col_max = series.max()
        col_range = col_max - col_min if col_max > col_min else 1
        return (series - col_min) / col_range

    snap_filtered["RelativeStrength_norm"] = _norm_col(snap_filtered["RelativeStrength"])
    snap_filtered["Amount_norm"] = _norm_col(snap_filtered["Amount"])
    snap_filtered["SectorConfidence_norm"] = _norm_col(snap_filtered["SectorConfidence"])

    # Market cap proxy: use cap_df if available, otherwise Amount
    if cap_df is not None and not cap_df.empty and "시가총액" in cap_df.columns:
        snap_filtered = snap_filtered.merge(cap_df[["시가총액"]], left_index=True,
                                             right_index=True, how="left")
        snap_filtered["시가총액"] = snap_filtered["시가총액"].fillna(snap_filtered["Amount"])
        snap_filtered["MarketCap_norm"] = _norm_col(snap_filtered["시가총액"])
    else:
        snap_filtered["MarketCap_norm"] = snap_filtered["Amount_norm"]

    snap_filtered["CompositeScore"] = (
        snap_filtered["RelativeStrength_norm"] * 0.3 +
        snap_filtered["Amount_norm"] * 0.2 +
        snap_filtered["SectorConfidence_norm"] * 0.3 +
        snap_filtered["MarketCap_norm"] * 0.2
    )

    result = snap_filtered.sort_values("CompositeScore", ascending=False).head(top_n)

    logger.debug(f"Macro sector leader detected: {len(result)} stocks")
    return enhance_dataframe(result)


def trigger_contrarian_value(trade_date: str, snapshot: pd.DataFrame,
                              prev_snapshot: pd.DataFrame, cap_df: pd.DataFrame = None,
                              top_n: int = 10) -> pd.DataFrame:
    """
    [New Trigger] Contrarian Value Pick (KR)
    - Identifies quality stocks in a deep drawdown (15%-40% below 52-week high)
    - Requires positive recovery signal today (Close > Open)
    - Scores on drawdown magnitude, liquidity, low P/B ratio, and daily recovery
    - Uses krx_data_client for 52-week high and fundamental data
    """
    from krx_data_client import get_market_ohlcv_by_date, get_market_fundamental_by_date

    logger.debug("trigger_contrarian_value started")

    common = snapshot.index.intersection(prev_snapshot.index)
    snap = snapshot.loc[common].copy()
    prev = prev_snapshot.loc[common].copy()

    # Absolute filters (100억원)
    snap = apply_absolute_filters(snap, min_value=SCREENING_MIN_TRADE_VALUE)

    # Filter rising stocks today (Close > Open) — positive recovery signal
    snap["DailyChange"] = ((snap["Close"] - prev["Close"]) / prev["Close"]) * 100
    snap = snap[snap["Close"] > snap["Open"]]

    if snap.empty:
        logger.debug("trigger_contrarian_value: No rising stocks after absolute filter")
        return pd.DataFrame()

    # Limit to top 50 by Amount to reduce data fetch calls
    candidates = snap.nlargest(50, "Amount").copy()

    # Calculate date range for 52-week high lookup
    trade_dt = datetime.datetime.strptime(trade_date, '%Y%m%d')
    start_dt = trade_dt - datetime.timedelta(days=365)
    start_date_str = start_dt.strftime('%Y%m%d')

    # Fetch 52-week high and fundamentals for each candidate
    rows = []
    for i, ticker in enumerate(candidates.index):
        logger.debug(f"trigger_contrarian_value: fetching data for {ticker} ({i+1}/{len(candidates)})")
        try:
            hist = get_market_ohlcv_by_date(start_date_str, trade_date, ticker)
            if hist.empty:
                continue
            high_52w = float(hist["High"].max())
            current_price = float(candidates.loc[ticker, "Close"])
            if high_52w <= 0:
                continue
            drawdown = (current_price - high_52w) / high_52w * 100

            # Filter: drawdown between -15% and -40%
            if not (-40.0 <= drawdown <= -15.0):
                continue

            fund_df = get_market_fundamental_by_date(start_date_str, trade_date, ticker)
            if fund_df.empty:
                continue

            # Get the latest available row
            latest = fund_df.iloc[-1]
            per = float(latest.get("PER", 0) or 0)
            pbr = float(latest.get("PBR", 0) or 0)

            # Must be profitable (PER > 0) and have valid PBR
            if per <= 0:
                continue
            if pbr <= 0:
                continue

            rows.append({
                "Ticker": ticker,
                "Close": current_price,
                "Volume": candidates.loc[ticker, "Volume"],
                "Amount": candidates.loc[ticker, "Amount"],
                "DailyChange": candidates.loc[ticker, "DailyChange"],
                "Drawdown": drawdown,
                "PriceToBook": pbr,
                "TrailingPE": per,
            })
        except Exception as e:
            logger.debug(f"trigger_contrarian_value: skipping {ticker} due to error: {e}")
            continue

    if not rows:
        logger.debug("trigger_contrarian_value: No qualifying stocks after fundamentals filter")
        return pd.DataFrame()

    result_df = pd.DataFrame(rows).set_index("Ticker")
    # Same fix as trigger_macro_sector_leader: downstream dispatch reads only
    # prev_day_change_rate, so mirror DailyChange into it (was reported as 0).
    result_df["prev_day_change_rate"] = result_df["DailyChange"]

    def _norm_col(series: pd.Series) -> pd.Series:
        col_min = series.min()
        col_max = series.max()
        col_range = col_max - col_min if col_max > col_min else 1
        return (series - col_min) / col_range

    # Drawdown magnitude: deeper = higher score (negate because drawdown is negative)
    result_df["Drawdown_norm"] = _norm_col(-result_df["Drawdown"])
    # Liquidity
    result_df["Amount_norm"] = _norm_col(result_df["Amount"])
    # Low PBR: lower = better value (invert)
    result_df["PB_norm"] = 1.0 - _norm_col(result_df["PriceToBook"])
    # Recovery signal: daily change > 0, normalized
    result_df["Recovery_norm"] = _norm_col(result_df["DailyChange"].clip(lower=0))

    result_df["CompositeScore"] = (
        result_df["Drawdown_norm"] * 0.3 +
        result_df["Amount_norm"] * 0.2 +
        result_df["PB_norm"] * 0.3 +
        result_df["Recovery_norm"] * 0.2
    )

    result = result_df.sort_values("CompositeScore", ascending=False).head(top_n)

    logger.debug(f"Contrarian value pick detected: {len(result)} stocks")
    return enhance_dataframe(result)


def _get_regime_slots(market_regime: str) -> tuple:
    """Return (topdown_slots, bottomup_slots) based on market regime."""
    REGIME_SLOTS = {
        "strong_bull": (2, 1),
        "moderate_bull": (1, 2),
        "sideways": (1, 2),
        "moderate_bear": (1, 2),
        "strong_bear": (0, 3),
    }
    td, bu = REGIME_SLOTS.get(market_regime, (1, 2))  # default: sideways ratios
    # Post-FTD 정찰(파일럿) 재진입: PULSE_PILOT_REEXPOSURE ON + 해당 시장 파일럿 윈도우면
    # 배치당 신규 진입 슬롯을 총 1개로 캡한다. post-FTD 정찰 진입은 주도주(top-down) 우선,
    # 정상 금액 1종목만. 금액은 절대 건드리지 않는다(sim/real parity). 신규 진입 수만 조인다.
    # fail-open: 파일럿 판정 중 예외 발생 시 원래 슬롯 유지(기존 약세장 브랜치 스타일).
    try:
        from cores.regime_policy import pilot_reexposure_active
        if (td + bu) > 1 and pilot_reexposure_active("kr"):
            capped = (1, 0) if td >= 1 else (0, min(bu, 1))
            logger.info("[PULSE_PILOT] 파일럿 신규진입 캡: %s (%d,%d)->(%d,%d)",
                        market_regime, td, bu, capped[0], capped[1])
            return capped
    except Exception as _pe:
        logger.debug("[PULSE_PILOT] slot-cap fail-open: %s", _pe)
    # 약세·횡보장 모멘텀추격(top-down) 억제 옵션 (env-gated, 기본 off = 현행 유지).
    # ON 시 sideways/moderate_bear의 top-down 슬롯을 0으로 → 급락 휩쏘장에서 momentum chase
    # 매수를 접고 가치형 bottom-up만 남긴다(총 슬롯도 감소 = 매수 절제). strong_bear는 이미 (0,3).
    if (td > 0 and market_regime in ("sideways", "moderate_bear")
            and os.getenv("REGIME_WEAK_NO_TOPDOWN", "false").strip().lower()
            in ("1", "true", "yes", "on")):
        logger.info("[REGIME_SLOTS] weak-regime top-down 억제: %s (%d,%d)->(0,%d)",
                    market_regime, td, bu, bu)
        return (0, bu)
    return (td, bu)


def _get_regime_selection_plan(market_regime: str) -> tuple[int, int, int]:
    """Return top-down, bottom-up, and their hard total selection limit."""
    topdown_slots, bottomup_slots = _get_regime_slots(market_regime)
    return (
        topdown_slots,
        bottomup_slots,
        max(0, int(topdown_slots) + int(bottomup_slots)),
    )


def _build_topdown_pool(trigger_candidates: dict, macro_context: dict, score_column: str) -> list:
    """Build top-down candidate pool from leading sectors.

    Returns list of (ticker, trigger_name, topdown_score, ticker_df) sorted by topdown_score desc.
    """
    if not macro_context:
        return []

    leading_sectors = macro_context.get("leading_sectors", [])
    if not leading_sectors:
        return []

    sector_map = macro_context.get("sector_map", {})
    if not sector_map:
        return []

    # Build confidence lookup: sector_name -> confidence
    sector_confidence = {}
    leading_names = set()
    for s in leading_sectors:
        name = s.get("sector", "")
        conf = s.get("confidence", 0.5)
        sector_confidence[name] = conf
        leading_names.add(name)

    pool = []
    for trigger_name, df in trigger_candidates.items():
        if df.empty or score_column not in df.columns:
            continue
        for ticker in df.index:
            stock_sector = sector_map.get(ticker, "")
            if not stock_sector:
                continue
            # Exact match first, then fuzzy substring match
            matched_sector = None
            if stock_sector in leading_names:
                matched_sector = stock_sector
            else:
                for l in leading_names:
                    if stock_sector in l or l in stock_sector:
                        matched_sector = l
                        break
            if matched_sector:
                base_score = df.loc[ticker, score_column]
                confidence = sector_confidence.get(matched_sector, 0.5)
                topdown_score = base_score * (1 + confidence * 0.3)
                pool.append((ticker, trigger_name, topdown_score, df.loc[[ticker]]))

    # Sort by topdown_score descending
    pool.sort(key=lambda x: x[2], reverse=True)
    return pool


# --- Comprehensive selection function ---
def select_final_tickers(triggers: dict, trade_date: str = None, use_hybrid: bool = True, lookback_days: int = 10, macro_context: dict = None) -> dict:
    """
    Consolidate stocks selected from each trigger and choose final stocks.

    Hybrid method (use_hybrid=True):
    1. Collect top 10 candidates from each trigger
    2. Calculate agent criteria scores for all candidates (analyze 10-20 day data)
    3. Calculate final score with composite score (40%) + agent score (60%)
    4. Select rank 1 by final score from each trigger

    Args:
        triggers: Dictionary of DataFrame results by trigger
        trade_date: Reference trading date (required in hybrid mode)
        use_hybrid: Whether to use hybrid selection (default: True)
        lookback_days: Number of past business days for agent score calculation (default: 10)

    Returns:
        Dictionary of finally selected stocks
    """
    final_result = {}

    # 1. Collect candidates from each trigger
    trigger_candidates = {}  # Trigger name -> DataFrame
    all_tickers = set()  # For duplicate checking

    for name, df in triggers.items():
        if not df.empty:
            # Max 10 candidates from each trigger (already returned with head(10))
            candidates = df.copy()
            trigger_candidates[name] = candidates
            all_tickers.update(candidates.index.tolist())

    if not trigger_candidates:
        logger.warning("No candidates from all triggers.")
        return final_result

    # 2. Hybrid mode: Calculate agent scores + #289 RS/extension signals
    if use_hybrid and trade_date:
        logger.info(f"Hybrid selection mode - Calculate agent scores with {lookback_days}-day data")

        # #289: regime-aware blend weights (composite, agent R/R, RS, extension)
        _regime = macro_context.get("market_regime", "sideways") if macro_context else "sideways"
        w_comp, w_agent, w_rs, w_ext = REGIME_SCORE_WEIGHTS.get(_regime, _DEFAULT_SCORE_WEIGHTS)
        logger.info(f"[#289] Blend weights for regime '{_regime}': "
                    f"composite={w_comp}, agent={w_agent}, RS={w_rs}, extension={w_ext}")

        # #289: pre-compute O'Neil-style signals across ALL unique candidates (cross-trigger),
        # then normalize the multi-week return into a relative-strength score (0~1).
        screening_signals = {}  # ticker -> {extension_in_adr, extension_score, return_nd}
        for _name, _cdf in trigger_candidates.items():
            for _ticker in _cdf.index:
                if _ticker in screening_signals:
                    continue
                _cp = _cdf.loc[_ticker, "Close"] if "Close" in _cdf.columns else 0
                screening_signals[_ticker] = calculate_screening_signals(_ticker, float(_cp), trade_date)

        rs_score_map = {}
        if screening_signals:
            _returns = [s["return_nd"] for s in screening_signals.values()]
            _r_min, _r_max = min(_returns), max(_returns)
            _r_range = _r_max - _r_min if _r_max > _r_min else 0.0
            for _ticker, s in screening_signals.items():
                rs_score_map[_ticker] = ((s["return_nd"] - _r_min) / _r_range) if _r_range > 0 else 0.5

        # Phase B: RS Rating SHADOW/LIVE gate (env: RS_RATING_ENABLED, 기본 false=SHADOW).
        # SHADOW: 기존 return_nd 기반 rs_score 100% 불변, oneil 백분위 로그만.
        # LIVE:   rs_score를 oneil 백분위(pct/99.0)로 교체; oneil_raw=None은 기존 값 유지.
        _rs_rating_enabled = os.getenv("RS_RATING_ENABLED", "false").strip().lower() == "true"
        _oneil_raw_map = {t: s["oneil_raw"] for t, s in screening_signals.items()
                          if s.get("oneil_raw") is not None}
        _oneil_pct_map = percentile_ratings(_oneil_raw_map) if _oneil_raw_map else {}
        if not _rs_rating_enabled:
            for _ticker, pct in _oneil_pct_map.items():
                logger.info("[RS-RATING][SHADOW] %s oneil_pct=%.0f cur_rs=%.2f",
                            _ticker, pct, rs_score_map.get(_ticker, 0.5))
        else:
            for _ticker in list(screening_signals.keys()):
                if _ticker in _oneil_pct_map:
                    rs_score_map[_ticker] = _oneil_pct_map[_ticker] / 99.0

        for name, candidates_df in trigger_candidates.items():
            # v1.16.6: Calculate agent scores by trigger type (agent_fit_score unchanged)
            scored_df = score_candidates_by_agent_criteria(candidates_df, trade_date, lookback_days, trigger_type=name)

            # #289: final score = regime-weighted blend of composite + agent + RS + extension
            if "composite_score" in scored_df.columns and "agent_fit_score" in scored_df.columns:
                # Normalize composite score (0~1) within trigger
                cp_max = scored_df["composite_score"].max()
                cp_min = scored_df["composite_score"].min()
                cp_range = cp_max - cp_min if cp_max > cp_min else 1
                scored_df["composite_score_norm"] = (scored_df["composite_score"] - cp_min) / cp_range

                # #289: attach RS + extension signals (cross-candidate)
                scored_df["rs_score"] = [rs_score_map.get(t, 0.5) for t in scored_df.index]
                scored_df["rs_relative"] = [screening_signals.get(t, {}).get("return_nd", 0.0) for t in scored_df.index]
                scored_df["extension_score"] = [screening_signals.get(t, {}).get("extension_score", 1.0) for t in scored_df.index]
                scored_df["extension_in_adr"] = [screening_signals.get(t, {}).get("extension_in_adr", 0.0) for t in scored_df.index]

                # #289: regime-aware final score (was composite*0.3 + agent*0.7)
                scored_df["final_score"] = (
                    scored_df["composite_score_norm"] * w_comp +
                    scored_df["agent_fit_score"] * w_agent +
                    scored_df["rs_score"] * w_rs +
                    scored_df["extension_score"] * w_ext
                )

                # Sort by final score
                scored_df = scored_df.sort_values("final_score", ascending=False)

                # Logging
                logger.info(f"[{name}] Hybrid score calculation complete:")
                for ticker in scored_df.index[:3]:
                    logger.info(f"  - {ticker} ({scored_df.loc[ticker, 'stock_name'] if 'stock_name' in scored_df.columns else ''}): "
                               f"Composite={scored_df.loc[ticker, 'composite_score']:.3f}, "
                               f"Agent={scored_df.loc[ticker, 'agent_fit_score']:.3f}, "
                               f"RS={scored_df.loc[ticker, 'rs_score']:.3f}, "
                               f"Ext={scored_df.loc[ticker, 'extension_score']:.3f}(adr={scored_df.loc[ticker, 'extension_in_adr']:.1f}), "
                               f"Final={scored_df.loc[ticker, 'final_score']:.3f}, "
                               f"R/R={scored_df.loc[ticker, 'risk_reward_ratio']:.2f}")

            trigger_candidates[name] = scored_df

    # 3. Final stock selection (hybrid top-down + bottom-up)
    selected_tickers = set()
    score_column = "final_score" if use_hybrid and trade_date else "composite_score"
    # Determine regime and slot allocation.  When macro context is available,
    # the configured top-down + bottom-up plan is also the final hard cap.  The
    # old fixed-three refill silently defeated weak-regime exposure reduction.
    market_regime = macro_context.get("market_regime", "sideways") if macro_context else "sideways"
    if macro_context:
        topdown_slots, bottomup_slots, max_selections = (
            _get_regime_selection_plan(market_regime)
        )
    else:
        # Preserve the legacy pure bottom-up fallback when macro intelligence
        # is unavailable rather than reducing opportunity on a data outage.
        topdown_slots, bottomup_slots, max_selections = (0, 3, 3)

    # Build top-down pool
    topdown_pool = _build_topdown_pool(trigger_candidates, macro_context, score_column)

    # Diagnostics
    if topdown_pool:
        topdown_sectors = set(macro_context.get("sector_map", {}).get(t[0], "") for t in topdown_pool)
        logger.info(f"Top-down pool: {len(topdown_pool)} candidates from sectors {topdown_sectors}")
    else:
        logger.info("Top-down pool: empty (pure bottom-up mode)")

    # Phase 1: Fill top-down slots
    topdown_filled = 0
    for ticker, trigger_name, td_score, ticker_df in topdown_pool:
        if topdown_filled >= topdown_slots:
            break
        if ticker not in selected_tickers:
            tagged_df = ticker_df.copy()
            tagged_df["SelectionChannel"] = "top-down"
            if trigger_name in final_result:
                final_result[trigger_name] = pd.concat([final_result[trigger_name], tagged_df])
            else:
                final_result[trigger_name] = tagged_df
            selected_tickers.add(ticker)
            topdown_filled += 1
            stock_sector = macro_context.get("sector_map", {}).get(ticker, "N/A") if macro_context else "N/A"
            logger.info(f"[TOP-DOWN] {ticker} selected (sector={stock_sector}, score={td_score:.3f}, trigger={trigger_name})")

    # Phase 2: Fill bottom-up slots (per-trigger top-1 logic)
    for name, df in trigger_candidates.items():
        if not df.empty and len(selected_tickers) < max_selections:
            if score_column in df.columns:
                sorted_df = df.sort_values(score_column, ascending=False)
            else:
                sorted_df = df
            for ticker in sorted_df.index:
                if ticker not in selected_tickers:
                    tagged_df = sorted_df.loc[[ticker]].copy()
                    tagged_df["SelectionChannel"] = "bottom-up"
                    if name in final_result:
                        final_result[name] = pd.concat([final_result[name], tagged_df])
                    else:
                        final_result[name] = tagged_df
                    selected_tickers.add(ticker)
                    logger.info(f"[BOTTOM-UP] {ticker} selected (trigger={name})")
                    break

    # Phase 3: Fill remaining by overall score if needed
    if len(selected_tickers) < max_selections:
        all_candidates = []
        for name, df in trigger_candidates.items():
            for ticker in df.index:
                if ticker not in selected_tickers:
                    score = df.loc[ticker, score_column] if score_column in df.columns else 0
                    all_candidates.append((name, ticker, score, df.loc[[ticker]]))
        all_candidates.sort(key=lambda x: x[2], reverse=True)

        for trigger_name, ticker, _, ticker_df in all_candidates:
            if ticker not in selected_tickers and len(selected_tickers) < max_selections:
                tagged_df = ticker_df.copy()
                tagged_df["SelectionChannel"] = "bottom-up"
                if trigger_name in final_result:
                    final_result[trigger_name] = pd.concat([final_result[trigger_name], tagged_df])
                else:
                    final_result[trigger_name] = tagged_df
                selected_tickers.add(ticker)
                logger.info(f"[BOTTOM-UP] {ticker} selected (fill, trigger={trigger_name})")

    # Log selection summary
    bottomup_count = len(selected_tickers) - topdown_filled
    strategy = "hybrid_topdown_bottomup" if topdown_filled > 0 else "pure_bottomup"
    logger.info(f"Selection summary: {topdown_filled} top-down + {bottomup_count} bottom-up = {len(selected_tickers)} total (regime={market_regime}, strategy={strategy})")

    return final_result

# How far back to walk when today is not a trading session. A long weekend plus
# a holiday stretch stays inside a week; beyond that something is wrong and we
# should say so rather than silently screening month-old data.
_TRADE_DATE_LOOKBACK_DAYS = 7


def _resolve_trade_date(today_str: str) -> str:
    """Resolve the batch reference trading date **without depending on KRX**.

    This used to be a single ``stock_api.get_nearest_business_day_in_a_week()``
    call — the batch's very first data call, with no fallback. When KRX blocked
    the db-server IP, the 2026-08-05 afternoon batch died here 64 seconds in and
    produced **zero KR reports**:

        KRXBlockedError: KRX 접근이 차단된 상태입니다. 198분 뒤(18:04)에 ...
        → "No stocks selected. Terminating process."

    Every other KRX call in this module is already protected — the ticker-name
    lookup degrades to bare codes, and the snapshot/market-cap calls sit behind
    ``load_market_snapshot_bundle``'s Naver fallback. This one line was the only
    unguarded one, and it ran first.

    The orchestrator already decides whether to run at all via
    ``check_market_day.is_market_day()``, which reads a local holiday calendar
    and touches no network. Using that same calendar here is both consistent
    with the gate that let the batch start and immune to KRX being unreachable.
    KRX is kept only as a fallback for the case where the calendar is
    unavailable, so it can no longer take the batch down on its own.
    """
    try:
        from check_market_day import is_market_day

        day = datetime.datetime.strptime(today_str, "%Y%m%d").date()
        for _ in range(_TRADE_DATE_LOOKBACK_DAYS):
            if is_market_day(day):
                return day.strftime("%Y%m%d")
            day -= datetime.timedelta(days=1)
        logger.warning(
            "No trading day found within %d days of %s; falling back to KRX",
            _TRADE_DATE_LOOKBACK_DAYS,
            today_str,
        )
    except Exception as exc:  # noqa: BLE001 - calendar must never end the batch
        logger.warning("Local trading calendar unavailable (%s); falling back to KRX", exc)

    try:
        return stock_api.get_nearest_business_day_in_a_week(today_str, prev=True)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "KRX trading-date lookup failed too (%s); using %s as-is. "
            "Screening may run against a non-session date.",
            exc,
            today_str,
        )
        return today_str


# --- Batch execution function ---
def run_batch(trigger_time: str, log_level: str = "INFO", output_file: str = None, macro_context: dict = None):
    """
    trigger_time: "morning" or "afternoon"
    log_level: "DEBUG", "INFO", "WARNING", etc. (INFO recommended for production)
    output_file: JSON file path to save results (optional)
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    ch.setLevel(numeric_level)
    logger.info(f"Log level: {log_level.upper()}")

    today_str = datetime.datetime.today().strftime("%Y%m%d")
    trade_date = _resolve_trade_date(today_str)
    logger.info(f"Batch reference trading date: {trade_date}")

    market_data = load_market_snapshot_bundle(trade_date)
    snapshot = market_data.snapshot
    prev_snapshot = market_data.prev_snapshot
    prev_date = market_data.prev_date
    cap_df = market_data.cap_df
    logger.debug(f"Previous trading date: {prev_date}")
    logger.debug(f"Market cap data stock count: {len(cap_df)}")

    if trigger_time == "morning":
        logger.info("=== Morning batch execution ===")
        # Execute morning triggers - pass cap_df
        res1 = trigger_morning_volume_surge(trade_date, snapshot, prev_snapshot, cap_df)
        res2 = trigger_morning_gap_up_momentum(trade_date, snapshot, prev_snapshot, cap_df)
        res3 = trigger_morning_value_to_cap_ratio(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {"거래량 급증 상위주": res1, "갭 상승 모멘텀 상위주": res2, "시총 대비 집중 자금 유입 상위주": res3}
    elif trigger_time == "afternoon":
        logger.info("=== Afternoon batch execution ===")
        # Execute afternoon triggers - pass cap_df
        res1 = trigger_afternoon_daily_rise_top(trade_date, snapshot, prev_snapshot, cap_df)
        res2 = trigger_afternoon_closing_strength(trade_date, snapshot, prev_snapshot, cap_df)
        res3 = trigger_afternoon_volume_surge_flat(trade_date, snapshot, prev_snapshot, cap_df)
        triggers = {"일중 상승률 상위주": res1, "마감 강도 상위주": res2, "거래량 증가 상위 횡보주": res3}
    else:
        logger.error("Invalid trigger_time value. Please enter 'morning' or 'afternoon'.")
        return

    # === New triggers: active based on market regime ===
    if macro_context:
        market_regime = macro_context.get("market_regime", "sideways")
        # #289: Macro sector trigger now active in ALL regimes (incl. strong_bull) so that
        # sector leaders surface even in surging markets; downstream RS/extension blend
        # re-ranks them to favor non-extended leaders.
        res_macro = trigger_macro_sector_leader(trade_date, snapshot, prev_snapshot, cap_df, macro_context)
        if not res_macro.empty:
            triggers["매크로 섹터 리더"] = res_macro
            logger.info(f"매크로 섹터 리더: {len(res_macro)} candidates")

        # Contrarian value: active in sideways, moderate_bear, strong_bear
        if market_regime in ("sideways", "moderate_bear", "strong_bear"):
            res_value = trigger_contrarian_value(trade_date, snapshot, prev_snapshot, cap_df)
            if not res_value.empty:
                triggers["역발상 가치주"] = res_value
                logger.info(f"역발상 가치주: {len(res_value)} candidates")

    # Log results by trigger
    for name, df in triggers.items():
        if df.empty:
            logger.info(f"{name}: No stocks meet the criteria.")
        else:
            logger.info(f"{name} detected stocks ({len(df)} stocks):")
            for ticker in df.index:
                stock_name = df.loc[ticker, "stock_name"] if "stock_name" in df.columns else ""
                logger.info(f"- {ticker} ({stock_name})")

            # Output detailed information only at debug level
            logger.debug(f"Detailed information:\n{df}\n{'-'*40}")

    # Final selection results
    final_results = select_final_tickers(triggers, trade_date=trade_date, macro_context=macro_context)

    # Save results as JSON (if requested)
    if output_file:
        import json

        # Include detailed information of selected stocks
        output_data = {}

        # Process by trigger type
        for trigger_type, stocks_df in final_results.items():
            if not stocks_df.empty:
                if trigger_type not in output_data:
                    output_data[trigger_type] = []

                for ticker in stocks_df.index:
                    stock_info = {
                        "code": ticker,
                        "name": stocks_df.loc[ticker, "stock_name"] if "stock_name" in stocks_df.columns else "",
                        "current_price": float(stocks_df.loc[ticker, "Close"]) if "Close" in stocks_df.columns else 0,
                        "change_rate": float(stocks_df.loc[ticker, "prev_day_change_rate"]) if "prev_day_change_rate" in stocks_df.columns else 0,
                        "volume": int(stocks_df.loc[ticker, "Volume"]) if "Volume" in stocks_df.columns else 0,
                        "trade_value": float(stocks_df.loc[ticker, "Amount"]) if "Amount" in stocks_df.columns else 0,
                    }

                    # Add trigger type specific data
                    if "volume_increase_rate" in stocks_df.columns and trigger_type == "거래량 급증 상위주":
                        stock_info["volume_increase"] = float(stocks_df.loc[ticker, "volume_increase_rate"])
                    elif "gap_up_rate" in stocks_df.columns:
                        stock_info["gap_rate"] = float(stocks_df.loc[ticker, "gap_up_rate"])
                    elif "trade_value_ratio" in stocks_df.columns:
                        stock_info["trade_value_ratio"] = float(stocks_df.loc[ticker, "trade_value_ratio"])
                        stock_info["market_cap"] = float(stocks_df.loc[ticker, "시가총액"])
                    elif "closing_strength" in stocks_df.columns:
                        stock_info["closing_strength"] = float(stocks_df.loc[ticker, "closing_strength"])

                    # Add agent score information (hybrid mode)
                    if "agent_fit_score" in stocks_df.columns:
                        stock_info["agent_fit_score"] = float(stocks_df.loc[ticker, "agent_fit_score"])
                        stock_info["risk_reward_ratio"] = float(stocks_df.loc[ticker, "risk_reward_ratio"]) if "risk_reward_ratio" in stocks_df.columns else 0
                        stock_info["stop_loss_pct"] = float(stocks_df.loc[ticker, "stop_loss_pct"]) * 100 if "stop_loss_pct" in stocks_df.columns else 0
                        stock_info["stop_loss_price"] = float(stocks_df.loc[ticker, "stop_loss_price"]) if "stop_loss_price" in stocks_df.columns else 0
                        stock_info["target_price"] = float(stocks_df.loc[ticker, "target_price"]) if "target_price" in stocks_df.columns else 0
                    if "final_score" in stocks_df.columns:
                        stock_info["final_score"] = float(stocks_df.loc[ticker, "final_score"])

                    # #289: observability — RS + extension signals
                    if "rs_score" in stocks_df.columns:
                        stock_info["rs_score"] = float(stocks_df.loc[ticker, "rs_score"])
                        stock_info["rs_relative"] = float(stocks_df.loc[ticker, "rs_relative"]) if "rs_relative" in stocks_df.columns else 0
                    if "extension_score" in stocks_df.columns:
                        stock_info["extension_score"] = float(stocks_df.loc[ticker, "extension_score"])
                        stock_info["extension_in_adr"] = float(stocks_df.loc[ticker, "extension_in_adr"]) if "extension_in_adr" in stocks_df.columns else 0

                    if "SelectionChannel" in stocks_df.columns:
                        stock_info["selection_channel"] = str(stocks_df.loc[ticker, "SelectionChannel"])

                    if "liquidity_lane" in stocks_df.columns:
                        stock_info["liquidity_lane"] = str(stocks_df.loc[ticker, "liquidity_lane"])
                        stock_info["liquidity_lane_rank"] = int(stocks_df.loc[ticker, "liquidity_lane_rank"])
                        stock_info["liquidity_floor"] = float(stocks_df.loc[ticker, "liquidity_floor"])
                        _ceiling = stocks_df.loc[ticker, "liquidity_ceiling"]
                        stock_info["liquidity_ceiling"] = None if pd.isna(_ceiling) else float(_ceiling)

                    output_data[trigger_type].append(stock_info)

        # Derive hybrid metadata from final_results
        _market_regime = macro_context.get("market_regime", "sideways") if macro_context else None
        _primary_trend_regime = macro_context.get("primary_trend_regime") if macro_context else None
        _effective_entry_regime = macro_context.get("effective_entry_regime") if macro_context else None
        _swing_state = macro_context.get("swing_state") if macro_context else None
        _topdown_slots, _bottomup_slots = _get_regime_slots(_market_regime) if _market_regime else (0, 3)
        _topdown_count = sum(
            1 for _, stocks_df in final_results.items()
            for ticker in stocks_df.index
            if "SelectionChannel" in stocks_df.columns and stocks_df.loc[ticker, "SelectionChannel"] == "top-down"
        )
        _bottomup_count = sum(
            1 for _, stocks_df in final_results.items()
            for ticker in stocks_df.index
            if "SelectionChannel" not in stocks_df.columns or stocks_df.loc[ticker, "SelectionChannel"] == "bottom-up"
        )

        # Add execution time and metadata
        output_data["metadata"] = {
            "run_time": datetime.datetime.now().isoformat(),
            "trigger_mode": trigger_time,
            "trade_date": trade_date,
            "selection_mode": "hybrid",
            "lookback_days": 10,
            "selection_strategy": "hybrid_topdown_bottomup" if macro_context else "pure_bottomup",
            "market_regime": _market_regime,
            "primary_trend_regime": _primary_trend_regime or _market_regime,
            "effective_entry_regime": _effective_entry_regime or _market_regime,
            "swing_state": _swing_state,
            "topdown_slots": _topdown_slots,
            "bottomup_slots": _bottomup_slots,
            "topdown_count": _topdown_count,
            "bottomup_count": _bottomup_count,
            "emerging_liquidity_min_trade_value": EMERGING_LIQUIDITY_MIN_TRADE_VALUE,
            "emerging_liquidity_max_candidates": EMERGING_LIQUIDITY_MAX_CANDIDATES,
        }

        # Save JSON file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Selection results saved to {output_file}.")

    return final_results

if __name__ == "__main__":
    # Usage: python trigger_batch.py morning [DEBUG|INFO|...] [--output filepath]
    import argparse

    parser = argparse.ArgumentParser(description="Execute trigger batch")
    parser.add_argument("mode", help="Execution mode (morning or afternoon)")
    parser.add_argument("log_level", nargs="?", default="INFO", help="Logging level")
    parser.add_argument("--output", help="JSON file path to save results")

    args = parser.parse_args()

    run_batch(args.mode, args.log_level, args.output)
