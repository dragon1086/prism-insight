from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus
from prism_core.data.providers.kr_official import (
    KROfficialCapability,
    KROfficialEvidenceEnvelope,
    KROfficialError,
    KROfficialProvider,
    KROfficialRequest,
    KROfficialSourceApproval,
    KROfficialSource,
    KROfficialTransportMode,
)


UTC = ZoneInfo("UTC")
OBSERVED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 7, 24, 8, 1, tzinfo=UTC)
INGESTED_AT = datetime(2026, 7, 24, 8, 2, tzinfo=UTC)
AS_OF = datetime(2026, 7, 24, 9, 0, tzinfo=UTC)
RAW = b'{"corp_code":"00126380","report_nm":"quarterly"}'


class FixtureTransport:
    mode = KROfficialTransportMode.FIXTURE

    def __init__(self, evidence: list[KROfficialEvidenceEnvelope | Exception]) -> None:
        self.evidence = evidence
        self.requests: list[KROfficialRequest] = []

    async def fetch(self, request: KROfficialRequest) -> KROfficialEvidenceEnvelope:
        self.requests.append(request)
        result = self.evidence.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class LiveFixtureTransport(FixtureTransport):
    mode = KROfficialTransportMode.LIVE


def test_evidence_preserves_dart_filing_pit_provenance() -> None:
    evidence = KROfficialEvidenceEnvelope(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:20260724000123",
        provider_symbol="00126380",
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        ingested_at=INGESTED_AT,
        as_of_date=AS_OF,
        release_id="20260724000123",
        revision=2,
        vintage="2026-07-24T08:01:00Z",
        terms_id="opendart-api-terms-2026-01",
        license_id="opendart-public-data-license",
        quality=DataQualityStatus.FRESH,
        correlation_id="req-7f83a1",
        raw_payload=RAW,
    )

    assert evidence.source is KROfficialSource.DART
    assert evidence.capability is KROfficialCapability.DART_FILING
    assert evidence.endpoint == "opendart.fss.or.kr/api/list.json"
    assert evidence.observed_at == OBSERVED_AT
    assert evidence.available_at == AVAILABLE_AT
    assert evidence.ingested_at == INGESTED_AT
    assert evidence.as_of_date == AS_OF
    assert evidence.release_id == "20260724000123"
    assert evidence.revision == 2
    assert evidence.vintage == "2026-07-24T08:01:00Z"
    assert evidence.raw_payload_hash == hashlib.sha256(RAW).hexdigest()
    assert evidence.terms_id == "opendart-api-terms-2026-01"
    assert evidence.license_id == "opendart-public-data-license"
    assert evidence.quality is DataQualityStatus.FRESH
    assert evidence.correlation_id == "req-7f83a1"
    assert evidence.raw_payload == RAW
    assert RAW.decode() not in repr(evidence)


def make_evidence(
    *,
    source: KROfficialSource,
    capability: KROfficialCapability,
    endpoint: str,
    source_record_id: str,
    raw_payload: bytes,
    quality: DataQualityStatus = DataQualityStatus.FRESH,
    fact_key: str | None = None,
    fact_hash: str | None = None,
    correlation_id: str | None = None,
    as_of_date: datetime = AS_OF,
    terms_id: str | None = None,
    license_id: str | None = None,
) -> KROfficialEvidenceEnvelope:
    return KROfficialEvidenceEnvelope(
        source=source,
        capability=capability,
        endpoint=endpoint,
        source_record_id=source_record_id,
        provider_symbol="005930",
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        ingested_at=INGESTED_AT,
        as_of_date=as_of_date,
        release_id=source_record_id.replace(":", "-"),
        revision=0,
        vintage="2026-07-24T08:01:00Z",
        terms_id=terms_id or f"{source.value.lower()}-terms-2026",
        license_id=license_id or f"{source.value.lower()}-license-2026",
        quality=quality,
        correlation_id=correlation_id or f"req-{source.value.lower()}-1",
        fact_key=fact_key,
        fact_hash=fact_hash,
        raw_payload=raw_payload,
    )


@pytest.mark.asyncio
async def test_provider_retains_three_official_capabilities_without_merging_sources() -> None:
    evidence = [
        make_evidence(
            source=KROfficialSource.KRX,
            capability=KROfficialCapability.KRX_MARKET_REFERENCE,
            endpoint="data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            source_record_id="krx:listing:005930",
            raw_payload=b'{"listed":true}',
        ),
        make_evidence(
            source=KROfficialSource.KIND,
            capability=KROfficialCapability.KIND_LISTED_COMPANY_DISCLOSURE,
            endpoint="kind.krx.co.kr/disclosure/search.do",
            source_record_id="kind:disclosure:20260724-1",
            raw_payload=b'{"disclosure":"listed-company"}',
        ),
        make_evidence(
            source=KROfficialSource.DART,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/list.json",
            source_record_id="dart:filing:20260724-1",
            raw_payload=RAW,
        ),
    ]
    transport = FixtureTransport(evidence.copy())
    requests = tuple(
        KROfficialRequest(
            source=item.source,
            capability=item.capability,
            endpoint=item.endpoint,
            provider_symbol=item.provider_symbol,
            correlation_id=item.correlation_id,
        )
        for item in evidence
    )

    result = await KROfficialProvider(transport=transport).collect(
        requests=requests, as_of_date=AS_OF
    )

    assert result.core_evidence_usable is True
    assert result.quality is DataQualityStatus.FRESH
    assert tuple(item.source for item in result.evidence) == (
        KROfficialSource.KRX,
        KROfficialSource.KIND,
        KROfficialSource.DART,
    )
    assert tuple(item.capability for item in result.evidence) == (
        KROfficialCapability.KRX_MARKET_REFERENCE,
        KROfficialCapability.KIND_LISTED_COMPANY_DISCLOSURE,
        KROfficialCapability.DART_FILING,
    )
    assert result.events == ()
    assert transport.requests == list(requests)


@pytest.mark.asyncio
async def test_missing_core_evidence_fails_closed_with_sanitized_event() -> None:
    request = KROfficialRequest(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        provider_symbol="005930",
        correlation_id="req-dart-missing",
    )
    transport = FixtureTransport([RuntimeError("secret upstream response body")])

    result = await KROfficialProvider(transport=transport).collect(
        requests=(request,), as_of_date=AS_OF
    )

    assert result.evidence == ()
    assert result.quality is DataQualityStatus.UNAVAILABLE
    assert result.core_evidence_usable is False
    assert len(result.events) == 1
    assert result.events[0].reason == "SOURCE_FETCH_FAILED"
    assert result.events[0].correlation_id == "req-dart-missing"
    assert "secret upstream response body" not in repr(result)


@pytest.mark.asyncio
async def test_conflicting_normalized_fact_hashes_are_retained_and_fail_closed() -> None:
    fact_key = "listing-status:005930:2026-07-24"
    krx = make_evidence(
        source=KROfficialSource.KRX,
        capability=KROfficialCapability.KRX_MARKET_REFERENCE,
        endpoint="data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        source_record_id="krx:listing:005930",
        raw_payload=b'{"listed":true}',
        fact_key=fact_key,
        fact_hash=hashlib.sha256(b'{"listed":true}').hexdigest(),
    )
    kind = make_evidence(
        source=KROfficialSource.KIND,
        capability=KROfficialCapability.KIND_LISTED_COMPANY_DISCLOSURE,
        endpoint="kind.krx.co.kr/disclosure/search.do",
        source_record_id="kind:listing:005930",
        raw_payload=b'{"listed":false}',
        fact_key=fact_key,
        fact_hash=hashlib.sha256(b'{"listed":false}').hexdigest(),
    )
    requests = tuple(
        KROfficialRequest(
            source=item.source,
            capability=item.capability,
            endpoint=item.endpoint,
            provider_symbol=item.provider_symbol,
            correlation_id=item.correlation_id,
        )
        for item in (krx, kind)
    )

    result = await KROfficialProvider(
        transport=FixtureTransport([krx, kind])
    ).collect(requests=requests, as_of_date=AS_OF)

    assert result.evidence == (krx, kind)
    assert result.quality is DataQualityStatus.CONFLICT
    assert result.core_evidence_usable is False
    assert [event.reason for event in result.events] == ["SOURCE_FACT_CONFLICT"]
    assert result.events[0].correlation_id is None
    assert result.events[0].fact_key == fact_key
    assert result.events[0].related_sources == (
        KROfficialSource.KRX,
        KROfficialSource.KIND,
    )


@pytest.mark.asyncio
async def test_live_transport_requires_exact_active_source_approval_and_call_bound() -> None:
    evidence = make_evidence(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:filing:20260724-1",
        raw_payload=RAW,
    )
    request = KROfficialRequest(
        source=evidence.source,
        capability=evidence.capability,
        endpoint=evidence.endpoint,
        provider_symbol=evidence.provider_symbol,
        correlation_id=evidence.correlation_id,
    )
    approval = KROfficialSourceApproval(
        approval_id="kr-official-dart-20260724",
        manifest_hash=hashlib.sha256(b"durable approval manifest").hexdigest(),
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        terms_id=evidence.terms_id,
        license_id=evidence.license_id,
        credential_scope="dart-read-only-filings",
        max_calls=1,
        max_cost_krw=0,
        approved_at=datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    )
    provider = KROfficialProvider(
        transport=LiveFixtureTransport([evidence]),
        approvals=(approval,),
        clock=lambda: datetime(2026, 7, 24, 8, 30, tzinfo=UTC),
    )

    result = await provider.collect(requests=(request,), as_of_date=AS_OF)

    assert result.core_evidence_usable is True
    with pytest.raises(KROfficialError, match="call bound"):
        await provider.collect(requests=(request,), as_of_date=AS_OF)


@pytest.mark.parametrize(
    "quality",
    [
        DataQualityStatus.STALE,
        DataQualityStatus.PARTIAL,
        DataQualityStatus.UNAVAILABLE,
        DataQualityStatus.CONFLICT,
    ],
)
@pytest.mark.asyncio
async def test_non_fresh_core_evidence_is_never_usable(
    quality: DataQualityStatus,
) -> None:
    evidence = make_evidence(
        source=KROfficialSource.KRX,
        capability=KROfficialCapability.KRX_MARKET_REFERENCE,
        endpoint="data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        source_record_id="krx:listing:005930",
        raw_payload=b'{"listed":true}',
        quality=quality,
    )
    request = KROfficialRequest(
        source=evidence.source,
        capability=evidence.capability,
        endpoint=evidence.endpoint,
        provider_symbol=evidence.provider_symbol,
        correlation_id=evidence.correlation_id,
    )

    result = await KROfficialProvider(
        transport=FixtureTransport([evidence])
    ).collect(requests=(request,), as_of_date=AS_OF)

    assert result.quality is quality
    assert result.core_evidence_usable is False


def test_event_or_release_date_never_substitutes_for_available_at() -> None:
    with pytest.raises(ValueError, match="available_at must be at or before as_of_date"):
        KROfficialEvidenceEnvelope(
            source=KROfficialSource.DART,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/list.json",
            source_record_id="dart:old-event-new-release",
            provider_symbol="00126380",
            observed_at=OBSERVED_AT,
            available_at=datetime(2026, 7, 25, 8, 1, tzinfo=UTC),
            ingested_at=datetime(2026, 7, 25, 8, 2, tzinfo=UTC),
            as_of_date=AS_OF,
            release_id="event-date-2020-01-01",
            revision=0,
            vintage="2026-07-25T08:01:00Z",
            terms_id="opendart-api-terms-2026-01",
            license_id="opendart-public-data-license",
            quality=DataQualityStatus.FRESH,
            correlation_id="req-future-availability",
            raw_payload=RAW,
        )


def test_evidence_rejects_untyped_quality_and_boolean_revision() -> None:
    common = dict(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:strict-types",
        provider_symbol="00126380",
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        ingested_at=INGESTED_AT,
        as_of_date=AS_OF,
        release_id="strict-types",
        vintage="2026-07-24T08:01:00Z",
        terms_id="opendart-api-terms-2026-01",
        license_id="opendart-public-data-license",
        correlation_id="req-strict-types",
        raw_payload=RAW,
    )
    with pytest.raises(ValueError, match="quality"):
        KROfficialEvidenceEnvelope(revision=0, quality="FRESH", **common)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="revision"):
        KROfficialEvidenceEnvelope(
            revision=True, quality=DataQualityStatus.FRESH, **common  # type: ignore[arg-type]
        )


def test_source_capability_and_endpoint_identity_fail_closed() -> None:
    with pytest.raises(ValueError, match="source and capability"):
        KROfficialRequest(
            source=KROfficialSource.KRX,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/list.json",
            provider_symbol="005930",
            correlation_id="req-mismatch",
        )
    with pytest.raises(ValueError, match="sanitized exact"):
        KROfficialRequest(
            source=KROfficialSource.DART,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/list.json?crtfc_key=secret",
            provider_symbol="005930",
            correlation_id="req-secret-query",
        )
    with pytest.raises(ValueError, match="sanitized exact"):
        KROfficialRequest(
            source=KROfficialSource.DART,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/list.json%3Fcrtfc_key=secret",
            provider_symbol="005930",
            correlation_id="req-encoded-secret",
        )
    with pytest.raises(ValueError, match="sanitized exact"):
        KROfficialRequest(
            source=KROfficialSource.DART,
            capability=KROfficialCapability.DART_FILING,
            endpoint="opendart.fss.or.kr/api/../secret",
            provider_symbol="005930",
            correlation_id="req-dot-segment",
        )


def test_live_transport_without_durable_approval_is_blocked_before_fetch() -> None:
    transport = LiveFixtureTransport([])

    with pytest.raises(KROfficialError, match="durable approval"):
        KROfficialProvider(transport=transport)

    assert transport.requests == []


def make_approval(
    *,
    source: KROfficialSource = KROfficialSource.DART,
    capability: KROfficialCapability = KROfficialCapability.DART_FILING,
    endpoint: str = "opendart.fss.or.kr/api/list.json",
    approved_at: datetime = datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
    expires_at: datetime = datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    max_calls: int = 3,
    max_cost_krw: int = 0,
) -> KROfficialSourceApproval:
    return KROfficialSourceApproval(
        approval_id="kr-official-review-regression",
        manifest_hash=hashlib.sha256(b"review regression manifest").hexdigest(),
        source=source,
        capability=capability,
        endpoint=endpoint,
        terms_id=f"{source.value.lower()}-terms-2026",
        license_id=f"{source.value.lower()}-license-2026",
        credential_scope=f"{source.value.lower()}-read-only",
        max_calls=max_calls,
        max_cost_krw=max_cost_krw,
        approved_at=approved_at,
        expires_at=expires_at,
    )


def dart_request() -> KROfficialRequest:
    return KROfficialRequest(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        provider_symbol="005930",
        correlation_id="req-dart-1",
    )


def test_live_approval_rejects_boolean_call_and_cost_bounds() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        make_approval(max_calls=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_cost_krw"):
        make_approval(max_cost_krw=False)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_live_approval_requires_clock_before_transport_fetch() -> None:
    transport = LiveFixtureTransport([])
    provider = KROfficialProvider(transport=transport, approvals=(make_approval(),))

    with pytest.raises(KROfficialError, match="injected clock"):
        await provider.collect(requests=(dart_request(),), as_of_date=AS_OF)

    assert transport.requests == []


@pytest.mark.parametrize(
    "clock",
    [
        datetime(2026, 7, 24, 6, 59, tzinfo=UTC),
        datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    ],
)
@pytest.mark.asyncio
async def test_not_yet_active_or_expired_approval_blocks_before_fetch(
    clock: datetime,
) -> None:
    transport = LiveFixtureTransport([])
    provider = KROfficialProvider(
        transport=transport,
        approvals=(make_approval(),),
        clock=lambda: clock,
    )

    with pytest.raises(KROfficialError, match="no active exact"):
        await provider.collect(requests=(dart_request(),), as_of_date=AS_OF)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_approval_endpoint_mismatch_blocks_before_fetch() -> None:
    transport = LiveFixtureTransport([])
    provider = KROfficialProvider(
        transport=transport,
        approvals=(make_approval(endpoint="opendart.fss.or.kr/api/company.json"),),
        clock=lambda: datetime(2026, 7, 24, 8, 30, tzinfo=UTC),
    )

    with pytest.raises(KROfficialError, match="no active exact"):
        await provider.collect(requests=(dart_request(),), as_of_date=AS_OF)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_live_response_terms_must_match_durable_approval() -> None:
    evidence = make_evidence(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:filing:terms-mismatch",
        raw_payload=RAW,
        terms_id="different-dart-terms",
    )
    provider = KROfficialProvider(
        transport=LiveFixtureTransport([evidence]),
        approvals=(make_approval(),),
        clock=lambda: datetime(2026, 7, 24, 8, 30, tzinfo=UTC),
    )

    with pytest.raises(KROfficialError, match="terms or license"):
        await provider.collect(requests=(dart_request(),), as_of_date=AS_OF)


@pytest.mark.asyncio
async def test_transport_cannot_spoof_request_correlation() -> None:
    evidence = make_evidence(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:filing:spoofed",
        raw_payload=RAW,
        correlation_id="req-spoofed",
    )

    with pytest.raises(KROfficialError, match="does not match"):
        await KROfficialProvider(
            transport=FixtureTransport([evidence])
        ).collect(requests=(dart_request(),), as_of_date=AS_OF)


@pytest.mark.asyncio
async def test_transport_cannot_return_evidence_for_another_as_of_boundary() -> None:
    evidence = make_evidence(
        source=KROfficialSource.DART,
        capability=KROfficialCapability.DART_FILING,
        endpoint="opendart.fss.or.kr/api/list.json",
        source_record_id="dart:filing:wrong-as-of",
        raw_payload=RAW,
        as_of_date=datetime(2026, 7, 24, 10, 0, tzinfo=UTC),
    )

    with pytest.raises(KROfficialError, match="wrong as-of"):
        await KROfficialProvider(
            transport=FixtureTransport([evidence])
        ).collect(requests=(dart_request(),), as_of_date=AS_OF)


@pytest.mark.asyncio
async def test_matching_normalized_facts_remain_source_separated_and_usable() -> None:
    fact_key = "listing-status:005930:2026-07-24"
    fact_hash = hashlib.sha256(b'{"listed":true}').hexdigest()
    krx = make_evidence(
        source=KROfficialSource.KRX,
        capability=KROfficialCapability.KRX_MARKET_REFERENCE,
        endpoint="data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
        source_record_id="krx:listing:agreement",
        raw_payload=b'{"provider":"krx","listed":true}',
        fact_key=fact_key,
        fact_hash=fact_hash,
    )
    kind = make_evidence(
        source=KROfficialSource.KIND,
        capability=KROfficialCapability.KIND_LISTED_COMPANY_DISCLOSURE,
        endpoint="kind.krx.co.kr/disclosure/search.do",
        source_record_id="kind:listing:agreement",
        raw_payload=b'{"provider":"kind","listed":true}',
        fact_key=fact_key,
        fact_hash=fact_hash,
    )
    requests = tuple(
        KROfficialRequest(
            source=item.source,
            capability=item.capability,
            endpoint=item.endpoint,
            provider_symbol=item.provider_symbol,
            correlation_id=item.correlation_id,
        )
        for item in (krx, kind)
    )

    result = await KROfficialProvider(
        transport=FixtureTransport([krx, kind])
    ).collect(requests=requests, as_of_date=AS_OF)

    assert result.evidence == (krx, kind)
    assert result.events == ()
    assert result.quality is DataQualityStatus.FRESH
    assert result.core_evidence_usable is True
