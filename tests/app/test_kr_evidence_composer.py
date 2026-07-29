from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_app.kr_evidence_composer import (
    KREvidenceComposer,
    SupplementalFundamentalsUnavailable,
)
from prism_app.live_kr_evidence import FMPIncomeEvidence, LiveKREvidenceError
from prism_core.data import (
    DataQualityStatus,
    FundamentalObservation,
    ObservationTime,
    SecurityId,
)
from prism_core.data.providers.dart import DARTFetchResult
from prism_core.data.providers.kind import KINDFetchResult, UnavailableKINDAdapter


AS_OF = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000092"))


def fundamental(period: date, value: str, accepted_hour: int) -> FundamentalObservation:
    accepted = datetime(2026, 3, 20, accepted_hour, tzinfo=timezone.utc)
    observed = datetime.combine(period, time.min, tzinfo=timezone.utc)
    return FundamentalObservation(
        security_id=STOCK_ID,
        provider="DART",
        provider_symbol="214450",
        source_record_id=f"2026032000000{accepted_hour}",
        source_hash=(str(accepted_hour) * 64)[:64],
        revision=0,
        timing=ObservationTime(
            observed_at=min(observed, accepted),
            available_at=accepted,
            ingested_at=AS_OF,
            as_of_date=AS_OF,
        ),
        quality=DataQualityStatus.FRESH,
        metric="net_income",
        period_start=date(period.year, 1, 1),
        period_end=period,
        value=Decimal(value),
        unit="KRW",
    )


class DART:
    def __init__(self, events: list[str], result: DARTFetchResult) -> None:
        self.events = events
        self.result = result

    async def fetch(self, **_: object) -> DARTFetchResult:
        self.events.append("DART")
        return self.result


class KIND:
    def __init__(self, events: list[str], result: KINDFetchResult) -> None:
        self.events = events
        self.result = result

    async def fetch(self, **_: object) -> KINDFetchResult:
        self.events.append("KIND")
        return self.result


class FMP:
    def __init__(self, events: list[str], result: FMPIncomeEvidence | Exception) -> None:
        self.events = events
        self.result = result

    async def fetch(self, **_: object) -> FMPIncomeEvidence:
        self.events.append("FMP")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def dart_result(*values: FundamentalObservation) -> DARTFetchResult:
    return DARTFetchResult(
        fundamentals=tuple(values),
        evidence_items=(),
        quality=(DataQualityStatus.FRESH if values else DataQualityStatus.UNAVAILABLE),
        risk_flags=(),
        issues=(() if values else ("NO_PIT_AVAILABLE_DART_FUNDAMENTALS",)),
        call_evidence=(),
    )


@pytest.mark.asyncio
async def test_official_dart_precedes_kind_and_fmp_is_only_supplemental() -> None:
    events: list[str] = []
    current = fundamental(date(2025, 12, 31), "12", 2)
    previous = fundamental(date(2024, 12, 31), "10", 1)
    kind = KINDFetchResult(
        evidence_items=(),
        quality=DataQualityStatus.FRESH,
        risk_flags=("TRADING_SUSPENSION",),
        issues=(),
        call_evidence=(),
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        dart=DART(events, dart_result(current, previous)),
        kind=KIND(events, kind),
        fmp=FMP(events, LiveKREvidenceError("coverage absent")),
    )

    income = await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["DART", "KIND", "FMP"]
    assert income.provider == "DART"
    assert income.current == Decimal("12")
    assert income.current_accepted_at == current.timing.available_at
    assert income.source_record_prefix == f"dart:receipt:{current.source_record_id}"
    assert income.source_url == (
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={current.source_record_id}"
    )
    assert composer.last_result is not None
    assert composer.last_result.hard_vetoes == ("OFFICIAL_TRADING_SUSPENSION",)
    assert "FMP_SUPPLEMENT_UNAVAILABLE" in composer.last_result.issues


@pytest.mark.asyncio
async def test_missing_fmp_and_official_fundamentals_is_a_supplemental_gap_not_generic_error() -> None:
    events: list[str] = []
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=FMP(events, LiveKREvidenceError("secret detail")),
    )

    with pytest.raises(SupplementalFundamentalsUnavailable) as caught:
        await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["DART", "FMP"]
    assert type(caught.value) is SupplementalFundamentalsUnavailable
    assert "secret" not in str(caught.value)
    assert composer.last_result is not None
    assert composer.last_result.quality is DataQualityStatus.UNAVAILABLE
    assert "MISSING_SUPPLEMENTAL_FUNDAMENTALS" in composer.last_result.hard_vetoes


@pytest.mark.asyncio
async def test_severe_official_same_vintage_conflict_is_a_deterministic_veto() -> None:
    events: list[str] = []
    first = fundamental(date(2025, 12, 31), "12", 2)
    conflicting = fundamental(date(2025, 12, 31), "13", 3)
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        dart=DART(events, dart_result(first, conflicting)),
        kind=UnavailableKINDAdapter(),
        fmp=None,
    )

    with pytest.raises(SupplementalFundamentalsUnavailable):
        await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert composer.last_result is not None
    assert "SEVERE_OFFICIAL_FILING_CONFLICT" in composer.last_result.hard_vetoes
    assert composer.last_result.quality is DataQualityStatus.CONFLICT


@pytest.mark.asyncio
async def test_unrecognized_official_risk_flag_fails_closed_without_echoing_provider_text() -> None:
    events: list[str] = []
    kind = KINDFetchResult(
        evidence_items=(),
        quality=DataQualityStatus.FRESH,
        risk_flags=("NEW_PROVIDER_RISK_DETAIL",),
        issues=(),
        call_evidence=(),
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        dart=DART(events, dart_result()),
        kind=KIND(events, kind),
        fmp=None,
    )

    with pytest.raises(SupplementalFundamentalsUnavailable):
        await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert composer.last_result is not None
    assert "UNRECOGNIZED_OFFICIAL_RISK_FLAG" in composer.last_result.hard_vetoes
    assert "NEW_PROVIDER_RISK_DETAIL" not in repr(composer.last_result)
