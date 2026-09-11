"""Offline tests for the KIS 30-stock intraday snapshot endpoint."""

from __future__ import annotations

from io import BytesIO
import zipfile

import pytest

pd = pytest.importorskip("pandas")

from cores.kis_market_snapshot import (  # noqa: E402
    build_kis_snapshot_bundle,
    KisMasterData,
    fetch_kis_master_data,
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


def test_builds_current_and_exact_previous_kis_bundle(monkeypatch):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    current = pd.DataFrame(
        {"Open": [10], "High": [12], "Low": [9], "Close": [11],
         "Volume": [100], "Amount": [1100]}, index=["005930"]
    )
    previous = current.copy()
    cap = pd.DataFrame({"시가총액": [1_000_000]}, index=["005930"])
    master = KisMasterData({"005930": "삼성전자"}, cap, {"005930": "19750611"},
                           "20260911", {"005930": "KOSPI"}, {"005930": "0001"},
                           {"005930": 100})
    calls = []
    def history(codes, day):
        calls.append(("history", day))
        return previous
    def snapshot(codes):
        calls.append(("current", None))
        return current
    bundle = build_kis_snapshot_bundle(
        "20260911", master_fetcher=lambda: master,
        snapshot_fetcher=snapshot, history_fetcher=history,
    )
    assert bundle.source == "kis"
    assert bundle.snapshot is current
    assert bundle.prev_snapshot is previous
    pd.testing.assert_frame_equal(bundle.cap_df, cap)
    assert bundle.prev_date == "20260910"
    assert calls == [("history", "20260910"), ("current", None)]


def test_current_master_cannot_be_relabelled_as_historical(monkeypatch):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    with pytest.raises(KisSnapshotError, match="historical"):
        build_kis_snapshot_bundle("20260910")


def test_official_master_previous_cap_units_and_market_fields():
    import cores.kis_market_snapshot as module
    def line(code, widths, cap_index, listing_index, volume_index, medium, small):
        fields = [" " * width for width in widths]
        fields[2] = "0007"
        fields[3] = medium
        fields[4] = small
        fields[cap_index] = "12345".rjust(widths[cap_index])
        fields[listing_index] = "20000101"
        fields[volume_index] = "789".rjust(widths[volume_index])
        prefix = f"{code:<9}{'KR7' + code:<12}".encode() + b"Company".ljust(40)
        return prefix + "".join(fields).encode()
    payloads = [
        _master_zip([line("005930", module._KOSPI_WIDTHS, 65, 49, 47, "0011", "0013")]),
        _master_zip([line("247540", module._KOSDAQ_WIDTHS, 59, 44, 42, "0021", "0000")]),
    ]
    master = fetch_kis_master_data(
        request_get=lambda *_args, **_kwargs: _DownloadResponse(payloads.pop(0)),
        min_stock_count=1,
    )
    assert master.cap_df["시가총액"].to_dict() == {"005930": 12345 * 100_000_000, "247540": 12345 * 100_000_000}
    assert master.markets == {"005930": "KOSPI", "247540": "KOSDAQ"}
    assert master.previous_volumes == {"005930": 789, "247540": 789}
    assert master.industry_codes == {"005930": "0013", "247540": "0021"}


@pytest.mark.parametrize("problem", ["missing_cap", "stale_master", "volume_mismatch", "missing_previous", "missing_current", "active_zero_cap"])
def test_bundle_fails_closed_on_unverifiable_coverage(monkeypatch, problem):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    frame = pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11],
                          "Volume": [100], "Amount": [1100]}, index=["005930"])
    cap_value = float("nan") if problem == "missing_cap" else 0 if problem == "active_zero_cap" else 1000000
    master = KisMasterData(
        {"005930": "삼성전자"}, pd.DataFrame({"시가총액": [cap_value]}, index=frame.index),
        {"005930": "19750611"}, "20260910" if problem == "stale_master" else "20260911",
        {"005930": "KOSPI"}, {"005930": "0001"},
        {"005930": 99 if problem == "volume_mismatch" else 100},
    )
    with pytest.raises(KisSnapshotError):
        build_kis_snapshot_bundle(
            "20260911", master_fetcher=lambda: master,
            snapshot_fetcher=lambda _: frame.iloc[:0] if problem == "missing_current" else frame,
            history_fetcher=lambda *_: frame.iloc[:0] if problem == "missing_previous" else frame,
        )


def test_single_missing_numeric_quote_field_is_not_accepted():
    class Partial(_Client):
        def _request(self, *args):
            row = _row("005930")
            row["acml_tr_pbmn"] = ""
            return _Response([row])
    with pytest.raises(KisSnapshotError, match="incomplete"):
        fetch_kis_intraday_snapshot(["005930"], client=Partial(), min_stock_count=1, request_interval_sec=0)


def test_zero_cap_excluded_only_on_published_nontrading_evidence(monkeypatch):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    frame = pd.DataFrame({"Open": [10, 0], "High": [12, 0], "Low": [9, 0],
                          "Close": [11, 0], "Volume": [100, 0], "Amount": [1100, 0]},
                         index=["005930", "472220"])
    master = KisMasterData(
        {"005930": "삼성전자", "472220": "SPAC"},
        pd.DataFrame({"시가총액": [1000000, 0]}, index=frame.index),
        {"005930": "19750611", "472220": "20240206"}, "20260911", {}, {},
        {"005930": 100, "472220": 0},
    )
    requested = []
    def history(codes, date):
        requested.extend(codes)
        return frame.loc[codes]
    bundle = build_kis_snapshot_bundle(
        "20260911", master_fetcher=lambda: master,
        snapshot_fetcher=lambda codes: frame.loc[codes], history_fetcher=history,
    )
    assert requested == ["005930"]
    assert bundle.snapshot.index.tolist() == ["005930"]
    assert bundle.snapshot.attrs["nontrading_zero_cap_excluded"] == ["472220"]
    assert bundle.snapshot.attrs["requested_universe"] == 2
    assert bundle.cap_df.attrs["precision_krw"] == 100000000


def test_zero_cap_issue_reactivation_during_cold_history_fails(monkeypatch):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    frame = pd.DataFrame({"Open": [10, 0], "High": [12, 0], "Low": [9, 0],
                          "Close": [11, 0], "Volume": [100, 0], "Amount": [1100, 0]},
                         index=["005930", "472220"])
    master = KisMasterData(
        {"005930": "삼성전자", "472220": "SPAC"},
        pd.DataFrame({"시가총액": [1000000, 0]}, index=frame.index),
        {}, "20260911", {}, {}, {"005930": 100, "472220": 0},
    )
    calls = []
    def snapshot(codes):
        out = frame.loc[codes].copy()
        if calls:
            out.loc["472220", "Volume"] = 1
        calls.append(True)
        return out
    with pytest.raises(KisSnapshotError, match="became active"):
        build_kis_snapshot_bundle(
            "20260911", master_fetcher=lambda: master,
            snapshot_fetcher=snapshot, history_fetcher=lambda codes, date: frame.loc[codes],
        )


@pytest.mark.parametrize("rollover", ["before_quote", "after_quote"])
def test_midnight_collection_cannot_relabel_new_session(monkeypatch, rollover):
    import cores.kis_market_snapshot as module
    times = iter(["20260911", "20260912"] if rollover == "before_quote"
                 else ["20260911", "20260911", "20260912"])
    monkeypatch.setattr(module, "_today_kst", lambda: next(times))
    frame = pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11],
                          "Volume": [100], "Amount": [1100]}, index=["005930"])
    master = KisMasterData({"005930": "삼성전자"},
                           pd.DataFrame({"시가총액": [1000000]}, index=frame.index),
                           {}, "20260911", {}, {}, {"005930": 100})
    calls = []
    def quote(codes):
        calls.append(True)
        return frame
    with pytest.raises(KisSnapshotError, match="crossed"):
        build_kis_snapshot_bundle("20260911", master_fetcher=lambda: master,
                                  snapshot_fetcher=quote, history_fetcher=lambda *_: frame)
    assert len(calls) == (0 if rollover == "before_quote" else 1)
