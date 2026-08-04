"""Offline tests for the KRX OPEN API snapshot source.

Every test drives a fake ``request_get`` so the suite never touches the network
and never spends the API quota. The live behaviour these mirror was measured on
2026-08-04 against the real service: 2,686 six-digit codes for 2026-08-03,
previous session 2026-07-31, and an empty payload for the current session.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

from cores.krx_openapi_snapshot import (  # noqa: E402
    KrxOpenApiError,
    fetch_krx_openapi_bundle,
)

AUTH = "test-key"


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _row(code, date, *, close=1000, open_=990, high=1010, low=980,
         volume=1000, amount=1_000_000, cap=600_000_000_000):
    return {
        "BAS_DD": date,
        "ISU_CD": code,
        "ISU_NM": f"종목{code}",
        "MKT_NM": "KOSPI",
        "SECT_TP_NM": "",
        "TDD_CLSPRC": str(close),
        "TDD_OPNPRC": str(open_),
        "TDD_HGPRC": str(high),
        "TDD_LWPRC": str(low),
        "ACC_TRDVOL": str(volume),
        "ACC_TRDVAL": str(amount),
        "MKTCAP": str(cap),
        "LIST_SHRS": "1000000",
    }


def _make_get(rows_by_date, *, status_by_date=None):
    """Serve KOSPI rows per date; KOSDAQ always empty so counts stay readable."""
    def _get(url, params=None, headers=None, timeout=None):
        date = (params or {})["basDd"]
        if status_by_date and date in status_by_date:
            return _Response({}, status_code=status_by_date[date])
        rows = rows_by_date.get(date, []) if url.endswith("stk_bydd_trd") else []
        return _Response({"OutBlock_1": rows})
    return _get


def _universe(date, count, **kw):
    return [_row(f"{i:06d}", date, **kw) for i in range(count)]


def test_builds_bundle_and_finds_previous_session():
    """A weekend gap is crossed by asking, not by computing a calendar."""
    rows = {
        "20260803": _universe("20260803", 2600, close=1100),
        # 20260802 / 20260801 are a weekend: empty, as the real API returns.
        "20260731": _universe("20260731", 2600, close=1000),
    }
    bundle = fetch_krx_openapi_bundle(
        "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
    )

    assert bundle.source == "krx_openapi"
    assert bundle.prev_date == "20260731"
    assert bundle.snapshot.shape == (2600, 6)
    assert bundle.prev_snapshot.shape == (2600, 6)
    assert list(bundle.cap_df.columns) == ["시가총액"]
    assert bundle.snapshot.index.equals(bundle.prev_snapshot.index)


def test_columns_match_the_screening_contract():
    """trigger_batch indexes these names directly; renaming one breaks it silently."""
    rows = {
        "20260803": _universe("20260803", 2600),
        "20260731": _universe("20260731", 2600),
    }
    bundle = fetch_krx_openapi_bundle(
        "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
    )
    assert list(bundle.snapshot.columns) == [
        "Open", "High", "Low", "Close", "Volume", "Amount"
    ]
    row = bundle.snapshot.iloc[0]
    assert (row["Open"], row["High"], row["Low"], row["Close"]) == (990, 1010, 980, 1000)


def test_non_stock_codes_are_dropped():
    """Warrants and the like carry non-numeric codes and must not enter the universe."""
    rows = {
        "20260803": _universe("20260803", 2600) + [_row("KR7005930", "20260803")],
        "20260731": _universe("20260731", 2600),
    }
    bundle = fetch_krx_openapi_bundle(
        "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
    )
    assert len(bundle.snapshot) == 2600
    assert all(code.isdigit() and len(code) == 6 for code in bundle.snapshot.index)


def test_current_session_is_refused_rather_than_returned_thin():
    """The API serves no same-day data; an empty response must not become an empty frame."""
    get = _make_get({"20260803": _universe("20260803", 2600)})
    with pytest.raises(KrxOpenApiError, match="not published yet"):
        fetch_krx_openapi_bundle("20260804", auth_key=AUTH, request_get=get)


def test_short_universe_is_refused():
    """A partial universe changes every trigger's normalisation, so it is an error."""
    rows = {"20260803": _universe("20260803", 100)}
    with pytest.raises(KrxOpenApiError, match="universe too small"):
        fetch_krx_openapi_bundle(
            "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
        )


def test_unauthorised_service_is_not_retried():
    """401 means the service was never approved for this key; retrying cannot fix it."""
    calls = []

    def _get(url, params=None, headers=None, timeout=None):
        calls.append(params["basDd"])
        return _Response({}, status_code=401)

    with pytest.raises(KrxOpenApiError, match="not authorised"):
        fetch_krx_openapi_bundle("20260803", auth_key=AUTH, request_get=_get)
    assert len(calls) == 1


def test_missing_auth_key_is_explicit(monkeypatch):
    monkeypatch.delenv("KRX_OPENAPI_AUTH_KEY", raising=False)
    with pytest.raises(KrxOpenApiError, match="KRX_OPENAPI_AUTH_KEY"):
        fetch_krx_openapi_bundle("20260803")


def test_blank_numeric_field_becomes_nan_not_zero():
    """A suspended issue reports an empty price; zero would pass filters it should fail."""
    rows = {
        "20260803": _universe("20260803", 2600) + [
            {**_row("999999", "20260803"), "TDD_CLSPRC": ""}
        ],
        "20260731": _universe("20260731", 2600),
    }
    bundle = fetch_krx_openapi_bundle(
        "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
    )
    assert pd.isna(bundle.snapshot.loc["999999", "Close"])


def test_mismatched_date_in_payload_is_rejected():
    """Serving a neighbouring session would silently shift every comparison by a day."""
    rows = {"20260803": _universe("20260731", 2600)}
    with pytest.raises(KrxOpenApiError, match="other dates"):
        fetch_krx_openapi_bundle(
            "20260803", auth_key=AUTH, request_get=_make_get(rows), min_stock_count=2500
        )


def test_bad_date_format_is_rejected():
    with pytest.raises(ValueError, match="YYYYMMDD"):
        fetch_krx_openapi_bundle("2026-08-03", auth_key=AUTH)
