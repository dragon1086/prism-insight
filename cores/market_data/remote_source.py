"""Read-only KIS transport through the existing authenticated SSH tunnel."""

from __future__ import annotations

import io
import json
import os
from urllib.parse import urlsplit

import httpx
import pandas as pd

from cores.market_data.source import Unavailable, Unsupported

MAX_PAYLOAD = 2_000_000


def encode_result(result):
    if isinstance(result, str):
        if len(result) > 256:
            raise ValueError("Invalid name")
        return {"kind": "name", "value": result}
    if not isinstance(result, pd.DataFrame) or len(result) > 1200 or len(result.columns) > 64:
        raise ValueError("Invalid frame")
    # pandas' JSON table schema retains numeric dtypes and datetime timezone.
    payload = {"kind": "frame", "table": json.loads(result.to_json(
        orient="table", date_format="iso", date_unit="ns", double_precision=15,
    )), "attrs": result.attrs, "index_name": result.index.name,
        "columns_name": result.columns.name}
    if len(json.dumps(payload, allow_nan=False).encode()) > MAX_PAYLOAD:
        raise ValueError("Result too large")
    return payload


def decode_result(payload):
    if payload["kind"] == "name":
        if not isinstance(payload["value"], str) or len(payload["value"]) > 256:
            raise ValueError("Invalid name")
        return payload["value"]
    if payload["kind"] != "frame":
        raise ValueError("Invalid result")
    frame = pd.read_json(io.StringIO(json.dumps(payload["table"])), orient="table")
    if len(frame) > 1200 or len(frame.columns) > 64:
        raise ValueError("Result too large")
    frame.index.name = payload["index_name"]
    frame.columns.name = payload["columns_name"]
    frame.attrs.update(payload["attrs"])
    return frame


class RemoteKisSource:
    """KIS remains the provider; no local credential or legacy fallback."""

    name = "kis-remote"

    def __init__(self, url: str, *, api_key: str | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise Unavailable("Remote market data requires a loopback SSH tunnel")
        self._url = url.rstrip("/") + "/market-data"
        self._key = api_key if api_key is not None else os.getenv("ARCHIVE_API_KEY", "")
        if not self._key:
            raise Unavailable("Remote market data authentication is not configured")

    def _fetch(self, capability, ticker, **params):
        try:
            with httpx.Client(timeout=35.0, follow_redirects=False, trust_env=False) as client:  # noqa: SIM117 - keep stream arguments readable
                with client.stream("POST", self._url,
                                   headers={"Authorization": f"Bearer {self._key}"},
                                   json={"capability": capability, "ticker": ticker, **params}) as response:
                    if response.status_code == 422:
                        raise Unsupported("Remote KIS capability or request unsupported")
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_PAYLOAD:
                            raise ValueError("Result too large")
                    return decode_result(json.loads(content))
        except Unsupported:
            raise
        except Exception:  # noqa: BLE001 - transport details must not reach report text
            raise Unavailable("Remote KIS market data unavailable") from None

    def price_history(self, ticker, start, end, *, adjusted=True):
        return self._fetch("price_history", ticker, start=start, end=end, adjusted=adjusted)

    def index_history(self, index_code, start, end):
        return self._fetch("index_history", index_code, start=start, end=end)

    def market_cap_history(self, ticker, start, end):
        return self._fetch("market_cap_history", ticker, start=start, end=end)

    def investor_flows(self, ticker, start, end):
        return self._fetch("investor_flows", ticker, start=start, end=end)

    def fundamentals(self, ticker, start, end):
        return self._fetch("fundamentals", ticker, start=start, end=end)

    def intraday_investor_estimate(self, ticker, *, as_of=None):
        return self._fetch("intraday_investor_estimate", ticker,
                           as_of=as_of.isoformat() if as_of is not None else None)

    def ticker_name(self, ticker):
        return self._fetch("ticker_name", ticker)

    def ticker_market(self, ticker):
        market = self._fetch("ticker_market", ticker)
        if not isinstance(market, str) or market not in {"KOSPI", "KOSDAQ"}:
            raise Unsupported("Remote KIS listing market unavailable")
        return market
