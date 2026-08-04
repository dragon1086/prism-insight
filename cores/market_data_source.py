"""Per-stock market data with a source that survives KRX being unavailable.

Screening already degrades gracefully: `trigger_batch.load_market_snapshot_bundle`
falls back to `cores.naver_market_snapshot` and still returns ~2,700 stocks. The
report path did not. `cores/stock_chart.py` called `krx_data_client` directly and
returned `None` on any failure, so on 2026-08-04 — when KRX restricted the
server's IP — every chart silently vanished and the afternoon report came out at
17,387 characters against a normal 351,311. The analysis text was there; the
prices, flows and charts were not.

This module is the seam that was missing. Callers ask here instead of asking KRX,
and here decides where the data comes from. Today that means KRX first and
FinanceDataReader (Naver) second, matching the `[FDR-FALLBACK]` pattern already
proven in `trigger_batch._get_recent_ohlcv`. When the broker migration lands,
only this file changes.

What the fallback cannot do is worth stating plainly: FinanceDataReader has no
investor-flow data. Those functions are re-exported unchanged so callers keep
their existing behaviour rather than silently receiving something different.
"""

from __future__ import annotations

import datetime
import logging

import pandas as pd
from krx_data_client import get_index_ohlcv_by_date as _krx_index_ohlcv
from krx_data_client import get_market_cap_by_date as _krx_market_cap

# Re-exported untouched: no free source offers an equivalent, so pretending
# otherwise would be worse than failing. The broker migration covers these.
from krx_data_client import (  # noqa: F401
    get_market_fundamental_by_date,
    get_market_ticker_name,
    get_market_trading_volume_by_date,
    get_market_trading_volume_by_investor,
)
from krx_data_client import get_market_ohlcv_by_date as _krx_ohlcv

logger = logging.getLogger(__name__)

# FinanceDataReader index symbols for the two indices the reports chart.
_INDEX_SYMBOLS = {
    "1001": "KS11",  # KOSPI
    "2001": "KQ11",  # KOSDAQ
}

# Both Korean and English headers appear depending on source and version.
_COLUMN_MAP = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "change": "Change",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
    "등락률": "Change",
}


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Give the frame the English headers and DatetimeIndex charts expect."""
    df = df.rename(
        columns={c: _COLUMN_MAP[str(c).lower()] for c in df.columns
                 if str(c).lower() in _COLUMN_MAP}
    )
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _to_dashed(yyyymmdd: str) -> str:
    # A calendar date with no clock or zone; naive is the correct type here.
    return datetime.datetime.strptime(  # noqa: DTZ007
        yyyymmdd, "%Y%m%d"
    ).strftime("%Y-%m-%d")


def _fdr_read(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    import FinanceDataReader as fdr

    df = fdr.DataReader(symbol, _to_dashed(start_date), _to_dashed(end_date))
    return _normalize(df) if df is not None and not df.empty else pd.DataFrame()


def _with_fallback(label: str, symbol: str, primary, start_date: str, end_date: str):
    """Try KRX, then Naver. Empty is a failure, not an answer.

    An empty frame reaches the caller as "no data for this stock", which renders
    as a missing chart rather than an error — that is precisely how the outage
    stayed invisible for two hours.
    """
    try:
        df = primary()
        if df is not None and len(df) > 0:
            return _normalize(df)
        logger.warning("%s: KRX returned no rows; trying FinanceDataReader", label)
    except Exception as exc:  # noqa: BLE001 - any KRX failure is a fallback trigger
        logger.warning("%s: KRX failed (%s); trying FinanceDataReader", label, exc)

    try:
        df = _fdr_read(symbol, start_date, end_date)
    except Exception as exc:  # noqa: BLE001
        logger.error("%s: FinanceDataReader also failed: %s", label, exc)
        return pd.DataFrame()

    if df.empty:
        logger.error("%s: FinanceDataReader returned no rows either", label)
        return df

    logger.warning("[FDR-FALLBACK] %s: FinanceDataReader(네이버) used", label)
    return df


def get_market_ohlcv_by_date(
    start_date: str,
    end_date: str,
    ticker: str,
    adjusted: bool = True,
) -> pd.DataFrame:
    """Daily OHLCV for one stock, from KRX or Naver."""
    return _with_fallback(
        f"ohlcv {ticker}",
        ticker,
        lambda: _krx_ohlcv(start_date, end_date, ticker, adjusted=adjusted),
        start_date,
        end_date,
    )


def get_index_ohlcv_by_date(
    start_date: str,
    end_date: str,
    index_ticker: str,
) -> pd.DataFrame:
    """Daily OHLCV for an index, from KRX or Naver."""
    symbol = _INDEX_SYMBOLS.get(str(index_ticker))
    if symbol is None:
        # Unknown index: no mapping to fall back to, so let the primary answer.
        try:
            df = _krx_index_ohlcv(start_date, end_date, index_ticker)
            return _normalize(df) if df is not None and len(df) else pd.DataFrame()
        except Exception as exc:  # noqa: BLE001
            logger.error("index %s: KRX failed and no fallback mapping: %s",
                         index_ticker, exc)
            return pd.DataFrame()

    return _with_fallback(
        f"index {index_ticker}",
        symbol,
        lambda: _krx_index_ohlcv(start_date, end_date, index_ticker),
        start_date,
        end_date,
    )


def get_market_cap_by_date(
    start_date: str,
    end_date: str,
    ticker: str,
) -> pd.DataFrame:
    """Market cap history.

    FinanceDataReader has no market-cap series, so the fallback reconstructs it
    from close price times shares outstanding. Shares outstanding is taken as a
    constant from the latest listing snapshot, which is wrong across splits and
    issuance — acceptable for a chart of relative movement, not for a figure
    quoted as fact. The frame is marked so callers can tell.
    """
    try:
        df = _krx_market_cap(start_date, end_date, ticker)
        if df is not None and len(df) > 0:
            return _normalize(df)
        logger.warning("cap %s: KRX returned no rows", ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cap %s: KRX failed (%s)", ticker, exc)

    try:
        import FinanceDataReader as fdr

        ohlcv = _fdr_read(ticker, start_date, end_date)
        if ohlcv.empty:
            return pd.DataFrame()

        listing = fdr.StockListing("KRX")
        row = listing[listing["Code"] == ticker]
        shares = None
        for column in ("Stocks", "Shares", "ListedShares"):
            if column in listing.columns and not row.empty:
                shares = row.iloc[0][column]
                break
        if not shares or pd.isna(shares):
            logger.error("cap %s: shares outstanding unavailable", ticker)
            return pd.DataFrame()

        out = pd.DataFrame(index=ohlcv.index)
        out["MarketCap"] = ohlcv["Close"] * float(shares)
        out.attrs["approximate"] = True
        logger.warning(
            "[FDR-FALLBACK] cap %s: reconstructed from close x shares "
            "(constant shares — approximate)",
            ticker,
        )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("cap %s: FinanceDataReader fallback failed: %s", ticker, exc)
        return pd.DataFrame()


def resolve_ticker_name(ticker: str) -> str | None:
    """Company name, falling back to the ticker itself.

    Charts label with this; a failed lookup should cost the label, not the chart.
    """
    try:
        return get_market_ticker_name(ticker)
    except Exception:  # noqa: BLE001
        return ticker
