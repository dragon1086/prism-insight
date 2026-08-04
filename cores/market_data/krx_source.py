"""KRX Data Marketplace as a market data source.

The richest source available — it is the only one here that publishes investor
flows — and the most fragile. KRX restricts IPs that query in bulk (2026-08-04:
"자동화 수단을 통한 비정상 대량 조회가 감지되어 해당 IP의 접속이 일시적으로
제한되었습니다"), so every call has to be treated as something that may simply
refuse today.

`krx_data_client` is imported lazily. Importing it at module scope would make
this file — and everything that reaches it — fail to load on a host without KRX
credentials, which defeats the point of having alternatives.
"""

from __future__ import annotations

import pandas as pd

from cores.market_data.schema import has_ohlcv, normalize
from cores.market_data.source import Unavailable


class KrxSource:
    name = "krx"

    @staticmethod
    def _client():
        import krx_data_client

        return krx_data_client

    @staticmethod
    def _require_rows(frame, what: str) -> pd.DataFrame:
        if frame is None or len(frame) == 0:
            raise Unavailable(f"KRX returned no rows for {what}")
        return normalize(frame)

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        try:
            frame = self._client().get_market_ohlcv_by_date(
                start, end, ticker, adjusted=adjusted
            )
        except Exception as exc:  # restriction, auth, timeout — all mean "not now"
            raise Unavailable(str(exc)) from exc
        frame = self._require_rows(frame, f"ohlcv {ticker}")
        if not has_ohlcv(frame):
            raise Unavailable(f"KRX ohlcv {ticker} missing OHLCV columns")
        return frame

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        try:
            frame = self._client().get_index_ohlcv_by_date(start, end, index_code)
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        return self._require_rows(frame, f"index {index_code}")

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        try:
            frame = self._client().get_market_cap_by_date(start, end, ticker)
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        return self._require_rows(frame, f"cap {ticker}")

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        try:
            frame = self._client().get_market_trading_volume_by_date(
                start, end, ticker
            )
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        return self._require_rows(frame, f"flows {ticker}")

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        try:
            frame = self._client().get_market_fundamental_by_date(start, end, ticker)
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        return self._require_rows(frame, f"fundamentals {ticker}")

    def ticker_name(self, ticker: str) -> str:
        try:
            name = self._client().get_market_ticker_name(ticker)
        except Exception as exc:  # any provider failure means "not now"
            raise Unavailable(str(exc)) from exc
        if not name:
            raise Unavailable(f"KRX has no name for {ticker}")
        return name
