from __future__ import annotations

import pytest

from cores.market_data.naver_source import NaverSource
from cores.market_data.source import Unavailable, Unsupported


class FakeResponse:
    def __init__(self, payload, *, status_error: Exception | None = None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


def _rows():
    return [
        {
            "itemCode": "005930",
            "bizdate": "20260805",
            "foreignerPureBuyQuant": "+2,298,577",
            "organPureBuyQuant": "-2,078,706",
            "individualPureBuyQuant": "-222,721",
        },
        {
            "itemCode": "005930",
            "bizdate": "20260804",
            "foreignerPureBuyQuant": "+850,527",
            "organPureBuyQuant": "-3,924,513",
            "individualPureBuyQuant": "+2,927,753",
        },
    ]


def test_naver_investor_flows_match_shared_schema_and_sort_dates():
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(_rows())

    frame = NaverSource(request_get=get).investor_flows(
        "005930", "20260801", "20260805"
    )

    assert list(frame.columns) == ["기관합계", "외국인합계", "개인"]
    assert list(frame.index.strftime("%Y%m%d")) == ["20260804", "20260805"]
    assert frame.loc["2026-08-05", "외국인합계"] == 2_298_577
    assert frame.loc["2026-08-05", "기관합계"] == -2_078_706
    assert calls[0][0].endswith("/api/stock/005930/trend")
    assert calls[0][1]["timeout"] == 10


def test_naver_investor_flows_respect_requested_range():
    source = NaverSource(request_get=lambda *args, **kwargs: FakeResponse(_rows()))

    frame = source.investor_flows("005930", "20260805", "20260805")

    assert list(frame.index.strftime("%Y%m%d")) == ["20260805"]


@pytest.mark.parametrize("payload", [[], {}, [{"bizdate": "bad"}]])
def test_naver_investor_flows_reject_unusable_payload(payload):
    source = NaverSource(request_get=lambda *args, **kwargs: FakeResponse(payload))

    with pytest.raises(Unavailable):
        source.investor_flows("005930", "20260801", "20260805")


def test_naver_source_declares_unimplemented_capabilities():
    source = NaverSource(request_get=lambda *args, **kwargs: FakeResponse(_rows()))

    with pytest.raises(Unsupported):
        source.price_history("005930", "20260801", "20260805")
    with pytest.raises(Unsupported):
        source.index_history("1001", "20260801", "20260805")

