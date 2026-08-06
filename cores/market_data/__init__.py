"""Per-instrument market data, from whichever provider can answer.

Callers ask here rather than asking a provider. What sits behind it — KRX,
FinanceDataReader, a broker — is assembled in `default_chain()` and can be
reordered or extended without touching a single call site.

The function names match the ones `krx_data_client` exposes so existing callers
did not have to change when this was introduced. That is a migration
convenience, not the interface: new code should prefer the verbs on
`MarketDataSource` (`price_history`, `investor_flows`, …), which say what is
wanted rather than which vendor it came from.

Why this exists: on 2026-08-04 KRX restricted the server's IP and
`cores/stock_chart.py`, which called KRX directly and returned `None` on any
failure, produced a report of 17,387 characters against a normal 351,311 — no
charts, no prices, and no error anywhere. Screening survived the same outage
because it already had a second source. This is that second source for the
report path.
"""

from __future__ import annotations

import logging
import os

import pandas as pd

from cores.market_data.fdr_source import FdrSource
from cores.market_data.kis_source import KisSource
from cores.market_data.krx_source import KrxSource
from cores.market_data.naver_source import NaverSource
from cores.market_data.source import (
    MarketDataSource,
    SourceChain,
    Unavailable,
    Unsupported,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FdrSource",
    "KisSource",
    "KrxSource",
    "MarketDataSource",
    "NaverSource",
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
]

_BUILDERS = {
    "krx": KrxSource,
    "fdr": FdrSource,
    "kis": KisSource,
    "naver": NaverSource,
}
# KIS is registered but not in the default order. It is the route we intend to
# migrate to, and putting it first is a decision to make with measurements
# rather than at import time — `PRISM_MARKET_DATA_SOURCES=kis,fdr,krx` promotes
# it without a code change.
_DEFAULT_ORDER = "krx,fdr"

_chain: SourceChain | None = None


def default_chain() -> SourceChain:
    """The chain this process uses, built once.

    `PRISM_MARKET_DATA_SOURCES` sets the order, so a host that cannot reach KRX
    at all can run `fdr` alone without a code change, and the broker source can
    be promoted to first the day it is ready.
    """
    global _chain
    if _chain is None:
        order = os.getenv("PRISM_MARKET_DATA_SOURCES", _DEFAULT_ORDER)
        sources: list[MarketDataSource] = []
        for name in (part.strip().lower() for part in order.split(",")):
            builder = _BUILDERS.get(name)
            if builder is None:
                logger.warning("unknown market data source %r; skipping", name)
                continue
            sources.append(builder())
        if not sources:
            logger.warning("no valid sources configured; falling back to %s", _DEFAULT_ORDER)
            sources = [KrxSource(), FdrSource()]
        _chain = SourceChain(sources)
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


def get_market_trading_volume_by_date(
    start_date: str, end_date: str, ticker: str
) -> pd.DataFrame:
    return _empty_on_exhaustion("investor_flows", ticker, start_date, end_date)


def get_market_trading_volume_by_investor(*args, **kwargs):
    """Not re-routed: no alternative source publishes investor flows.

    Kept as a direct pass-through so the caller's behaviour is unchanged rather
    than quietly becoming something else.
    """
    import krx_data_client

    return krx_data_client.get_market_trading_volume_by_investor(*args, **kwargs)


def get_market_ticker_name(ticker: str) -> str:
    """Company name, falling back to the ticker.

    A chart label is not worth failing a chart over.
    """
    try:
        return default_chain().fetch("ticker_name", ticker)
    except Unavailable:
        return ticker
