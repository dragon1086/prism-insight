"""Market snapshot from the KRX Data Marketplace OPEN API — the sanctioned route.

On 2026-08-04 KRX restricted the server's IP for "자동화 수단을 통한 비정상 대량
조회" and cited 이용약관 제10조 제2호, which forbids automated collection. The same
notice named `openapi.krx.co.kr` as the official alternative. This module is that
alternative: an authenticated, contracted API instead of a scraped session.

It produces the same ``MarketSnapshotBundle`` the screening batch already consumes,
so it slots in beside the KRX-scraping and Naver paths without changing callers.

What this source can and cannot do — verified by live calls on 2026-08-04:

- **Full universe, one call per market.** KOSPI 943 + KOSDAQ 1,820 rows; filtering
  to six-digit codes leaves exactly 2,686 — the same count the Naver fallback
  produced during the outage. No ETFs or ETNs are mixed in.
- **Every field the triggers need**: open/high/low/close, volume, traded value and
  market capitalisation all arrive in the one response, so ``cap_df`` needs no
  second request and cannot disagree with the prices.
- **End-of-day only.** ``basDd`` for the current session returned zero rows at
  23:44 KST on the same day, so this cannot serve the intraday 09:30 and 14:46
  screening snapshots. It is a previous-day and historical source.
- **One date per call.** There is no range parameter; ``strtDd``/``endDd`` are
  ignored and return nothing. Time series would cost one request per day, so the
  chart path stays with a source that supports ranges.
- **No investor flows.** The 주식 category offers daily trading and issue master
  data only; 투자자별 거래실적 is not among the published services.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable

import pandas as pd
import requests

from cores.naver_market_snapshot import MarketSnapshotBundle

logger = logging.getLogger(__name__)

_BASE_URL = "https://data-dbg.krx.co.kr/svc/apis/sto"
_ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
_REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume", "Amount"]

# The response names its payload consistently across services.
_PAYLOAD_KEY = "OutBlock_1"

# KRX ships preferred shares and SPACs in the same feed; both carry six-digit
# codes and both are present in the universe the screening triggers have always
# seen, so the filter is deliberately only "is this a stock code".
_TICKER_RE = re.compile(r"\d{6}")

_FIELD_MAP = {
    "Open": "TDD_OPNPRC",
    "High": "TDD_HGPRC",
    "Low": "TDD_LWPRC",
    "Close": "TDD_CLSPRC",
    "Volume": "ACC_TRDVOL",
    "Amount": "ACC_TRDVAL",
}


class KrxOpenApiError(RuntimeError):
    """The OPEN API could not produce a usable snapshot."""


def _auth_key(explicit: str | None) -> str:
    key = explicit or os.getenv("KRX_OPENAPI_AUTH_KEY", "")
    if not key:
        raise KrxOpenApiError(
            "KRX_OPENAPI_AUTH_KEY is not set; apply at https://openapi.krx.co.kr"
        )
    return key


def _as_number(value) -> float:
    """Parse a KRX numeric string.

    Fields arrive as strings and a suspended or newly listed issue can carry an
    empty one. Returning NaN rather than 0 matters: zero would read as a real
    price of zero and pass filters that a missing value should not.
    """
    if value in (None, ""):
        return float("nan")
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return float("nan")


def _fetch_market(
    market: str,
    trade_date: str,
    auth_key: str,
    request_get: Callable,
    *,
    timeout: float,
    max_attempts: int,
    retry_wait_sec: float,
) -> list[dict]:
    url = f"{_BASE_URL}/{_ENDPOINTS[market]}"
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = request_get(
                url,
                params={"basDd": trade_date},
                headers={"AUTH_KEY": auth_key},
                timeout=timeout,
            )
            if response.status_code == 401:
                # Distinct from a transient failure: the key is valid but this
                # service was never approved for it, and retrying cannot help.
                raise KrxOpenApiError(
                    f"{market} 일별매매정보 is not authorised for this key "
                    "(apply for the service at openapi.krx.co.kr)"
                )
            response.raise_for_status()
            payload = response.json()
        except KrxOpenApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried below, re-raised if final
            last_error = exc
            if attempt < max_attempts:
                logger.warning(
                    "KRX OPEN API %s %s failed (attempt %d/%d): %s",
                    market,
                    trade_date,
                    attempt,
                    max_attempts,
                    exc,
                )
                time.sleep(retry_wait_sec * attempt)
                continue
            raise KrxOpenApiError(
                f"{market} {trade_date} request failed after {max_attempts} attempts: {exc}"
            ) from exc

        rows = payload.get(_PAYLOAD_KEY)
        if rows is None:
            raise KrxOpenApiError(
                f"{market} {trade_date}: unexpected response shape {list(payload)}"
            )
        return rows

    raise KrxOpenApiError(f"{market} {trade_date} failed: {last_error}")


def _rows_for_date(
    trade_date: str,
    auth_key: str,
    request_get: Callable,
    **kwargs,
) -> list[dict]:
    """Both markets for one date, keyed to stocks only."""
    rows: list[dict] = []
    for market in _ENDPOINTS:
        rows.extend(
            _fetch_market(market, trade_date, auth_key, request_get, **kwargs)
        )

    kept = [row for row in rows if _TICKER_RE.fullmatch(str(row.get("ISU_CD", "")))]

    # A date the exchange did not trade returns an empty list rather than an
    # error, and so does a session whose data has not been published yet. The
    # caller has to tell those apart from a real failure, so both surface as
    # "no rows" and the decision is made there.
    stale = {row.get("BAS_DD") for row in kept} - {trade_date}
    if stale:
        raise KrxOpenApiError(
            f"{trade_date}: response carried other dates {sorted(stale)}"
        )
    return kept


def _frames(rows: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split rows into an OHLCV frame and a market-cap frame on one index."""
    index = sorted(str(row["ISU_CD"]) for row in rows)
    by_code = {str(row["ISU_CD"]): row for row in rows}

    ohlcv = pd.DataFrame(
        [
            {name: _as_number(by_code[code][field]) for name, field in _FIELD_MAP.items()}
            for code in index
        ],
        index=index,
        columns=_REQUIRED_COLUMNS,
        dtype=float,
    )
    cap = pd.DataFrame(
        {"시가총액": [_as_number(by_code[code].get("MKTCAP")) for code in index]},
        index=index,
        dtype=float,
    )
    return ohlcv, cap


def fetch_krx_openapi_bundle(
    trade_date: str,
    *,
    auth_key: str | None = None,
    request_get: Callable = requests.get,
    min_stock_count: int = 2500,
    timeout: float = 20.0,
    max_attempts: int = 3,
    retry_wait_sec: float = 0.5,
    max_lookback_days: int = 10,
) -> MarketSnapshotBundle:
    """Build the screening bundle for ``trade_date`` from the OPEN API.

    ``trade_date`` must be a session whose data has already been published —
    same-day data is not available (see the module docstring), so the live
    morning and afternoon batches cannot call this for the current session. It
    serves previous-day, historical and backtest requests.

    Raises ``KrxOpenApiError`` rather than returning a thin frame: a partial
    universe silently changes what every trigger scores, which is the failure
    mode that made the 2026-08-04 outage take two hours to recognise.
    """
    if not re.fullmatch(r"\d{8}", trade_date):
        raise ValueError("trade_date must be YYYYMMDD")

    key = _auth_key(auth_key)
    request_kwargs = dict(
        timeout=timeout, max_attempts=max_attempts, retry_wait_sec=retry_wait_sec
    )

    rows = _rows_for_date(trade_date, key, request_get, **request_kwargs)
    if not rows:
        raise KrxOpenApiError(
            f"{trade_date}: no rows. Either not a trading day, or end-of-day data "
            "is not published yet (this API does not serve the current session)."
        )
    if len(rows) < min_stock_count:
        raise KrxOpenApiError(
            f"{trade_date}: universe too small ({len(rows)}/{min_stock_count})"
        )

    prev_date, prev_rows = _previous_session(
        trade_date, key, request_get, max_lookback_days, request_kwargs
    )
    if len(prev_rows) < min_stock_count:
        raise KrxOpenApiError(
            f"{prev_date}: previous-session universe too small "
            f"({len(prev_rows)}/{min_stock_count})"
        )

    snapshot, cap_df = _frames(rows)
    prev_snapshot, _ = _frames(prev_rows)

    logger.info(
        "[MARKET-DATA] source=KRX_OPENAPI date=%s stocks=%d prev_date=%s prev_stocks=%d",
        trade_date,
        len(snapshot),
        prev_date,
        len(prev_snapshot),
    )

    return MarketSnapshotBundle(
        snapshot=snapshot,
        prev_snapshot=prev_snapshot,
        cap_df=cap_df,
        prev_date=prev_date,
        source="krx_openapi",
    )


def _previous_session(
    trade_date: str,
    auth_key: str,
    request_get: Callable,
    max_lookback_days: int,
    request_kwargs: dict,
) -> tuple[str, list[dict]]:
    """Walk back day by day until a session with data appears.

    The API has no calendar endpoint, and an empty response is how both weekends
    and holidays present, so the previous session is found by asking rather than
    by computing it — which also keeps this correct across holiday changes.
    """
    day = pd.Timestamp(trade_date)
    for _ in range(max_lookback_days):
        day -= pd.Timedelta(days=1)
        candidate = day.strftime("%Y%m%d")
        rows = _rows_for_date(candidate, auth_key, request_get, **request_kwargs)
        if rows:
            return candidate, rows

    raise KrxOpenApiError(
        f"no session with data in the {max_lookback_days} days before {trade_date}"
    )
