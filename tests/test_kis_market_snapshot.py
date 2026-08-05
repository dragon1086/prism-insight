"""Offline tests for the KIS 30-stock intraday snapshot endpoint."""

from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

pd = pytest.importorskip("pandas")

from cores.kis_market_snapshot import (  # noqa: E402
    build_kis_openapi_snapshot_bundle,
    KisSnapshotError,
    fetch_kis_master_universe,
    fetch_kis_intraday_snapshot,
)


class _Body:
    def __init__(self, output):
        self.output = output


class _Response:
    def __init__(self, output, *, ok=True, error=""):
        self._body = _Body(output)
        self._ok = ok
        self._error = error

    def isOK(self):  # noqa: N802
        return self._ok

    def getBody(self):  # noqa: N802
        return self._body

    def getErrorMessage(self):  # noqa: N802
        return self._error


def _row(code, price=1000):
    return {
        "inter_shrn_iscd": code,
        "inter_kor_isnm": f"종목{code}",
        "inter2_oprc": str(price - 10),
        "inter2_hgpr": str(price + 20),
        "inter2_lwpr": str(price - 30),
        "inter2_prpr": str(price),
        "acml_vol": "12345",
        "acml_tr_pbmn": "678900",
        "inter2_prdy_clpr": str(price - 5),
    }


class _Client:
    def __init__(self, *, drop=None, reject=False):
        self.calls = []
        self.drop = set(drop or [])
        self.reject = reject

    def _request(self, api_url, tr_id, params):
        self.calls.append((api_url, tr_id, params))
        if self.reject:
            return _Response([], ok=False, error="EGW00201")
        codes = [
            params[f"FID_INPUT_ISCD_{i}"]
            for i in range(1, 31)
            if f"FID_INPUT_ISCD_{i}" in params
        ]
        return _Response([_row(code) for code in codes if code not in self.drop])


def test_chunks_thirty_tickers_and_maps_screening_columns():
    tickers = [f"{i:06d}" for i in range(61)]
    client = _Client()

    frame = fetch_kis_intraday_snapshot(
        tickers, client=client, min_stock_count=1, retry_wait_sec=0, request_interval_sec=0
    )

    assert len(client.calls) == 3
    assert len(frame) == 61
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume", "Amount"]
    assert frame.loc["000000", "Close"] == 1000
    assert client.calls[0][1] == "FHKST11300006"


def test_missing_ticker_is_rejected_instead_of_returning_a_thin_universe():
    client = _Client(drop={"000031"})
    with pytest.raises(KisSnapshotError, match="missing 1 tickers"):
        fetch_kis_intraday_snapshot(
            [f"{i:06d}" for i in range(40)],
            client=client,
            min_stock_count=1,
            retry_wait_sec=0,
            request_interval_sec=0,
        )


def test_rejected_chunk_retries_then_raises_with_kis_reason():
    client = _Client(reject=True)
    with pytest.raises(KisSnapshotError, match="EGW00201"):
        fetch_kis_intraday_snapshot(
            ["005930"], client=client, min_stock_count=1,
            max_attempts=2, retry_wait_sec=0, request_interval_sec=0,
        )
    assert len(client.calls) == 2


def test_small_requested_universe_is_refused_by_default():
    with pytest.raises(KisSnapshotError, match="universe too small"):
        fetch_kis_intraday_snapshot(["005930"], client=_Client(), request_interval_sec=0)


class _DownloadResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def _master_zip(lines):
    payload = b"\n".join(lines) + b"\n"
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("market_code.mst", payload)
    return buffer.getvalue()


def _master_line(code, name, *, etp=False):
    # Official KIS fixed-width prefix: short code 9, standard code 12, name 40.
    tail = bytearray(b" " * 227)
    if etp:
        tail[22:23] = b"2"  # KOSPI ETP 상품구분코드
    return f"{code:<9}{'KR7' + code:<12}{name:<40}".encode("euc-kr") + bytes(tail)


def test_master_universe_combines_markets_and_drops_non_numeric_codes():
    payloads = [
        _master_zip([
            _master_line("005930", "삼성전자"),
            _master_line("069500", "KODEX 200", etp=True),
            _master_line("Q123456", "ETN"),
        ]),
        _master_zip([_master_line("247540", "에코프로비엠")]),
    ]

    def get(_url, timeout=None):
        return _DownloadResponse(payloads.pop(0))

    universe = fetch_kis_master_universe(request_get=get, min_stock_count=1)

    assert universe == {"005930": "삼성전자", "247540": "에코프로비엠"}


def test_master_universe_refuses_partial_download():
    payload = _master_zip([_master_line("005930", "삼성전자")])
    with pytest.raises(KisSnapshotError, match="universe too small"):
        fetch_kis_master_universe(
            request_get=lambda *_args, **_kwargs: _DownloadResponse(payload)
        )


def test_builds_current_kis_and_previous_openapi_bundle():
    current = pd.DataFrame(
        {"Open": [10], "High": [12], "Low": [9], "Close": [11],
         "Volume": [100], "Amount": [1100]}, index=["005930"]
    )
    previous = current.copy()
    cap = pd.DataFrame({"시가총액": [1_000_000]}, index=["005930"])

    class _Previous:
        snapshot = previous
        cap_df = cap
        trade_date = "20260804"

    bundle = build_kis_openapi_snapshot_bundle(
        "20260805",
        universe_fetcher=lambda: {"005930": "삼성전자"},
        snapshot_fetcher=lambda codes: current,
        previous_fetcher=lambda _date: _Previous(),
    )

    assert bundle.source == "kis+krx_openapi"
    assert bundle.snapshot is current
    assert bundle.prev_snapshot is previous
    assert bundle.cap_df is cap
    assert bundle.prev_date == "20260804"
