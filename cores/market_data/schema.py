"""One shape for price frames, whoever produced them.

Every provider names its columns differently — KRX returns Korean headers in
some versions and English in others, FinanceDataReader returns English, a broker
API will return something else again. Charts and indicators should not have to
know. Normalising at the edge means the rest of the codebase sees exactly one
schema and a new provider costs a mapping entry rather than a sweep through
callers.
"""

from __future__ import annotations

import datetime

import pandas as pd

# The columns downstream code indexes by name.
OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")

_HEADERS = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
    "change": "Change",
    "marketcap": "MarketCap",
    "시가": "Open",
    "고가": "High",
    "저가": "Low",
    "종가": "Close",
    "거래량": "Volume",
    "등락률": "Change",
    "시가총액": "MarketCap",
}


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename headers, index by date, sort oldest first."""
    renamed = frame.rename(
        columns={
            column: _HEADERS[str(column).lower()]
            for column in frame.columns
            if str(column).lower() in _HEADERS
        }
    )
    if not isinstance(renamed.index, pd.DatetimeIndex):
        renamed.index = pd.to_datetime(renamed.index)
    return renamed.sort_index()


def has_ohlcv(frame: pd.DataFrame) -> bool:
    """Is this usable as a price series?

    A frame that is merely non-empty is not enough: a provider that returns
    rows without a Close silently produces a blank chart instead of an error.
    """
    return not frame.empty and set(OHLCV_COLUMNS).issubset(frame.columns)


def to_dashed(yyyymmdd: str) -> str:
    """`20260804` -> `2026-08-04`, the format most providers want."""
    # A calendar date carries no clock or zone; naive is the correct type.
    return datetime.datetime.strptime(  # noqa: DTZ007
        yyyymmdd, "%Y%m%d"
    ).strftime("%Y-%m-%d")
