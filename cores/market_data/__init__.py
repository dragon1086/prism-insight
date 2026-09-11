"""KIS-only Korean market data with explicit unsupported/missing results.

Legacy provider environment variables cannot reactivate retired data sources.
The compatibility function names are API names, not exchange-login clients.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from cores.market_data.kis_source import KisSource
from cores.market_data.source import (
    MarketDataSource,
    SourceChain,
    Unavailable,
    Unsupported,
)

logger = logging.getLogger(__name__)

__all__ = [
    "KisSource",
    "MarketDataSource",
    "SourceChain",
    "Unavailable",
    "Unsupported",
    "default_chain",
    "get_index_ohlcv_by_date",
    "get_market_cap_by_date",
    "get_market_fundamental_by_date",
    "get_market_ohlcv_by_date",
    "get_market_ticker_name",
    "get_market_trading_volume_by_date",
    "get_market_trading_volume_by_investor",
    "set_default_chain",
    "get_market_ohlcv_by_ticker",
    "get_market_cap_by_ticker",
    "get_market_ticker_list",
    "get_nearest_business_day_in_a_week",
]

_DEFAULT_ORDER = "kis"

_chain: SourceChain | None = None
_KST = ZoneInfo("Asia/Seoul")


def _now_kst() -> datetime:
    return datetime.now(_KST)


def default_chain() -> SourceChain:
    """Build the sole production provider; ignore stale provider-order settings."""
    global _chain
    if _chain is None:
        order = os.getenv("PRISM_MARKET_DATA_SOURCES", _DEFAULT_ORDER)
        if order.strip().lower() != "kis":
            logger.warning("Retired market-data provider settings ignored; KIS is the only provider")
        _chain = SourceChain([KisSource()])
        logger.info("market data sources: %s", " -> ".join(_chain.names))
    return _chain


def set_default_chain(chain: SourceChain | None) -> None:
    """Replace the process chain. For tests and for deliberate reconfiguration."""
    global _chain
    _chain = chain


def _empty_on_exhaustion(capability: str, *args, **kwargs) -> pd.DataFrame:
    """Existing callers expect a frame, not an exception.

    `stock_chart` treats an empty frame as "no data for this instrument" and
    skips the chart. That is the right end state once every source has been
    tried — but it is logged at error level, because the silent version of this
    is exactly what hid the 2026-08-04 outage.
    """
    try:
        return default_chain().fetch(capability, *args, **kwargs)
    except Unavailable as exc:
        logger.error("%s", exc)
        return pd.DataFrame()


def get_market_ohlcv_by_date(
    start_date: str, end_date: str, ticker: str, adjusted: bool = True
) -> pd.DataFrame:
    return _empty_on_exhaustion(
        "price_history", ticker, start_date, end_date, adjusted=adjusted
    )


def get_index_ohlcv_by_date(
    start_date: str, end_date: str, index_ticker: str
) -> pd.DataFrame:
    return _empty_on_exhaustion("index_history", str(index_ticker), start_date, end_date)


def get_market_cap_by_date(
    start_date: str, end_date: str, ticker: str
) -> pd.DataFrame:
    return _empty_on_exhaustion("market_cap_history", ticker, start_date, end_date)


def get_market_fundamental_by_date(
    start_date: str, end_date: str, ticker: str
) -> pd.DataFrame:
    return _empty_on_exhaustion("fundamentals", ticker, start_date, end_date)


def _with_individual_other_total(frame: pd.DataFrame) -> pd.DataFrame:
    """Add the combined group used by an intraday estimate chart.

    Historical daily rows provide exact personal and other values, while the
    intraday endpoint exposes only their combined residual. Giving historical
    rows the same combined column keeps the cumulative series continuous and
    avoids charting the combined estimate alongside its component columns.
    """
    if frame.empty or "개인·기타합계" in frame.columns or "개인" not in frame.columns:
        return frame

    if "기타합계" in frame.columns:
        other = frame["기타합계"]
    else:
        other_columns = [
            column for column in ("기타법인", "기타단체") if column in frame.columns
        ]
        if not other_columns:
            return frame
        other = frame[other_columns].sum(axis=1, min_count=1)

    return frame.assign(**{"개인·기타합계": frame["개인"].add(other)})


def get_market_trading_volume_by_date(
    start_date: str, end_date: str, ticker: str
) -> pd.DataFrame:
    now = _now_kst()
    today = now.strftime("%Y%m%d")
    wants_today = start_date <= today <= end_date
    estimate_window = (
        wants_today
        and now.weekday() < 5
        and time(9, 30) <= now.time() < time(15, 40)
    )

    if not estimate_window:
        return _empty_on_exhaustion("investor_flows", ticker, start_date, end_date)

    # Keep the historical part on the ordinary source chain, but ask only
    # through yesterday: KIS's daily endpoint rejects requests before 15:40
    # when today's date is used as the as-of date.
    history_end = min(
        end_date, (now.date() - timedelta(days=1)).strftime("%Y%m%d")
    )
    history = (
        _empty_on_exhaustion("investor_flows", ticker, start_date, history_end)
        if start_date <= history_end
        else pd.DataFrame()
    )

    try:
        estimate = default_chain().fetch(
            "intraday_investor_estimate", ticker, as_of=now
        )
    except Unavailable as exc:
        logger.warning("KIS intraday investor estimate unavailable: %s", exc)
        return history

    history = _with_individual_other_total(history)
    combined = pd.concat([history, estimate]).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.attrs.update(estimate.attrs)
    logger.info(
        "using %s intraday investor estimate for %s as of %s",
        estimate.attrs.get("estimate_source", "KIS"),
        ticker,
        estimate.attrs.get("estimate_as_of", "unknown"),
    )
    return combined


def get_market_trading_volume_by_investor(
    start_date: str, end_date: str, ticker: str
) -> pd.DataFrame:
    """Compatibility alias for the shared investor-flow source chain."""
    return get_market_trading_volume_by_date(start_date, end_date, ticker)


def get_market_ticker_name(ticker: str) -> str:
    """Company name, falling back to the ticker.

    A chart label is not worth failing a chart over.
    """
    try:
        return default_chain().fetch("ticker_name", ticker)
    except Unavailable:
        return ticker


def get_nearest_business_day_in_a_week(target_date: str | None = None, prev: bool = True) -> str:
    """Local exchange calendar only; authentication is never needed for dates."""
    from check_market_day import is_market_day
    day = datetime.strptime(target_date, "%Y%m%d").date() if target_date else _now_kst().date()
    for _ in range(14):
        if is_market_day(day):
            return day.strftime("%Y%m%d")
        day += timedelta(days=-1 if prev else 1)
    raise Unavailable("No trading session found within the local calendar bound")


def _current_master(date: str | None = None):
    from cores.kis_market_snapshot import fetch_kis_master_data
    today = _now_kst().strftime("%Y%m%d")
    if date is not None and str(date) != today:
        raise Unsupported("Historical full-market universes are unavailable from today's KIS master")
    master = fetch_kis_master_data()
    if master.observed_date != today:
        raise Unavailable("KIS master observation date is not today")
    return master


def get_market_ticker_list(date: str | None = None, market: str = "ALL") -> list[str]:
    master = _current_master(date)
    if market not in {"ALL", "KOSPI", "KOSDAQ"}:
        raise Unsupported("Unsupported KIS master market")
    return [code for code in master.names if market == "ALL" or master.markets.get(code) == market]


def get_market_ohlcv_by_ticker(date: str, market: str = "ALL") -> pd.DataFrame:
    from cores.kis_market_snapshot import fetch_kis_intraday_snapshot
    master = _current_master(date)
    if market not in {"ALL", "KOSPI", "KOSDAQ"}:
        raise Unsupported("Unsupported KIS master market")
    # Complete-universe validation remains at the collection boundary.
    frame = fetch_kis_intraday_snapshot(master.names)
    if market != "ALL":
        frame = frame.loc[[code for code in frame.index if master.markets.get(code) == market]]
    return frame


def get_market_cap_by_ticker(date: str, market: str = "ALL") -> pd.DataFrame:
    """Latest master-published prior-session cap, not today's intraday cap."""
    master = _current_master(date)
    if market not in {"ALL", "KOSPI", "KOSDAQ"}:
        raise Unsupported("Unsupported KIS master market")
    frame = master.cap_df.copy()
    if market != "ALL":
        frame = frame.loc[[code for code in frame.index if master.markets.get(code) == market]]
    frame.attrs.update(source="kis_master", data_status="previous_session_snapshot", observed_date=master.observed_date)
    return frame
