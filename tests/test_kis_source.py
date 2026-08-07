"""Offline tests for the KIS market data source.

A fake trading client stands in for `DomesticStockTrading`, so nothing here
authenticates, spends KIS quota, or touches the token file the live trading
loops share.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

pd = pytest.importorskip("pandas")

from cores.market_data.kis_source import KisSource  # noqa: E402
from cores.market_data.source import Unavailable, Unsupported  # noqa: E402


class _Body:
    def __init__(self, output2):
        self.output2 = output2


class _Response:
    def __init__(self, output2, ok=True, error=""):
        self._body = _Body(output2)
        self._ok = ok
        self._error = error

    def isOK(self):  # noqa: N802 - mirrors the KIS SDK
        return self._ok

    def getBody(self):  # noqa: N802
        return self._body

    def getErrorMessage(self):  # noqa: N802
        return self._error


class _FakeClient:
    """Replays canned windows and records what was asked for."""

    def __init__(self, windows=None, ok=True, error="", name="삼성전자"):
        self.windows = windows if windows is not None else []
        self.calls: list[dict] = []
        self.requests: list[tuple[str, str, dict]] = []
        self._ok = ok
        self._error = error
        self._name = name

    def _request(self, api_url, tr_id, params):
        self.calls.append(params)
        self.requests.append((api_url, tr_id, params))
        if not self._ok:
            return _Response([], ok=False, error=self._error)
        index = min(len(self.calls) - 1, len(self.windows) - 1) if self.windows else 0
        rows = self.windows[index] if self.windows else []
        return _Response(rows)

    def get_current_price(self, ticker):
        return {"stock_name": self._name}


def _price_row(date, close=1000):
    return {
        "stck_bsop_date": date,
        "stck_oprc": str(close - 10),
        "stck_hgpr": str(close + 10),
        "stck_lwpr": str(close - 20),
        "stck_clpr": str(close),
        "acml_vol": "12345",
        "acml_tr_pbmn": "678900",
    }


def _source(client):
    source = KisSource()
    source._client = client
    return source


def test_price_history_maps_to_the_shared_schema():
    client = _FakeClient([[_price_row("20260803", 1000), _price_row("20260731", 990)]])
    frame = _source(client).price_history("005930", "20260731", "20260803")

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume", "Amount"]
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index.is_monotonic_increasing
    assert frame.loc["2026-08-03", "Close"] == 1000
    assert frame.loc["2026-08-03", "Open"] == 990


def test_long_range_is_walked_rather_than_truncated():
    """The endpoint caps rows per call; a single call would silently short the range."""
    client = _FakeClient(
        [
            [_price_row("20260803"), _price_row("20260802")],  # first window
            [_price_row("20260801"), _price_row("20260731")],  # second, older
            [],                                                 # nothing left
        ]
    )
    frame = _source(client).price_history("005930", "20260731", "20260803")

    assert len(frame) == 4
    assert str(frame.index.min().date()) == "2026-07-31"
    assert len(client.calls) >= 2, "a truncated first window must be followed up"


def test_walk_stops_when_a_window_returns_nothing_new():
    """A source stuck on the same rows must not loop forever."""
    same = [_price_row("20260803")]
    client = _FakeClient([same, same, same])
    frame = _source(client).price_history("005930", "20200101", "20260803")

    assert len(frame) == 1
    assert len(client.calls) <= 3


def test_investor_flows_uses_the_column_names_the_chart_indexes():
    rows = [
        {
            "stck_bsop_date": "20260803",
            "orgn_ntby_qty": "1000",
            "frgn_ntby_qty": "-2000",
            "prsn_ntby_qty": "500",
            "etc_ntby_qty": "500",
            "etc_corp_ntby_vol": "12",
            "etc_orgt_ntby_vol": "488",
        }
    ]
    frame = _source(_FakeClient([rows])).investor_flows("005930", "20260801", "20260803")

    assert set(frame.columns) == {
        "기관합계",
        "외국인합계",
        "개인",
        "기타합계",
        "기타법인",
        "기타단체",
    }
    assert frame.loc["2026-08-03", "외국인합계"] == -2000
    assert frame.loc["2026-08-03", "기타합계"] == 500


def _flow_row(date, qty="1"):
    return {"stck_bsop_date": date, "orgn_ntby_qty": qty, "frgn_ntby_qty": qty,
            "prsn_ntby_qty": qty, "etc_ntby_qty": qty,
            "etc_corp_ntby_vol": qty, "etc_orgt_ntby_vol": "0"}


def test_investor_flows_trims_to_the_requested_range():
    """The endpoint answers with a fixed window around the as-of date, so rows
    outside what was asked for arrive and must not leak through."""
    rows = [_flow_row("20260731"), _flow_row("20260803"), _flow_row("20260804")]
    frame = _source(_FakeClient([rows])).investor_flows("005930", "20260801", "20260803")
    assert str(frame.index.max().date()) == "2026-08-03"
    assert str(frame.index.min().date()) == "2026-08-03"


def test_investor_flows_asks_as_of_the_end_date():
    """FID_INPUT_DATE_1 is an as-of date, not a range start. Sending `start`
    returned 06-22..07-28 for a 07-28..08-03 request — the month before."""
    client = _FakeClient([[_flow_row("20260803")]])
    _source(client).investor_flows("005930", "20260728", "20260803")
    assert client.calls[0]["FID_INPUT_DATE_1"] == "20260803"


def _estimate_row(bucket, foreign="100", institution="200"):
    return {
        "bsop_hour_gb": str(bucket),
        "frgn_fake_ntby_qty": foreign,
        "orgn_fake_ntby_qty": institution,
        "sum_fake_ntby_qty": str(int(foreign) + int(institution)),
    }


def _kst(hour, minute):
    return datetime(2026, 8, 7, hour, minute, tzinfo=ZoneInfo("Asia/Seoul"))


@pytest.mark.parametrize(
    "as_of,expected_bucket,expected_label",
    [
        (_kst(9, 30), "1", "09:30"),
        (_kst(10, 0), "2", "10:00"),
        (_kst(11, 20), "3", "11:20"),
        (_kst(13, 20), "4", "13:20"),
        (_kst(14, 30), "5", "14:30"),
    ],
)
def test_intraday_estimate_selects_latest_published_bucket(
    as_of, expected_bucket, expected_label
):
    rows = [_estimate_row(bucket, str(int(bucket) * 100), str(int(bucket) * 200))
            for bucket in range(1, 6)]
    client = _FakeClient([rows])

    frame = _source(client).intraday_investor_estimate("005930", as_of=as_of)

    assert frame.attrs["estimate_bucket"] == expected_bucket
    assert expected_label in frame.attrs["estimate_note"]
    assert frame.iloc[0]["외국인합계"] == int(expected_bucket) * 100
    assert frame.iloc[0]["개인·기타합계"] == -(int(expected_bucket) * 300)
    assert "역산" in frame.attrs["estimate_note"]
    assert client.requests[0][1] == "HHPTJ04160200"
    assert client.calls[0] == {"MKSC_SHRN_ISCD": "005930"}


def test_first_intraday_bucket_does_not_fabricate_institution_zero():
    frame = _source(_FakeClient([[_estimate_row(1, "753000", "0")]])).intraday_investor_estimate(
        "005930", as_of=_kst(9, 35)
    )

    assert frame.iloc[0]["외국인합계"] == 753000
    assert pd.isna(frame.iloc[0]["기관합계"])
    assert frame.iloc[0]["개인·기타합계"] == -753000
    assert "개인" not in frame.columns
    assert "기타법인" not in frame.columns


@pytest.mark.parametrize("as_of", [_kst(9, 29), _kst(15, 40)])
def test_intraday_estimate_is_not_used_outside_published_window(as_of):
    client = _FakeClient([[_estimate_row(1)]])
    with pytest.raises(Unsupported, match="session window"):
        _source(client).intraday_investor_estimate("005930", as_of=as_of)
    assert client.calls == []


def _index_row():
    return {
        "stck_bsop_date": "20260803",
        "bstp_nmix_oprc": "6358.27",
        "bstp_nmix_hgpr": "6393.00",
        "bstp_nmix_lwpr": "6230.35",
        "bstp_nmix_prpr": "6257.45",
        "acml_vol": "1000",
        "acml_tr_pbmn": "2000",
    }


def test_index_history_reads_the_index_field_names():
    client = _FakeClient([[_index_row()]])
    frame = _source(client).index_history("1001", "20260803", "20260803")
    assert frame.loc["2026-08-03", "Close"] == pytest.approx(6257.45)


def test_index_codes_are_translated_not_passed_through():
    """KRX calls KOSPI 1001; KIS calls KOSDAQ 1001. Passing it through returns
    a real number for the wrong index — verified live: 737.35 vs 6257.45."""
    client = _FakeClient([[_index_row()]])
    _source(client).index_history("1001", "20260803", "20260803")
    assert client.calls[0]["FID_INPUT_ISCD"] == "0001", "KOSPI must become 0001"

    client = _FakeClient([[_index_row()]])
    _source(client).index_history("2001", "20260803", "20260803")
    assert client.calls[0]["FID_INPUT_ISCD"] == "1001", "KOSDAQ must become 1001"


def test_unmapped_index_is_unsupported_rather_than_guessed():
    with pytest.raises(Unsupported, match="업종코드"):
        _source(_FakeClient()).index_history("9999", "20260803", "20260803")


def test_empty_response_is_unavailable_not_an_empty_frame():
    """An empty frame renders as a blank chart; the caller must be told instead."""
    with pytest.raises(Unavailable, match="no rows"):
        _source(_FakeClient([[]])).price_history("005930", "20260801", "20260803")


def test_rejected_request_is_unavailable():
    client = _FakeClient(ok=False, error="초당 거래건수를 초과하였습니다")
    with pytest.raises(Unavailable, match="rejected"):
        _source(client).price_history("005930", "20260801", "20260803")


def test_blank_value_is_nan_not_zero():
    rows = [{**_price_row("20260803"), "stck_clpr": ""}]
    frame = _source(_FakeClient([rows])).price_history("005930", "20260803", "20260803")
    assert pd.isna(frame.loc["2026-08-03", "Close"])


@pytest.mark.parametrize("capability", ["market_cap_history", "fundamentals"])
def test_series_only_capabilities_are_declared_unsupported(capability):
    """Unsupported, not faked: the chain should fall through to a source that has it."""
    source = _source(_FakeClient())
    with pytest.raises(Unsupported):
        getattr(source, capability)("005930", "20260801", "20260803")


def test_ticker_name_is_unsupported_rather_than_the_market_name():
    """`get_current_price` maps stock_name to rprs_mrkt_kor_name, so 005930
    answers "KOSPI200". Charts would be labelled with a market, not a company."""
    with pytest.raises(Unsupported, match="company name"):
        _source(_FakeClient()).ticker_name("005930")


def test_adjusted_flag_is_passed_through():
    client = _FakeClient([[_price_row("20260803")]])
    _source(client).price_history("005930", "20260803", "20260803", adjusted=False)
    assert client.calls[0]["FID_ORG_ADJ_PRC"] == "1"

    client = _FakeClient([[_price_row("20260803")]])
    _source(client).price_history("005930", "20260803", "20260803", adjusted=True)
    assert client.calls[0]["FID_ORG_ADJ_PRC"] == "0"


def test_module_imports_without_kis_credentials():
    """The chain must load on hosts that have no trading config at all."""
    import importlib

    module = importlib.import_module("cores.market_data")
    assert "kis" in module._BUILDERS
    # Registered but not default: existing behaviour is unchanged until promoted.
    assert module._DEFAULT_ORDER == "krx,fdr"


def test_rejection_reports_why_even_when_the_error_body_is_broken():
    """A rejection that says nothing is what makes an outage take hours."""
    class _Broken(_Response):
        def getErrorMessage(self):  # noqa: N802
            raise RuntimeError("body parse failed")

    class _BrokenClient(_FakeClient):
        def _request(self, api_url, tr_id, params):
            self.calls.append(params)
            return _Broken([], ok=False)

    with pytest.raises(Unavailable, match="unreadable"):
        _source(_BrokenClient()).price_history("005930", "20260803", "20260803")


def test_rejection_includes_the_kis_reason():
    client = _FakeClient(ok=False, error="초당 거래건수를 초과하였습니다")
    with pytest.raises(Unavailable, match="초당 거래건수"):
        _source(client).price_history("005930", "20260803", "20260803")
