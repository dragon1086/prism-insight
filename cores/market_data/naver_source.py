"""Recent investor flows from Naver Finance's public stock trend endpoint.

This is a narrow resilience adapter, not a general market-data source. The
endpoint exposes only the latest ten sessions, but that is enough to keep a
same-day report honest about recent foreign, institutional, and individual net
buying when authenticated KRX is unavailable and KIS is closed until 15:40.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import requests

from cores.market_data.schema import normalize
from cores.market_data.source import Unavailable, Unsupported

_TREND_URL = "https://m.stock.naver.com/api/stock/{ticker}/trend"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://m.stock.naver.com/",
}
_COLUMNS = {
    "organPureBuyQuant": "기관합계",
    "foreignerPureBuyQuant": "외국인합계",
    "individualPureBuyQuant": "개인",
}


class NaverSource:
    name = "naver"

    def __init__(self, *, request_get: Callable = requests.get) -> None:
        self._request_get = request_get

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        try:
            response = self._request_get(
                _TREND_URL.format(ticker=ticker),
                headers=_HEADERS,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise Unavailable(f"Naver trend request failed: {exc}") from exc

        if not isinstance(payload, list) or not payload:
            raise Unavailable("Naver trend returned no rows")

        records = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            date = str(row.get("bizdate") or "")
            if len(date) != 8 or not date.isdigit():
                continue
            try:
                record = {"Date": date}
                record.update(
                    {
                        target: _signed_int(row.get(source))
                        for source, target in _COLUMNS.items()
                    }
                )
            except (TypeError, ValueError):
                continue
            records.append(record)

        if not records:
            raise Unavailable("Naver trend rows failed schema validation")

        frame = pd.DataFrame.from_records(records).set_index("Date")
        frame = normalize(frame)
        frame = frame[
            (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        ]
        if frame.empty:
            raise Unavailable(f"Naver trend has no rows in {start}..{end}")
        frame.attrs["coverage"] = "latest_10_sessions"
        return frame

    def price_history(self, ticker: str, start: str, end: str, *, adjusted: bool = True):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def index_history(self, index_code: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def market_cap_history(self, ticker: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def fundamentals(self, ticker: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def ticker_name(self, ticker: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")


def _signed_int(value: object) -> int:
    if not isinstance(value, (str, int)):
        raise TypeError("investor flow is not numeric")
    return int(str(value).replace(",", "").replace("+", "").strip())

