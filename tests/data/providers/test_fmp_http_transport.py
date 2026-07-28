from __future__ import annotations

import hashlib
import inspect
import json
import sys
import ast
import asyncio
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers import fmp_http
from prism_core.data.providers.fmp import (
    CapabilityStatus,
    FMPInstrument,
    FMPMarketDataProvider,
    FMP_RAW_EOD_PATH,
    FMPRateLimitError,
)
from prism_core.data.providers.fmp_http import (
    AioHttpFMPRequester,
    FMPHTTPResponse,
    FMPHTTPTransport,
    FMPHTTPTransportError,
    load_fmp_api_key_from_env,
)
from prism_core.data.providers.fmp_models import FMPApiKey, FMPRequest


UTC = ZoneInfo("UTC")
SECRET = "fixture-fmp-key-never-render"
AS_OF = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)


class SequenceRequester:
    def __init__(self, responses: list[FMPHTTPResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    async def request(self, **kwargs) -> FMPHTTPResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def _response(
    payload: object,
    *,
    status_code: int = 200,
    retry_after_seconds: float | None = None,
) -> FMPHTTPResponse:
    return FMPHTTPResponse(
        status_code=status_code,
        body=json.dumps(payload, separators=(",", ":")).encode(),
        received_at=datetime(2026, 7, 24, 1, 1, tzinfo=UTC),
        latency_ms=125,
        retry_after_seconds=retry_after_seconds,
    )


def _fetch_request(*, symbols: list[str] | None = None) -> FMPRequest:
    return FMPRequest(
        operation="fetch_price_page",
        path=FMP_RAW_EOD_PATH,
        params={
            "as_of_date": AS_OF.isoformat(),
            "page": 1,
            "symbols": symbols or ["AAPL"],
        },
    )


@pytest.mark.asyncio
async def test_single_wire_call_maps_documented_unadjusted_rows_to_raw_only_page() -> None:
    wire_payload = [
        {
            "symbol": "AAPL",
            "date": "2026-07-23",
            "adjOpen": 210.1,
            "adjHigh": 214.25,
            "adjLow": 209.8,
            "adjClose": 213.9,
            "volume": 51500000,
        }
    ]
    requester = SequenceRequester([_response(wire_payload)])
    transport = FMPHTTPTransport(requester=requester, lookback_days=10)
    request = _fetch_request()

    envelope = await transport.execute(request, api_key=FMPApiKey(SECRET))

    assert len(requester.requests) == 1
    wire_request = requester.requests[0]
    assert wire_request["url"] == "https://financialmodelingprep.com" + FMP_RAW_EOD_PATH
    assert wire_request["params"] == {
        "apikey": SECRET,
        "from": "2026-07-14",
        "symbol": "AAPL",
        "to": "2026-07-23",
    }
    assert "?" not in str(wire_request["url"])
    assert envelope.status_code == 200
    assert envelope.quality is DataQualityStatus.FRESH
    assert envelope.observed_at == datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
    assert envelope.available_at == datetime(2026, 7, 23, 20, 15, tzinfo=UTC)
    assert envelope.payload == {
        "data": [
            {
                "symbol": "AAPL",
                "date": "2026-07-23",
                "rawOpen": "210.1",
                "rawHigh": "214.25",
                "rawLow": "209.8",
                "rawClose": "213.9",
                "rawVolume": "51500000",
            }
        ],
        "pagination": {
            "page": 1,
            "totalPages": 1,
            "hasMore": False,
            "nextPage": None,
        },
    }
    assert all("adjusted" not in name.lower() for name in envelope.payload["data"][0])
    assert envelope.source_hash == hashlib.sha256(
        json.dumps(envelope.payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert transport.evidence[0].endpoint == FMP_RAW_EOD_PATH
    assert transport.evidence[0].status_code == 200
    assert transport.evidence[0].request_correlation == request.request_hash
    assert transport.evidence[0].latency_ms == 125
    assert transport.evidence[0].raw_payload_hash == hashlib.sha256(
        json.dumps(wire_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert SECRET not in repr(transport)
    assert SECRET not in repr(transport.evidence)


@pytest.mark.asyncio
async def test_weekend_daily_read_treats_latest_completed_friday_as_fresh() -> None:
    sunday = datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    request = FMPRequest(
        operation="fetch_price_page",
        path=FMP_RAW_EOD_PATH,
        params={
            "as_of_date": sunday.isoformat(),
            "page": 1,
            "symbols": ["AAPL"],
        },
    )
    requester = SequenceRequester(
        [
            _response(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-24",
                        "adjOpen": 210.1,
                        "adjHigh": 214.25,
                        "adjLow": 209.8,
                        "adjClose": 213.9,
                        "volume": 51_500_000,
                    }
                ]
            )
        ]
    )

    envelope = await FMPHTTPTransport(requester=requester).execute(
        request,
        api_key=FMPApiKey(SECRET),
    )

    assert envelope.quality is DataQualityStatus.FRESH
    assert requester.requests[0]["params"]["to"] == "2026-07-26"


@pytest.mark.asyncio
async def test_nyse_weekday_holiday_treats_prior_completed_session_as_fresh() -> None:
    presidents_day = datetime(2026, 2, 16, 23, 0, tzinfo=UTC)
    request = FMPRequest(
        operation="fetch_price_page",
        path=FMP_RAW_EOD_PATH,
        params={
            "as_of_date": presidents_day.isoformat(),
            "page": 1,
            "symbols": ["AAPL"],
        },
    )
    requester = SequenceRequester(
        [
            _response(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2026-02-13",
                        "adjOpen": 210.1,
                        "adjHigh": 214.25,
                        "adjLow": 209.8,
                        "adjClose": 213.9,
                        "volume": 51_500_000,
                    }
                ]
            )
        ]
    )

    envelope = await FMPHTTPTransport(requester=requester).execute(
        request,
        api_key=FMPApiKey(SECRET),
    )

    assert envelope.quality is DataQualityStatus.FRESH


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "wire_payload", "expected"),
    [
        (
            200,
            [
                {
                    "symbol": "AAPL",
                    "date": "2026-07-23",
                    "adjOpen": 210.1,
                    "adjHigh": 214.25,
                    "adjLow": 209.8,
                    "adjClose": 213.9,
                    "volume": 51500000,
                }
            ],
            CapabilityStatus.SUPPORTED,
        ),
        (403, {"error": f"plan denied {SECRET}"}, CapabilityStatus.FORBIDDEN),
        (200, {"unexpected": f"schema {SECRET}"}, CapabilityStatus.MALFORMED),
    ],
)
async def test_capability_probe_classifies_entitlement_without_retaining_provider_errors(
    status_code: int,
    wire_payload: object,
    expected: CapabilityStatus,
) -> None:
    transport = FMPHTTPTransport(
        requester=SequenceRequester([_response(wire_payload, status_code=status_code)])
    )
    provider = FMPMarketDataProvider(
        transport=transport,
        api_key=FMPApiKey(SECRET),
        instruments=(),
        clock=lambda: datetime(2026, 7, 24, 1, 1, tzinfo=UTC),
        max_attempts=1,
    )

    result = await provider.probe_capability(as_of_date=AS_OF)

    assert result.status is expected
    assert SECRET not in repr(result)
    assert SECRET not in repr(transport.evidence)
    if expected is CapabilityStatus.SUPPORTED:
        assert result.response is not None
        assert result.response.payload == {"entitled": True}
    else:
        assert result.response is not None
        assert result.response.payload == {}


@pytest.mark.asyncio
async def test_rate_limit_exposes_only_bounded_retry_hint() -> None:
    transport = FMPHTTPTransport(
        requester=SequenceRequester(
            [
                _response(
                    {"error": f"limited {SECRET}"},
                    status_code=429,
                    retry_after_seconds=12.0,
                )
            ]
        )
    )

    with pytest.raises(FMPRateLimitError) as caught:
        await transport.execute(_fetch_request(), api_key=FMPApiKey(SECRET))

    assert caught.value.retry_after_seconds == 12.0
    assert SECRET not in repr(caught.value)
    assert transport.evidence[0].status_code == 429
    assert SECRET not in repr(transport.evidence)


def test_env_loader_is_explicit_redacted_and_fails_closed_on_missing_or_conflict(
    monkeypatch,
) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINANCIAL_MODELING_PREP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="unavailable"):
        load_fmp_api_key_from_env()

    monkeypatch.setenv("FMP_API_KEY", SECRET)
    loaded = load_fmp_api_key_from_env()
    assert loaded.get_secret_value() == SECRET
    assert SECRET not in repr(loaded)

    monkeypatch.setenv("FINANCIAL_MODELING_PREP_API_KEY", "different-key")
    with pytest.raises(RuntimeError, match="conflicting") as caught:
        load_fmp_api_key_from_env()
    assert SECRET not in repr(caught.value)
    assert "different-key" not in repr(caught.value)


@pytest.mark.asyncio
async def test_aiohttp_requester_applies_split_timeouts_and_sanitizes_retry_after(
    monkeypatch,
) -> None:
    chunks = [b'{"error":"limited"}', b""]
    captured: dict[str, object] = {}

    class FakeContent:
        async def read(self, size: int) -> bytes:
            return chunks.pop(0)

    class FakeResponse:
        status = 429
        content_length = None
        content = FakeContent()
        headers = {"Retry-After": "9"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url, *, params):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    def make_timeout(**kwargs):
        captured["timeout"] = kwargs
        return object()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=make_timeout,
            ClientSession=lambda **kwargs: FakeSession(),
        ),
    )
    monotonic_values = iter((10.0, 10.125))
    requester = AioHttpFMPRequester(
        clock=lambda: datetime(2026, 7, 24, 1, 1, tzinfo=UTC),
        monotonic=lambda: next(monotonic_values),
    )

    response = await requester.request(
        method="GET",
        url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
        params={"apikey": SECRET, "symbol": "AAPL"},
        connect_timeout_seconds=2.0,
        read_timeout_seconds=3.0,
        total_timeout_seconds=4.0,
        max_response_bytes=1024,
    )

    assert captured["url"] == "https://financialmodelingprep.com" + FMP_RAW_EOD_PATH
    assert captured["timeout"] == {"connect": 2.0, "sock_read": 3.0, "total": 4.0}
    assert response.status_code == 429
    assert response.body == b'{"error":"limited"}'
    assert response.retry_after_seconds == 9.0
    assert response.latency_ms == 125
    assert SECRET not in repr(response)


@pytest.mark.asyncio
async def test_aiohttp_requester_ignores_nonfinite_retry_after(monkeypatch) -> None:
    chunks = [b"[]", b""]

    class FakeContent:
        async def read(self, size: int) -> bytes:
            return chunks.pop(0)

    class FakeResponse:
        status = 429
        content_length = None
        content = FakeContent()
        headers = {"Retry-After": "inf"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url, *, params):
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=lambda **kwargs: FakeSession(),
        ),
    )
    requester = AioHttpFMPRequester()

    response = await requester.request(
        method="GET",
        url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
        params={"apikey": SECRET},
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        total_timeout_seconds=2,
        max_response_bytes=32,
    )

    assert response.retry_after_seconds is None


@pytest.mark.asyncio
async def test_multi_symbol_request_fails_before_any_hidden_wire_fanout() -> None:
    requester = SequenceRequester([])
    transport = FMPHTTPTransport(requester=requester)

    with pytest.raises(FMPHTTPTransportError, match="exactly one symbol"):
        await transport.execute(
            _fetch_request(symbols=["AAPL", "MSFT"]),
            api_key=FMPApiKey(SECRET),
        )

    assert requester.requests == []


@pytest.mark.asyncio
async def test_requester_rejects_non_market_data_url_before_importing_aiohttp() -> None:
    requester = AioHttpFMPRequester()

    with pytest.raises(FMPHTTPTransportError, match="not allowlisted"):
        await requester.request(
            method="GET",
            url="https://financialmodelingprep.com/stable/account",
            params={"apikey": SECRET},
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            total_timeout_seconds=2,
            max_response_bytes=32,
        )


@pytest.mark.asyncio
async def test_http_transport_normalizes_through_existing_pit_provider_contract() -> None:
    security_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000095"))
    transport = FMPHTTPTransport(
        requester=SequenceRequester(
            [
                _response(
                    [
                        {
                            "symbol": "AAPL",
                            "date": "2026-07-23",
                            "adjOpen": 210.1,
                            "adjHigh": 214.25,
                            "adjLow": 209.8,
                            "adjClose": 213.9,
                            "volume": 51500000,
                        }
                    ]
                )
            ]
        )
    )
    provider = FMPMarketDataProvider(
        transport=transport,
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=security_id,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: datetime(2026, 7, 24, 1, 1, tzinfo=UTC),
        max_attempts=1,
    )

    result = await provider.fetch_result(
        security_ids=(security_id,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.snapshot.price_bars) == 1
    bar = result.snapshot.price_bars[0]
    assert str(bar.raw_close) == "213.9"
    assert bar.adjusted_close is None
    assert bar.adjustment_as_of is None
    assert bar.provider_symbol == "AAPL"
    assert bar.timing.observed_at < bar.timing.available_at <= AS_OF


@pytest.mark.asyncio
async def test_http_transport_excludes_incomplete_current_session_rows() -> None:
    transport = FMPHTTPTransport(
        requester=SequenceRequester(
            [
                _response(
                    [
                        {
                            "symbol": "AAPL",
                            "date": "2026-07-24",
                            "adjOpen": 214,
                            "adjHigh": 216,
                            "adjLow": 213,
                            "adjClose": 215,
                            "volume": 10,
                        },
                        {
                            "symbol": "AAPL",
                            "date": "2026-07-23",
                            "adjOpen": 210,
                            "adjHigh": 214,
                            "adjLow": 209,
                            "adjClose": 213,
                            "volume": 20,
                        },
                    ]
                )
            ]
        )
    )

    envelope = await transport.execute(_fetch_request(), api_key=FMPApiKey(SECRET))

    assert [row["date"] for row in envelope.payload["data"]] == ["2026-07-23"]
    assert envelope.available_at <= AS_OF
    assert envelope.quality is DataQualityStatus.FRESH


@pytest.mark.asyncio
async def test_empty_wire_page_is_explicitly_unavailable() -> None:
    transport = FMPHTTPTransport(requester=SequenceRequester([_response([])]))

    envelope = await transport.execute(_fetch_request(), api_key=FMPApiKey(SECRET))

    assert envelope.quality is DataQualityStatus.UNAVAILABLE
    assert envelope.payload == {
        "data": [],
        "pagination": {
            "page": 1,
            "totalPages": 1,
            "hasMore": False,
            "nextPage": None,
        },
    }
    assert envelope.observed_at == AS_OF
    assert envelope.available_at == AS_OF


@pytest.mark.parametrize(
    "base_url",
    (
        "http://financialmodelingprep.com",
        "https://example.com",
        "https://financialmodelingprep.com/account",
        "https://financialmodelingprep.com?apikey=secret",
    ),
)
def test_transport_rejects_non_allowlisted_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        FMPHTTPTransport(requester=SequenceRequester([]), base_url=base_url)


@pytest.mark.asyncio
async def test_aiohttp_requester_rejects_oversize_and_sanitizes_timeout(
    monkeypatch,
) -> None:
    class OversizeContent:
        async def read(self, size: int) -> bytes:
            return b"x" * size

    class OversizeResponse:
        status = 200
        content_length: int | None = None
        content = OversizeContent()
        headers: dict[str, str] = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class OversizeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def get(self, url, *, params):
            return OversizeResponse()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=lambda **kwargs: OversizeSession(),
        ),
    )
    requester = AioHttpFMPRequester()
    with pytest.raises(FMPHTTPTransportError, match="size limit"):
        await requester.request(
            method="GET",
            url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
            params={"apikey": SECRET},
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            total_timeout_seconds=2,
            max_response_bytes=32,
        )

    OversizeResponse.content_length = 33
    with pytest.raises(FMPHTTPTransportError, match="size limit"):
        await requester.request(
            method="GET",
            url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
            params={"apikey": SECRET},
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            total_timeout_seconds=2,
            max_response_bytes=32,
        )

    class TimeoutSession(OversizeSession):
        def get(self, url, *, params):
            raise asyncio.TimeoutError(f"timed out with {SECRET}")

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=lambda **kwargs: TimeoutSession(),
        ),
    )
    from prism_core.data.providers.fmp import FMPTimeoutError

    with pytest.raises(FMPTimeoutError) as caught:
        await requester.request(
            method="GET",
            url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
            params={"apikey": SECRET},
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            total_timeout_seconds=2,
            max_response_bytes=32,
        )
    assert SECRET not in repr(caught.value)

    class FailureSession(OversizeSession):
        def get(self, url, *, params):
            raise RuntimeError(f"wire failure {SECRET}")

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=lambda **kwargs: FailureSession(),
        ),
    )
    with pytest.raises(FMPHTTPTransportError, match="request failed") as caught:
        await requester.request(
            method="GET",
            url="https://financialmodelingprep.com" + FMP_RAW_EOD_PATH,
            params={"apikey": SECRET},
            connect_timeout_seconds=1,
            read_timeout_seconds=1,
            total_timeout_seconds=2,
            max_response_bytes=32,
        )
    assert SECRET not in repr(caught.value)


def test_fmp_http_module_has_no_broker_surface_or_non_market_data_literal_path() -> None:
    tree = ast.parse(inspect.getsource(fmp_http))
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    literal_paths = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("/")
        and len(node.value) > 1
    }

    assert not any(
        module == "trading" or module.startswith("trading.") for module in imported_modules
    )
    assert literal_paths <= {FMP_RAW_EOD_PATH}
    assert not any(
        name in dir(FMPHTTPTransport)
        for name in ("account", "balance", "buy", "cancel", "holdings", "order", "sell")
    )
