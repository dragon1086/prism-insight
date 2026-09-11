"""Intraday all-stock snapshot from KIS's 30-stock quote endpoint."""

from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import math
import os
import struct
import tempfile
import time
from typing import Iterable
import zipfile

import pandas as pd
import requests


@dataclass(frozen=True)
class MarketSnapshotBundle:
    snapshot: pd.DataFrame
    prev_snapshot: pd.DataFrame
    cap_df: pd.DataFrame
    prev_date: str
    source: str


@dataclass(frozen=True)
class KisMasterData:
    names: dict[str, str]
    cap_df: pd.DataFrame
    listed_dates: dict[str, str]
    observed_date: str
    markets: dict[str, str]
    industry_codes: dict[str, str]
    previous_volumes: dict[str, float]


_MASTER_CACHE: KisMasterData | None = None


def _today_kst() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")


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
    """Return KIS stock names, sharing the same day's master download."""
    return dict(fetch_kis_master_data(
        request_get=request_get, timeout=timeout, min_stock_count=min_stock_count,
    ).names)


def fetch_kis_master_data(
    *, request_get=requests.get, timeout: float = 30.0, min_stock_count: int = 2500,
) -> KisMasterData:
    """Official KIS master: names and previous-session cap (100 million KRW).

    Field definitions: KIS stocks_info/종목마스터정보(코스피).h and
    종목마스터정보(코스닥).h, prdy_avls_scal. This is not current market cap.
    """
    global _MASTER_CACHE
    today = _today_kst()
    if request_get is requests.get and _MASTER_CACHE is not None:
        if _MASTER_CACHE.observed_date == today and len(_MASTER_CACHE.names) >= min_stock_count:
            return _MASTER_CACHE
    universe: dict[str, str] = {}
    caps: dict[str, float] = {}
    listings: dict[str, str] = {}
    markets: dict[str, str] = {}
    industry_codes: dict[str, str] = {}
    previous_volumes: dict[str, float] = {}
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
                    def field(index):
                        offset = sum(widths[:index])
                        return tail[offset:offset + widths[index]].decode("ascii", errors="ignore").strip()
                    cap_index, listing_index = (65, 49) if etp_index == 12 else (59, 44)
                    cap = pd.to_numeric(field(cap_index), errors="coerce")
                    caps[code] = float(cap) * 100_000_000
                    listings[code] = field(listing_index)
                    markets[code] = "KOSPI" if etp_index == 12 else "KOSDAQ"
                    # Official index-industry small -> medium -> large.
                    # The caller resolves the exact code using KIS idxcode.mst;
                    # a missing code remains unknown, never a guessed sector.
                    industry_codes[code] = next(
                        (field(index) for index in (4, 3, 2)
                         if field(index).isdigit() and int(field(index)) > 0),
                        "",
                    )
                    previous_volumes[code] = float(pd.to_numeric(field(47 if etp_index == 12 else 42), errors="coerce"))
    except KisSnapshotError:
        raise
    except Exception as exc:
        raise KisSnapshotError(f"KIS master download failed: {exc}") from exc

    if len(universe) < min_stock_count:
        raise KisSnapshotError(
            f"KIS master universe too small ({len(universe)}/{min_stock_count})"
        )
    cap_df = pd.DataFrame.from_dict(caps, orient="index", columns=["시가총액"]).sort_index()
    cap_df.attrs.update(source="kis_master", observed_date=today, basis="previous_session", unit="KRW", precision_krw=100_000_000)
    result = KisMasterData(dict(sorted(universe.items())), cap_df, listings, today, markets, industry_codes, previous_volumes)
    if request_get is requests.get:
        _MASTER_CACHE = result
    return result


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
    if out.isna().any().any() or not out.map(math.isfinite).all().all() or (out < 0).any().any():
        raise KisSnapshotError("KIS multi-price returned incomplete or invalid numeric snapshot data")
    return out


def previous_session(trade_date: str) -> str:
    """Local calendar only. No web login and no guessed fallback session."""
    from check_market_day import is_market_day

    day = datetime.strptime(trade_date, "%Y%m%d").date()
    for _ in range(20):
        day -= timedelta(days=1)
        if is_market_day(day):
            return day.strftime("%Y%m%d")
    raise KisSnapshotError(f"No previous session found before {trade_date}")


def _valid_history_row(row) -> bool:
    if not isinstance(row, dict) or set(row) != set(_COLUMNS.values()):
        return False
    try:
        values = {key: float(value) for key, value in row.items()}
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) and value >= 0 for value in values.values()):
        return False
    if all(value == 0 for value in values.values()):
        return True  # Preserve an explicitly dated zero-activity provider row.
    if values["Close"] <= 0:
        return False
    if values["Volume"] == 0 and values["Open"] == values["High"] == values["Low"] == 0:
        return True  # Suspended session: zero OHLC is actual KIS data, not fabricated.
    return (0 < values["Low"] <= min(values["Open"], values["Close"])
            <= max(values["Open"], values["Close"]) <= values["High"])


def _master_volume_match_kind(master_volume, daily_volume) -> str | None:
    """Compare exact counts, with the observed KIS master binary32 rendering.

    On 2026-09-11 six raw 12-character master volumes were exactly the
    IEEE-754 binary32 rounding of the corresponding integer daily volumes.
    This is observed compatibility, not a documented provider guarantee and
    not a percentage tolerance. Never round or replace the actual daily bars.
    """
    if isinstance(master_volume, bool) or isinstance(daily_volume, bool):
        return None
    try:
        master, daily = float(master_volume), float(daily_volume)
    except (ValueError, TypeError):
        return None
    # Official master field is 12 decimal characters. Integers in this range
    # are represented exactly by Python binary64, before the explicit cast.
    if not all(math.isfinite(value) and value.is_integer() and 0 <= value <= 999_999_999_999
               for value in (master, daily)):
        return None
    if master == daily:
        return "exact"
    rounded = float(struct.unpack("!f", struct.pack("!f", daily))[0])
    return "float32_master_observed" if master == rounded else None


def fetch_kis_previous_history(
    tickers: Iterable[str], previous_date: str, *, source=None, cache_dir=None,
    request_interval_sec: float = 0.12, max_duration_sec: float = 900,
) -> pd.DataFrame:
    """Complete dated OHLCV; one bounded sequential KIS call per cold ticker.

    Verified per-ticker cache is shared across AM/PM, and partial successful
    rows survive a failed run. Never accept a missing date, fake bars, or a
    previous provider's cache. Cold 2,685 symbols cost at least ~322s plus I/O.
    """
    from cores.market_data.kis_source import KisSource

    datetime.strptime(previous_date, "%Y%m%d")
    codes = sorted(set(tickers))
    if not codes:
        raise KisSnapshotError("KIS history requested universe is empty")
    directory = Path(cache_dir) if cache_dir is not None else Path(__file__).resolve().parents[1] / "runtime" / "kis_previous_history_v1"
    directory = directory / previous_date
    directory.mkdir(parents=True, exist_ok=True)
    history_source = source or KisSource()
    started = time.monotonic()
    rows = {}
    missing = []
    failures = 0
    for code in codes:
        if len(code) != 6 or not code.isdigit():
            raise KisSnapshotError("Invalid history ticker")
        path = directory / f"{code}.json"
        try:
            saved = json.loads(path.read_text())
            if (saved.get("schema") == 1 and saved.get("source") == "kis"
                    and saved.get("date") == previous_date and saved.get("ticker") == code
                    and saved.get("adjusted") is False and _valid_history_row(saved.get("row"))):
                rows[code] = saved["row"]
                continue
        except (OSError, ValueError, AttributeError):
            pass
        if time.monotonic() - started > max_duration_sec:
            raise KisSnapshotError(f"KIS history deadline; coverage={len(rows)}/{len(codes)}")
        try:
            frame = history_source.price_history(code, previous_date, previous_date, adjusted=False)
            # This is an acceptance deadline, not cancellation of a blocking
            # transport request. A late response must not enter the cache.
            if time.monotonic() - started > max_duration_sec:
                raise KisSnapshotError(f"KIS history deadline after response; coverage={len(rows)}/{len(codes)}")
            exact = frame.loc[frame.index == pd.Timestamp(previous_date)]
            if len(exact) != 1:
                raise ValueError("exact session missing or duplicated")
            row = {column: float(exact.iloc[0][column]) for column in _COLUMNS.values()}
            if not _valid_history_row(row):
                raise ValueError("incomplete or invalid OHLCV/amount")
            payload = {"schema": 1, "source": "kis", "date": previous_date,
                       "ticker": code, "adjusted": False, "row": row}
            with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, allow_nan=False)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            rows[code] = row
            failures = 0
        except KisSnapshotError:
            raise
        except Exception:
            missing.append(code)
            failures += 1
            if failures >= 5:
                raise KisSnapshotError(f"KIS history repeated failure; coverage={len(rows)}/{len(codes)}") from None
        finally:
            if request_interval_sec:
                time.sleep(request_interval_sec)
    if missing or set(rows) != set(codes):
        raise KisSnapshotError(f"KIS history incomplete coverage={len(rows)}/{len(codes)}; missing={missing[:10]}")
    if time.monotonic() - started > max_duration_sec:
        raise KisSnapshotError(f"KIS history deadline before acceptance; coverage={len(rows)}/{len(codes)}")
    result = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=list(_COLUMNS.values())).astype(float).sort_index()
    result.attrs.update(source="kis", trade_date=previous_date, adjusted=False, coverage="complete")
    return result


def build_kis_snapshot_bundle(
    trade_date: str, *, master_fetcher=fetch_kis_master_data,
    snapshot_fetcher=fetch_kis_intraday_snapshot, history_fetcher=fetch_kis_previous_history,
) -> MarketSnapshotBundle:
    """All inputs from KIS. Current master cap is explicitly previous-session cap."""
    if trade_date != _today_kst():
        raise KisSnapshotError("Current KIS quotes/master cannot serve a historical trade_date")
    master = master_fetcher()
    if master.observed_date != trade_date:
        raise KisSnapshotError("Stale KIS master")
    prev_date = previous_session(trade_date)
    # IPOs without a previous session cannot participate in existing two-day
    # triggers. Exclusion is supported by the official listing date, not a
    # catch-all tolerance for failed data requests.
    codes = sorted(code for code in master.names if master.listed_dates.get(code, "") != trade_date)
    cap = master.cap_df.reindex(codes).copy()
    if cap["시가총액"].isna().any() or (cap["시가총액"] < 0).any():
        raise KisSnapshotError("KIS master previous-cap coverage incomplete")
    zero_cap = cap.index[cap["시가총액"] == 0].tolist()
    if zero_cap:
        # A published zero-cap, zero-activity issue (e.g. a suspended SPAC)
        # cannot pass ANY existing liquidity trigger. Verify it is nontrading
        # rather than fabricating a dated bar if history is no longer served.
        probe = snapshot_fetcher(codes)
        if set(probe.index) != set(codes):
            raise KisSnapshotError("KIS current snapshot coverage incomplete")
        if (any(master.previous_volumes.get(code) != 0 for code in zero_cap)
                or (probe.loc[zero_cap, ["Volume", "Amount"]] != 0).any().any()):
            raise KisSnapshotError("Active stock has zero KIS master market cap")
        codes = [code for code in codes if code not in zero_cap]
        cap = cap.loc[codes].copy()
        if not codes:
            raise KisSnapshotError("No eligible two-session KIS universe")
    previous = history_fetcher(codes, prev_date)
    if set(previous.index) != set(codes):
        raise KisSnapshotError("KIS previous history coverage incomplete")
    # Master has no per-row date. Its explicitly previous-session volume must
    # agree with the dated bars before assigning that same date to its cap.
    volume_matches = {
        code: _master_volume_match_kind(master.previous_volumes.get(code), previous.at[code, "Volume"])
        for code in codes
    }
    if any(kind is None for kind in volume_matches.values()):
        raise KisSnapshotError("KIS master previous-volume/session mismatch; cap date unverified")
    rounded_volume_codes = sorted(code for code, kind in volume_matches.items()
                                  if kind == "float32_master_observed")
    # Do slow cold history first; current quotes must not age by several minutes.
    if _today_kst() != trade_date:
        raise KisSnapshotError("KIS snapshot collection crossed the requested session date")
    snapshot = snapshot_fetcher(sorted([*codes, *zero_cap]))
    if _today_kst() != trade_date:
        raise KisSnapshotError("KIS quote response crossed the requested session date")
    if set(snapshot.index) != set(codes) | set(zero_cap):
        raise KisSnapshotError("KIS current snapshot coverage incomplete")
    if zero_cap:
        if (snapshot.loc[zero_cap, ["Volume", "Amount"]] != 0).any().any():
            raise KisSnapshotError("Excluded zero-cap issue became active during history collection")
        snapshot = snapshot.loc[codes].copy()
    cap.attrs.update(source="kis_master", trade_date=prev_date, unit="KRW", precision_krw=100_000_000)
    cap.attrs.update(master_volume_validation="exact_or_observed_binary32_rendering",
                     master_volume_binary32_compatibility=rounded_volume_codes)
    snapshot.attrs.update(
        source="kis", observed_date=trade_date,
        requested_universe=len(master.names), eligible_coverage=len(codes),
        ipo_excluded=sum(master.listed_dates.get(code) == trade_date for code in master.names),
        nontrading_zero_cap_excluded=zero_cap,
    )
    return MarketSnapshotBundle(snapshot, previous, cap, prev_date, "kis")
