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

import os
import random
import threading
import time

import pandas as pd

from cores.market_data.schema import has_ohlcv, normalize
from cores.market_data.source import Unavailable

# Minimum spacing between consecutive KRX requests, plus jitter.
#
# This lived in ``trigger_batch.get_multi_day_ohlcv`` and therefore protected
# exactly one call site; every other path into KRX — including everything routed
# through this chain — hit the service back to back. Bursts are what produce the
# read-timeouts, and sustained bursts are what KRX calls "비정상 대량 조회".
# Spacing belongs to the source, not to one caller.
#
# ``KRX_MIN_GAP_SEC=0`` disables it.
_THROTTLE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


def _throttle() -> None:
    try:
        min_gap = float(os.getenv("KRX_MIN_GAP_SEC", "0.4"))
    except (TypeError, ValueError):
        min_gap = 0.4
    if min_gap <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_gap - (time.monotonic() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0.0, 0.2))
        _LAST_CALL[0] = time.monotonic()


class KrxSource:
    name = "krx"

    @staticmethod
    def _client():
        import krx_data_client

        _throttle()
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
