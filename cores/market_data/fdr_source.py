"""FinanceDataReader (Naver) as a market data source.

Free, unauthenticated, and unaffected by KRX restricting an IP — which is what
makes it a useful second opinion rather than a replacement. It carries prices
and indices well; it has no investor flows and no market-cap series, and those
gaps are declared rather than papered over.
"""

from __future__ import annotations

import logging
import threading

import pandas as pd

from cores.market_data.schema import has_ohlcv, normalize, to_dashed
from cores.market_data.source import Unavailable, Unsupported

logger = logging.getLogger(__name__)

# KRX index codes to FinanceDataReader symbols. Only the indices the reports
# chart; an unknown code is declared unsupported rather than guessed at.
INDEX_SYMBOLS = {
    "1001": "KS11",  # KOSPI
    "2001": "KQ11",  # KOSDAQ
}


class FdrSource:
    name = "fdr"

    def __init__(self) -> None:
        # `StockListing` downloads the whole market. Market cap is requested per
        # stock, so without this the listing would be fetched once per ticker.
        self._listing: pd.DataFrame | None = None
        self._listing_lock = threading.Lock()

    @staticmethod
    def _fdr():
        import FinanceDataReader as fdr

        return fdr

    def _read(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        try:
            frame = self._fdr().DataReader(symbol, to_dashed(start), to_dashed(end))
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        if frame is None or frame.empty:
            raise Unavailable(f"FinanceDataReader returned no rows for {symbol}")
        return normalize(frame)

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        # Naver serves adjusted prices; the flag cannot be honoured either way,
        # so it is accepted and ignored rather than refused.
        frame = self._read(ticker, start, end)
        if not has_ohlcv(frame):
            raise Unavailable(f"FDR ohlcv {ticker} missing OHLCV columns")
        return frame

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        symbol = INDEX_SYMBOLS.get(str(index_code))
        if symbol is None:
            raise Unsupported(f"no FDR symbol mapped for index {index_code}")
        return self._read(symbol, start, end)

    def _shares_outstanding(self, ticker: str) -> float:
        with self._listing_lock:
            if self._listing is None:
                try:
                    self._listing = self._fdr().StockListing("KRX")
                except Exception as exc:  # any provider failure means "not now"
                    raise Unavailable(f"KRX listing unavailable: {exc}") from exc

        listing = self._listing
        row = listing[listing["Code"] == ticker] if "Code" in listing.columns else None
        if row is None or row.empty:
            raise Unavailable(f"{ticker} not in the listing snapshot")

        for column in ("Stocks", "Shares", "ListedShares"):
            if column in listing.columns:
                value = row.iloc[0][column]
                if value and not pd.isna(value):
                    return float(value)
        raise Unavailable(f"shares outstanding missing for {ticker}")

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Reconstructed, and marked as such.

        There is no market-cap series here, so this is close price times shares
        outstanding, with shares taken as today's constant. That is wrong across
        splits and issuance — fine for the shape of a chart, not for a number
        quoted as fact, hence `attrs["approximate"]`.
        """
        prices = self._read(ticker, start, end)
        if "Close" not in prices.columns:
            raise Unavailable(f"FDR {ticker} has no Close to derive cap from")
        shares = self._shares_outstanding(ticker)

        frame = pd.DataFrame(index=prices.index)
        frame["MarketCap"] = prices["Close"] * shares
        frame.attrs["approximate"] = True
        logger.info(
            "cap %s reconstructed from close x %s shares (constant — approximate)",
            ticker,
            f"{shares:,.0f}",
        )
        return frame

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        raise Unsupported("FinanceDataReader does not publish investor flows")

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        raise Unsupported("FinanceDataReader does not publish PER/PBR history")

    def ticker_name(self, ticker: str) -> str:
        try:
            shares_listing = self._listing
            if shares_listing is None:
                with self._listing_lock:
                    if self._listing is None:
                        self._listing = self._fdr().StockListing("KRX")
                    shares_listing = self._listing
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(f"KRX listing unavailable: {exc}") from exc

        if "Code" not in shares_listing.columns or "Name" not in shares_listing.columns:
            raise Unavailable("listing snapshot has no Code/Name columns")
        row = shares_listing[shares_listing["Code"] == ticker]
        if row.empty:
            raise Unavailable(f"{ticker} not in the listing snapshot")
        return str(row.iloc[0]["Name"])
