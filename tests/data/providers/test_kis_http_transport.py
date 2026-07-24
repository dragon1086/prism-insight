from __future__ import annotations

import hashlib
import inspect
import json
import ast
import asyncio
import sys
from types import SimpleNamespace
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import (
    KISInstrument,
    KISMarketDataProvider,
    ProviderEventKind,
)
from prism_core.data.providers import kis_http
from prism_core.data.providers.kis_http import (
    AioHttpKISRequester,
    DAILY_PRICE_PATH,
    TOKEN_PATH,
    KISHTTPResponse,
    KISHTTPTransport,
    KISMarketDataCredentials,
    KISMarketDataTransportError,
)


KST = ZoneInfo("Asia/Seoul")


def _token_response(*, received_at: datetime, token: str = "fixture-token") -> KISHTTPResponse:
    return KISHTTPResponse(
        status_code=200,
        body=json.dumps({"access_token": token, "expires_in": 3600}).encode(),
        received_at=received_at,
    )


def _quote_response(*, received_at: datetime, status_code: int = 200) -> KISHTTPResponse:
    return KISHTTPResponse(
        status_code=status_code,
        body=json.dumps(
            {
                "rt_cd": "0",
                "output2": [
                    {
                        "stck_bsop_date": "20260724",
                        "stck_oprc": "70000",
                        "stck_hgpr": "71500",
                        "stck_lwpr": "69500",
                        "stck_clpr": "71000",
                        "acml_vol": "12345678",
                    }
                ],
            },
            separators=(",", ":"),
        ).encode(),
        received_at=received_at,
    )


class SequenceRequester:
    def __init__(self, responses: list[KISHTTPResponse]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []

    async def request(self, **kwargs) -> KISHTTPResponse:
        self.requests.append(kwargs)
        return self.responses.pop(0)


def test_credentials_are_loaded_from_market_data_env_and_redacted(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "fixture-app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "fixture-app-secret")

    credentials = KISMarketDataCredentials.from_env()

    assert credentials.app_key == "fixture-app-key"
    assert credentials.app_secret == "fixture-app-secret"
    rendered = repr(credentials)
    assert "fixture-app-key" not in rendered
    assert "fixture-app-secret" not in rendered
    assert "REDACTED" in rendered


@pytest.mark.asyncio
async def test_transport_calls_only_daily_quotation_and_normalizes_wire_response() -> None:
    quote_body = json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "success",
            "output2": [
                {
                    "stck_bsop_date": "20260724",
                    "stck_oprc": "70000",
                    "stck_hgpr": "71500",
                    "stck_lwpr": "69500",
                    "stck_clpr": "71000",
                    "acml_vol": "12345678",
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    requests: list[dict[str, object]] = []

    class FixtureRequester:
        async def request(self, **kwargs) -> KISHTTPResponse:
            requests.append(kwargs)
            if kwargs["url"].endswith("/oauth2/tokenP"):
                return KISHTTPResponse(
                    status_code=200,
                    body=b'{"access_token":"fixture-token","expires_in":3600}',
                    received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST),
                )
            return KISHTTPResponse(
                status_code=200,
                body=quote_body,
                received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST),
            )

    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=FixtureRequester(),
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
    )

    payload = await transport.fetch(
        "KIS",
        as_of_date=datetime(2026, 7, 24, 17, 2, tzinfo=KST),
    )

    assert [request["method"] for request in requests] == ["POST", "GET"]
    assert requests[0]["url"] == "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    assert requests[1]["url"] == (
        "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/"
        "quotations/inquire-daily-itemchartprice"
    )
    assert requests[1]["params"] == {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": "005930",
        "FID_INPUT_DATE_1": "20260724",
        "FID_INPUT_DATE_2": "20260724",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    serialized_requests = repr(requests).lower()
    assert "account" not in serialized_requests
    assert "cano" not in serialized_requests
    assert "order" not in serialized_requests
    assert payload.payload["prices"] == [
            {
                "provider_symbol": "005930",
                "trade_date": "2026-07-24",
                "open": "70000",
                "high": "71500",
                "low": "69500",
                "close": "71000",
                "volume": "12345678",
            }
        ]
    assert payload.observed_at == datetime(2026, 7, 24, 15, 30, tzinfo=KST)
    assert payload.available_at == datetime(2026, 7, 24, 15, 31, tzinfo=KST)
    assert payload.quality is DataQualityStatus.FRESH
    assert payload.source_hash == hashlib.sha256(quote_body).hexdigest()
    evidence = payload.payload["transport_evidence"]
    assert [item["endpoint"] for item in evidence] == [
        "/oauth2/tokenP",
        "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
    ]
    assert [item["status_code"] for item in evidence] == [200, 200]
    assert evidence[-1]["received_at"] == "2026-07-24T17:01:00+09:00"
    assert evidence[0]["raw_payload_hash"] is None
    assert evidence[-1]["raw_payload_hash"] == payload.source_hash


@pytest.mark.asyncio
async def test_incomplete_current_daily_bar_fails_closed() -> None:
    class IntradayRequester:
        async def request(self, **kwargs) -> KISHTTPResponse:
            if kwargs["url"].endswith("/oauth2/tokenP"):
                body = b'{"access_token":"fixture-token","expires_in":3600}'
            else:
                body = json.dumps(
                    {
                        "rt_cd": "0",
                        "output2": [
                            {
                                "stck_bsop_date": "20260724",
                                "stck_oprc": "70000",
                                "stck_hgpr": "70500",
                                "stck_lwpr": "69500",
                                "stck_clpr": "70200",
                                "acml_vol": "1234",
                            }
                        ],
                    }
                ).encode()
            return KISHTTPResponse(
                status_code=200,
                body=body,
                received_at=datetime(2026, 7, 24, 10, 1, tzinfo=KST),
            )

    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=IntradayRequester(),
        clock=lambda: datetime(2026, 7, 24, 10, 0, tzinfo=KST),
    )

    with pytest.raises(KISMarketDataTransportError, match="completed daily bar"):
        await transport.fetch(
            "KIS",
            as_of_date=datetime(2026, 7, 24, 10, 0, tzinfo=KST),
        )


@pytest.mark.asyncio
async def test_http_transport_normalizes_through_existing_pit_provider_contract() -> None:
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            _quote_response(received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST)),
        ]
    )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
    )
    security_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
    provider_clock = iter(
        (
            datetime(2026, 7, 24, 17, 2, tzinfo=KST),
            datetime(2026, 7, 24, 17, 3, tzinfo=KST),
        )
    )
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(KISInstrument(security_id=security_id, kis_symbol="005930"),),
        clock=lambda: next(provider_clock),
        max_attempts=1,
    )

    result = await provider.fetch_result(
        security_ids=(security_id,),
        as_of_date=datetime(2026, 7, 24, 17, 2, tzinfo=KST),
    )

    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.snapshot.price_bars) == 1
    bar = result.snapshot.price_bars[0]
    assert bar.provider_symbol == "005930"
    assert bar.timing.observed_at == datetime(2026, 7, 24, 15, 30, tzinfo=KST)
    assert bar.timing.available_at == datetime(2026, 7, 24, 15, 31, tzinfo=KST)
    assert bar.timing.ingested_at == datetime(2026, 7, 24, 17, 3, tzinfo=KST)
    assert bar.timing.as_of_date == datetime(2026, 7, 24, 17, 2, tzinfo=KST)
    assert bar.source_hash == result.raw_payloads[0].source_hash
    assert len(bar.source_hash) == 64


@pytest.mark.asyncio
async def test_token_cache_has_expiry_margin_and_is_refreshed_after_expiry() -> None:
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            _quote_response(received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST)),
            _quote_response(received_at=datetime(2026, 7, 24, 17, 2, tzinfo=KST)),
            _token_response(
                received_at=datetime(2026, 7, 24, 18, 0, tzinfo=KST),
                token="fixture-token-refreshed",
            ),
            _quote_response(received_at=datetime(2026, 7, 24, 18, 1, tzinfo=KST)),
        ]
    )
    current_time = [datetime(2026, 7, 24, 17, 2, tzinfo=KST)]
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        clock=lambda: current_time[0],
        min_request_interval_seconds=0,
    )

    await transport.fetch("KIS", as_of_date=current_time[0])
    await transport.fetch("KIS", as_of_date=current_time[0])
    current_time[0] = datetime(2026, 7, 24, 18, 2, tzinfo=KST)
    await transport.fetch("KIS", as_of_date=current_time[0])

    assert [request["url"] for request in requester.requests].count(
        "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    ) == 2


@pytest.mark.asyncio
async def test_concurrent_fetches_share_one_cached_token_request() -> None:
    token_requests = 0
    quote_requests = 0

    class ConcurrentRequester:
        async def request(self, **kwargs) -> KISHTTPResponse:
            nonlocal quote_requests, token_requests
            await asyncio.sleep(0)
            if kwargs["url"].endswith(TOKEN_PATH):
                token_requests += 1
                return _token_response(
                    received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)
                )
            quote_requests += 1
            return _quote_response(
                received_at=datetime(2026, 7, 24, 17, quote_requests, tzinfo=KST)
            )

    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=ConcurrentRequester(),
        clock=lambda: datetime(2026, 7, 24, 17, 3, tzinfo=KST),
        min_request_interval_seconds=0,
    )
    as_of = datetime(2026, 7, 24, 17, 3, tzinfo=KST)

    first, second = await asyncio.gather(
        transport.fetch("KIS", as_of_date=as_of),
        transport.fetch("KIS", as_of_date=as_of),
    )

    assert token_requests == 1
    assert quote_requests == 2
    assert first.source_hash == second.source_hash


@pytest.mark.asyncio
async def test_error_status_never_exposes_provider_body_headers_or_credentials() -> None:
    secret_body = (
        b'{"error":"fixture-app-key fixture-app-secret fixture-token account order"}'
    )
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            KISHTTPResponse(
                status_code=500,
                body=secret_body,
                received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST),
            ),
        ]
    )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
    )

    with pytest.raises(KISMarketDataTransportError) as caught:
        await transport.fetch(
            "KIS", as_of_date=datetime(2026, 7, 24, 17, 2, tzinfo=KST)
        )

    rendered = repr(caught.value)
    for secret in ("fixture-app-key", "fixture-app-secret", "fixture-token", "account", "order"):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_timeout_size_and_rate_bounds_are_forwarded_and_enforced_per_request() -> None:
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            _quote_response(received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST)),
        ]
    )
    monotonic_values = iter((0.0, 0.0, 0.5))
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        timeout_seconds=2.5,
        max_response_bytes=65536,
        min_request_interval_seconds=0.5,
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
        monotonic=lambda: next(monotonic_values),
        sleeper=sleeper,
    )

    await transport.fetch(
        "KIS", as_of_date=datetime(2026, 7, 24, 17, 2, tzinfo=KST)
    )

    assert sleeps == [0.5]
    assert {request["timeout_seconds"] for request in requester.requests} == {2.5}
    assert {request["max_response_bytes"] for request in requester.requests} == {65536}


@pytest.mark.asyncio
async def test_aiohttp_requester_rejects_stream_larger_than_configured_limit(
    monkeypatch,
) -> None:
    class FakeContent:
        async def read(self, size: int) -> bytes:
            return b"x" * size

    class FakeResponse:
        status = 200
        content_length = None
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def request(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    fake_aiohttp = SimpleNamespace(
        ClientTimeout=lambda **kwargs: object(),
        ClientSession=lambda **kwargs: FakeSession(),
    )
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
    requester = AioHttpKISRequester(
        clock=lambda: datetime(2026, 7, 24, 17, 0, tzinfo=KST)
    )

    with pytest.raises(KISMarketDataTransportError, match="size limit"):
        await requester.request(
            method="GET",
            url="https://openapi.koreainvestment.com:9443" + DAILY_PRICE_PATH,
            headers={},
            json_body=None,
            params={},
            timeout_seconds=1.0,
            max_response_bytes=32,
        )


@pytest.mark.asyncio
async def test_aiohttp_requester_accumulates_short_chunks_until_eof(monkeypatch) -> None:
    chunks = [b'{"rt_', b'cd":"0"}', b""]

    class FakeContent:
        async def read(self, size: int) -> bytes:
            return chunks.pop(0)

    class FakeResponse:
        status = 200
        content_length = None
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

        def request(self, *args, **kwargs) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(
            ClientTimeout=lambda **kwargs: object(),
            ClientSession=lambda **kwargs: FakeSession(),
        ),
    )

    response = await AioHttpKISRequester(
        clock=lambda: datetime(2026, 7, 24, 17, 0, tzinfo=KST)
    ).request(
        method="GET",
        url="https://openapi.koreainvestment.com:9443" + DAILY_PRICE_PATH,
        headers={},
        json_body=None,
        params={},
        timeout_seconds=1.0,
        max_response_bytes=32,
    )

    assert response.body == b'{"rt_cd":"0"}'


@pytest.mark.asyncio
async def test_rate_limit_retries_are_bounded_by_provider_and_return_unavailable() -> None:
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            KISHTTPResponse(
                status_code=429,
                body=b'{"secret_provider_body":"not retained"}',
                received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST),
            ),
            KISHTTPResponse(
                status_code=429,
                body=b'{"secret_provider_body":"not retained"}',
                received_at=datetime(2026, 7, 24, 17, 2, tzinfo=KST),
            ),
        ]
    )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        clock=lambda: datetime(2026, 7, 24, 17, 3, tzinfo=KST),
        min_request_interval_seconds=0,
    )
    security_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(KISInstrument(security_id=security_id, kis_symbol="005930"),),
        clock=lambda: datetime(2026, 7, 24, 17, 3, tzinfo=KST),
        max_attempts=2,
    )

    result = await provider.fetch_result(
        security_ids=(security_id,),
        as_of_date=datetime(2026, 7, 24, 17, 3, tzinfo=KST),
    )

    assert len(requester.requests) == 3
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert result.raw_payloads == ()
    assert [event.kind for event in result.events] == [
        ProviderEventKind.RATE_LIMIT,
        ProviderEventKind.RATE_LIMIT,
        ProviderEventKind.RETRY_EXHAUSTED,
    ]
    assert "secret_provider_body" not in repr(result.events)


@pytest.mark.asyncio
async def test_non_retryable_provider_error_propagates_without_fabricated_snapshot() -> None:
    requester = SequenceRequester(
        [
            _token_response(received_at=datetime(2026, 7, 24, 17, 0, tzinfo=KST)),
            KISHTTPResponse(
                status_code=500,
                body=b'{"secret_provider_body":"not retained"}',
                received_at=datetime(2026, 7, 24, 17, 1, tzinfo=KST),
            ),
        ]
    )
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials("fixture-app-key", "fixture-app-secret"),
        symbols=("005930",),
        requester=requester,
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
        min_request_interval_seconds=0,
    )
    security_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(KISInstrument(security_id=security_id, kis_symbol="005930"),),
        clock=lambda: datetime(2026, 7, 24, 17, 2, tzinfo=KST),
        max_attempts=3,
    )

    with pytest.raises(KISMarketDataTransportError) as caught:
        await provider.fetch_result(
            security_ids=(security_id,),
            as_of_date=datetime(2026, 7, 24, 17, 2, tzinfo=KST),
        )

    assert len(requester.requests) == 2
    assert "secret_provider_body" not in repr(caught.value)


@pytest.mark.parametrize(
    "base_url",
    (
        "http://openapi.koreainvestment.com:9443",
        "https://example.com:9443",
        "https://openapi.koreainvestment.com:443",
        "https://openapi.koreainvestment.com:9443/account",
        "https://openapi.koreainvestment.com:9443?next=order",
    ),
)
def test_transport_rejects_non_allowlisted_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        KISHTTPTransport(
            credentials=KISMarketDataCredentials(
                "fixture-app-key", "fixture-app-secret"
            ),
            symbols=("005930",),
            requester=SequenceRequester([]),
            base_url=base_url,
        )


def test_transport_has_only_allowlisted_http_paths_and_no_broker_import_or_method() -> None:
    tree = ast.parse(inspect.getsource(kis_http))
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
    public_methods = {
        name
        for name in dir(KISHTTPTransport)
        if not name.startswith("_") and callable(getattr(KISHTTPTransport, name))
    }

    assert not any(
        module == "trading" or module.startswith("trading.") for module in imported_modules
    )
    assert literal_paths == {TOKEN_PATH, DAILY_PRICE_PATH}
    assert public_methods == {"fetch"}
