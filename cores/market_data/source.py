"""What a market data provider has to offer, and how several are combined.

The protocol is written from what the reports actually ask for, not from any
one provider's API. That is deliberate: define the port by the need and each
provider becomes an adapter, so adding a broker means writing one class rather
than editing every call site.

No provider covers everything. FinanceDataReader has no investor flows; a broker
may have flows but no index history. Rather than returning something plausible
but wrong, a source declares a capability unsupported and the chain moves on —
which is also how a caller learns that nobody can answer, instead of receiving
an empty frame that renders as a blank chart.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import pandas as pd

logger = logging.getLogger(__name__)


class Unsupported(Exception):
    """This provider does not offer this capability at all.

    Distinct from failure: there is nothing to retry and nothing is wrong. The
    chain skips to the next source without logging an error.
    """


class Unavailable(Exception):
    """This provider could not answer right now.

    Restriction, timeout, auth, an empty result — all the same to the chain,
    which tries the next source. The message is kept for the log because the
    2026-08-04 outage was prolonged by a failure that reported only "not found".
    """


@runtime_checkable
class MarketDataSource(Protocol):
    """One provider of per-instrument market data.

    Implementations raise `Unsupported` for capabilities they lack and
    `Unavailable` when a call fails. Returning an empty frame is not an
    acceptable way to signal either.
    """

    name: str

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        """Daily OHLCV for one stock. Dates are `YYYYMMDD`."""

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        """Daily OHLCV for an index, keyed by the KRX index code."""

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Market capitalisation over time, column `MarketCap`."""

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Net buying by investor type — the figure only exchanges publish."""

    def intraday_investor_estimate(
        self, ticker: str, *, as_of=None
    ) -> pd.DataFrame:
        """Current-session investor-flow estimate, when a provider publishes it."""

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """PER/PBR/dividend yield over time."""

    def ticker_name(self, ticker: str) -> str:
        """Company name for a ticker."""


class SourceChain:
    """Ask each source in turn until one answers.

    Order is the caller's choice rather than a constant in this file, so the
    same code can run KRX-first in production and broker-first once the
    migration lands, without an edit here.
    """

    def __init__(self, sources: list[MarketDataSource]) -> None:
        if not sources:
            raise ValueError("a chain needs at least one source")
        self._sources = sources

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._sources]

    def fetch(self, capability: str, *args, **kwargs) -> pd.DataFrame | str:
        """Call `capability` on each source until one succeeds.

        Raises `Unavailable` listing every attempt when none can answer. The
        alternative — returning empty — is what let a two-hour outage look like
        "these stocks have no data".
        """
        attempts: list[str] = []
        for source in self._sources:
            method = getattr(source, capability, None)
            if method is None:
                attempts.append(f"{source.name}: no such capability")
                continue
            try:
                result = method(*args, **kwargs)
            except Unsupported:
                attempts.append(f"{source.name}: unsupported")
                continue
            except Unavailable as exc:
                attempts.append(f"{source.name}: {exc}")
                logger.warning(
                    "%s unavailable for %s; trying next source", source.name, capability
                )
                continue
            except Exception as exc:  # noqa: BLE001 - a broken source must not stop the chain
                attempts.append(f"{source.name}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "%s raised on %s (%s); trying next source",
                    source.name,
                    capability,
                    exc,
                )
                continue

            if source is not self._sources[0]:
                logger.warning(
                    "[FALLBACK] %s answered %s after %s",
                    source.name,
                    capability,
                    ", ".join(attempts) or "primary",
                )
            return result

        raise Unavailable(f"{capability}: no source could answer ({'; '.join(attempts)})")
