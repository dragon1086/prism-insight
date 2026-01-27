#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US Surge Detector Module

Data retrieval and caching functions for US stock surge detection.
Uses yfinance for market data access.
"""

import datetime
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from typing import Tuple, Optional, List

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
    from io import StringIO

    try:
        # Wikipedia requires User-Agent header to avoid 403 Forbidden
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        url = 'https://en.wikipedia.org/wiki/Nasdaq-100'

        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML tables (NASDAQ-100 table is usually the 4th or 5th table)
        tables = pd.read_html(StringIO(response.text))
        # Find the table with 'Ticker' column
        for table in tables:
            if 'Ticker' in table.columns:
                tickers = table['Ticker'].tolist()
                tickers = [t.replace('.', '-') for t in tickers]
                logger.info(f"Loaded {len(tickers)} NASDAQ-100 tickers from Wikipedia")
                return tickers

        logger.warning("Could not find NASDAQ-100 table with 'Ticker' column")
        return []
    except Exception as e:
        logger.error(f"Failed to load NASDAQ-100 tickers: {e}")
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


def get_snapshot(trade_date: str, tickers: List[str] = None) -> pd.DataFrame:
    """
    Get OHLCV snapshot for all specified tickers on the given date.

    Args:
        trade_date: Trading date in YYYYMMDD format
        tickers: List of ticker symbols (default: S&P 500)

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
        Index: Ticker symbols
    """
    logger.debug(f"get_snapshot called: {trade_date}")

    if tickers is None:
        tickers = get_sp500_tickers()

    # Convert date format
    date_str = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
    end_date = datetime.datetime.strptime(trade_date, '%Y%m%d')
    start_date = end_date - datetime.timedelta(days=5)  # Get a few days for safety

    try:
        # Download data for all tickers at once (more efficient)
        data = yf.download(
            tickers,
            start=start_date.strftime('%Y-%m-%d'),
            end=(end_date + datetime.timedelta(days=1)).strftime('%Y-%m-%d'),
            progress=False,
            threads=True
        )

        if data.empty:
            logger.error(f"No OHLCV data for {trade_date}")
            raise ValueError(f"No OHLCV data for {trade_date}")

        # Get the specific date's data
        if isinstance(data.columns, pd.MultiIndex):
            # Multiple tickers case
            rows = []

            # Try to get exact date or nearest available date
            available_dates = data.index.strftime('%Y%m%d').tolist()
            if trade_date in available_dates:
                target_idx = data.index[available_dates.index(trade_date)]
            else:
                # Find nearest date
                target_idx = data.index[-1]
                logger.warning(f"Date {trade_date} not found, using {target_idx.strftime('%Y%m%d')}")

            for ticker in tickers:
                try:
                    row = {
                        'Ticker': ticker,
                        'Open': data.loc[target_idx, ('Open', ticker)],
                        'High': data.loc[target_idx, ('High', ticker)],
                        'Low': data.loc[target_idx, ('Low', ticker)],
                        'Close': data.loc[target_idx, ('Close', ticker)],
                        'Volume': data.loc[target_idx, ('Volume', ticker)],
                    }
                    rows.append(row)
                except Exception:
                    continue

            # Create DataFrame from collected rows
            snapshot = pd.DataFrame(rows)
            if not snapshot.empty:
                snapshot = snapshot.set_index('Ticker')
                # Calculate Amount (trade value) = Close * Volume
                snapshot['Amount'] = snapshot['Close'] * snapshot['Volume']

        else:
            # Single ticker case
            snapshot = data.loc[[data.index[-1]]].copy()
            snapshot.index = [tickers[0]]
            snapshot['Amount'] = snapshot['Close'] * snapshot['Volume']

        # Remove rows with NaN values
        snapshot = snapshot.dropna()

        logger.debug(f"Snapshot data sample:\n{snapshot.head()}")
        logger.info(f"Retrieved snapshot for {len(snapshot)} tickers")

        return snapshot

    except Exception as e:
        logger.error(f"Error getting snapshot: {e}")
        raise ValueError(f"Failed to get snapshot for {trade_date}: {e}")


def get_previous_snapshot(trade_date: str, tickers: List[str] = None) -> Tuple[pd.DataFrame, str]:
    """
    Get OHLCV snapshot for the previous trading day.

    Args:
        trade_date: Trading date in YYYYMMDD format
        tickers: List of ticker symbols

    Returns:
        Tuple of (DataFrame, previous_date_string)
    """
    if tickers is None:
        tickers = get_sp500_tickers()

    # Calculate previous day
    date_obj = datetime.datetime.strptime(trade_date, '%Y%m%d')
    prev_date_obj = date_obj - datetime.timedelta(days=1)

    # Skip weekends
    while prev_date_obj.weekday() >= 5:  # Saturday=5, Sunday=6
        prev_date_obj -= datetime.timedelta(days=1)

    prev_date = prev_date_obj.strftime('%Y%m%d')

    # Get data for previous 5 days to ensure we get a trading day
    start_date = prev_date_obj - datetime.timedelta(days=7)

    try:
        data = yf.download(
            tickers,
            start=start_date.strftime('%Y-%m-%d'),
            end=prev_date_obj.strftime('%Y-%m-%d'),
            progress=False,
            threads=True
        )

        if data.empty:
            logger.error(f"No previous snapshot data")
            raise ValueError(f"No previous snapshot data for {prev_date}")

        # Get the last available date before trade_date
        if isinstance(data.columns, pd.MultiIndex):
            rows = []
            target_idx = data.index[-1]
            actual_date = target_idx.strftime('%Y%m%d')

            for ticker in tickers:
                try:
                    row = {
                        'Ticker': ticker,
                        'Open': data.loc[target_idx, ('Open', ticker)],
                        'High': data.loc[target_idx, ('High', ticker)],
                        'Low': data.loc[target_idx, ('Low', ticker)],
                        'Close': data.loc[target_idx, ('Close', ticker)],
                        'Volume': data.loc[target_idx, ('Volume', ticker)],
                    }
                    rows.append(row)
                except Exception:
                    continue

            snapshot = pd.DataFrame(rows)
            if not snapshot.empty:
                snapshot = snapshot.set_index('Ticker')
                snapshot['Amount'] = snapshot['Close'] * snapshot['Volume']
        else:
            snapshot = data.loc[[data.index[-1]]].copy()
            snapshot.index = [tickers[0]]
            snapshot['Amount'] = snapshot['Close'] * snapshot['Volume']
            actual_date = data.index[-1].strftime('%Y%m%d')

        snapshot = snapshot.dropna()

        logger.debug(f"Previous trading day: {actual_date}")
        logger.info(f"Retrieved previous snapshot for {len(snapshot)} tickers")

        return snapshot, actual_date

    except Exception as e:
        logger.error(f"Error getting previous snapshot: {e}")
        raise ValueError(f"Failed to get previous snapshot: {e}")


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

        return data.tail(days)

    except Exception as e:
        logger.error(f"Error getting multi-day data for {ticker}: {e}")
        return pd.DataFrame()


def get_market_cap_df(tickers: List[str] = None) -> pd.DataFrame:
    """
    Get market capitalization data for all tickers.

    Args:
        tickers: List of ticker symbols

    Returns:
        DataFrame with market cap data, indexed by ticker
    """
    if tickers is None:
        tickers = get_sp500_tickers()

    logger.debug(f"Getting market cap for {len(tickers)} tickers")

    market_caps = {}

    # Process in batches for efficiency
    batch_size = 50
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]

        for ticker in batch:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                market_cap = info.get('marketCap', 0)
                if market_cap and market_cap > 0:
                    market_caps[ticker] = {'MarketCap': market_cap}
            except Exception as e:
                logger.debug(f"Error getting market cap for {ticker}: {e}")
                continue

    if not market_caps:
        logger.error("No market cap data retrieved")
        return pd.DataFrame()

    cap_df = pd.DataFrame.from_dict(market_caps, orient='index')
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
    Get the nearest business day.

    Args:
        date_str: Date in YYYYMMDD format
        prev: If True, look for previous business day; if False, look for next

    Returns:
        Date string in YYYYMMDD format
    """
    date_obj = datetime.datetime.strptime(date_str, '%Y%m%d')

    # Skip weekends
    while date_obj.weekday() >= 5:  # Saturday=5, Sunday=6
        if prev:
            date_obj -= datetime.timedelta(days=1)
        else:
            date_obj += datetime.timedelta(days=1)

    # TODO: Add US market holiday check using pandas-market-calendars
    # For now, just skip weekends

    return date_obj.strftime('%Y%m%d')


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
