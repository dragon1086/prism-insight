from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from prism_core.data.contracts import DataQualityStatus
from prism_core.market import (
    DeterministicMetric,
    GroupLeadership,
    KRMarketContext,
    KRMarketRegime,
    MarketContextTiming,
    RegimeAssessment,
    RegimeFeature,
    SessionState,
    SourceClock,
    SourceRole,
    classify_kr_regime,
)


KST = ZoneInfo("Asia/Seoul")


def _clock() -> SourceClock:
    return SourceClock(
        source="KIS",
        role=SourceRole.PRIMARY,
        observed_at=datetime(2026, 7, 28, 15, 30, tzinfo=KST),
        available_at=datetime(2026, 7, 28, 15, 31, tzinfo=KST),
        ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
        quality=DataQualityStatus.FRESH,
        evidence_ids=("kis:evidence",),
    )


def test_source_clock_requires_explicit_primary_or_supplemental_role() -> None:
    with pytest.raises(ValidationError, match="Field required"):
        SourceClock(
            source="KIS",
            observed_at=datetime(2026, 7, 28, 15, 30, tzinfo=KST),
            available_at=datetime(2026, 7, 28, 15, 31, tzinfo=KST),
            ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            quality=DataQualityStatus.FRESH,
            evidence_ids=("kis:evidence",),
        )


def test_group_leadership_rejects_duplicate_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="leadership evidence IDs must be unique"):
        GroupLeadership(
            group_id="KRX:SEMICONDUCTORS",
            rank=1,
            concentration_pct=Decimal("25"),
            source="KIS",
            evidence_ids=("kis:evidence", "kis:evidence"),
        )


def _complete_regime() -> RegimeAssessment:
    return classify_kr_regime(
        (
            RegimeFeature(
                name="kospi_close",
                value=Decimal("101"),
                unit="index_points",
                evidence_ids=("kis:evidence",),
            ),
            RegimeFeature(
                name="kospi_ma20",
                value=Decimal("100"),
                unit="index_points",
                evidence_ids=("kis:evidence",),
            ),
            RegimeFeature(
                name="kospi_return_10d_pct",
                value=Decimal("1"),
                unit="percent",
                evidence_ids=("kis:evidence",),
            ),
        )
    )


def test_context_rejects_fresh_quality_when_primary_source_is_stale() -> None:
    stale_clock = _clock().model_copy(
        update={"quality": DataQualityStatus.STALE}
    )

    with pytest.raises(ValidationError, match="quality must equal derived primary quality"):
        KRMarketContext(
            timing=MarketContextTiming(
                session_date=date(2026, 7, 28),
                session_state=SessionState.PRIOR_CLOSE,
                as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
                ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ),
            source_clocks=(stale_clock,),
            index_state=(),
            breadth=(),
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=_complete_regime(),
            evidence_ids=("kis:evidence",),
            quality=DataQualityStatus.FRESH,
            conflicts=(),
            missing_fields=(),
        )


def test_known_regime_cannot_be_constructed_without_classifier_features() -> None:
    with pytest.raises(ValidationError, match="known regime must match kr_regime_v1"):
        RegimeAssessment(
            regime=KRMarketRegime.SIDEWAYS,
            version="caller_selected",
            features=(),
            reason_codes=("DEFAULT_SIDEWAYS",),
            missing_features=(),
        )


@pytest.mark.parametrize("values", [("1", "2"), ("2", "1")])
def test_context_rejects_duplicate_metric_keys_in_every_input_order(
    values: tuple[str, str],
) -> None:
    duplicate_metrics = tuple(
        DeterministicMetric(
            name="kospi_close",
            value=Decimal(value),
            unit="index_points",
            source="KIS",
            evidence_ids=("kis:evidence",),
        )
        for value in values
    )

    with pytest.raises(ValidationError, match="collection keys must be unique"):
        KRMarketContext(
            timing=MarketContextTiming(
                session_date=date(2026, 7, 28),
                session_state=SessionState.PRIOR_CLOSE,
                as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
                ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ),
            source_clocks=(_clock(),),
            index_state=duplicate_metrics,
            breadth=(),
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=RegimeAssessment.unknown(missing_features=("kospi_ma20",)),
            evidence_ids=("kis:evidence",),
            quality=DataQualityStatus.UNAVAILABLE,
            conflicts=(),
            missing_fields=("regime_features",),
        )


def test_context_rejects_unbound_metric_evidence_id() -> None:
    with pytest.raises(ValidationError, match="every nested evidence ID"):
        KRMarketContext(
            timing=MarketContextTiming(
                session_date=date(2026, 7, 28),
                session_state=SessionState.PRIOR_CLOSE,
                as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
                ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ),
            source_clocks=(_clock(),),
            index_state=(
                DeterministicMetric(
                    name="kospi_close",
                    value=Decimal("101"),
                    unit="index_points",
                    source="KIS",
                    evidence_ids=("kis:unbound",),
                ),
            ),
            breadth=(),
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=RegimeAssessment.unknown(missing_features=("kospi_ma20",)),
            evidence_ids=("kis:evidence",),
            quality=DataQualityStatus.UNAVAILABLE,
            conflicts=(),
            missing_fields=("regime_features",),
        )


@pytest.mark.parametrize(
    ("close", "ma20", "return_10d", "expected"),
    [
        ("101", "100", "1", KRMarketRegime.MODERATE_BULL),
        ("99", "100", "-5", KRMarketRegime.STRONG_BEAR),
        ("99", "100", "-1", KRMarketRegime.MODERATE_BEAR),
        ("101", "100", "-1", KRMarketRegime.SIDEWAYS),
    ],
)
def test_regime_directional_branches_require_complete_features(
    close: str, ma20: str, return_10d: str, expected: KRMarketRegime
) -> None:
    evidence_ids = ("kis:index",)
    assessment = classify_kr_regime(
        tuple(
            RegimeFeature(
                name=name,
                value=Decimal(value),
                unit=unit,
                evidence_ids=evidence_ids,
            )
            for name, value, unit in (
                ("kospi_close", close, "index_points"),
                ("kospi_ma20", ma20, "index_points"),
                ("kospi_return_10d_pct", return_10d, "percent"),
            )
        )
    )

    assert assessment.regime is expected
    assert assessment.missing_features == ()


def test_decimal_metric_round_trip_preserves_context_identity() -> None:
    context = KRMarketContext(
        timing=MarketContextTiming(
            session_date=date(2026, 7, 28),
            session_state=SessionState.PRIOR_CLOSE,
            as_of=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
            ingested_at=datetime(2026, 7, 29, 8, 0, tzinfo=KST),
        ),
        source_clocks=(_clock(),),
        index_state=(
            DeterministicMetric(
                name="kospi_return_10d_pct",
                value=Decimal("1.2500"),
                unit="percent",
                source="KIS",
                evidence_ids=("kis:evidence",),
            ),
        ),
        breadth=(),
        investor_flows=(),
        macro_indicators=(),
        group_leadership=(),
        regime=RegimeAssessment.unknown(missing_features=("kospi_close",)),
        evidence_ids=("kis:evidence",),
        quality=DataQualityStatus.UNAVAILABLE,
        conflicts=(),
        missing_fields=("regime_features",),
    )

    restored = KRMarketContext.model_validate_json(context.to_canonical_json())

    assert restored.index_state[0].value == Decimal("1.2500")
    assert restored.content_hash == context.content_hash


def test_conflict_quality_requires_an_explicit_conflict_reason() -> None:
    with pytest.raises(ValidationError, match="CONFLICT context quality requires"):
        KRMarketContext(
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
            quality=DataQualityStatus.CONFLICT,
            conflicts=(),
            missing_fields=("index_state",),
        )
