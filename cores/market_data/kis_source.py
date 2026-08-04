"""한국투자증권 Open API as a market data source.

The contracted route. KRX restricted the server's IP on 2026-08-04 for automated
bulk collection, and the fix is not gentler scraping — it is asking a provider we
have an agreement with. KIS is that provider, and it is the only one here besides
KRX that publishes investor flows.

Two things shape this adapter:

**It borrows the trading client rather than authenticating on its own.** KIS
issues one token per app key, and `kis_auth` keeps global environment state that
`changeTREnv` mutates under `get_trading_env_lock()`. A second auth path on the
same host would fight the live trading loops for that state and could force a
token re-issue underneath them. `DomesticStockTrading(auto_trading=False)`
already resolves the account, reuses the cached token and takes the lock on every
request, so this reuses it. The flag is false because nothing here places orders.

**The daily chart endpoint caps how many rows one call returns.** A year-long
request comes back truncated with no error, which would quietly chart a shorter
period than asked for. So ranges are walked backwards in windows until the
requested start is reached, rather than trusting a single call.

Imports of the trading package are deferred: it reads `trading/config/kis_devlp.yaml`
at import time and would make this module — and the whole source chain — fail to
load on a host without KIS credentials.
"""

from __future__ import annotations

import logging
import threading

import pandas as pd

from cores.market_data.schema import has_ohlcv, normalize
from cores.market_data.source import Unavailable, Unsupported

logger = logging.getLogger(__name__)

_MARKET_DOMESTIC = "J"
_MARKET_INDEX = "U"

_DAILY_CHART = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_DAILY_CHART_TR = "FHKST03010100"

_INDEX_CHART = "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice"
_INDEX_CHART_TR = "FHKUP03500100"

_INVESTOR_DAILY = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
_INVESTOR_DAILY_TR = "FHPTJ04160001"

# KIS names every price field the same way across these endpoints.
_PRICE_COLUMNS = {
    "stck_oprc": "Open",
    "stck_hgpr": "High",
    "stck_lwpr": "Low",
    "stck_clpr": "Close",
    "acml_vol": "Volume",
    "acml_tr_pbmn": "Amount",
}

# KRX index codes to KIS 업종코드. These collide in a way that fails silently:
# prism (and pykrx) call KOSPI `1001`, but KIS calls KOSDAQ `1001`. Passing the
# code straight through returned 737.35 for a KOSPI request on 2026-08-03, when
# KOSPI closed at 6257.45 — a plausible number for the wrong index, which is the
# worst kind of wrong. Verified by live call: KIS 0001=KOSPI, 1001=KOSDAQ,
# 2001=KOSPI200.
_INDEX_CODES = {
    "1001": "0001",  # KOSPI
    "2001": "1001",  # KOSDAQ
}

_INDEX_COLUMNS = {
    "bstp_nmix_oprc": "Open",
    "bstp_nmix_hgpr": "High",
    "bstp_nmix_lwpr": "Low",
    "bstp_nmix_prpr": "Close",
    "acml_vol": "Volume",
    "acml_tr_pbmn": "Amount",
}

# Downstream code (cores/stock_chart.py:1142) indexes investor flows by these
# Korean names, so the adapter speaks them rather than making the chart change.
_FLOW_COLUMNS = {
    "orgn_ntby_qty": "기관합계",
    "frgn_ntby_qty": "외국인합계",
    "prsn_ntby_qty": "개인",
    "etc_corp_ntby_vol": "기타법인",
}

_DATE_FIELD = "stck_bsop_date"

# How many windows a single range may take before we stop asking. A daily chart
# call returns on the order of 100 rows, so this covers several years without
# looping forever against an endpoint that has stopped moving backwards.
_MAX_WINDOWS = 40


class KisSource:
    name = "kis"

    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ client

    def _trading(self):
        """The shared, already-authenticated trading client.

        Built once and reused: construction resolves the account and calls
        `ka.auth()`, and doing that per request would be both slow and a way to
        churn the token the trading loops depend on.
        """
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        from trading.domestic_stock_trading import DomesticStockTrading

                        self._client = DomesticStockTrading(auto_trading=False)
                    except Exception as exc:  # missing config, auth failure, import
                        raise Unavailable(f"KIS client unavailable: {exc}") from exc
        return self._client

    def _fetch(self, api_url: str, tr_id: str, params: dict) -> object:
        try:
            response = self._trading()._request(api_url, tr_id, params)
        except Unavailable:
            raise
        except Exception as exc:  # transport, auth, rate limit — all "not now"
            raise Unavailable(f"KIS {tr_id} failed: {exc}") from exc

        if not response or not response.isOK():
            raise Unavailable(
                f"KIS {tr_id} rejected the request: {self._error_detail(response)}"
            )
        return response.getBody()

    @staticmethod
    def _error_detail(response) -> str:
        """KIS's reason for refusing, or why we could not read one.

        Reported rather than swallowed: the difference between a rate limit and
        a bad ticker is the whole diagnosis, and a rejection that says nothing
        is what turned the 2026-08-04 outage into a two-hour hunt.
        """
        getter = getattr(response, "getErrorMessage", None)
        if getter is None:
            return "no error message on the response"
        try:
            return str(getter() or "").strip() or "empty error message"
        except Exception as exc:  # noqa: BLE001 - never mask the original failure
            return f"error message unreadable ({type(exc).__name__}: {exc})"

    # ------------------------------------------------------------------ frames

    @staticmethod
    def _to_frame(rows: list[dict], columns: dict[str, str], what: str) -> pd.DataFrame:
        """Rows to a date-indexed, numeric frame in the shared schema."""
        if not rows:
            raise Unavailable(f"KIS returned no rows for {what}")

        frame = pd.DataFrame(rows)
        if _DATE_FIELD not in frame.columns:
            raise Unavailable(f"KIS {what} response has no {_DATE_FIELD}")

        present = {src: dst for src, dst in columns.items() if src in frame.columns}
        if not present:
            raise Unavailable(f"KIS {what} response has none of the expected fields")

        out = frame[[_DATE_FIELD, *present]].rename(columns=present)
        out.index = pd.to_datetime(out.pop(_DATE_FIELD), format="%Y%m%d")
        # Every value arrives as a string; a blank one is missing, not zero.
        out = out.apply(pd.to_numeric, errors="coerce")
        return normalize(out[~out.index.duplicated(keep="first")])

    def _walk_range(
        self, start: str, end: str, fetch_window
    ) -> list[dict]:
        """Collect rows for [start, end], one window at a time.

        The endpoint truncates long ranges silently. Rather than assume a row
        limit, this keeps asking for what is still missing and stops when a
        window returns nothing new — so it stays correct if the cap changes.
        """
        collected: dict[str, dict] = {}
        cursor = end

        for _ in range(_MAX_WINDOWS):
            rows = fetch_window(start, cursor)
            fresh = {
                str(row[_DATE_FIELD]): row
                for row in rows
                if row.get(_DATE_FIELD) and str(row[_DATE_FIELD]) not in collected
            }
            if not fresh:
                break
            collected.update(fresh)

            earliest = min(fresh)
            if earliest <= start:
                break
            # Step one day back from the earliest row we have, so the next
            # window ends where this one began without re-fetching it.
            cursor = (pd.Timestamp(earliest) - pd.Timedelta(days=1)).strftime("%Y%m%d")
            if cursor < start:
                break

        return [collected[key] for key in sorted(collected)]

    # -------------------------------------------------------------- capabilities

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        def window(window_start: str, window_end: str) -> list[dict]:
            body = self._fetch(
                _DAILY_CHART,
                _DAILY_CHART_TR,
                {
                    "FID_COND_MRKT_DIV_CODE": _MARKET_DOMESTIC,
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": window_start,
                    "FID_INPUT_DATE_2": window_end,
                    "FID_PERIOD_DIV_CODE": "D",
                    # 0 is 수정주가 — split- and issuance-adjusted, which is what
                    # every indicator downstream assumes.
                    "FID_ORG_ADJ_PRC": "0" if adjusted else "1",
                },
            )
            return list(getattr(body, "output2", None) or [])

        rows = self._walk_range(start, end, window)
        frame = self._to_frame(rows, _PRICE_COLUMNS, f"ohlcv {ticker}")
        if not has_ohlcv(frame):
            raise Unavailable(f"KIS ohlcv {ticker} missing OHLCV columns")
        return frame

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        kis_code = _INDEX_CODES.get(str(index_code))
        if kis_code is None:
            # Guessing would return a real number for a different index.
            raise Unsupported(f"no KIS 업종코드 mapped for index {index_code}")

        def window(window_start: str, window_end: str) -> list[dict]:
            body = self._fetch(
                _INDEX_CHART,
                _INDEX_CHART_TR,
                {
                    "FID_COND_MRKT_DIV_CODE": _MARKET_INDEX,
                    "FID_INPUT_ISCD": kis_code,
                    "FID_INPUT_DATE_1": window_start,
                    "FID_INPUT_DATE_2": window_end,
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            return list(getattr(body, "output2", None) or [])

        rows = self._walk_range(start, end, window)
        return self._to_frame(rows, _INDEX_COLUMNS, f"index {index_code}")

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Net buying by investor type — the gap KRX alone used to fill.

        `FID_INPUT_DATE_1` is an as-of date, not a range start: the endpoint
        answers with roughly 30 sessions ending there. Passing the caller's
        `start` therefore returns the month *before* the range and none of it —
        a request for 07-28..08-03 came back 06-22..07-28 in testing. So the
        cursor is the end date, and longer ranges are walked back like prices.
        """
        def window(window_start: str, window_end: str) -> list[dict]:
            body = self._fetch(
                _INVESTOR_DAILY,
                _INVESTOR_DAILY_TR,
                {
                    "FID_COND_MRKT_DIV_CODE": _MARKET_DOMESTIC,
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": window_end,
                    "FID_ORG_ADJ_PRC": "0",
                    "FID_ETC_CLS_CODE": "",
                },
            )
            return list(getattr(body, "output2", None) or [])

        rows = self._walk_range(start, end, window)
        frame = self._to_frame(rows, _FLOW_COLUMNS, f"flows {ticker}")
        return frame[
            (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        ]

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        # The chart endpoint carries 시가총액 only in output1, as one current
        # value — there is no series. Reconstructing it from a constant share
        # count is what FdrSource already does, and doing it here too would just
        # hide which source the approximation came from.
        raise Unsupported("KIS publishes market cap as a current value, not a series")

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        # PER/PBR/EPS/BPS come from inquire-price as today's snapshot only.
        raise Unsupported("KIS publishes PER/PBR as current values, not a series")

    def ticker_name(self, ticker: str) -> str:
        # inquire-price carries no company name. The nearest fields are the
        # market and the sector, and `get_current_price` maps `stock_name` to
        # `rprs_mrkt_kor_name` — so asking it for 005930 answers "KOSPI200",
        # not "삼성전자". Returning that would label every chart with a market
        # name, so this is declared missing and the chain falls through.
        raise Unsupported("KIS inquire-price does not return a company name")
