from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus
from prism_core.data.providers.sec_edgar import (
    SECEdgarCapability,
    SECEdgarEvidenceEnvelope,
    SECEdgarError,
    SECEdgarProvider,
    SECEdgarRequest,
    SECEdgarSourceApproval,
    SECEdgarTransportMode,
    SecondaryEvidenceReference,
)
from prism_core.data.providers.fmp_models import FMPCorporateActionResponseEnvelope


UTC = ZoneInfo("UTC")
OBSERVED_AT = datetime(2026, 7, 24, 20, 0, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 7, 24, 20, 1, tzinfo=UTC)
INGESTED_AT = datetime(2026, 7, 24, 20, 2, tzinfo=UTC)
AS_OF = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
RAW = b'{"accessionNumber":"0000320193-26-000079","form":"10-Q"}'


class FixtureTransport:
    mode = SECEdgarTransportMode.FIXTURE

    def __init__(self, responses: list[SECEdgarEvidenceEnvelope | Exception]) -> None:
        self.responses = responses
        self.requests: list[SECEdgarRequest] = []
        self.approvals: list[SECEdgarSourceApproval | None] = []

    async def fetch(
        self,
        request: SECEdgarRequest,
        *,
        approval: SECEdgarSourceApproval | None = None,
    ) -> SECEdgarEvidenceEnvelope:
        self.requests.append(request)
        self.approvals.append(approval)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LiveFixtureTransport(FixtureTransport):
    mode = SECEdgarTransportMode.LIVE


def make_evidence(
    *,
    capability: SECEdgarCapability = SECEdgarCapability.COMPANY_SUBMISSIONS,
    endpoint: str = "data.sec.gov/submissions/CIK0000320193.json",
    source_record_id: str = "sec-edgar:0000320193-26-000079",
    quality: DataQualityStatus = DataQualityStatus.FRESH,
    fact_key: str | None = None,
    fact_hash: str | None = None,
    correlation_id: str = "req-sec-aapl-10q",
    as_of_date: datetime = AS_OF,
    observed_at: datetime = OBSERVED_AT,
    available_at: datetime = AVAILABLE_AT,
    ingested_at: datetime = INGESTED_AT,
    filing_date: date = date(2026, 7, 24),
    period_of_report: date | None = date(2026, 6, 27),
) -> SECEdgarEvidenceEnvelope:
    return SECEdgarEvidenceEnvelope(
        capability=capability,
        endpoint=endpoint,
        source_record_id=source_record_id,
        cik="0000320193",
        provider_symbol="AAPL",
        accession_number="0000320193-26-000079",
        form_type="10-Q",
        filing_date=filing_date,
        period_of_report=period_of_report,
        observed_at=observed_at,
        available_at=available_at,
        ingested_at=ingested_at,
        as_of_date=as_of_date,
        release_id="0000320193-26-000079",
        revision=1,
        vintage="2026-07-24T20:01:00Z",
        terms_id="sec-edgar-access-2025-09",
        license_id="sec-public-filings",
        quality=quality,
        correlation_id=correlation_id,
        fact_key=fact_key,
        fact_hash=fact_hash,
        raw_payload=RAW,
    )


def make_request(*, correlation_id: str = "req-sec-aapl-10q") -> SECEdgarRequest:
    return SECEdgarRequest(
        capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
        endpoint="data.sec.gov/submissions/CIK0000320193.json",
        cik="0000320193",
        provider_symbol="AAPL",
        accession_number="0000320193-26-000079",
        correlation_id=correlation_id,
    )


def make_company_facts_request() -> SECEdgarRequest:
    return SECEdgarRequest(
        capability=SECEdgarCapability.COMPANY_FACTS,
        endpoint="data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        cik="0000320193",
        provider_symbol="AAPL",
        accession_number="0000320193-26-000079",
        correlation_id="req-sec-aapl-facts",
    )


def test_evidence_preserves_sec_filing_pit_and_legal_provenance() -> None:
    evidence = SECEdgarEvidenceEnvelope(
        capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
        endpoint="data.sec.gov/submissions/CIK0000320193.json",
        source_record_id="sec-edgar:0000320193-26-000079",
        cik="0000320193",
        provider_symbol="AAPL",
        accession_number="0000320193-26-000079",
        form_type="10-Q",
        filing_date=date(2026, 7, 24),
        period_of_report=date(2026, 6, 27),
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        ingested_at=INGESTED_AT,
        as_of_date=AS_OF,
        release_id="0000320193-26-000079",
        revision=1,
        vintage="2026-07-24T20:01:00Z",
        terms_id="sec-edgar-access-2025-09",
        license_id="sec-public-filings",
        quality=DataQualityStatus.FRESH,
        correlation_id="req-sec-aapl-10q",
        raw_payload=RAW,
    )

    assert evidence.provider == "SEC_EDGAR"
    assert evidence.capability is SECEdgarCapability.COMPANY_SUBMISSIONS
    assert evidence.endpoint == "data.sec.gov/submissions/CIK0000320193.json"
    assert evidence.cik == "0000320193"
    assert evidence.accession_number == "0000320193-26-000079"
    assert evidence.form_type == "10-Q"
    assert evidence.filing_date == date(2026, 7, 24)
    assert evidence.period_of_report == date(2026, 6, 27)
    assert evidence.observed_at == OBSERVED_AT
    assert evidence.available_at == AVAILABLE_AT
    assert evidence.ingested_at == INGESTED_AT
    assert evidence.as_of_date == AS_OF
    assert evidence.release_id == "0000320193-26-000079"
    assert evidence.revision == 1
    assert evidence.vintage == "2026-07-24T20:01:00Z"
    assert evidence.raw_payload_hash == hashlib.sha256(RAW).hexdigest()
    assert evidence.terms_id == "sec-edgar-access-2025-09"
    assert evidence.license_id == "sec-public-filings"
    assert evidence.quality is DataQualityStatus.FRESH
    assert evidence.correlation_id == "req-sec-aapl-10q"
    assert evidence.raw_payload == RAW
    assert RAW.decode() not in repr(evidence)


@pytest.mark.asyncio
async def test_provider_returns_source_separated_fresh_sec_evidence() -> None:
    evidence = make_evidence()
    transport = FixtureTransport([evidence])
    request = make_request()

    result = await SECEdgarProvider(transport=transport).collect(
        requests=(request,), as_of_date=AS_OF
    )

    assert result.evidence == (evidence,)
    assert result.secondary_references == ()
    assert result.events == ()
    assert result.quality is DataQualityStatus.FRESH
    assert result.core_evidence_usable is True
    assert transport.requests == [request]


@pytest.mark.asyncio
async def test_missing_sec_evidence_fails_closed_with_sanitized_event() -> None:
    transport = FixtureTransport([RuntimeError("private upstream response body")])

    result = await SECEdgarProvider(transport=transport).collect(
        requests=(make_request(),), as_of_date=AS_OF
    )

    assert result.evidence == ()
    assert result.quality is DataQualityStatus.UNAVAILABLE
    assert result.core_evidence_usable is False
    assert [event.reason for event in result.events] == ["SEC_FETCH_FAILED"]
    assert "private upstream response body" not in repr(result)


def make_approval(
    *,
    max_calls: int = 2,
    minimum_request_interval_ms: int = 100,
    max_cost_usd_cents: int = 0,
    approved_at: datetime = datetime(2026, 7, 24, 19, 0, tzinfo=UTC),
    expires_at: datetime = datetime(2026, 7, 24, 22, 0, tzinfo=UTC),
) -> SECEdgarSourceApproval:
    return SECEdgarSourceApproval(
        approval_id="sec-edgar-aapl-20260724",
        manifest_hash=hashlib.sha256(b"durable SEC approval manifest").hexdigest(),
        capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
        endpoint="data.sec.gov/submissions/CIK0000320193.json",
        terms_id="sec-edgar-access-2025-09",
        license_id="sec-public-filings",
        sec_identity_id="sec-identity-manifest-20260724",
        credential_scope="none-public-read-only",
        max_calls=max_calls,
        minimum_request_interval_ms=minimum_request_interval_ms,
        max_cost_usd_cents=max_cost_usd_cents,
        approved_at=approved_at,
        expires_at=expires_at,
    )


def test_live_transport_is_blocked_without_durable_sec_identification_approval() -> None:
    transport = LiveFixtureTransport([])

    with pytest.raises(SECEdgarError, match="durable approval"):
        SECEdgarProvider(transport=transport)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_live_approval_enforces_exact_scope_call_and_rate_bounds_before_fetch() -> None:
    now = [datetime(2026, 7, 24, 20, 30, tzinfo=UTC)]
    evidence = make_evidence()
    transport = LiveFixtureTransport([evidence, evidence])
    provider = SECEdgarProvider(
        transport=transport,
        approvals=(make_approval(max_calls=2),),
        clock=lambda: now[0],
    )

    result = await provider.collect(requests=(make_request(),), as_of_date=AS_OF)

    assert result.core_evidence_usable is True
    assert transport.approvals == [make_approval(max_calls=2)]
    transport_approval = transport.approvals[0]
    assert transport_approval is not None
    assert transport_approval.sec_identity_id == "sec-identity-manifest-20260724"
    with pytest.raises(SECEdgarError, match="rate bound"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert len(transport.requests) == 1

    now[0] += timedelta(milliseconds=100)
    await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    now[0] += timedelta(milliseconds=100)
    with pytest.raises(SECEdgarError, match="call bound"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_secondary_fmp_and_generic_sec_evidence_remain_separate_and_conflict() -> None:
    official_hash = hashlib.sha256(b'{"shares":100}').hexdigest()
    fmp_hash = hashlib.sha256(b'{"shares":200}').hexdigest()
    fact_key = "shares-outstanding:0000320193:2026Q2"
    official = make_evidence(fact_key=fact_key, fact_hash=official_hash)
    references = (
        SecondaryEvidenceReference(
            provider="FMP",
            source_record_id="fmp:aapl:shares:2026Q2",
            fact_key=fact_key,
            fact_hash=fmp_hash,
        ),
        SecondaryEvidenceReference(
            provider="SEC_GENERIC",
            source_record_id="legacy-sec:aapl:shares:2026Q2",
            fact_key=fact_key,
            fact_hash=official_hash,
        ),
    )

    result = await SECEdgarProvider(
        transport=FixtureTransport([official])
    ).collect(
        requests=(make_request(),),
        as_of_date=AS_OF,
        secondary_references=references,
    )

    assert result.evidence == (official,)
    assert result.secondary_references == references
    assert result.quality is DataQualityStatus.CONFLICT
    assert result.core_evidence_usable is False
    assert [event.reason for event in result.events] == ["OFFICIAL_SECONDARY_CONFLICT"]
    assert result.events[0].related_providers == ("SEC_EDGAR", "FMP")


@pytest.mark.asyncio
async def test_conflicting_official_endpoints_retain_both_and_fail_closed() -> None:
    fact_key = "shares-outstanding:0000320193:2026Q2"
    submissions = make_evidence(
        fact_key=fact_key,
        fact_hash=hashlib.sha256(b'{"shares":100}').hexdigest(),
    )
    company_facts = make_evidence(
        capability=SECEdgarCapability.COMPANY_FACTS,
        endpoint="data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        source_record_id="sec-edgar:companyfacts:0000320193-26-000079",
        fact_key=fact_key,
        fact_hash=hashlib.sha256(b'{"shares":200}').hexdigest(),
        correlation_id="req-sec-aapl-facts",
    )

    result = await SECEdgarProvider(
        transport=FixtureTransport([submissions, company_facts])
    ).collect(
        requests=(make_request(), make_company_facts_request()), as_of_date=AS_OF
    )

    assert result.evidence == (submissions, company_facts)
    assert result.quality is DataQualityStatus.CONFLICT
    assert result.core_evidence_usable is False
    assert [event.reason for event in result.events] == ["SEC_OFFICIAL_FACT_CONFLICT"]


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
async def test_non_fresh_core_sec_evidence_is_never_usable(
    quality: DataQualityStatus,
) -> None:
    result = await SECEdgarProvider(
        transport=FixtureTransport([make_evidence(quality=quality)])
    ).collect(requests=(make_request(),), as_of_date=AS_OF)

    assert result.quality is quality
    assert result.core_evidence_usable is False


def test_filing_period_and_filing_date_never_substitute_for_available_at() -> None:
    with pytest.raises(ValueError, match="available_at must be at or before as_of_date"):
        SECEdgarEvidenceEnvelope(
            capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
            endpoint="data.sec.gov/submissions/CIK0000320193.json",
            source_record_id="sec-edgar:0000320193-20-000001",
            cik="0000320193",
            provider_symbol="AAPL",
            accession_number="0000320193-20-000001",
            form_type="10-K",
            filing_date=date(2020, 1, 1),
            period_of_report=date(2019, 9, 30),
            observed_at=OBSERVED_AT,
            available_at=AS_OF + timedelta(minutes=1),
            ingested_at=AS_OF + timedelta(minutes=2),
            as_of_date=AS_OF,
            release_id="0000320193-20-000001",
            revision=0,
            vintage="2026-07-24T21:01:00Z",
            terms_id="sec-edgar-access-2025-09",
            license_id="sec-public-filings",
            quality=DataQualityStatus.FRESH,
            correlation_id="req-sec-future-availability",
            raw_payload=RAW,
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "example.com/submissions/CIK0000320193.json",
        "data.sec.gov/submissions/CIK0000320193.json?api_key=secret",
        "data.sec.gov/submissions/../private.json",
        "data.sec.gov/submissions/%2e%2e/private.json",
    ],
)
def test_request_rejects_non_sec_or_unsanitized_endpoint_identity(endpoint: str) -> None:
    with pytest.raises(ValueError, match="sanitized exact SEC"):
        SECEdgarRequest(
            capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
            endpoint=endpoint,
            cik="0000320193",
            provider_symbol="AAPL",
            accession_number="0000320193-26-000079",
            correlation_id="req-sec-invalid-endpoint",
        )


def test_request_rejects_endpoint_that_does_not_match_capability_or_cik() -> None:
    with pytest.raises(ValueError, match="capability, CIK, and accession"):
        SECEdgarRequest(
            capability=SECEdgarCapability.COMPANY_FACTS,
            endpoint="data.sec.gov/submissions/CIK0000320193.json",
            cik="0000320193",
            provider_symbol="AAPL",
            accession_number="0000320193-26-000079",
            correlation_id="req-sec-wrong-capability",
        )
    with pytest.raises(ValueError, match="capability, CIK, and accession"):
        SECEdgarRequest(
            capability=SECEdgarCapability.COMPANY_SUBMISSIONS,
            endpoint="data.sec.gov/submissions/CIK0000789019.json",
            cik="0000320193",
            provider_symbol="AAPL",
            accession_number="0000320193-26-000079",
            correlation_id="req-sec-wrong-cik",
        )


def test_sec_edgar_contracts_are_public_without_replacing_generic_sec_transport() -> None:
    import prism_core.data as data
    import prism_core.data.providers as providers

    expected = {
        "SECEdgarCapability",
        "SECEdgarError",
        "SECEdgarEvidenceEnvelope",
        "SECEdgarFetchResult",
        "SECEdgarProvider",
        "SECEdgarProviderEvent",
        "SECEdgarRequest",
        "SECEdgarSourceApproval",
        "SECEdgarTransport",
        "SECEdgarTransportMode",
        "SecondaryEvidenceReference",
    }
    assert expected <= set(providers.__all__)
    assert expected <= set(data.__all__)
    assert data.SECEdgarProvider is SECEdgarProvider
    assert "SECOfficialEvidenceTransport" in providers.__all__


@pytest.mark.asyncio
async def test_generic_sec_envelope_cannot_cross_official_transport_boundary() -> None:
    generic = FMPCorporateActionResponseEnvelope(
        provider="sec",
        source_record_id="sec:legacy:aapl:10q",
        revision=0,
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        payload={"form": "10-Q"},
        quality=DataQualityStatus.FRESH,
    )
    transport = FixtureTransport([generic])  # type: ignore[list-item]

    with pytest.raises(SECEdgarError, match="invalid evidence type"):
        await SECEdgarProvider(transport=transport).collect(
            requests=(make_request(),), as_of_date=AS_OF
        )

    assert transport.requests == [make_request()]


def test_sec_public_foundation_rejects_nonzero_cost_approval() -> None:
    with pytest.raises(ValueError, match="cost bound must be zero"):
        make_approval(max_cost_usd_cents=1)


def test_filing_document_endpoint_is_one_exact_accession_document() -> None:
    request = SECEdgarRequest(
        capability=SECEdgarCapability.FILING_DOCUMENT,
        endpoint=(
            "www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000079/aapl-20260627.htm"
        ),
        cik="0000320193",
        provider_symbol="AAPL",
        accession_number="0000320193-26-000079",
        correlation_id="req-sec-aapl-document",
    )
    assert request.capability is SECEdgarCapability.FILING_DOCUMENT

    with pytest.raises(ValueError, match="capability, CIK, and accession"):
        SECEdgarRequest(
            capability=SECEdgarCapability.FILING_DOCUMENT,
            endpoint=(
                "www.sec.gov/Archives/edgar/data/320193/"
                "000032019326000079/nested/aapl-20260627.htm"
            ),
            cik="0000320193",
            provider_symbol="AAPL",
            accession_number="0000320193-26-000079",
            correlation_id="req-sec-nested-document",
        )


def test_sec_evidence_pins_all_pit_ordering_and_filing_date_types() -> None:
    with pytest.raises(ValueError, match="observed_at must be at or before available_at"):
        make_evidence(observed_at=AVAILABLE_AT + timedelta(seconds=1))
    with pytest.raises(ValueError, match="available_at must be at or before ingested_at"):
        make_evidence(ingested_at=AVAILABLE_AT - timedelta(seconds=1))
    with pytest.raises(ValueError, match="filing_date must be a date"):
        make_evidence(filing_date=datetime(2026, 7, 24, tzinfo=UTC))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="period_of_report must be a date or None"):
        make_evidence(
            period_of_report=datetime(2026, 6, 27, tzinfo=UTC)  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_failed_live_fetch_consumes_call_budget_to_prevent_retry_hammering() -> None:
    now = [datetime(2026, 7, 24, 20, 30, tzinfo=UTC)]
    transport = LiveFixtureTransport([RuntimeError("upstream unavailable")])
    provider = SECEdgarProvider(
        transport=transport,
        approvals=(make_approval(max_calls=1),),
        clock=lambda: now[0],
    )

    result = await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert result.quality is DataQualityStatus.UNAVAILABLE

    now[0] += timedelta(milliseconds=100)
    with pytest.raises(SECEdgarError, match="call bound"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert len(transport.requests) == 1
