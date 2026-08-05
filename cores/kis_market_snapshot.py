"""Intraday all-stock snapshot from KIS's 30-stock quote endpoint."""

from __future__ import annotations

from io import BytesIO
import time
from typing import Iterable
import zipfile

import pandas as pd
import requests

from cores.naver_market_snapshot import MarketSnapshotBundle


_URL = "/uapi/domestic-stock/v1/quotations/intstock-multprice"
_TR_ID = "FHKST11300006"
_CHUNK_SIZE = 30
_KOSPI_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1,
    1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21,
    2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
_KOSDAQ_WIDTHS = [
    2, 1, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 9, 5, 5, 1, 1, 1, 2, 1, 1, 1,
    2, 2, 2, 3, 1, 3, 12, 12, 8, 15, 21, 2, 7, 1, 1, 1,
    1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1,
]
_MASTER_SPECS = (
    ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", _KOSPI_WIDTHS, 12),
    ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", _KOSDAQ_WIDTHS, 8),
)
_COLUMNS = {
    "inter2_oprc": "Open",
    "inter2_hgpr": "High",
    "inter2_lwpr": "Low",
    "inter2_prpr": "Close",
    "acml_vol": "Volume",
    "acml_tr_pbmn": "Amount",
}


class KisSnapshotError(RuntimeError):
    """KIS could not return a complete, schema-valid intraday universe."""


def fetch_kis_master_universe(
    *, request_get=requests.get, timeout: float = 30.0, min_stock_count: int = 2500
) -> dict[str, str]:
    """Download today's official KIS KOSPI/KOSDAQ master and return stocks."""
    universe: dict[str, str] = {}
    try:
        for url, widths, etp_index in _MASTER_SPECS:
            response = request_get(url, timeout=timeout)
            response.raise_for_status()
            with zipfile.ZipFile(BytesIO(response.content)) as archive:
                names = archive.namelist()
                if not names:
                    raise KisSnapshotError(f"KIS master archive is empty: {url}")
                content = archive.read(names[0])
            tail_size = sum(widths)
            etp_offset = sum(widths[:etp_index])
            etp_width = widths[etp_index]
            for line in content.splitlines():
                if len(line) < 61:
                    continue
                code = line[0:9].decode("euc-kr", errors="ignore").strip()
                name = line[21:61].decode("euc-kr", errors="ignore").strip()
                tail = line[-tail_size:]
                etp = tail[etp_offset : etp_offset + etp_width].decode(
                    "ascii", errors="ignore"
                ).strip()
                if len(code) == 6 and code.isdigit() and name and etp != "2":
                    universe[code] = name
    except KisSnapshotError:
        raise
    except Exception as exc:
        raise KisSnapshotError(f"KIS master download failed: {exc}") from exc

    if len(universe) < min_stock_count:
        raise KisSnapshotError(
            f"KIS master universe too small ({len(universe)}/{min_stock_count})"
        )
    return dict(sorted(universe.items()))


def _trading_client():
    from trading.domestic_stock_trading import DomesticStockTrading

    return DomesticStockTrading(auto_trading=False)


def _params(codes: list[str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for position, code in enumerate(codes, 1):
        params[f"FID_COND_MRKT_DIV_CODE_{position}"] = "J"
        params[f"FID_INPUT_ISCD_{position}"] = code
    return params


def fetch_kis_intraday_snapshot(
    tickers: Iterable[str],
    *,
    client=None,
    min_stock_count: int = 2500,
    max_attempts: int = 3,
    retry_wait_sec: float = 1.0,
    request_interval_sec: float = 0.1,
) -> pd.DataFrame:
    """Return a complete OHLCV/amount snapshot, 30 tickers per KIS call."""
    codes = sorted({str(code).strip().zfill(6) for code in tickers})
    if len(codes) < min_stock_count:
        raise KisSnapshotError(
            f"KIS requested universe too small ({len(codes)}/{min_stock_count})"
        )

    trading = client or _trading_client()
    rows: dict[str, dict] = {}

    for offset in range(0, len(codes), _CHUNK_SIZE):
        chunk = codes[offset : offset + _CHUNK_SIZE]
        last_reason = "no response"
        for attempt in range(1, max_attempts + 1):
            response = trading._request(_URL, _TR_ID, _params(chunk))
            if response and response.isOK():
                for row in list(getattr(response.getBody(), "output", None) or []):
                    code = str(row.get("inter_shrn_iscd", "")).strip().zfill(6)
                    if code:
                        rows[code] = dict(row)
                break
            last_reason = (
                str(response.getErrorMessage()) if response else "no response"
            )
            if attempt < max_attempts and retry_wait_sec:
                time.sleep(retry_wait_sec * attempt)
        else:
            raise KisSnapshotError(
                f"KIS multi-price chunk {offset // _CHUNK_SIZE + 1} failed: {last_reason}"
            )
        if request_interval_sec:
            time.sleep(request_interval_sec)

    missing = sorted(set(codes) - set(rows))
    if missing:
        raise KisSnapshotError(
            f"KIS multi-price response missing {len(missing)} tickers; sample={missing[:10]}"
        )

    frame = pd.DataFrame.from_dict(rows, orient="index")
    absent = set(_COLUMNS) - set(frame.columns)
    if absent:
        raise KisSnapshotError(f"KIS multi-price missing fields: {sorted(absent)}")

    out = frame[list(_COLUMNS)].rename(columns=_COLUMNS)
    out = out.apply(pd.to_numeric, errors="coerce").sort_index()
    if out[list(_COLUMNS.values())].isna().all(axis=1).any():
        raise KisSnapshotError("KIS multi-price returned rows with no numeric snapshot data")
    return out


def build_kis_openapi_snapshot_bundle(
    trade_date: str,
    *,
    universe_fetcher=fetch_kis_master_universe,
    snapshot_fetcher=fetch_kis_intraday_snapshot,
    previous_fetcher=None,
) -> MarketSnapshotBundle:
    """Combine today's official KIS quotes with the previous OPEN API session."""
    if previous_fetcher is None:
        from cores.krx_openapi_snapshot import fetch_previous_krx_openapi_snapshot

        previous_fetcher = fetch_previous_krx_openapi_snapshot

    universe = universe_fetcher()
    snapshot = snapshot_fetcher(universe.keys())
    previous = previous_fetcher(trade_date)
    return MarketSnapshotBundle(
        snapshot=snapshot,
        prev_snapshot=previous.snapshot,
        cap_df=previous.cap_df,
        prev_date=previous.trade_date,
        source="kis+krx_openapi",
    )
