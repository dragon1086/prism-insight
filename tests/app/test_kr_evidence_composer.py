from __future__ import annotations

from dataclasses import replace
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
from prism_core.data.providers.kis_fundamentals import KISFundamentalFetchResult
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


class KIS:
    def __init__(self, events: list[str], result: KISFundamentalFetchResult) -> None:
        self.events = events
        self.result = result

    async def fetch(self, **_: object) -> KISFundamentalFetchResult:
        self.events.append("KIS")
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


def kis_result(
    current: FundamentalObservation, previous: FundamentalObservation
) -> KISFundamentalFetchResult:
    return KISFundamentalFetchResult(
        fundamentals=(previous, current),
        evidence_items=(),
        quality=DataQualityStatus.FRESH,
        issues=(),
        earnings_current=current.value,
        earnings_previous=previous.value,
        earnings_current_period=current.period_end,
        earnings_previous_period=previous.period_end,
        available_at=current.timing.available_at,
        ingested_at=current.timing.ingested_at,
        capabilities=(),
        limitations=("KIS_FILING_ACCEPTED_AT_UNAVAILABLE",),
    )


@pytest.mark.asyncio
async def test_kis_is_primary_and_dart_kind_are_optional_verification_not_fmp_replacement() -> None:
    events: list[str] = []
    current = fundamental(date(2025, 12, 31), "12", 2).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    previous = fundamental(date(2024, 12, 31), "10", 1).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, kis_result(current, previous)),
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=FMP(events, LiveKREvidenceError("must not be called")),
    )

    income = await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["KIS", "DART"]
    assert income.provider == "KIS"
    assert income.current == Decimal("12")
    assert income.previous == Decimal("10")
    assert composer.last_result is not None
    assert composer.last_result.selected_provider == "KIS"
    assert composer.last_result.fundamentals == (previous, current)


@pytest.mark.asyncio
async def test_valid_kis_earnings_remain_primary_when_unrelated_capability_is_partial() -> None:
    events: list[str] = []
    current = fundamental(date(2025, 12, 31), "12", 2).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    previous = fundamental(date(2024, 12, 31), "10", 1).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    partial = replace(
        kis_result(current, previous),
        quality=DataQualityStatus.PARTIAL,
        issues=("KIS_CAPABILITY_UNAVAILABLE:growth_ratio",),
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, partial),
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=None,
    )

    income = await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert income.provider == "KIS"
    assert composer.last_result is not None
    assert composer.last_result.selected_provider == "KIS"
    assert composer.last_result.quality is DataQualityStatus.PARTIAL
    assert "KIS_CAPABILITY_UNAVAILABLE:growth_ratio" in composer.last_result.issues


@pytest.mark.asyncio
async def test_conflicting_optional_dart_supplement_does_not_veto_fresh_kis_core() -> None:
    events: list[str] = []
    kis_current = fundamental(date(2025, 12, 31), "12", 2).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    kis_previous = fundamental(date(2024, 12, 31), "10", 1).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    dart_first = fundamental(date(2025, 12, 31), "11", 2)
    dart_conflict = fundamental(date(2025, 12, 31), "13", 3)
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, kis_result(kis_current, kis_previous)),
        dart=DART(events, dart_result(dart_first, dart_conflict)),
        kind=UnavailableKINDAdapter(),
        fmp=None,
    )

    income = await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert income.provider == "KIS"
    assert composer.last_result is not None
    assert "SEVERE_OFFICIAL_FILING_CONFLICT" not in composer.last_result.hard_vetoes
    assert "DART_SUPPLEMENT_CONFLICT" in composer.last_result.issues


@pytest.mark.asyncio
async def test_partial_kis_bundle_keeps_valid_earnings_but_reports_capability_gap() -> None:
    events: list[str] = []
    current = fundamental(date(2025, 12, 31), "12", 2).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    previous = fundamental(date(2024, 12, 31), "10", 1).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    partial = replace(
        kis_result(current, previous),
        quality=DataQualityStatus.PARTIAL,
        issues=("KIS_CAPABILITY_UNAVAILABLE:growth_ratio",),
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, partial),
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=FMP(events, LiveKREvidenceError("must not be called")),
    )

    income = await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["KIS", "DART"]
    assert income.provider == "KIS"
    assert composer.last_result is not None
    assert composer.last_result.quality is DataQualityStatus.PARTIAL
    assert "KIS_CAPABILITY_UNAVAILABLE:growth_ratio" in composer.last_result.issues


@pytest.mark.asyncio
async def test_missing_kis_core_uses_explicit_gap_and_does_not_silently_substitute_fmp() -> None:
    events: list[str] = []
    unavailable = KISFundamentalFetchResult(
        fundamentals=(),
        evidence_items=(),
        quality=DataQualityStatus.PARTIAL,
        issues=("KIS_COMPARABLE_ANNUAL_EARNINGS_UNAVAILABLE",),
        earnings_current=None,
        earnings_previous=None,
        earnings_current_period=None,
        earnings_previous_period=None,
        available_at=AS_OF,
        ingested_at=AS_OF,
        capabilities=(),
        limitations=("KIS_FILING_ACCEPTED_AT_UNAVAILABLE",),
    )
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, unavailable),
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=FMP(events, FMPIncomeEvidence(
            current=Decimal("99"),
            previous=Decimal("1"),
            current_period="2025-12-31",
            current_accepted_at=AS_OF,
            current_unit="KRW",
            previous_period="2024-12-31",
            previous_accepted_at=AS_OF,
            previous_unit="KRW",
            ingested_at=AS_OF,
            response_hash="f" * 64,
        )),
    )

    with pytest.raises(SupplementalFundamentalsUnavailable):
        await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["KIS", "DART"]
    assert composer.last_result is not None
    assert "KIS_COMPARABLE_ANNUAL_EARNINGS_UNAVAILABLE" in composer.last_result.issues
    assert "MISSING_SUPPLEMENTAL_FUNDAMENTALS" in composer.last_result.hard_vetoes


@pytest.mark.asyncio
async def test_inconsistent_kis_earnings_result_fails_closed_with_named_gap() -> None:
    events: list[str] = []
    current = fundamental(date(2025, 12, 31), "12", 2).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    previous = fundamental(date(2024, 12, 31), "10", 1).model_copy(
        update={"provider": "KIS", "provider_symbol": "214450"}
    )
    inconsistent = replace(kis_result(current, previous), fundamentals=())
    composer = KREvidenceComposer(
        stock_code="214450",
        security_id=STOCK_ID,
        kis=KIS(events, inconsistent),
        dart=DART(events, dart_result()),
        kind=UnavailableKINDAdapter(),
        fmp=FMP(events, LiveKREvidenceError("must not be called")),
    )

    with pytest.raises(SupplementalFundamentalsUnavailable):
        await composer.fetch(symbol="214450.KS", as_of=AS_OF)

    assert events == ["KIS", "DART"]
    assert composer.last_result is not None
    assert "KIS_FUNDAMENTAL_RESULT_INCONSISTENT" in composer.last_result.issues
    assert "MISSING_SUPPLEMENTAL_FUNDAMENTALS" in composer.last_result.hard_vetoes


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
