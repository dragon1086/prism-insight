from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.fmp import (
    CapabilityStatus,
    FMPEventKind,
    FMPFallbackMode,
    FMPFallbackReason,
    FMPFallbackTransport,
    FMPInstrument,
    FMPMarketDataProvider,
    FMPRateLimitError,
    FMPTimeoutError,
)
from prism_core.data.providers.fmp_models import (
    FMPApiKey,
    FMPFallbackRequest,
    FMPFallbackResponseEnvelope,
    FMPRequest,
    FMPResponseEnvelope,
)


UTC = ZoneInfo("UTC")
SECRET = "fmp-secret-never-render"
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000091"))
SECOND_SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000092"))
AS_OF = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
INGESTED = datetime(2026, 7, 24, 1, 1, tzinfo=UTC)


def price_page_payload(
    rows: list[object],
    *,
    page: int = 1,
    total_pages: int = 1,
    has_more: bool = False,
    next_page: int | None = None,
) -> dict[str, object]:
    return {
        "data": rows,
        "pagination": {
            "page": page,
            "totalPages": total_pages,
            "hasMore": has_more,
            "nextPage": next_page,
        },
    }


def test_secret_is_redacted_and_raw_envelope_is_immutable() -> None:
    api_key = FMPApiKey(SECRET)
    request = FMPRequest(
        operation="probe_capability",
        path="/stable/historical-price-eod/full",
        params={"symbol": "AAPL", "limit": 1},
    )
    raw = {"data": []}
    envelope = FMPResponseEnvelope(
        status_code=200,
        source_record_id="fmp:prices:AAPL:2026-07-23:page-1",
        revision=2,
        observed_at=datetime(2026, 7, 23, 20, 0, tzinfo=UTC),
        available_at=datetime(2026, 7, 23, 20, 1, tzinfo=UTC),
        payload=raw,
        quality=DataQualityStatus.FRESH,
    )
    source_hash = envelope.source_hash

    raw["data"].append({"close": "fabricated-later"})

    rendered = " ".join((repr(api_key), str(api_key), repr(request), repr(envelope)))
    assert SECRET not in rendered
    assert envelope.payload == {"data": []}
    assert envelope.source_hash == source_hash
    assert len(source_hash) == 64
    assert request.canonical_identity == request.canonical_identity
    assert len(request.request_hash) == 64


@pytest.mark.parametrize("name", ["apikey", "api_key", "token", "access-token"])
def test_request_rejects_secret_bearing_parameters(name: str) -> None:
    with pytest.raises(ValueError, match="secret parameters"):
        FMPRequest(operation="probe", path="/stable/test", params={name: SECRET})


@pytest.mark.asyncio
async def test_capability_probe_requires_explicit_supported_marker() -> None:
    class ProbeTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            assert request.operation == "probe_capability"
            assert SECRET not in request.canonical_identity
            assert api_key.get_secret_value() == SECRET
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id="fmp:capability:historical-price-eod",
                revision=0,
                observed_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                available_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                payload={"entitled": True},
            )

    provider = FMPMarketDataProvider(
        transport=ProbeTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.probe_capability(as_of_date=AS_OF)

    assert result.status is CapabilityStatus.SUPPORTED
    assert result.events == ()
    assert result.response is not None
    assert SECRET not in repr(provider)
    assert SECRET not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "payload", "expected"),
    [
        (401, {"error": "invalid credential"}, CapabilityStatus.FORBIDDEN),
        (403, {"error": "plan restriction"}, CapabilityStatus.FORBIDDEN),
        (404, {"error": "unsupported endpoint"}, CapabilityStatus.FORBIDDEN),
        (200, {"entitled": False}, CapabilityStatus.FORBIDDEN),
        (200, {"unexpected": True}, CapabilityStatus.MALFORMED),
    ],
)
async def test_capability_probe_distinguishes_forbidden_from_malformed(
    status_code: int,
    payload: object,
    expected: CapabilityStatus,
) -> None:
    attempts = 0

    class ProbeTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            nonlocal attempts
            attempts += 1
            return FMPResponseEnvelope(
                status_code=status_code,
                source_record_id="fmp:capability:fixture",
                revision=0,
                observed_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                available_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                payload=payload,
            )

    provider = FMPMarketDataProvider(
        transport=ProbeTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(),
        clock=lambda: INGESTED,
    )

    result = await provider.probe_capability(as_of_date=AS_OF)

    assert result.status is expected
    assert attempts == 1
    assert len(result.events) == 1
    assert SECRET not in result.events[0].detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_kind"),
    [
        ("timeout", CapabilityStatus.TIMEOUT, FMPEventKind.TIMEOUT),
        ("raised_rate_limit", CapabilityStatus.RATE_LIMIT, FMPEventKind.RATE_LIMIT),
        ("returned_429", CapabilityStatus.RATE_LIMIT, FMPEventKind.RATE_LIMIT),
    ],
)
async def test_capability_retry_outcomes_are_bounded_observable_and_sanitized(
    mode: str,
    expected_status: CapabilityStatus,
    expected_kind: FMPEventKind,
) -> None:
    attempts = 0
    sleeps: list[float] = []

    class RetryTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            nonlocal attempts
            attempts += 1
            if mode == "timeout":
                raise FMPTimeoutError(f"timeout with {SECRET}")
            if mode == "raised_rate_limit":
                raise FMPRateLimitError(f"rate limit with {SECRET}")
            return FMPResponseEnvelope(
                status_code=429,
                source_record_id="fmp:capability:rate-limit",
                revision=0,
                observed_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                available_at=datetime(2026, 7, 24, 0, 59, tzinfo=UTC),
                payload={"error": f"rate limited {SECRET}"},
            )

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    provider = FMPMarketDataProvider(
        transport=RetryTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(),
        clock=lambda: INGESTED,
        max_attempts=2,
        sleeper=sleeper,
    )

    result = await provider.probe_capability(as_of_date=AS_OF)

    assert result.status is expected_status
    assert attempts == 2
    assert sleeps == [1.0]
    assert [event.kind for event in result.events] == [
        expected_kind,
        expected_kind,
        FMPEventKind.RETRY_EXHAUSTED,
    ]
    assert SECRET not in " ".join(event.detail for event in result.events)
    assert result.response is None


class PricePageTransport:
    async def execute(
        self, request: FMPRequest, *, api_key: FMPApiKey
    ) -> FMPResponseEnvelope:
        assert request.operation == "fetch_price_page"
        assert request.params == {"page": 1, "symbols": ["AAPL"]}
        assert SECRET not in request.canonical_identity
        assert api_key.get_secret_value() == SECRET
        return FMPResponseEnvelope(
            status_code=200,
            source_record_id="fmp:prices:AAPL:2026-07-23:page-1",
            revision=2,
            observed_at=datetime(2026, 7, 23, 20, 0, tzinfo=UTC),
            available_at=datetime(2026, 7, 23, 20, 1, tzinfo=UTC),
            payload=price_page_payload(
                [
                    {
                        "symbol": "AAPL",
                        "date": "2026-07-23",
                        "rawOpen": "210.10",
                        "rawHigh": "214.25",
                        "rawLow": "209.80",
                        "rawClose": "213.90",
                        "rawVolume": "51500000",
                        "adjustedOpen": "105.05",
                        "adjustedHigh": "107.125",
                        "adjustedLow": "104.90",
                        "adjustedClose": "106.95",
                        "adjustedVolume": "103000000",
                        "adjustmentAsOf": "2026-07-23T20:01:00+00:00",
                    }
                ]
            ),
        )


@pytest.mark.asyncio
async def test_one_fixture_page_normalizes_us_price_snapshot_with_pit_provenance() -> None:
    provider = FMPMarketDataProvider(
        transport=PricePageTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.events == ()
    assert result.snapshot.market == "US"
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.raw_payloads) == 1
    assert len(result.snapshot.symbol_mappings) == 1
    mapping = result.snapshot.symbol_mappings[0]
    assert mapping.security_id == SECURITY_ID
    assert mapping.provider == "fmp"
    assert mapping.provider_symbol == "AAPL"
    assert mapping.source_hash == result.raw_payloads[0].source_hash
    bar = result.snapshot.price_bars[0]
    assert bar.security_id == SECURITY_ID
    assert bar.provider == "fmp"
    assert bar.provider_symbol == "AAPL"
    assert bar.source_record_id == "fmp:prices:AAPL:2026-07-23:page-1:price:AAPL:2026-07-23"
    assert bar.source_hash == result.raw_payloads[0].source_hash
    assert bar.revision == 2
    assert bar.currency == "USD"
    assert bar.raw_close == Decimal("213.90")
    assert bar.adjusted_close == Decimal("106.95")
    assert bar.adjustment_as_of == datetime(2026, 7, 23, 20, 1, tzinfo=UTC)
    assert bar.timing.observed_at == datetime(2026, 7, 23, 20, 0, tzinfo=UTC)
    assert bar.timing.available_at == datetime(2026, 7, 23, 20, 1, tzinfo=UTC)
    assert bar.timing.ingested_at == INGESTED
    assert bar.timing.as_of_date == AS_OF
    assert len(result.snapshot.content_hash) == 64


@pytest.mark.asyncio
async def test_bounded_multi_page_prices_are_collected_and_ordered_deterministically() -> None:
    calls: list[tuple[int, str]] = []

    class TwoPageTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            page = int(request.params["page"])
            calls.append((page, request.request_hash))
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation=request.operation,
                    path=request.path,
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=api_key,
            )
            row = dict(base.payload["data"][0])
            if page == 1:
                payload = price_page_payload(
                    [row], page=1, total_pages=2, has_more=True, next_page=2
                )
            else:
                row["date"] = "2026-07-22"
                payload = price_page_payload(
                    [row], page=2, total_pages=2, has_more=False, next_page=None
                )
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=f"fmp:prices:AAPL:page-{page}",
                revision=2,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=payload,
            )

    provider = FMPMarketDataProvider(
        transport=TwoPageTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_pages=2,
        max_requests=3,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert [page for page, _ in calls] == [1, 2]
    assert len({request_hash for _, request_hash in calls}) == 2
    assert result.request_hashes == tuple(request_hash for _, request_hash in calls)
    assert [bar.bar_start.date().isoformat() for bar in result.snapshot.price_bars] == [
        "2026-07-22",
        "2026-07-23",
    ]
    assert [page.source_record_id for page in result.raw_payloads] == [
        "fmp:prices:AAPL:page-1",
        "fmp:prices:AAPL:page-2",
    ]
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert result.events == ()


@pytest.mark.asyncio
async def test_one_aggregate_request_budget_covers_retries_across_all_pages() -> None:
    class RetriedTwoPageTransport:
        def __init__(self, *, fail_second_page: bool = False) -> None:
            self.calls = 0
            self.page_one_calls = 0
            self.fail_second_page = fail_second_page

        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            self.calls += 1
            page = int(request.params["page"])
            if page == 1:
                self.page_one_calls += 1
                if self.page_one_calls == 1:
                    raise FMPTimeoutError("sanitized by provider")
            elif self.fail_second_page:
                raise FMPTimeoutError("sanitized by provider")
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation=request.operation,
                    path=request.path,
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=api_key,
            )
            payload = base.payload
            assert isinstance(payload, dict)
            rows = payload["data"]
            assert isinstance(rows, list)
            row = dict(rows[0])
            if page == 2:
                row["date"] = "2026-07-22"
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=f"fmp:prices:AAPL:budget-page-{page}",
                revision=2,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload(
                    [row],
                    page=page,
                    total_pages=2,
                    has_more=page == 1,
                    next_page=2 if page == 1 else None,
                ),
            )

    instrument = FMPInstrument(
        security_id=SECURITY_ID,
        fmp_symbol="AAPL",
        valid_from=datetime(1980, 12, 12, tzinfo=UTC),
    )
    retried_transport = RetriedTwoPageTransport()
    retried = FMPMarketDataProvider(
        transport=retried_transport,
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_attempts=2,
        max_pages=2,
        max_requests=3,
    )
    direct_transport = RetriedTwoPageTransport()
    direct_transport.page_one_calls = 1
    direct = FMPMarketDataProvider(
        transport=direct_transport,
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_attempts=2,
        max_pages=2,
        max_requests=3,
    )

    retried_result = await retried.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )
    direct_result = await direct.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert retried_transport.calls == 3
    assert direct_transport.calls == 2
    assert retried_result.snapshot.snapshot_id == direct_result.snapshot.snapshot_id
    assert [item.kind for item in retried_result.events] == [FMPEventKind.TIMEOUT]

    exhausted_transport = RetriedTwoPageTransport(fail_second_page=True)
    exhausted = FMPMarketDataProvider(
        transport=exhausted_transport,
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_attempts=2,
        max_pages=2,
        max_requests=3,
    )
    exhausted_result = await exhausted.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert exhausted_transport.calls == 3
    assert len(exhausted_result.raw_payloads) == 1
    assert exhausted_result.snapshot.price_bars == ()
    assert exhausted_result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert [item.kind for item in exhausted_result.events] == [
        FMPEventKind.TIMEOUT,
        FMPEventKind.TIMEOUT,
        FMPEventKind.RETRY_EXHAUSTED,
        FMPEventKind.FALLBACK_DISABLED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("violation", "expected_kind"),
    [
        ("missing_metadata", FMPEventKind.PAGINATION_MALFORMED),
        ("missing_terminal_page", FMPEventKind.MISSING_PAGE),
        ("repeated_page", FMPEventKind.REPEATED_PAGE),
        ("skipped_page", FMPEventKind.MISSING_PAGE),
        ("changed_total", FMPEventKind.CONFLICT),
        ("over_limit", FMPEventKind.PAGE_LIMIT_EXCEEDED),
    ],
)
async def test_pagination_integrity_failures_are_distinct_and_fail_closed(
    violation: str,
    expected_kind: FMPEventKind,
) -> None:
    class InvalidPaginationTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            page = int(request.params["page"])
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation=request.operation,
                    path=request.path,
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=api_key,
            )
            payload = base.payload
            assert isinstance(payload, dict)
            rows = payload["data"]
            assert isinstance(rows, list)
            if violation == "missing_metadata":
                page_payload: object = {"data": rows}
            elif violation == "missing_terminal_page":
                page_payload = price_page_payload(
                    rows, page=1, total_pages=2, has_more=False, next_page=None
                )
            elif violation == "repeated_page" and page == 2:
                page_payload = price_page_payload(
                    rows, page=1, total_pages=2, has_more=True, next_page=2
                )
            elif violation == "skipped_page" and page == 2:
                page_payload = price_page_payload(
                    rows, page=3, total_pages=3, has_more=False, next_page=None
                )
            elif violation == "changed_total" and page == 2:
                page_payload = price_page_payload(
                    rows, page=2, total_pages=3, has_more=True, next_page=3
                )
            elif violation == "over_limit":
                page_payload = price_page_payload(
                    rows, page=1, total_pages=4, has_more=True, next_page=2
                )
            else:
                page_payload = price_page_payload(
                    rows,
                    page=page,
                    total_pages=2,
                    has_more=page == 1,
                    next_page=2 if page == 1 else None,
                )
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=f"fmp:pagination:{violation}:page-{page}",
                revision=0,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=page_payload,
            )

    provider = FMPMarketDataProvider(
        transport=InvalidPaginationTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_pages=3,
        max_requests=4,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality in {
        DataQualityStatus.UNAVAILABLE,
        DataQualityStatus.CONFLICT,
    }
    assert any(item.kind is expected_kind for item in result.events)
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_conflicting_duplicate_page_identity_marks_the_whole_snapshot_conflict() -> None:
    class ConflictingIdentityTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            page = int(request.params["page"])
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation=request.operation,
                    path=request.path,
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=api_key,
            )
            payload = base.payload
            assert isinstance(payload, dict)
            rows = payload["data"]
            assert isinstance(rows, list)
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id="fmp:prices:AAPL:reused-page-identity",
                revision=2,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload(
                    rows,
                    page=page,
                    total_pages=2,
                    has_more=page == 1,
                    next_page=2 if page == 1 else None,
                ),
            )

    provider = FMPMarketDataProvider(
        transport=ConflictingIdentityTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_pages=2,
        max_requests=2,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert len(result.raw_payloads) == 2
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.CONFLICT
    assert [item.kind for item in result.events] == [
        FMPEventKind.CONFLICT,
        FMPEventKind.FALLBACK_DISABLED,
    ]


@pytest.mark.asyncio
async def test_non_success_page_with_valid_looking_data_fails_closed() -> None:
    class ErrorStatusTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=500,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
            )

    provider = FMPMarketDataProvider(
        transport=ErrorStatusTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert len(result.raw_payloads) == 1
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert [item.kind for item in result.events] == [
        FMPEventKind.UNAVAILABLE,
        FMPEventKind.FALLBACK_DISABLED,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_kind"),
    [
        ("missing_close", FMPEventKind.PARTIAL),
        ("invalid_ohlc", FMPEventKind.MALFORMED),
        ("partial_adjustment", FMPEventKind.MALFORMED),
    ],
)
async def test_invalid_core_price_rows_are_withheld_with_explicit_quality_event(
    mutation: str,
    expected_kind: FMPEventKind,
) -> None:
    class InvalidRowTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            row = dict(base.payload["data"][0])
            if mutation == "missing_close":
                del row["rawClose"]
            elif mutation == "invalid_ohlc":
                row["rawHigh"] = "200"
            else:
                del row["adjustedVolume"]
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload([row]),
            )

    provider = FMPMarketDataProvider(
        transport=InvalidRowTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert any(event.kind is expected_kind for event in result.events)


@pytest.mark.asyncio
async def test_non_object_price_row_degrades_snapshot_instead_of_silent_fresh() -> None:
    class NonObjectRowTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            payload = base.payload
            assert isinstance(payload, dict)
            rows = payload.get("data")
            assert isinstance(rows, list)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload([rows[0], "malformed-row"]),
            )

    provider = FMPMarketDataProvider(
        transport=NonObjectRowTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert any(event.kind is FMPEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("envelope_quality", "expected_kind"),
    [
        (DataQualityStatus.STALE, FMPEventKind.STALE),
        (DataQualityStatus.PARTIAL, FMPEventKind.PARTIAL),
        (DataQualityStatus.CONFLICT, FMPEventKind.CONFLICT),
    ],
)
async def test_provider_quality_is_preserved_and_never_relabelled_fresh(
    envelope_quality: DataQualityStatus,
    expected_kind: FMPEventKind,
) -> None:
    class QualityTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=envelope_quality,
            )

    provider = FMPMarketDataProvider(
        transport=QualityTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.primary_quality is envelope_quality
    assert result.fallback_mode is FMPFallbackMode.DISABLED
    assert result.selected_provider is None
    assert result.snapshot.quality is envelope_quality
    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert any(event.kind is expected_kind for event in result.events)
    assert any(event.kind is FMPEventKind.FALLBACK_DISABLED for event in result.events)


@pytest.mark.asyncio
async def test_explicit_research_fallback_keeps_primary_and_fallback_provenance_separate() -> None:
    class StalePrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    class FixtureFallback(FMPFallbackTransport):
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            assert request.provider == "yfinance_fixture"
            assert request.params == {
                "as_of_date": AS_OF.isoformat(),
                "symbols": ["AAPL"],
            }
            return FMPFallbackResponseEnvelope(
                provider="yfinance_fixture",
                status_code=200,
                source_record_id="yfinance-fixture:AAPL:2026-07-23",
                revision=4,
                observed_at=datetime(2026, 7, 23, 20, 2, tzinfo=UTC),
                available_at=datetime(2026, 7, 23, 20, 3, tzinfo=UTC),
                payload=(await PricePageTransport().execute(
                    FMPRequest(
                        operation="fetch_price_page",
                        path="/stable/historical-price-eod/full",
                        params={"page": 1, "symbols": ["AAPL"]},
                    ),
                    api_key=FMPApiKey(SECRET),
                )).payload,
                quality=DataQualityStatus.FRESH,
            )

    provider = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=FixtureFallback(),
        fallback_provider="yfinance_fixture",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.primary_quality is DataQualityStatus.STALE
    assert len(result.raw_payloads) == 1
    assert len(result.fallback_raw_payloads) == 1
    assert result.raw_payloads[0].source_record_id.startswith("fmp:")
    assert result.fallback_raw_payloads[0].source_record_id.startswith("yfinance-fixture:")
    assert result.selected_provider == "yfinance_fixture"
    assert result.fallback_reason is FMPFallbackReason.STALE
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert {bar.provider for bar in result.snapshot.price_bars} == {"yfinance_fixture"}
    assert {mapping.provider for mapping in result.snapshot.symbol_mappings} == {
        "yfinance_fixture"
    }
    assert result.snapshot.price_bars[0].source_hash == result.fallback_raw_payloads[0].source_hash
    assert result.snapshot.price_bars[0].revision == 4
    assert result.snapshot.price_bars[0].timing.observed_at == datetime(
        2026, 7, 23, 20, 2, tzinfo=UTC
    )
    assert len(result.fallback_request_hashes) == 1
    assert SECRET not in result.fallback_request_hashes[0]
    assert any(event.kind is FMPEventKind.FALLBACK_SELECTED for event in result.events)


@pytest.mark.asyncio
async def test_fallback_cannot_reuse_primary_source_record_identity() -> None:
    primary_source_record_id = "fmp:prices:AAPL:2026-07-23:page-1"

    class StalePrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            assert base.source_record_id == primary_source_record_id
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    class ReusedIdentityFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=200,
                source_record_id=primary_source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.FRESH,
            )

    provider = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=ReusedIdentityFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.selected_provider is None
    assert result.snapshot.price_bars == ()
    assert result.fallback_raw_payloads == ()
    assert any(
        event.kind is FMPEventKind.FALLBACK_MALFORMED for event in result.events
    )


@pytest.mark.asyncio
async def test_aggregate_budget_reserves_one_bounded_fallback_attempt_after_rate_limit() -> None:
    primary_calls = 0
    fallback_calls = 0

    class LimitedPrimary:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            nonlocal primary_calls
            primary_calls += 1
            return FMPResponseEnvelope(
                status_code=429,
                source_record_id="fmp:prices:rate-limited",
                revision=0,
                observed_at=datetime(2026, 7, 23, 20, 0, tzinfo=UTC),
                available_at=datetime(2026, 7, 23, 20, 1, tzinfo=UTC),
                payload={"error": "limited"},
            )

    class FreshFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            nonlocal fallback_calls
            fallback_calls += 1
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=200,
                source_record_id="fixture-alt:AAPL:2026-07-23",
                revision=0,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.FRESH,
            )

    provider = FMPMarketDataProvider(
        transport=LimitedPrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_attempts=2,
        max_requests=2,
        fallback_max_attempts=1,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=FreshFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert primary_calls == 1
    assert fallback_calls == 1
    assert result.selected_provider == "fixture_alt"
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert [event.kind for event in result.events] == [
        FMPEventKind.RATE_LIMIT,
        FMPEventKind.RETRY_EXHAUSTED,
        FMPEventKind.FALLBACK_SELECTED,
    ]


def test_explicit_aggregate_budget_must_leave_one_primary_attempt() -> None:
    with pytest.raises(
        ValueError, match="max_requests must leave at least one primary request"
    ):
        FMPMarketDataProvider(
            transport=PricePageTransport(),
            api_key=FMPApiKey(SECRET),
            instruments=(),
            clock=lambda: INGESTED,
            max_requests=1,
            fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
            fallback_transport=object(),  # type: ignore[arg-type]
            fallback_provider="fixture_alt",
            fallback_max_attempts=1,
        )


@pytest.mark.asyncio
async def test_fallback_retry_is_bounded_observable_and_retry_stable() -> None:
    class StalePrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    class RetryFallback:
        def __init__(self, *, fail_once: bool) -> None:
            self.calls = 0
            self.fail_once = fail_once

        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            self.calls += 1
            if self.fail_once and self.calls == 1:
                raise FMPTimeoutError("fallback timeout is sanitized")
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=200,
                source_record_id="fixture-alt:AAPL:stable",
                revision=1,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.FRESH,
            )

    instrument = FMPInstrument(
        security_id=SECURITY_ID,
        fmp_symbol="AAPL",
        valid_from=datetime(1980, 12, 12, tzinfo=UTC),
    )
    retried_transport = RetryFallback(fail_once=True)
    direct_transport = RetryFallback(fail_once=False)
    retried = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_requests=3,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=retried_transport,
        fallback_provider="fixture_alt",
        fallback_max_attempts=2,
    )
    direct = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_requests=3,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=direct_transport,
        fallback_provider="fixture_alt",
        fallback_max_attempts=2,
    )

    retried_result = await retried.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )
    direct_result = await direct.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert retried_transport.calls == 2
    assert direct_transport.calls == 1
    assert retried_result.snapshot.snapshot_id == direct_result.snapshot.snapshot_id
    assert retried_result.fallback_request_hashes == direct_result.fallback_request_hashes
    assert [event.kind for event in retried_result.events] == [
        FMPEventKind.STALE,
        FMPEventKind.FALLBACK_TIMEOUT,
        FMPEventKind.FALLBACK_SELECTED,
    ]


@pytest.mark.asyncio
async def test_recovered_primary_timeout_does_not_replace_stale_fallback_reason() -> None:
    class RetriedStalePrimary(PricePageTransport):
        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            self.calls += 1
            if self.calls == 1:
                raise FMPTimeoutError("transient primary timeout")
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    class FreshFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=200,
                source_record_id="fixture-alt:AAPL:recovered-primary-timeout",
                revision=0,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.FRESH,
            )

    primary = RetriedStalePrimary()
    provider = FMPMarketDataProvider(
        transport=primary,
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_attempts=2,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=FreshFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert primary.calls == 2
    assert result.primary_quality is DataQualityStatus.STALE
    assert result.fallback_reason is FMPFallbackReason.STALE
    assert result.selected_provider == "fixture_alt"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fallback_quality", "status_code", "payload_mode", "expected_kind"),
    [
        (DataQualityStatus.STALE, 200, "valid", FMPEventKind.FALLBACK_STALE),
        (DataQualityStatus.PARTIAL, 200, "valid", FMPEventKind.FALLBACK_PARTIAL),
        (DataQualityStatus.CONFLICT, 200, "valid", FMPEventKind.FALLBACK_CONFLICT),
        (
            DataQualityStatus.UNAVAILABLE,
            200,
            "valid",
            FMPEventKind.FALLBACK_UNAVAILABLE,
        ),
        (DataQualityStatus.FRESH, 500, "valid", FMPEventKind.FALLBACK_UNAVAILABLE),
        (DataQualityStatus.FRESH, 200, "malformed", FMPEventKind.FALLBACK_MALFORMED),
        (DataQualityStatus.FRESH, 200, "future", FMPEventKind.FALLBACK_MALFORMED),
    ],
)
async def test_degraded_fallback_is_retained_classified_and_never_normalized(
    fallback_quality: DataQualityStatus,
    status_code: int,
    payload_mode: str,
    expected_kind: FMPEventKind,
) -> None:
    class PartialPrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.PARTIAL,
            )

    class DegradedFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            payload = base.payload if payload_mode != "malformed" else {"data": "bad"}
            available_at = (
                datetime(2026, 7, 24, 1, 30, tzinfo=UTC)
                if payload_mode == "future"
                else base.available_at
            )
            observed_at = (
                datetime(2026, 7, 24, 1, 29, tzinfo=UTC)
                if payload_mode == "future"
                else base.observed_at
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=status_code,
                source_record_id=f"fixture-alt:{payload_mode}:{fallback_quality.value}",
                revision=2,
                observed_at=observed_at,
                available_at=available_at,
                payload=payload,
                quality=fallback_quality,
            )

    provider = FMPMarketDataProvider(
        transport=PartialPrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=DegradedFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.primary_quality is DataQualityStatus.PARTIAL
    assert result.selected_provider is None
    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert len(result.raw_payloads) == 1
    assert len(result.fallback_raw_payloads) == 1
    fallback_event = next(event for event in result.events if event.provider == "fixture_alt")
    assert fallback_event.kind is expected_kind
    assert SECRET not in fallback_event.detail


@pytest.mark.asyncio
async def test_research_mode_without_injected_fallback_is_explicit_and_fail_closed() -> None:
    class StalePrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    provider = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.fallback_reason is FMPFallbackReason.STALE
    assert result.selected_provider is None
    assert result.snapshot.price_bars == ()
    assert any(
        event.kind is FMPEventKind.FALLBACK_MISSING_TRANSPORT
        for event in result.events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_mode", ["conflict", "malformed"])
async def test_conflicting_or_malformed_primary_never_invokes_fallback(
    primary_mode: str,
) -> None:
    fallback_calls = 0

    class InvalidPrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            payload = base.payload
            if primary_mode == "malformed":
                payload = {
                    "data": "bad",
                    "pagination": {
                        "page": 1,
                        "totalPages": 1,
                        "hasMore": False,
                        "nextPage": None,
                    },
                }
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=payload,
                quality=(
                    DataQualityStatus.CONFLICT
                    if primary_mode == "conflict"
                    else DataQualityStatus.FRESH
                ),
            )

    class MustNotCallFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            nonlocal fallback_calls
            fallback_calls += 1
            raise AssertionError("invalid primary evidence must not be laundered")

    provider = FMPMarketDataProvider(
        transport=InvalidPrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=MustNotCallFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert fallback_calls == 0
    assert result.fallback_reason is None
    assert result.selected_provider is None
    assert result.snapshot.price_bars == ()
    assert any(event.kind is FMPEventKind.FALLBACK_INELIGIBLE for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_location", ["source_record_id", "payload"])
async def test_fallback_secret_echo_is_not_retained_or_rendered(
    secret_location: str,
) -> None:
    class StalePrimary(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=200,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.STALE,
            )

    class SecretEchoFallback:
        async def execute(
            self, request: FMPFallbackRequest
        ) -> FMPFallbackResponseEnvelope:
            base = await PricePageTransport().execute(
                FMPRequest(
                    operation="fetch_price_page",
                    path="/stable/historical-price-eod/full",
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=FMPApiKey(SECRET),
            )
            return FMPFallbackResponseEnvelope(
                provider="fixture_alt",
                status_code=200,
                source_record_id=(
                    f"fixture-alt:{SECRET}"
                    if secret_location == "source_record_id"
                    else "fixture-alt:safe"
                ),
                revision=1,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=(
                    {"data": [], "debug": SECRET}
                    if secret_location == "payload"
                    else base.payload
                ),
                quality=DataQualityStatus.FRESH,
            )

    provider = FMPMarketDataProvider(
        transport=StalePrimary(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        fallback_mode=FMPFallbackMode.RESEARCH_FIXTURE,
        fallback_transport=SecretEchoFallback(),
        fallback_provider="fixture_alt",
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,), as_of_date=AS_OF
    )

    assert result.fallback_raw_payloads == ()
    assert result.snapshot.price_bars == ()
    assert any(event.kind is FMPEventKind.FALLBACK_MALFORMED for event in result.events)
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_provider_unavailable_payload_is_retained_but_not_normalized() -> None:
    class UnavailableTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
                quality=DataQualityStatus.UNAVAILABLE,
            )

    provider = FMPMarketDataProvider(
        transport=UnavailableTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert len(result.raw_payloads) == 1
    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert any(event.kind is FMPEventKind.UNAVAILABLE for event in result.events)


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["future_availability", "before_market_close"])
async def test_point_in_time_violations_are_withheld_without_contract_exception(
    violation: str,
) -> None:
    class PITViolationTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            if violation == "future_availability":
                observed_at = datetime(2026, 7, 24, 0, 59, tzinfo=UTC)
                available_at = datetime(2026, 7, 24, 1, 30, tzinfo=UTC)
            else:
                observed_at = datetime(2026, 7, 23, 19, 0, tzinfo=UTC)
                available_at = datetime(2026, 7, 23, 19, 1, tzinfo=UTC)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=observed_at,
                available_at=available_at,
                payload=base.payload,
            )

    provider = FMPMarketDataProvider(
        transport=PITViolationTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert any(event.kind is FMPEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_inactive_symbol_interval_fails_closed_before_transport() -> None:
    calls = 0

    class MustNotCallTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            nonlocal calls
            calls += 1
            raise AssertionError("inactive instruments must not reach transport")

    provider = FMPMarketDataProvider(
        transport=MustNotCallTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
                valid_to=datetime(2026, 7, 23, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert calls == 0
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert [event.kind for event in result.events] == [
        FMPEventKind.INELIGIBLE,
        FMPEventKind.FALLBACK_DISABLED,
    ]


@pytest.mark.asyncio
async def test_unknown_and_missing_symbols_are_explicit_without_guessing() -> None:
    class SymbolMismatchTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(
                FMPRequest(
                    operation=request.operation,
                    path=request.path,
                    params={"page": 1, "symbols": ["AAPL"]},
                ),
                api_key=api_key,
            )
            unknown = dict(base.payload["data"][0])
            unknown["symbol"] = "UNKNOWN"
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload([base.payload["data"][0], unknown]),
            )

    provider = FMPMarketDataProvider(
        transport=SymbolMismatchTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
            FMPInstrument(
                security_id=SECOND_SECURITY_ID,
                fmp_symbol="MSFT",
                valid_from=datetime(1986, 3, 13, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID, SECOND_SECURITY_ID),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert {event.kind for event in result.events} >= {
        FMPEventKind.UNMATCHED,
        FMPEventKind.MISSING,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conflicting", "expected_quality", "expected_count"),
    [
        (False, DataQualityStatus.FRESH, 1),
        (True, DataQualityStatus.CONFLICT, 0),
    ],
)
async def test_duplicate_rows_dedupe_or_fail_closed_on_conflict(
    conflicting: bool,
    expected_quality: DataQualityStatus,
    expected_count: int,
) -> None:
    class DuplicateTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            duplicate = dict(base.payload["data"][0])
            if conflicting:
                duplicate["adjustedClose"] = "106.96"
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload([base.payload["data"][0], duplicate]),
            )

    provider = FMPMarketDataProvider(
        transport=DuplicateTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert len(result.snapshot.price_bars) == expected_count
    assert len(result.snapshot.symbol_mappings) == expected_count
    assert result.snapshot.quality is expected_quality
    assert any(event.kind is FMPEventKind.CONFLICT for event in result.events) is conflicting


@pytest.mark.asyncio
async def test_response_that_echoes_secret_is_rejected_before_retention() -> None:
    class EchoTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload={
                    **price_page_payload(base.payload["data"]),
                    "requestEcho": {"apikey": SECRET},
                },
            )

    provider = FMPMarketDataProvider(
        transport=EchoTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.raw_payloads == ()
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    rendered = repr(result) + " ".join(event.detail for event in result.events)
    assert SECRET not in rendered
    assert any(event.kind is FMPEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_secret_echo_detection_handles_json_escaped_characters() -> None:
    escaped_secret = 'fmp-"quoted"-\\-line\n-π'

    class EscapedEchoTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=FMPApiKey(SECRET))
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload={
                    **price_page_payload(base.payload["data"]),
                    "nested": [escaped_secret],
                },
            )

    provider = FMPMarketDataProvider(
        transport=EscapedEchoTransport(),
        api_key=FMPApiKey(escaped_secret),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.raw_payloads == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert escaped_secret not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["future_trading_date", "future_adjustment_vintage"])
async def test_row_level_price_vintages_fail_closed(
    violation: str,
) -> None:
    class FutureRowTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            payload = base.payload
            assert isinstance(payload, dict)
            rows = payload["data"]
            assert isinstance(rows, list)
            row = dict(rows[0])
            if violation == "future_trading_date":
                row["date"] = "2026-07-25"
            else:
                row["adjustmentAsOf"] = "2026-07-24T02:00:00+00:00"
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=base.source_record_id,
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=price_page_payload([row]),
            )

    provider = FMPMarketDataProvider(
        transport=FutureRowTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.symbol_mappings == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert any(event.kind is FMPEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_source_identity_that_echoes_secret_is_rejected_before_retention() -> None:
    class SourceEchoTransport(PricePageTransport):
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            base = await super().execute(request, api_key=api_key)
            return FMPResponseEnvelope(
                status_code=base.status_code,
                source_record_id=f"fmp:prices:{SECRET}:page-1",
                revision=base.revision,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload=base.payload,
            )

    provider = FMPMarketDataProvider(
        transport=SourceEchoTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.raw_payloads == ()
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert SECRET not in repr(result)


def test_fmp_contracts_are_exported_from_public_data_packages() -> None:
    from prism_core import data
    from prism_core.data import providers

    expected = {
        "CapabilityProbeResult",
        "CapabilityStatus",
        "FMPApiKey",
        "FMPEventKind",
        "FMPFallbackMode",
        "FMPFallbackReason",
        "FMPFallbackRequest",
        "FMPFallbackResponseEnvelope",
        "FMPFallbackTransport",
        "FMPFetchResult",
        "FMPInstrument",
        "FMPMarketDataProvider",
        "FMPPagination",
        "FMPProviderEvent",
        "FMPRateLimitError",
        "FMPRequest",
        "FMPResponseEnvelope",
        "FMPTimeoutError",
        "FMPTransport",
    }

    assert expected <= set(data.__all__)
    assert expected <= set(providers.__all__)
    assert data.FMPMarketDataProvider is FMPMarketDataProvider


@pytest.mark.asyncio
async def test_price_page_retry_is_bounded_and_does_not_change_snapshot_identity() -> None:
    class RetryOnceTransport:
        def __init__(self) -> None:
            self.attempts = 0
            self.success = PricePageTransport()

        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            self.attempts += 1
            if self.attempts == 1:
                raise FMPTimeoutError(f"timeout while using {SECRET}")
            return await self.success.execute(request, api_key=api_key)

    retry_transport = RetryOnceTransport()
    instrument = FMPInstrument(
        security_id=SECURITY_ID,
        fmp_symbol="AAPL",
        valid_from=datetime(1980, 12, 12, tzinfo=UTC),
    )
    retried = FMPMarketDataProvider(
        transport=retry_transport,
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_attempts=2,
    )
    direct = FMPMarketDataProvider(
        transport=PricePageTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(instrument,),
        clock=lambda: INGESTED,
        max_attempts=2,
    )

    retried_result = await retried.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )
    direct_result = await direct.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert retry_transport.attempts == 2
    assert retried_result.snapshot.snapshot_id == direct_result.snapshot.snapshot_id
    assert retried_result.snapshot.content_hash == direct_result.snapshot.content_hash
    assert [event.kind for event in retried_result.events] == [FMPEventKind.TIMEOUT]
    assert SECRET not in repr(retried_result)


@pytest.mark.asyncio
async def test_exhausted_price_page_retry_returns_unavailable_snapshot() -> None:
    class AlwaysLimitedTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            return FMPResponseEnvelope(
                status_code=429,
                source_record_id="fmp:prices:limited",
                revision=1,
                observed_at=datetime(2026, 7, 23, 20, 0, tzinfo=UTC),
                available_at=datetime(2026, 7, 23, 20, 1, tzinfo=UTC),
                payload={"error": "rate limited"},
            )

    provider = FMPMarketDataProvider(
        transport=AlwaysLimitedTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
        max_attempts=2,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.raw_payloads == ()
    assert len(result.request_hashes) == 1
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert [event.kind for event in result.events] == [
        FMPEventKind.RATE_LIMIT,
        FMPEventKind.RATE_LIMIT,
        FMPEventKind.RETRY_EXHAUSTED,
        FMPEventKind.FALLBACK_DISABLED,
    ]


@pytest.mark.asyncio
async def test_unexpected_transport_exception_is_sanitized_and_fails_closed() -> None:
    class BrokenTransport:
        async def execute(
            self, request: FMPRequest, *, api_key: FMPApiKey
        ) -> FMPResponseEnvelope:
            raise RuntimeError(f"bad transport with {SECRET}")

    provider = FMPMarketDataProvider(
        transport=BrokenTransport(),
        api_key=FMPApiKey(SECRET),
        instruments=(
            FMPInstrument(
                security_id=SECURITY_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: INGESTED,
    )

    capability = await provider.probe_capability(as_of_date=AS_OF)
    fetched = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert capability.status is CapabilityStatus.MALFORMED
    assert fetched.snapshot.quality is DataQualityStatus.UNAVAILABLE
    rendered = repr(capability) + repr(fetched)
    assert SECRET not in rendered
    assert [event.kind for event in capability.events] == [FMPEventKind.MALFORMED]
    assert [event.kind for event in fetched.events] == [
        FMPEventKind.MALFORMED,
        FMPEventKind.FALLBACK_DISABLED,
    ]
