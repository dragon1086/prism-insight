from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from prism_core.data.contracts import DataQualityStatus
from prism_core.market import (
    ContextDisposition,
    DeterministicMetric,
    KRMarketContext,
    KRMarketRegime,
    MarketContextTiming,
    RegimeAssessment,
    RegimeFeature,
    SessionState,
    SourceClock,
    SourceRole,
    classify_kr_regime,
    derive_context_quality,
)


KST = ZoneInfo("Asia/Seoul")


def _clock(source: str = "KIS") -> SourceClock:
    return SourceClock(
        source=source,
        role=SourceRole.PRIMARY,
        observed_at=datetime(2026, 7, 28, 15, 30, tzinfo=KST),
        available_at=datetime(2026, 7, 28, 15, 31, tzinfo=KST),
        ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
        quality=DataQualityStatus.FRESH,
        evidence_ids=(f"{source.lower()}:evidence",),
    )


def test_unknown_regime_is_analysis_incomplete_and_never_action_eligible() -> None:
    context = KRMarketContext(
        timing=MarketContextTiming(
            session_date=date(2026, 7, 28),
            session_state=SessionState.PRIOR_CLOSE,
            as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
        ),
        source_clocks=(_clock(),),
        index_state=(),
        breadth=(
            DeterministicMetric(
                name="returned_security_count",
                value=Decimal("30"),
                unit="count",
                source="KIS",
                evidence_ids=("kis:evidence",),
            ),
        ),
        investor_flows=(),
        macro_indicators=(),
        group_leadership=(),
        regime=RegimeAssessment.unknown(
            missing_features=("kospi_close", "kospi_ma20", "kospi_return_10d_pct")
        ),
        evidence_ids=("kis:evidence",),
        quality=DataQualityStatus.UNAVAILABLE,
        conflicts=(),
        missing_fields=("index_state", "regime_features"),
    )

    assert context.regime.regime is KRMarketRegime.UNKNOWN
    assert context.disposition is ContextDisposition.ANALYSIS_INCOMPLETE
    assert context.action_eligible is False
    assert "SIDEWAYS" not in context.to_canonical_json()


def test_context_rejects_source_available_after_evaluation_boundary() -> None:
    with pytest.raises(ValidationError, match="available_at must be at or before context as_of"):
        KRMarketContext(
            timing=MarketContextTiming(
                session_date=date(2026, 7, 28),
                session_state=SessionState.PRIOR_CLOSE,
                as_of=datetime(2026, 7, 28, 15, 30, tzinfo=KST),
                ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ),
            source_clocks=(_clock(),),
            index_state=(),
            breadth=(),
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=RegimeAssessment.unknown(missing_features=("kospi_close",)),
            evidence_ids=("kis:evidence",),
            quality=DataQualityStatus.UNAVAILABLE,
            conflicts=(),
            missing_fields=("index_state",),
        )


def test_versioned_regime_is_derived_only_from_complete_deterministic_features() -> None:
    evidence_ids = ("kis:index",)
    assessment = classify_kr_regime(
        (
            RegimeFeature(
                name="kospi_close",
                value=Decimal("110"),
                unit="index_points",
                evidence_ids=evidence_ids,
            ),
            RegimeFeature(
                name="kospi_ma20",
                value=Decimal("100"),
                unit="index_points",
                evidence_ids=evidence_ids,
            ),
            RegimeFeature(
                name="kospi_return_10d_pct",
                value=Decimal("5.1"),
                unit="percent",
                evidence_ids=evidence_ids,
            ),
        )
    )

    assert assessment.regime is KRMarketRegime.STRONG_BULL
    assert assessment.version == "kr_regime_v1"
    assert assessment.missing_features == ()

    missing = classify_kr_regime(assessment.features[:2])
    assert missing.regime is KRMarketRegime.UNKNOWN
    assert missing.missing_features == ("kospi_return_10d_pct",)


def test_conflicts_and_missing_core_fields_propagate_fail_closed_quality() -> None:
    clocks = (_clock(),)

    assert derive_context_quality(
        source_clocks=clocks,
        conflicts=("KOSPI_CLOSE_PROVIDER_CONFLICT",),
        missing_fields=(),
    ) is DataQualityStatus.CONFLICT
    assert derive_context_quality(
        source_clocks=clocks,
        conflicts=(),
        missing_fields=("index_state",),
    ) is DataQualityStatus.UNAVAILABLE


def test_stale_quality_precedes_partial_quality_deterministically() -> None:
    stale = _clock().model_copy(update={"quality": DataQualityStatus.STALE})
    partial = _clock().model_copy(
        update={"source": "KIS_SECONDARY", "quality": DataQualityStatus.PARTIAL}
    )

    assert derive_context_quality(
        source_clocks=(stale, partial),
        conflicts=(),
        missing_fields=(),
    ) is DataQualityStatus.STALE


def test_canonical_serialization_round_trips_without_order_drift() -> None:
    context = KRMarketContext(
        timing=MarketContextTiming(
            session_date=date(2026, 7, 28),
            session_state=SessionState.PRIOR_CLOSE,
            as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
        ),
        source_clocks=(_clock(),),
        index_state=(),
        breadth=(),
        investor_flows=(),
        macro_indicators=(),
        group_leadership=(),
        regime=RegimeAssessment.unknown(missing_features=("kospi_close",)),
        evidence_ids=("kis:evidence",),
        quality=DataQualityStatus.UNAVAILABLE,
        conflicts=(),
        missing_fields=("index_state",),
    )

    encoded = context.to_canonical_json()
    decoded = KRMarketContext.model_validate_json(encoded)

    assert decoded.to_canonical_json() == encoded
    assert decoded.content_hash == context.content_hash


def test_metric_collections_reject_non_deterministic_name_order() -> None:
    with pytest.raises(ValidationError, match="deterministic order"):
        KRMarketContext(
            timing=MarketContextTiming(
                session_date=date(2026, 7, 28),
                session_state=SessionState.PRIOR_CLOSE,
                as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
                ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ),
            source_clocks=(_clock(),),
            index_state=(),
            breadth=(
                DeterministicMetric(
                    name="z_metric",
                    value=Decimal("1"),
                    unit="count",
                    source="KIS",
                    evidence_ids=("kis:evidence",),
                ),
                DeterministicMetric(
                    name="a_metric",
                    value=Decimal("2"),
                    unit="count",
                    source="KIS",
                    evidence_ids=("kis:evidence",),
                ),
            ),
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=RegimeAssessment.unknown(missing_features=("kospi_close",)),
            evidence_ids=("kis:evidence",),
            quality=DataQualityStatus.UNAVAILABLE,
            conflicts=(),
            missing_fields=("index_state",),
        )
