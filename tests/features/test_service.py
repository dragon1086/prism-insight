"""Deterministic quant feature-service contract tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_DOWN, ROUND_UP, localcontext
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus, SecurityId
import pytest

from prism_core.data.quality import (
    DataQualityGate,
    QualityDecision,
    QualityDisposition,
)
from prism_core.features import (
    BenchmarkPoint,
    FeatureComputationInput,
    FeatureComputationRejected,
    NumericObservation,
    PriceBasis,
    PricePoint,
    QuantFeatureService,
    normalized_feature_snapshot,
)
from prism_core.strategies import (
    FeatureSnapshot,
    Market,
    SWING_V1,
    TREND_V1,
    StrategyId,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 24, 21, tzinfo=UTC)
DATA_SNAPSHOT_ID = UUID("10000000-0000-0000-0000-000000000001")
SECURITY_ID = SecurityId(value=UUID("20000000-0000-0000-0000-000000000002"))


def _prices(count: int = 260) -> tuple[PricePoint, ...]:
    start = AS_OF - timedelta(days=count)
    return tuple(
        PricePoint(
            observed_at=start + timedelta(days=index),
            available_at=start + timedelta(days=index, hours=1),
            high=Decimal(101 + index),
            low=Decimal(99 + index),
            close=Decimal(100 + index),
            volume=Decimal(1_000_000 + index * 10_000),
        )
        for index in range(count)
    )


def _input() -> FeatureComputationInput:
    prices = _prices()
    benchmark = tuple(
        BenchmarkPoint(
            available_at=point.available_at,
            close=point.close - Decimal("20"),
        )
        for point in prices
    )
    quality = tuple(
        (field, DataQualityStatus.FRESH)
        for field in ("calendar", "evidence", "fundamental", "price", "regime")
    )
    gate = DataQualityGate(
        core_fields={"calendar", "evidence", "price", "regime"},
        report_only_fields={"fundamental"},
    )
    return FeatureComputationInput(
        data_snapshot_id=DATA_SNAPSHOT_ID,
        market=Market.US,
        security_id=SECURITY_ID,
        as_of=AS_OF,
        price_basis=PriceBasis.ADJUSTED,
        prices=prices,
        benchmark_points=benchmark,
        observations=(
            NumericObservation(
                name="catalyst_recency_sessions",
                value=Decimal("3"),
                available_at=AS_OF - timedelta(days=1),
            ),
            NumericObservation(
                name="regime_swing_compatibility",
                value=Decimal("72.5"),
                available_at=AS_OF,
            ),
        ),
        field_quality=quality,
        quality_decision=gate.evaluate(dict(quality)),
    )


def test_same_swing_input_and_version_produce_byte_stable_existing_contract() -> None:
    service = QuantFeatureService(feature_version="quant.features.v1")

    first = service.compute(SWING_V1, _input())
    second = service.compute(SWING_V1, _input())

    assert type(first) is FeatureSnapshot
    assert first is not second
    assert first == second
    assert first.feature_snapshot_id == second.feature_snapshot_id
    assert normalized_feature_snapshot(first) == normalized_feature_snapshot(second)
    assert first.strategy_id is StrategyId.SWING_V1
    assert first.strategy_version == SWING_V1.version
    assert first.data_snapshot_id == DATA_SNAPSHOT_ID
    assert first.market is Market.US
    assert first.security_id == SECURITY_ID
    assert first.as_of == AS_OF
    assert first.data_quality_status is DataQualityStatus.FRESH
    assert first.quality_disposition == _input().quality_decision.disposition
    assert tuple(value.name for value in first.values) == tuple(
        sorted(
            (
                "swing_v1.atr_percent_14d",
                "swing_v1.average_dollar_volume_20d",
                "swing_v1.average_volume_20d_shares",
                "swing_v1.benchmark_excess_return_20d_percentage_points",
                "swing_v1.breakout_distance_20d_percent",
                "swing_v1.catalyst_recency_sessions",
                "swing_v1.price_momentum_5d",
                "swing_v1.price_return_5d_percent",
                "swing_v1.regime_compatibility",
                "swing_v1.relative_strength_20d",
                "swing_v1.volume_expansion_20d",
                "swing_v1.volume_expansion_20d_percent",
            )
        )
    )
    values = {item.name: item.value for item in first.values}
    assert values["swing_v1.price_return_5d_percent"] == values[
        "swing_v1.price_momentum_5d"
    ]
    assert values[
        "swing_v1.benchmark_excess_return_20d_percentage_points"
    ] == values["swing_v1.relative_strength_20d"]
    assert values["swing_v1.volume_expansion_20d_percent"] == values[
        "swing_v1.volume_expansion_20d"
    ]
    assert values["swing_v1.average_volume_20d_shares"] == Decimal(
        "3495000.000000"
    )
    assert values["swing_v1.breakout_distance_20d_percent"] == Decimal(
        "0.000000"
    )


def test_shadow_score_inputs_pin_real_formula_units_and_signs() -> None:
    service = QuantFeatureService(feature_version="SHADOW_FEATURES_V1")
    swing_values = {
        item.name: item.value for item in service.compute(SWING_V1, _input()).values
    }
    trend_values = {
        item.name: item.value
        for item in service.compute(
            TREND_V1,
            replace(
                _input(),
                observations=(
                    NumericObservation("earnings_current", Decimal("12"), AS_OF),
                    NumericObservation("earnings_previous", Decimal("10"), AS_OF),
                    NumericObservation("industry_leadership", Decimal("81"), AS_OF),
                    NumericObservation(
                        "regime_trend_compatibility", Decimal("66"), AS_OF
                    ),
                ),
            ),
        ).values
    }

    assert swing_values["swing_v1.price_return_5d_percent"] == Decimal(
        "1.412429378531"
    )
    assert swing_values[
        "swing_v1.benchmark_excess_return_20d_percentage_points"
    ] == Decimal("-0.369887461740")
    assert swing_values["swing_v1.volume_expansion_20d_percent"] == Decimal(
        "103.012912482066"
    )
    assert swing_values["swing_v1.atr_percent_14d"] == Decimal(
        "0.557103064067"
    )
    assert trend_values["trend_v1.price_above_200d"] == Decimal(
        "38.342967244701"
    )
    assert trend_values["trend_v1.moving_average_alignment"] == Decimal(
        "28.901734104046"
    )
    assert trend_values[
        "trend_v1.benchmark_excess_return_60d_percentage_points"
    ] == Decimal("-1.438486711979")
    assert trend_values["trend_v1.earnings_trend"] == Decimal("20.000000000000")


def test_swing_rejects_before_any_short_window_can_be_used() -> None:
    inputs = _input()

    with pytest.raises(
        FeatureComputationRejected,
        match="SWING_V1 requires 21 completed sessions",
    ):
        QuantFeatureService(feature_version="SHADOW_FEATURES_V1").compute(
            SWING_V1,
            replace(
                inputs,
                prices=inputs.prices[-20:],
                benchmark_points=inputs.benchmark_points[-20:],
            ),
        )


def test_normalized_output_is_independent_of_ambient_decimal_context() -> None:
    service = QuantFeatureService(feature_version="quant.features.v1")
    inputs = _input()

    with localcontext(Context(prec=9, rounding=ROUND_DOWN)):
        rounded_down = normalized_feature_snapshot(service.compute(SWING_V1, inputs))
    with localcontext(Context(prec=60, rounding=ROUND_UP)):
        rounded_up = normalized_feature_snapshot(service.compute(SWING_V1, inputs))

    assert rounded_down == rounded_up


def test_trend_and_swing_compute_separate_owned_feature_families() -> None:
    inputs = _input()
    trend_inputs = replace(
        inputs,
        observations=(
            NumericObservation("earnings_current", Decimal("12"), AS_OF),
            NumericObservation("earnings_previous", Decimal("10"), AS_OF),
            NumericObservation("industry_leadership", Decimal("81"), AS_OF),
            NumericObservation("regime_trend_compatibility", Decimal("66"), AS_OF),
        ),
    )
    service = QuantFeatureService(feature_version="quant.features.v1")

    swing = service.compute(SWING_V1, inputs)
    trend = service.compute(TREND_V1, trend_inputs)

    assert swing.feature_snapshot_id != trend.feature_snapshot_id
    assert all(item.name.startswith("swing_v1.") for item in swing.values)
    assert all(item.name.startswith("trend_v1.") for item in trend.values)
    assert not ({item.name for item in swing.values} & {item.name for item in trend.values})
    assert {
        "trend_v1.benchmark_excess_return_60d_percentage_points",
        "trend_v1.average_volume_20d_shares",
        "trend_v1.distance_below_52_week_high_percent",
        "trend_v1.earnings_trend",
        "trend_v1.industry_leadership",
        "trend_v1.moving_average_alignment",
        "trend_v1.price_above_200d",
        "trend_v1.regime_compatibility",
        "trend_v1.relative_strength_60d",
    }.issubset(item.name for item in trend.values)
    trend_values = {item.name: item.value for item in trend.values}
    assert trend_values["trend_v1.distance_below_52_week_high_percent"] == Decimal(
        "0.277777777778"
    )


def test_unscored_catalyst_and_industry_context_are_not_core_feature_requirements() -> None:
    inputs = _input()
    swing = QuantFeatureService(feature_version="SHADOW_FEATURES_V1").compute(
        SWING_V1,
        replace(
            inputs,
            observations=(
                NumericObservation(
                    "regime_swing_compatibility", Decimal("70"), AS_OF
                ),
            ),
        ),
    )
    trend = QuantFeatureService(feature_version="SHADOW_FEATURES_V1").compute(
        TREND_V1,
        replace(
            inputs,
            observations=(
                NumericObservation("earnings_current", Decimal("12"), AS_OF),
                NumericObservation("earnings_previous", Decimal("10"), AS_OF),
                NumericObservation(
                    "regime_trend_compatibility", Decimal("65"), AS_OF
                ),
            ),
        ),
    )

    assert "swing_v1.catalyst_recency_sessions" not in {
        item.name for item in swing.values
    }
    assert "trend_v1.industry_leadership" not in {
        item.name for item in trend.values
    }


def test_service_rejects_strategy_contract_with_uncomputed_required_feature() -> None:
    incompatible = replace(
        SWING_V1,
        entry_template=replace(
            SWING_V1.entry_template,
            required_feature_names=(
                *SWING_V1.entry_template.required_feature_names,
                "swing_v1.uncomputed_required_feature",
            ),
        ),
    )

    with pytest.raises(ValueError, match="missing strategy-required features"):
        QuantFeatureService(feature_version="quant.features.v1").compute(
            incompatible, _input()
        )


def test_input_rejects_numeric_observation_not_available_at_as_of() -> None:
    inputs = _input()
    future = NumericObservation(
        name="regime_swing_compatibility",
        value=Decimal("70"),
        available_at=AS_OF + timedelta(microseconds=1),
    )

    with pytest.raises(ValueError, match="unavailable at as_of"):
        replace(inputs, observations=(future,))


def test_input_rejects_benchmark_point_not_available_at_as_of() -> None:
    inputs = _input()
    future = BenchmarkPoint(
        available_at=AS_OF + timedelta(microseconds=1),
        close=Decimal("100"),
    )

    with pytest.raises(ValueError, match="benchmark input was unavailable at as_of"):
        replace(inputs, benchmark_points=(*inputs.benchmark_points[:-1], future))


def test_input_rejects_unordered_benchmark_points() -> None:
    inputs = _input()

    with pytest.raises(ValueError, match="benchmark_points must be ordered"):
        replace(
            inputs,
            benchmark_points=(
                inputs.benchmark_points[1],
                inputs.benchmark_points[0],
                *inputs.benchmark_points[2:],
            ),
        )


def test_input_rejects_price_and_benchmark_session_misalignment() -> None:
    inputs = _input()

    with pytest.raises(ValueError, match="price and benchmark sessions must align"):
        replace(inputs, benchmark_points=inputs.benchmark_points[1:])


@pytest.mark.parametrize(
    ("status", "decision"),
    [
        (
            DataQualityStatus.STALE,
            QualityDecision(
                disposition=QualityDisposition.REJECT,
                reasons=("stale_core:price",),
                missing_fields=(),
                stale_fields=("price",),
            ),
        ),
        (
            DataQualityStatus.PARTIAL,
            QualityDecision(
                disposition=QualityDisposition.REJECT,
                reasons=("partial_core:price",),
                missing_fields=("price",),
                stale_fields=(),
            ),
        ),
        (
            DataQualityStatus.CONFLICT,
            QualityDecision(
                disposition=QualityDisposition.REJECT,
                reasons=("conflict_core:price",),
                missing_fields=(),
                stale_fields=(),
            ),
        ),
    ],
)
def test_core_quality_rejection_never_produces_feature_snapshot(
    status: DataQualityStatus, decision: QualityDecision
) -> None:
    inputs = _input()
    quality = tuple(
        (name, status if name == "price" else value)
        for name, value in inputs.field_quality
    )

    with pytest.raises(ValueError, match=decision.reasons[0]):
        QuantFeatureService(feature_version="quant.features.v1").compute(
            SWING_V1,
            replace(inputs, field_quality=quality, quality_decision=decision),
        )


def test_report_only_quality_is_preserved_but_never_upgraded_to_accept() -> None:
    inputs = _input()
    quality = tuple(
        (name, DataQualityStatus.PARTIAL if name == "fundamental" else status)
        for name, status in inputs.field_quality
    )
    decision = QualityDecision(
        disposition=QualityDisposition.REPORT_ONLY,
        reasons=("partial_report_only:fundamental",),
        missing_fields=("fundamental",),
        stale_fields=(),
    )

    snapshot = QuantFeatureService(feature_version="quant.features.v1").compute(
        SWING_V1,
        replace(inputs, field_quality=quality, quality_decision=decision),
    )

    assert snapshot.data_quality_status is DataQualityStatus.PARTIAL
    assert snapshot.quality_disposition is QualityDisposition.REPORT_ONLY


def test_missing_core_quality_field_fails_closed_even_if_decision_claims_accept() -> None:
    inputs = _input()
    incomplete_quality = tuple(
        item for item in inputs.field_quality if item[0] != "price"
    )

    with pytest.raises(ValueError, match="missing core quality fields: price"):
        QuantFeatureService(feature_version="quant.features.v1").compute(
            SWING_V1,
            replace(inputs, field_quality=incomplete_quality),
        )


def test_quality_decision_must_match_full_field_level_gate_result() -> None:
    inputs = _input()
    quality = tuple(
        (name, DataQualityStatus.STALE if name == "price" else status)
        for name, status in inputs.field_quality
    )

    with pytest.raises(ValueError, match="quality_decision does not match"):
        QuantFeatureService(feature_version="quant.features.v1").compute(
            SWING_V1,
            replace(inputs, field_quality=quality),
        )
