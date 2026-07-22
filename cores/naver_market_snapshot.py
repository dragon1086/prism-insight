"""Emergency Naver-backed market snapshot for the KR screening batch.

The normal source remains KRX.  This module is deliberately narrow and is
called only after the complete KRX snapshot bundle has failed.  Naver's bulk
market list supplies close/volume/amount/market-cap; the same XML endpoint used
by pykrx supplies regular-session OHLCV for stocks liquid enough to reach the
screening triggers.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

import pandas as pd
import requests


logger = logging.getLogger(__name__)

_BULK_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
_DAILY_URL = "https://fchart.stock.naver.com/sise.nhn"
_MARKETS = ("KOSPI", "KOSDAQ")
_PAGE_SIZE = 100  # Naver rejects larger page sizes.
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com/",
}
_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Amount"]


class NaverSnapshotError(RuntimeError):
    """The Naver fallback could not produce a safe screening snapshot."""


@dataclass(frozen=True)
class MarketSnapshotBundle:
    """All market-wide inputs consumed by ``trigger_batch.run_batch``."""

    snapshot: pd.DataFrame
    prev_snapshot: pd.DataFrame
    cap_df: pd.DataFrame
    prev_date: str
    source: str


def _as_int(value) -> int:
    if value in (None, "", "N/A"):
        return 0
    return int(str(value).replace(",", ""))


def _request_json(
    request_get: Callable,
    url: str,
    *,
    params: dict,
    timeout: float,
    max_attempts: int,
    retry_wait_sec: float,
) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_get(
                url,
                params=params,
                headers=_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("response is not a JSON object")
            return payload
        except Exception as exc:  # network, status, decode, or schema failure
            last_exc = exc
            if attempt < max_attempts and retry_wait_sec > 0:
                time.sleep(retry_wait_sec * attempt)
    raise NaverSnapshotError(f"Naver request failed after {max_attempts} attempts: {last_exc}")


def _fetch_bulk_rows(
    request_get: Callable,
    *,
    timeout: float,
    max_attempts: int,
    retry_wait_sec: float,
    max_workers: int,
) -> list[dict]:
    pages: list[tuple[str, int, dict | None]] = []
    expected_counts: dict[str, int] = {}

    for market in _MARKETS:
        url = _BULK_URL.format(market=market)
        first = _request_json(
            request_get,
            url,
            params={"page": 1, "pageSize": _PAGE_SIZE},
            timeout=timeout,
            max_attempts=max_attempts,
            retry_wait_sec=retry_wait_sec,
        )
        total_count = _as_int(first.get("totalCount"))
        first_rows = first.get("stocks")
        if total_count <= 0 or not isinstance(first_rows, list):
            raise NaverSnapshotError(f"Naver bulk schema invalid for {market}")
        expected_counts[market] = total_count
        pages.append((market, 1, first))
        for page in range(2, math.ceil(total_count / _PAGE_SIZE) + 1):
            pages.append((market, page, None))

    fetched: dict[tuple[str, int], dict] = {
        (market, page): payload
        for market, page, payload in pages
        if payload is not None
    }
    pending = [(market, page) for market, page, payload in pages if payload is None]

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(pending)))) as pool:
            futures = {
                pool.submit(
                    _request_json,
                    request_get,
                    _BULK_URL.format(market=market),
                    params={"page": page, "pageSize": _PAGE_SIZE},
                    timeout=timeout,
                    max_attempts=max_attempts,
                    retry_wait_sec=retry_wait_sec,
                ): (market, page)
                for market, page in pending
            }
            for future in as_completed(futures):
                fetched[futures[future]] = future.result()

    all_rows: list[dict] = []
    for market in _MARKETS:
        market_rows: list[dict] = []
        page_numbers = sorted(page for mkt, page in fetched if mkt == market)
        for page in page_numbers:
            rows = fetched[(market, page)].get("stocks")
            if not isinstance(rows, list):
                raise NaverSnapshotError(
                    f"Naver bulk schema invalid for {market} page {page}"
                )
            market_rows.extend(rows)

        expected = expected_counts[market]
        if len(market_rows) < expected * 0.98:
            raise NaverSnapshotError(
                f"Naver bulk coverage too low for {market}: {len(market_rows)}/{expected}"
            )
        all_rows.extend(market_rows)

    return all_rows


def _parse_daily_xml(content: bytes, trade_date: str) -> tuple[dict, dict]:
    # Naver declares EUC-KR, which Python's bundled expat parser rejects as a
    # multi-byte encoding. Decode explicitly and extract only the inert data
    # attribute. Do not invoke an XML parser on this untrusted network input;
    # the endpoint's external entities and declarations must never be resolved.
    text = content.decode("euc-kr", errors="replace")
    rows: dict[str, dict[str, int]] = {}
    for match in re.finditer(r'<item\s+data="([^"]*)"\s*/?>', text):
        raw = match.group(1)
        fields = raw.split("|")
        if len(fields) != 6 or not re.fullmatch(r"\d{8}", fields[0]):
            continue
        date, open_, high, low, close, volume = fields
        rows[date] = {
            "Open": _as_int(open_),
            "High": _as_int(high),
            "Low": _as_int(low),
            "Close": _as_int(close),
            "Volume": _as_int(volume),
        }

    current = rows.get(trade_date)
    previous_dates = sorted(date for date in rows if date < trade_date)
    if current is None or not previous_dates:
        raise ValueError(f"missing current/previous rows for {trade_date}")

    previous = rows[previous_dates[-1]].copy()
    previous["Date"] = previous_dates[-1]

    if min(current["Open"], current["High"], current["Low"], current["Close"]) <= 0:
        raise ValueError("non-positive OHLC")
    if current["High"] < max(current["Open"], current["Close"]):
        raise ValueError("high price invariant failed")
    if current["Low"] > min(current["Open"], current["Close"]):
        raise ValueError("low price invariant failed")
    if current["Volume"] < 0 or previous["Volume"] < 0:
        raise ValueError("negative volume")

    return current, previous


def _fetch_daily_pair(
    request_get: Callable,
    ticker: str,
    trade_date: str,
    *,
    timeout: float,
    max_attempts: int,
    retry_wait_sec: float,
) -> tuple[dict, dict]:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = request_get(
                _DAILY_URL,
                params={
                    "symbol": ticker,
                    "timeframe": "day",
                    "count": 10,
                    "requestType": "0",
                },
                headers=_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            return _parse_daily_xml(response.content, trade_date)
        except Exception as exc:  # transport, parse, or data invariant
            last_exc = exc
            if attempt < max_attempts and retry_wait_sec > 0:
                time.sleep(retry_wait_sec * attempt)
    raise NaverSnapshotError(f"{ticker} detail failed: {last_exc}")


def fetch_naver_snapshot_bundle(
    trade_date: str,
    *,
    request_get: Callable = requests.get,
    min_stock_count: int = 2500,
    detail_min_amount: int = 10_000_000_000,
    min_detail_coverage: float = 0.98,
    max_workers: int = 5,
    timeout: float = 10.0,
    max_attempts: int = 3,
    retry_wait_sec: float = 0.5,
) -> MarketSnapshotBundle:
    """Build a KRX-compatible current/previous/cap bundle from Naver.

    Detailed OHLCV is fetched only for rows that can pass the screening
    pipeline's 10B KRW minimum amount gate.  All rows remain in the base frames
    so market-average volume calculations preserve their original universe.
    """
    if not re.fullmatch(r"\d{8}", trade_date):
        raise ValueError("trade_date must be YYYYMMDD")
    if not 0 < min_detail_coverage <= 1:
        raise ValueError("min_detail_coverage must be in (0, 1]")

    bulk_rows = _fetch_bulk_rows(
        request_get,
        timeout=timeout,
        max_attempts=max_attempts,
        retry_wait_sec=retry_wait_sec,
        max_workers=max_workers,
    )

    normalized: dict[str, dict] = {}
    bulk_dates: list[str] = []
    for row in bulk_rows:
        code = str(row.get("itemCode", ""))
        if row.get("stockEndType") != "stock" or not re.fullmatch(r"\d{6}", code):
            continue

        # Display fields use mixed units (notably trading value/market cap are
        # shown in KRW millions).  Never guess units when the raw fields drift.
        required_raw = (
            "closePriceRaw",
            "compareToPreviousClosePriceRaw",
            "accumulatedTradingVolumeRaw",
            "accumulatedTradingValueRaw",
            "marketValueRaw",
        )
        if any(row.get(field) in (None, "") for field in required_raw):
            continue

        close = _as_int(row["closePriceRaw"])
        volume = _as_int(row["accumulatedTradingVolumeRaw"])
        amount = _as_int(row["accumulatedTradingValueRaw"])
        market_cap = _as_int(row["marketValueRaw"])
        change = _as_int(row["compareToPreviousClosePriceRaw"])
        traded_at = str(row.get("localTradedAt", ""))
        if len(traded_at) >= 10:
            bulk_dates.append(traded_at[:10].replace("-", ""))
        if close <= 0:
            continue

        normalized[code] = {
            "Close": close,
            "Volume": max(volume, 0),
            "Amount": max(amount, 0),
            "MarketCap": max(market_cap, 0),
            "PrevClose": close - change,
        }

    if len(normalized) < min_stock_count:
        raise NaverSnapshotError(
            f"Naver stock coverage too low: {len(normalized)}/{min_stock_count}"
        )
    if len(bulk_dates) < min_stock_count * 0.95:
        raise NaverSnapshotError(
            f"Naver timestamp coverage too low: {len(bulk_dates)}/{min_stock_count}"
        )
    if bulk_dates.count(trade_date) / len(bulk_dates) < 0.95:
        observed = Counter(bulk_dates).most_common(1)
        raise NaverSnapshotError(
            f"Naver bulk data is stale for {trade_date}; observed={observed}"
        )

    index = sorted(normalized)
    snapshot = pd.DataFrame(index=index, columns=_REQUIRED_COLUMNS, dtype=float)
    prev_snapshot = pd.DataFrame(index=index, columns=_REQUIRED_COLUMNS, dtype=float)
    cap_df = pd.DataFrame(index=index, columns=["시가총액"], dtype=float)

    for code in index:
        row = normalized[code]
        snapshot.loc[code, ["Close", "Volume", "Amount"]] = [
            row["Close"],
            row["Volume"],
            row["Amount"],
        ]
        prev_snapshot.loc[code, "Close"] = row["PrevClose"]
        cap_df.loc[code, "시가총액"] = row["MarketCap"]

    detail_codes = [
        code for code in index if normalized[code]["Amount"] >= detail_min_amount
    ]
    detail_results: dict[str, tuple[dict, dict]] = {}
    detail_failures: dict[str, str] = {}

    if detail_codes:
        with ThreadPoolExecutor(
            max_workers=max(1, min(max_workers, len(detail_codes)))
        ) as pool:
            futures = {
                pool.submit(
                    _fetch_daily_pair,
                    request_get,
                    code,
                    trade_date,
                    timeout=timeout,
                    max_attempts=max_attempts,
                    retry_wait_sec=retry_wait_sec,
                ): code
                for code in detail_codes
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    detail_results[code] = future.result()
                except Exception as exc:
                    detail_failures[code] = str(exc)

    coverage = len(detail_results) / len(detail_codes) if detail_codes else 1.0
    if coverage < min_detail_coverage:
        raise NaverSnapshotError(
            "Naver detail coverage too low: "
            f"{len(detail_results)}/{len(detail_codes)} ({coverage:.1%}); "
            f"failures={list(detail_failures)[:10]}"
        )

    previous_dates: list[str] = []
    for code, (current, previous) in detail_results.items():
        snapshot.loc[code, ["Open", "High", "Low", "Close", "Volume"]] = [
            current["Open"],
            current["High"],
            current["Low"],
            current["Close"],
            current["Volume"],
        ]
        prev_snapshot.loc[code, ["Open", "High", "Low", "Close", "Volume"]] = [
            previous["Open"],
            previous["High"],
            previous["Low"],
            previous["Close"],
            previous["Volume"],
        ]
        previous_dates.append(previous["Date"])

    # A failed detail row must never leak through the amount gate with NaN OHLC.
    for code in detail_failures:
        snapshot.loc[code, "Amount"] = 0

    if previous_dates:
        prev_date, prev_count = Counter(previous_dates).most_common(1)[0]
        if prev_count / len(previous_dates) < min_detail_coverage:
            raise NaverSnapshotError(
                f"Naver previous-date coverage too low: {prev_count}/{len(previous_dates)}"
            )
    else:
        prev_date = (dt.datetime.strptime(trade_date, "%Y%m%d") - dt.timedelta(days=1)).strftime(
            "%Y%m%d"
        )

    logger.warning(
        "[NAVER-SNAPSHOT-FALLBACK] stocks=%d detail=%d/%d prev_date=%s failures=%d",
        len(snapshot),
        len(detail_results),
        len(detail_codes),
        prev_date,
        len(detail_failures),
    )
    return MarketSnapshotBundle(
        snapshot=snapshot,
        prev_snapshot=prev_snapshot,
        cap_df=cap_df,
        prev_date=prev_date,
        source="naver",
    )
