#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Surge Detector Module

Data retrieval and caching functions for US stock surge detection.
Uses yfinance for market data access.
"""

import datetime
import logging
import os
import time
from threading import local
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import yfinance as yf
import sys
from pathlib import Path
from typing import Tuple, List
from prism_core.ohlcv_shape import normalize_single_ticker_ohlcv

# Import check_market_day functions for US holiday handling
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_market_day import get_last_trading_day, get_next_trading_day

# Logger setup
logger = logging.getLogger(__name__)


def get_sp500_tickers() -> List[str]:
    """
    Get list of S&P 500 tickers from Wikipedia.

    Returns:
        List of ticker symbols
    """
    import requests
    from io import StringIO

    try:
        # Wikipedia requires User-Agent header to avoid 403 Forbidden
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML tables
        tables = pd.read_html(StringIO(response.text))
        table = tables[0]

        tickers = table['Symbol'].tolist()
        # Clean up tickers (some have dots that need to be replaced with dashes for yfinance)
        tickers = [t.replace('.', '-') for t in tickers]
        logger.info(f"Loaded {len(tickers)} S&P 500 tickers from Wikipedia")
        return tickers
    except Exception as e:
        logger.error(f"Failed to load S&P 500 tickers: {e}")
        # Fallback to major stocks
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
            "UNH", "JNJ", "JPM", "V", "PG", "XOM", "HD", "CVX", "MA", "ABBV",
            "MRK", "LLY", "PEP", "KO", "COST", "AVGO", "MCD", "TMO", "WMT",
            "CSCO", "ACN", "ABT", "DHR", "NEE", "LIN", "PM", "TXN", "CMCSA"
        ]


def get_nasdaq100_tickers() -> List[str]:
    """
    Get list of NASDAQ-100 tickers.

    Returns:
        List of ticker symbols
    """
    import requests
    import re
    from io import StringIO

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    # Constituents moved to a dedicated page; keep one bounded legacy fallback.
    urls = (
        'https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies',
        'https://en.wikipedia.org/wiki/Nasdaq-100',
    )
    for url in urls:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            candidates = []
            for table in pd.read_html(StringIO(response.text)):
                if isinstance(table.columns, pd.MultiIndex) or not table.columns.is_unique:
                    continue
                columns = {str(column).strip() for column in table.columns}
                if len(columns) != len(table.columns) or not {'Ticker', 'Company'} <= columns:
                    continue
                # Change-history tables must never masquerade as constituents.
                if any(re.search(r'\b(added|removed|former|replaced|date|year)\b', column, re.I)
                       for column in columns):
                    continue
                candidates.append(table.rename(columns=lambda column: str(column).strip()))
            if len(candidates) != 1 or candidates[0].empty:
                raise ValueError("Missing or ambiguous current NASDAQ-100 constituent table")
            table = candidates[0]
            tickers = []
            for ticker, company in zip(table['Ticker'], table['Company']):
                if (not isinstance(ticker, str) or not re.fullmatch(r'[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?', ticker.strip())
                        or not isinstance(company, str) or not company.strip()):
                    raise ValueError("Invalid NASDAQ-100 constituent row")
                tickers.append(ticker.strip().replace('.', '-'))
            tickers = list(dict.fromkeys(tickers))
            logger.info(f"Loaded {len(tickers)} NASDAQ-100 tickers from Wikipedia")
            return tickers
        except Exception as e:
            logger.warning(f"NASDAQ-100 constituent source unavailable ({url}): {type(e).__name__}")
    logger.error("Failed to load validated NASDAQ-100 constituents from both sources")
    return []


def get_major_tickers() -> List[str]:
    """
    Get combined list of major US stock tickers (S&P 500 + NASDAQ-100).
    Removes duplicates.

    Returns:
        List of unique ticker symbols
    """
    sp500 = set(get_sp500_tickers())
    nasdaq100 = set(get_nasdaq100_tickers())
    combined = sp500.union(nasdaq100)
    logger.info(f"Total unique tickers: {len(combined)}")
    return list(combined)


# A per-thread detached response serves the immediately following prior-session
# request. Keep frames out of attrs (pandas compares attrs during concatenation).
_SNAPSHOT_HISTORY_TTL_SECONDS = 60.0
_snapshot_history = local()
_SNAPSHOT_FIELDS = ['Open', 'High', 'Low', 'Close', 'Volume']
_DAILY_OPTIONS = dict(auto_adjust=True, back_adjust=False, repair=False,
                      keepna=True, interval='1d', prepost=False, actions=False)


def _daily_recovery_enabled():
    return os.getenv('US_DAILY_CACHE_ENABLED', 'true').strip().lower() == 'true'


def _download_daily(*args, **kwargs):
    # Public download() swallows per-symbol exceptions and logs a summary.
    # Capture only a boolean, never raw provider text. Concurrent overlapping
    # rate-limit logs may conservatively suppress a retry, never enable one.
    class RateLimitCapture(logging.Handler):
        limited = False

        def emit(self, record):
            message = record.getMessage().lower()
            if any(marker in message for marker in ('ratelimit', 'too many requests', '429')):
                self.limited = True

    handler = RateLimitCapture()
    yf_logger = logging.getLogger('yfinance')
    from yfinance.utils import YFLogFormatter
    unobservable = (not yf_logger.isEnabledFor(logging.ERROR)
                    or any(type(f) is not YFLogFormatter for f in yf_logger.filters))
    yf_logger.addHandler(handler)
    try:
        data = yf.download(*args, **kwargs)
    finally:
        yf_logger.removeHandler(handler)
    if isinstance(data, pd.DataFrame):
        data.attrs['recovery_retry_suppressed'] = handler.limited or unobservable
    return data


def _recover_daily_snapshot(data, requested_date, tickers, captured_at, reference_data=None):
    """Fresh -> same-basis completed cache -> bounded exact-date retry.

    Recovery never fills from repaired/intraday/different-date observations.
    The recorded clock is request start, not a later persistence/read time.
    """
    snapshot = _snapshot_from_history(data, requested_date, tickers)
    if not _daily_recovery_enabled():
        return snapshot
    try:
        from prism_core.us_daily_cache import DailyBarCache, histories_share_basis
    except ImportError:
        logger.warning('Optional daily cache unavailable; keeping original provider result')
        return snapshot
    root = Path(os.getenv('PRISM_US_DAILY_CACHE_DIR') or
                str(Path(__file__).resolve().parents[2] / 'runtime/us_daily_ohlcv_cache')).expanduser()
    cache = DailyBarCache(root, now=captured_at)
    original = dict(snapshot.attrs['snapshot_coverage'])
    sources = {ticker: 'provider' for ticker in snapshot.index}
    captures = {}
    normalized = {}
    cache_saved = 0

    def frame_for(raw, ticker):
        if (not isinstance(raw, pd.DataFrame) or raw.empty or
                (not isinstance(raw.columns, pd.MultiIndex) and len(tickers) != 1)):
            return pd.DataFrame()
        return normalize_single_ticker_ohlcv(raw, ticker)

    # Preserve the originating current response as basis when the 60s reuse
    # expires and a new previous-response download is necessary.
    for ticker in tickers:
        frame = frame_for(data, ticker)
        normalized[ticker] = frame
        if reference_data is not None and ticker in snapshot.index:
            reference = frame_for(reference_data, ticker)
            if not histories_share_basis(reference, frame, requested_date):
                snapshot = snapshot.drop(index=ticker)
                sources.pop(ticker, None)
        if ticker not in snapshot.index:
            reference = frame_for(reference_data, ticker) if reference_data is not None else frame
            try:
                cached = cache.load_row(ticker, requested_date, reference)
            except Exception:
                cached = None
            if cached is not None:
                snapshot.loc[ticker, _SNAPSHOT_FIELDS] = [cached[k] for k in _SNAPSHOT_FIELDS]
                snapshot.loc[ticker, 'Amount'] = cached['Close'] * cached['Volume']
                sources[ticker] = 'cache'
                captures[ticker] = cached['captured_at']
        # Read old target+anchors before saving a new response generation.
        try:
            cache_saved += cache.save_history(ticker, frame)
        except Exception:
            logger.debug('Completed-day cache write unavailable; provider result retained')

    missing = [ticker for ticker in tickers if ticker not in snapshot.index]
    started = time.monotonic()
    retries = 0
    stop_reason = ('bulk_error_or_unobservable' if isinstance(data, pd.DataFrame)
                   and data.attrs.get('recovery_retry_suppressed') else None)
    for ticker in missing:
        if retries >= 10:
            stop_reason = 'retry_count_budget'
            break
        if stop_reason:
            break
        reference = frame_for(reference_data, ticker) if reference_data is not None else normalized[ticker]
        if not histories_share_basis(reference, reference, requested_date):
            continue
        if time.monotonic() - started >= 15:
            stop_reason = 'retry_time_budget'
            break
        retries += 1
        request_time = datetime.datetime.now(datetime.timezone.utc)
        target = datetime.datetime.strptime(requested_date, '%Y%m%d').date()
        try:
            retry = yf.Ticker(ticker).history(
                start=(target-datetime.timedelta(days=7)).isoformat(),
                end=(target+datetime.timedelta(days=1)).isoformat(), timeout=5,
                raise_errors=True, **_DAILY_OPTIONS)
        except Exception as exc:
            from yfinance.exceptions import YFRateLimitError
            if isinstance(exc, YFRateLimitError):
                stop_reason = 'rate_limit'
                break
            continue
        if time.monotonic() - started >= 15:
            stop_reason = 'retry_time_budget'
            break  # A late response is not accepted merely because it arrived.
        candidate = _snapshot_from_history(retry, requested_date, [ticker])
        reference = frame_for(reference_data, ticker) if reference_data is not None else normalized[ticker]
        if not candidate.empty and histories_share_basis(reference, retry, requested_date):
            snapshot.loc[ticker, candidate.columns] = candidate.loc[ticker]
            sources[ticker] = 'retry'
            try:
                cache_saved += DailyBarCache(root, now=request_time).save_history(ticker, retry)
            except Exception:
                logger.debug('Completed-day retry cache write unavailable; retry result retained')
    snapshot = snapshot.reindex([t for t in tickers if t in snapshot.index])
    count = len(tickers)
    remaining = count-len(snapshot)
    snapshot.attrs['snapshot_coverage'] = {
        'requested_date': requested_date, 'requested_count': count,
        'valid_count': len(snapshot), 'missing_count': remaining,
        'status': ('COMPLETE' if count and not remaining else 'PARTIAL' if len(snapshot) else 'UNAVAILABLE'),
        'reason_counts': {'unresolved_after_recovery': remaining} if remaining else {},
        'provider_reason_counts': original['reason_counts'],
        'sources': sources, 'cache_captured_at': captures,
        'cache_saved_count': cache_saved, 'retry_attempts': retries,
        'retry_stop_reason': stop_reason,
    }
    return snapshot


def _snapshot_from_history(data: pd.DataFrame, requested_date: str,
                           tickers: List[str]) -> pd.DataFrame:
    """Accept only exact-session, unambiguous, finite observations."""
    rows = []
    reasons = {}

    def missing(reason):
        reasons[reason] = reasons.get(reason, 0) + 1

    ambiguous_flat = (isinstance(data, pd.DataFrame) and not data.empty
                      and not isinstance(data.columns, pd.MultiIndex)
                      and len(tickers) != 1)
    for ticker in tickers:
        if ambiguous_flat:
            missing('ambiguous_flat_universe')
            continue
        frame = normalize_single_ticker_ohlcv(data, ticker)
        if frame.empty or not set(_SNAPSHOT_FIELDS) <= set(frame.columns):
            missing('missing_or_ambiguous_symbol')
            continue
        if not isinstance(frame.index, pd.DatetimeIndex):
            missing('invalid_date_index')
            continue
        matching = frame.index.strftime('%Y%m%d') == requested_date
        if int(matching.sum()) != 1:
            missing('missing_exact_date' if not matching.any() else 'duplicate_exact_date')
            continue
        if 'Repaired?' in frame.columns:
            repaired = frame.loc[matching, 'Repaired?'].iloc[0]
            if pd.isna(repaired) or repaired != False:
                missing('repaired_ohlcv_rejected')
                continue
        values = pd.to_numeric(frame.loc[matching, _SNAPSHOT_FIELDS].iloc[0],
                               errors='coerce').to_numpy(dtype=float)
        with np.errstate(over='ignore', invalid='ignore'):
            amount = values[3] * values[4]
        if (not np.isfinite(values).all() or not np.isfinite(amount)
                or (values[:4] <= 0).any() or values[4] < 0):
            missing('invalid_ohlcv')
            continue
        rows.append({'Ticker': ticker, **dict(zip(_SNAPSHOT_FIELDS, values)),
                     'Amount': float(amount)})
    snapshot = pd.DataFrame(rows, columns=['Ticker', *_SNAPSHOT_FIELDS, 'Amount']).set_index('Ticker')
    count = len(tickers)
    snapshot.attrs['snapshot_coverage'] = {
        'requested_date': requested_date,
        'requested_count': count,
        'valid_count': len(snapshot),
        'missing_count': count - len(snapshot),
        'status': ('COMPLETE' if count and len(snapshot) == count else
                   'PARTIAL' if len(snapshot) else 'UNAVAILABLE'),
        'reason_counts': reasons,
    }
    return snapshot


def get_snapshot(trade_date: str, tickers: List[str] = None) -> pd.DataFrame:
    """Get exact-date OHLCV with explicit coverage; never relabel an older bar."""
    tickers = list(tickers) if tickers is not None else get_sp500_tickers()
    end_date = datetime.datetime.strptime(trade_date, '%Y%m%d')
    start_date = end_date - datetime.timedelta(days=5)
    # Invalidate an earlier invocation even if this download fails.
    _snapshot_history.value = None
    try:
        captured_at = datetime.datetime.now(datetime.timezone.utc)
        data = _download_daily(
            tickers, start=start_date.strftime('%Y-%m-%d'),
            end=(end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
            progress=False, threads=True, **_DAILY_OPTIONS)
        snapshot = _recover_daily_snapshot(data, trade_date, tickers, captured_at)
        if isinstance(data, pd.DataFrame):
            _snapshot_history.value = ((trade_date, tuple(tickers)),
                                       time.monotonic(), data.copy(deep=True), captured_at)
        logger.info("Retrieved current snapshot: %s", snapshot.attrs['snapshot_coverage'])
        return snapshot
    except Exception as exc:
        raise ValueError(f"Failed to get snapshot for {trade_date}: {exc}") from exc


def get_previous_snapshot(trade_date: str, tickers: List[str] = None) -> Tuple[pd.DataFrame, str]:
    """Get the exact preceding NYSE session, reusing matching fresh history once."""
    tickers = list(tickers) if tickers is not None else get_sp500_tickers()
    date_obj = datetime.datetime.strptime(trade_date, '%Y%m%d').date()
    prev_date_obj = get_last_trading_day(date_obj - datetime.timedelta(days=1))
    prev_date = prev_date_obj.strftime('%Y%m%d')
    data = None
    reference_data = None
    cached = getattr(_snapshot_history, 'value', None)
    if cached is not None:
        key, stored_at, cached_data, capture_time = cached
        age = time.monotonic() - stored_at
        if key == (trade_date, tuple(tickers)):
            _snapshot_history.value = None
            if 0 <= age <= _SNAPSHOT_HISTORY_TTL_SECONDS:
                data = cached_data.copy(deep=True)
            else:
                reference_data = cached_data
        elif age < 0 or age > _SNAPSHOT_HISTORY_TTL_SECONDS:
            _snapshot_history.value = None
    try:
        if data is None:
            capture_time = datetime.datetime.now(datetime.timezone.utc)
            data = _download_daily(
                tickers,
                start=(prev_date_obj - datetime.timedelta(days=7)).strftime('%Y-%m-%d'),
                end=(prev_date_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
                progress=False, threads=True, **_DAILY_OPTIONS)
        snapshot = _recover_daily_snapshot(data, prev_date, tickers, capture_time, reference_data)
        logger.info("Retrieved previous snapshot: %s", snapshot.attrs['snapshot_coverage'])
        return snapshot, prev_date
    except Exception as exc:
        raise ValueError(f"Failed to get previous snapshot for {prev_date}: {exc}") from exc


def get_multi_day_ohlcv(ticker: str, end_date: str, days: int = 10) -> pd.DataFrame:
    """
    Get N days of OHLCV data for a specific ticker.

    Args:
        ticker: Stock ticker symbol
        end_date: End date in YYYYMMDD format
        days: Number of trading days to retrieve

    Returns:
        DataFrame with OHLCV data
    """
    end_dt = datetime.datetime.strptime(end_date, '%Y%m%d')
    start_dt = end_dt - datetime.timedelta(days=days * 2)  # Extra buffer for non-trading days

    try:
        data = yf.download(
            ticker,
            start=start_dt.strftime('%Y-%m-%d'),
            end=(end_dt + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
            progress=False
        )

        if data.empty:
            logger.warning(f"No {days}-day data for {ticker}")
            return pd.DataFrame()

        normalized = normalize_single_ticker_ohlcv(data, ticker)
        if normalized.empty:
            logger.warning("OHLCV_SHAPE_UNAVAILABLE: %s", ticker)
        return normalized.tail(days)

    except Exception as e:
        logger.error(f"Error getting multi-day data for {ticker}: {e}")
        return pd.DataFrame()


def get_market_cap_df(tickers: List[str] = None, max_workers: int = 8) -> pd.DataFrame:
    """
    Get market capitalization data for all tickers.

    Args:
        tickers: List of ticker symbols

    Returns:
        DataFrame with market cap data, indexed by ticker
    """
    if tickers is None:
        tickers = get_sp500_tickers()

    tickers = list(dict.fromkeys(tickers))
    logger.debug(f"Getting market cap for {len(tickers)} tickers")

    def fetch_one(ticker: str) -> tuple[str, float | None]:
        try:
            # ``fast_info`` is materially cheaper than the full ``info`` quote
            # summary used by the retired 517-symbol serial path.
            market_cap = float(yf.Ticker(ticker).fast_info["marketCap"])
            return ticker, market_cap if market_cap > 0 else None
        except Exception as e:
            logger.debug(f"Error getting market cap for {ticker}: {e}")
            return ticker, None

    market_caps: dict[str, dict[str, float]] = {}
    workers = max(1, min(int(max_workers), len(tickers))) if tickers else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker, market_cap = future.result()
            if market_cap is not None:
                market_caps[ticker] = {"MarketCap": market_cap}

    if not market_caps:
        logger.error("No market cap data retrieved")
        return pd.DataFrame()

    ordered = {ticker: market_caps[ticker] for ticker in tickers if ticker in market_caps}
    cap_df = pd.DataFrame.from_dict(ordered, orient='index')
    logger.info(f"Retrieved market cap for {len(cap_df)} tickers")

    return cap_df


def get_ticker_name(ticker: str) -> str:
    """
    Get company name for a ticker symbol.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Company name or empty string if not found
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return info.get('shortName', info.get('longName', ''))
    except Exception:
        return ''


def get_nearest_business_day(date_str: str, prev: bool = True) -> str:
    """
    Get the nearest business day (handles weekends AND US market holidays).

    Uses pandas-market-calendars NYSE calendar to properly handle:
    - Weekends (Saturday, Sunday)
    - US Market Holidays (MLK Day, Presidents Day, Good Friday, etc.)

    Args:
        date_str: Date in YYYYMMDD format
        prev: If True, look for previous/current trading day; if False, look for next

    Returns:
        Date string in YYYYMMDD format
    """
    date_obj = datetime.datetime.strptime(date_str, '%Y%m%d').date()

    if prev:
        # Get most recent trading day ON OR BEFORE the given date
        result = get_last_trading_day(date_obj)
    else:
        # Get next trading day AFTER the given date
        result = get_next_trading_day(date_obj)

    return result.strftime('%Y%m%d')


def filter_low_liquidity(df: pd.DataFrame, threshold: float = 0.2) -> pd.DataFrame:
    """
    Filter out stocks with volume in the bottom N percentile.

    Args:
        df: DataFrame with Volume column
        threshold: Percentile threshold (default: bottom 20%)

    Returns:
        Filtered DataFrame
    """
    volume_cutoff = np.percentile(df['Volume'], threshold * 100)
    return df[df['Volume'] > volume_cutoff]


def apply_absolute_filters(df: pd.DataFrame, min_value: float = 10000000) -> pd.DataFrame:
    """
    Apply absolute value filters:
    - Minimum trading value (default: $10M)
    - Sufficient liquidity (>20% of market average volume)

    Args:
        df: DataFrame with Amount and Volume columns
        min_value: Minimum trading value in USD (default: $10M)

    Returns:
        Filtered DataFrame
    """
    # Minimum trading value filter ($10M)
    filtered_df = df[df['Amount'] >= min_value].copy()

    # Filter for stocks with >= 20% of market average volume
    avg_volume = df['Volume'].mean()
    min_volume = avg_volume * 0.2
    filtered_df = filtered_df[filtered_df['Volume'] >= min_volume]

    return filtered_df


def normalize_and_score(df: pd.DataFrame, ratio_col: str, abs_col: str,
                       ratio_weight: float = 0.6, abs_weight: float = 0.4,
                       ascending: bool = False) -> pd.DataFrame:
    """
    Calculate composite score using normalized values.

    Args:
        df: DataFrame with specified columns
        ratio_col: Column name for ratio metric
        abs_col: Column name for absolute metric
        ratio_weight: Weight for ratio (default: 0.6)
        abs_weight: Weight for absolute (default: 0.4)
        ascending: Sort order (default: False for descending)

    Returns:
        DataFrame with composite score column
    """
    if df.empty:
        return df

    result = df.copy()

    # Normalize columns
    ratio_max = result[ratio_col].max()
    ratio_min = result[ratio_col].min()
    abs_max = result[abs_col].max()
    abs_min = result[abs_col].min()

    ratio_range = ratio_max - ratio_min if ratio_max > ratio_min else 1
    abs_range = abs_max - abs_min if abs_max > abs_min else 1

    result[f"{ratio_col}_norm"] = (result[ratio_col] - ratio_min) / ratio_range
    result[f"{abs_col}_norm"] = (result[abs_col] - abs_min) / abs_range

    # Calculate composite score
    result["CompositeScore"] = (
        result[f"{ratio_col}_norm"] * ratio_weight +
        result[f"{abs_col}_norm"] * abs_weight
    )

    return result.sort_values("CompositeScore", ascending=ascending)


def enhance_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add company names to DataFrame.

    Args:
        df: DataFrame indexed by ticker symbols

    Returns:
        DataFrame with CompanyName column added
    """
    if not df.empty:
        result = df.copy()
        result["CompanyName"] = result.index.map(get_ticker_name)
        return result
    return df
