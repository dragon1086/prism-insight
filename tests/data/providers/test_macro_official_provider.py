from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus
from prism_core.data.providers.macro_official import (
    MacroOfficialCapability,
    MacroOfficialEvidenceEnvelope,
    MacroOfficialError,
    MacroOfficialProvider,
    MacroOfficialRequest,
    MacroOfficialSource,
    MacroOfficialSourceApproval,
    MacroOfficialTransportMode,
)


UTC = ZoneInfo("UTC")
OBSERVED_AT = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
AVAILABLE_AT = datetime(2026, 6, 3, 14, 0, tzinfo=UTC)
INGESTED_AT = datetime(2026, 6, 3, 14, 1, tzinfo=UTC)
AS_OF = datetime(2026, 6, 3, 15, 0, tzinfo=UTC)
RAW = b'{"series_id":"GDP","value":"30100.0"}'


class FixtureTransport:
    mode = MacroOfficialTransportMode.FIXTURE

    def __init__(
        self, responses: list[MacroOfficialEvidenceEnvelope | Exception]
    ) -> None:
        self.responses = responses
        self.requests: list[MacroOfficialRequest] = []
        self.approvals: list[MacroOfficialSourceApproval | None] = []

    async def fetch(
        self,
        request: MacroOfficialRequest,
        *,
        approval: MacroOfficialSourceApproval | None,
    ) -> MacroOfficialEvidenceEnvelope:
        self.requests.append(request)
        self.approvals.append(approval)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class LiveFixtureTransport(FixtureTransport):
    mode = MacroOfficialTransportMode.LIVE


def make_request(
    *,
    source: MacroOfficialSource = MacroOfficialSource.FRED,
    capability: MacroOfficialCapability = MacroOfficialCapability.FRED_SERIES_CURRENT,
    endpoint: str = "api.stlouisfed.org/fred/series/observations",
    series_id: str = "GDP",
    vintage_date: date | None = None,
    correlation_id: str = "req-fred-gdp-current",
) -> MacroOfficialRequest:
    return MacroOfficialRequest(
        source=source,
        capability=capability,
        endpoint=endpoint,
        series_id=series_id,
        vintage_date=vintage_date,
        correlation_id=correlation_id,
    )


def make_evidence(
    *,
    source: MacroOfficialSource = MacroOfficialSource.FRED,
    capability: MacroOfficialCapability = MacroOfficialCapability.FRED_SERIES_CURRENT,
    endpoint: str = "api.stlouisfed.org/fred/series/observations",
    series_id: str = "GDP",
    vintage_date: date = date(2026, 6, 3),
    quality: DataQualityStatus = DataQualityStatus.FRESH,
    correlation_id: str = "req-fred-gdp-current",
    fact_key: str | None = None,
    fact_hash: str | None = None,
    available_at: datetime = AVAILABLE_AT,
    as_of_date: datetime = AS_OF,
) -> MacroOfficialEvidenceEnvelope:
    return MacroOfficialEvidenceEnvelope(
        source=source,
        capability=capability,
        endpoint=endpoint,
        source_record_id=f"{source.value.lower()}:GDP:2026Q1:{vintage_date.isoformat()}",
        series_id=series_id,
        observation_period="2026Q1",
        observed_at=OBSERVED_AT,
        available_at=available_at,
        ingested_at=max(INGESTED_AT, available_at),
        as_of_date=as_of_date,
        release_id="gdp-2026-06-03",
        revision=0,
        vintage_date=vintage_date,
        terms_id="macro-terms-2026-01",
        license_id="official-macro-public-data",
        quality=quality,
        correlation_id=correlation_id,
        fact_key=fact_key,
        fact_hash=fact_hash,
        raw_payload=RAW,
    )


def make_approval(
    *,
    max_calls: int = 2,
    minimum_request_interval_ms: int = 100,
    terms_id: str = "macro-terms-2026-01",
    license_id: str = "official-macro-public-data",
) -> MacroOfficialSourceApproval:
    return MacroOfficialSourceApproval(
        approval_id="fred-gdp-current-20260603",
        manifest_hash=hashlib.sha256(b"durable macro approval manifest").hexdigest(),
        source=MacroOfficialSource.FRED,
        capability=MacroOfficialCapability.FRED_SERIES_CURRENT,
        endpoint="api.stlouisfed.org/fred/series/observations",
        terms_id=terms_id,
        license_id=license_id,
        credential_scope="fred-read-only-series-key",
        max_calls=max_calls,
        minimum_request_interval_ms=minimum_request_interval_ms,
        max_cost_usd_cents=0,
        approved_at=datetime(2026, 6, 3, 13, 0, tzinfo=UTC),
        expires_at=datetime(2026, 6, 3, 16, 0, tzinfo=UTC),
    )


def test_macro_evidence_preserves_source_pit_revision_and_legal_provenance() -> None:
    evidence = MacroOfficialEvidenceEnvelope(
        source=MacroOfficialSource.ALFRED,
        capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        endpoint="api.stlouisfed.org/fred/series/observations",
        source_record_id="alfred:GDP:2026-06-03:2026Q1",
        series_id="GDP",
        observation_period="2026Q1",
        observed_at=OBSERVED_AT,
        available_at=AVAILABLE_AT,
        ingested_at=INGESTED_AT,
        as_of_date=AS_OF,
        release_id="gdp-2026-06-03",
        revision=2,
        vintage_date=date(2026, 6, 3),
        terms_id="fred-terms-2026-01",
        license_id="fred-public-data",
        quality=DataQualityStatus.FRESH,
        correlation_id="req-alfred-gdp-2026q1",
        fact_key="GDP:2026Q1:2026-06-03",
        fact_hash=hashlib.sha256(b"30100.0").hexdigest(),
        raw_payload=RAW,
    )

    assert evidence.provider == "ALFRED"
    assert evidence.observation_period == "2026Q1"
    assert evidence.available_at == AVAILABLE_AT
    assert evidence.vintage_date == date(2026, 6, 3)
    assert evidence.raw_payload_hash == hashlib.sha256(RAW).hexdigest()
    assert evidence.raw_payload == RAW
    assert RAW.decode() not in repr(evidence)


@pytest.mark.asyncio
async def test_provider_keeps_fred_alfred_and_ecos_evidence_separate() -> None:
    fred = make_evidence()
    alfred = make_evidence(
        source=MacroOfficialSource.ALFRED,
        capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        vintage_date=date(2026, 5, 1),
        correlation_id="req-alfred-gdp-vintage",
    )
    ecos = make_evidence(
        source=MacroOfficialSource.ECOS,
        capability=MacroOfficialCapability.ECOS_STATISTIC_SEARCH,
        endpoint="ecos.bok.or.kr/api/StatisticSearch",
        series_id="200Y001",
        correlation_id="req-ecos-gdp-current",
    )
    requests = (
        make_request(),
        make_request(
            source=MacroOfficialSource.ALFRED,
            capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
            vintage_date=date(2026, 5, 1),
            correlation_id="req-alfred-gdp-vintage",
        ),
        make_request(
            source=MacroOfficialSource.ECOS,
            capability=MacroOfficialCapability.ECOS_STATISTIC_SEARCH,
            endpoint="ecos.bok.or.kr/api/StatisticSearch",
            series_id="200Y001",
            correlation_id="req-ecos-gdp-current",
        ),
    )

    result = await MacroOfficialProvider(
        transport=FixtureTransport([fred, alfred, ecos])
    ).collect(requests=requests, as_of_date=AS_OF)

    assert result.evidence == (fred, alfred, ecos)
    assert [item.provider for item in result.evidence] == ["FRED", "ALFRED", "ECOS"]
    assert result.events == ()
    assert result.quality is DataQualityStatus.FRESH
    assert result.core_evidence_usable is True


@pytest.mark.asyncio
async def test_missing_or_non_fresh_macro_evidence_fails_closed() -> None:
    missing = await MacroOfficialProvider(
        transport=FixtureTransport([RuntimeError("private upstream body")])
    ).collect(requests=(make_request(),), as_of_date=AS_OF)
    stale = await MacroOfficialProvider(
        transport=FixtureTransport([make_evidence(quality=DataQualityStatus.STALE)])
    ).collect(requests=(make_request(),), as_of_date=AS_OF)

    assert missing.quality is DataQualityStatus.UNAVAILABLE
    assert missing.core_evidence_usable is False
    assert [event.reason for event in missing.events] == ["MACRO_FETCH_FAILED"]
    assert "private upstream body" not in repr(missing)
    assert stale.quality is DataQualityStatus.STALE
    assert stale.core_evidence_usable is False


@pytest.mark.asyncio
async def test_conflicting_macro_facts_are_retained_but_fail_closed() -> None:
    key = "GDP:2026Q1:comparison"
    fred = make_evidence(fact_key=key, fact_hash=hashlib.sha256(b"1").hexdigest())
    alfred = make_evidence(
        source=MacroOfficialSource.ALFRED,
        capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        correlation_id="req-alfred-gdp-vintage",
        vintage_date=date(2026, 5, 1),
        fact_key=key,
        fact_hash=hashlib.sha256(b"2").hexdigest(),
    )

    result = await MacroOfficialProvider(
        transport=FixtureTransport([fred, alfred])
    ).collect(
        requests=(
            make_request(),
            make_request(
                source=MacroOfficialSource.ALFRED,
                capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
                vintage_date=date(2026, 5, 1),
                correlation_id="req-alfred-gdp-vintage",
            ),
        ),
        as_of_date=AS_OF,
    )

    assert result.evidence == (fred, alfred)
    assert result.quality is DataQualityStatus.CONFLICT
    assert result.core_evidence_usable is False
    assert result.events[0].reason == "MACRO_FACT_CONFLICT"
    assert result.events[0].related_sources == (
        MacroOfficialSource.FRED,
        MacroOfficialSource.ALFRED,
    )


def test_observation_period_and_vintage_never_substitute_for_available_at() -> None:
    with pytest.raises(ValueError, match="available_at must be at or before as_of_date"):
        make_evidence(
            available_at=AS_OF + timedelta(minutes=1),
            as_of_date=AS_OF,
        )
    with pytest.raises(ValueError, match="vintage_date cannot exceed as_of_date"):
        make_evidence(vintage_date=AS_OF.date() + timedelta(days=1))


def test_requests_pin_source_capability_endpoint_and_vintage_semantics() -> None:
    with pytest.raises(ValueError, match="source and capability"):
        make_request(
            source=MacroOfficialSource.FRED,
            capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
            vintage_date=date(2026, 5, 1),
        )
    with pytest.raises(ValueError, match="endpoint must match"):
        make_request(endpoint="example.com/fred/series/observations")
    with pytest.raises(ValueError, match="vintage_date is required"):
        make_request(
            source=MacroOfficialSource.ALFRED,
            capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        )
    with pytest.raises(ValueError, match="only valid for ALFRED"):
        make_request(vintage_date=date(2026, 5, 1))


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
async def test_every_non_fresh_core_macro_quality_is_unusable(
    quality: DataQualityStatus,
) -> None:
    result = await MacroOfficialProvider(
        transport=FixtureTransport([make_evidence(quality=quality)])
    ).collect(requests=(make_request(),), as_of_date=AS_OF)

    assert result.quality is quality
    assert result.core_evidence_usable is False


def test_live_transport_is_blocked_without_durable_source_approval() -> None:
    transport = LiveFixtureTransport([])

    with pytest.raises(MacroOfficialError, match="durable approval"):
        MacroOfficialProvider(transport=transport)

    assert transport.requests == []


@pytest.mark.asyncio
async def test_live_approval_enforces_exact_scope_call_and_rate_bounds() -> None:
    now = [datetime(2026, 6, 3, 14, 30, tzinfo=UTC)]
    evidence = make_evidence()
    transport = LiveFixtureTransport([evidence, evidence])
    approval = make_approval(max_calls=2)
    provider = MacroOfficialProvider(
        transport=transport,
        approvals=(approval,),
        clock=lambda: now[0],
    )

    result = await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert result.core_evidence_usable is True
    assert transport.approvals == [approval]
    assert transport.approvals[0].credential_scope == "fred-read-only-series-key"

    with pytest.raises(MacroOfficialError, match="rate bound"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert len(transport.requests) == 1

    now[0] += timedelta(milliseconds=100)
    await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    now[0] += timedelta(milliseconds=100)
    with pytest.raises(MacroOfficialError, match="call bound"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)
    assert len(transport.requests) == 2


@pytest.mark.asyncio
async def test_live_evidence_terms_and_license_must_match_approval() -> None:
    transport = LiveFixtureTransport([make_evidence()])
    provider = MacroOfficialProvider(
        transport=transport,
        approvals=(make_approval(terms_id="different-terms"),),
        clock=lambda: datetime(2026, 6, 3, 14, 30, tzinfo=UTC),
    )

    with pytest.raises(MacroOfficialError, match="terms or license"):
        await provider.collect(requests=(make_request(),), as_of_date=AS_OF)


@pytest.mark.asyncio
async def test_transport_evidence_must_match_request_and_as_of_boundary() -> None:
    wrong_correlation = make_evidence(correlation_id="req-wrong-correlation")
    with pytest.raises(MacroOfficialError, match="does not match its request"):
        await MacroOfficialProvider(
            transport=FixtureTransport([wrong_correlation])
        ).collect(requests=(make_request(),), as_of_date=AS_OF)

    wrong_as_of = make_evidence(as_of_date=AS_OF + timedelta(hours=1))
    with pytest.raises(MacroOfficialError, match="wrong as-of boundary"):
        await MacroOfficialProvider(transport=FixtureTransport([wrong_as_of])).collect(
            requests=(make_request(),), as_of_date=AS_OF
        )

    wrong_vintage = make_evidence(
        source=MacroOfficialSource.ALFRED,
        capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        vintage_date=date(2026, 5, 2),
        correlation_id="req-alfred-gdp-vintage",
    )
    alfred_request = make_request(
        source=MacroOfficialSource.ALFRED,
        capability=MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
        vintage_date=date(2026, 5, 1),
        correlation_id="req-alfred-gdp-vintage",
    )
    with pytest.raises(MacroOfficialError, match="wrong requested vintage"):
        await MacroOfficialProvider(transport=FixtureTransport([wrong_vintage])).collect(
            requests=(alfred_request,), as_of_date=AS_OF
        )


@pytest.mark.asyncio
async def test_live_approval_requires_aware_clock_and_active_window() -> None:
    approval = make_approval()
    no_clock = MacroOfficialProvider(
        transport=LiveFixtureTransport([make_evidence()]), approvals=(approval,)
    )
    with pytest.raises(MacroOfficialError, match="injected clock"):
        await no_clock.collect(requests=(make_request(),), as_of_date=AS_OF)

    naive_clock = MacroOfficialProvider(
        transport=LiveFixtureTransport([make_evidence()]),
        approvals=(approval,),
        clock=lambda: datetime(2026, 6, 3, 14, 30),
    )
    with pytest.raises(MacroOfficialError, match="timezone-aware"):
        await naive_clock.collect(requests=(make_request(),), as_of_date=AS_OF)

    expired = MacroOfficialProvider(
        transport=LiveFixtureTransport([make_evidence()]),
        approvals=(approval,),
        clock=lambda: approval.expires_at,
    )
    with pytest.raises(MacroOfficialError, match="no active exact macro approval"):
        await expired.collect(requests=(make_request(),), as_of_date=AS_OF)


def test_macro_official_contracts_are_public() -> None:
    import prism_core.data as data
    import prism_core.data.providers as providers

    expected = {
        "MacroOfficialCapability",
        "MacroOfficialError",
        "MacroOfficialEvidenceEnvelope",
        "MacroOfficialFetchResult",
        "MacroOfficialProvider",
        "MacroOfficialProviderEvent",
        "MacroOfficialRequest",
        "MacroOfficialSource",
        "MacroOfficialSourceApproval",
        "MacroOfficialTransport",
        "MacroOfficialTransportMode",
    }
    assert expected <= set(providers.__all__)
    assert expected <= set(data.__all__)
    assert data.MacroOfficialProvider is MacroOfficialProvider
