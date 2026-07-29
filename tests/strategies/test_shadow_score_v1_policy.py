from dataclasses import replace
from datetime import datetime, timezone
from decimal import Context, Decimal, ROUND_DOWN, ROUND_UP, localcontext
from uuid import UUID

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.strategies import Market, StrategyId
from prism_core.strategies.contracts import FeatureSnapshot, FeatureValue
from prism_core.strategies.quant_score import (
    QuantScoreService,
    evaluate_shadow_entry_thresholds,
    shadow_score_v1_policy,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY


def test_shadow_score_v1_policies_are_shared_across_kr_us_and_strategy_separated() -> None:
    swing_kr = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)
    swing_us = shadow_score_v1_policy(StrategyId.SWING_V1, Market.US)
    trend_kr = shadow_score_v1_policy(StrategyId.TREND_V1, Market.KR)
    trend_us = shadow_score_v1_policy(StrategyId.TREND_V1, Market.US)

    assert swing_kr is swing_us
    assert trend_kr is trend_us
    assert swing_kr is not trend_kr
    assert swing_kr.score_version == "SHADOW_SCORE_V1.SWING_V1"
    assert trend_kr.score_version == "SHADOW_SCORE_V1.TREND_V1"
    assert sum((rule.weight for rule in swing_kr.rules), Decimal("0")) == Decimal("1")
    assert sum((rule.weight for rule in trend_kr.rules), Decimal("0")) == Decimal("1")
    assert tuple(rule.weight for rule in swing_kr.rules) == (
        Decimal("0.30"),
        Decimal("0.20"),
        Decimal("0.20"),
        Decimal("0.10"),
        Decimal("0.20"),
    )
    assert tuple(rule.weight for rule in trend_kr.rules) == (
        Decimal("0.25"),
        Decimal("0.20"),
        Decimal("0.20"),
        Decimal("0.15"),
        Decimal("0.20"),
    )
    assert "swing_v1.catalyst_recency_sessions" not in {
        rule.feature_name for rule in swing_kr.rules
    }
    assert "trend_v1.industry_leadership" not in {
        rule.feature_name for rule in trend_kr.rules
    }

    assert swing_kr.thresholds.values == (
        ("swing_v1.min_liquidity", Decimal("100000"), "shares_per_session"),
        ("swing_v1.min_quant_score", Decimal("65"), "score_0_100"),
        ("swing_v1.max_atr_percent", Decimal("8"), "percent"),
        ("swing_v1.entry_breakout_buffer", Decimal("0.5"), "percent"),
    )
    assert trend_kr.thresholds.values == (
        ("trend_v1.min_liquidity", Decimal("100000"), "shares_per_session"),
        ("trend_v1.min_quant_score", Decimal("65"), "score_0_100"),
        ("trend_v1.min_trend_strength", Decimal("0"), "percent"),
        ("trend_v1.max_pullback_from_high", Decimal("15"), "percent_below_high"),
    )


def _swing_snapshot(price_return: str) -> FeatureSnapshot:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    return FeatureSnapshot(
        feature_snapshot_id=UUID("10000000-0000-0000-0000-000000000001"),
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.KR,
        security_id=SecurityId(
            value=UUID("20000000-0000-0000-0000-000000000002")
        ),
        data_snapshot_id=UUID("30000000-0000-0000-0000-000000000003"),
        as_of=datetime(2026, 7, 29, tzinfo=timezone.utc),
        feature_version="SHADOW_FEATURES_V1",
        values=(
            FeatureValue("swing_v1.price_return_5d_percent", Decimal(price_return)),
            FeatureValue(
                "swing_v1.benchmark_excess_return_20d_percentage_points",
                Decimal("5"),
            ),
            FeatureValue("swing_v1.volume_expansion_20d_percent", Decimal("125")),
            FeatureValue("swing_v1.atr_percent_14d", Decimal("5")),
            FeatureValue("swing_v1.catalyst_recency_sessions", Decimal("10")),
            FeatureValue("swing_v1.regime_compatibility", Decimal("50")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def test_swing_5d_return_uses_percentage_point_bounds_without_point_one_saturation() -> None:
    service = QuantScoreService()
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)

    component_scores = []
    for raw in ("-10", "0", "10", "-0.1", "0.1"):
        result = service.score(strategy, _swing_snapshot(raw), policy)
        component_scores.append(result.components[0])

    assert tuple(item.name for item in component_scores) == (
        "swing_v1.momentum_state_score",
    ) * 5
    assert tuple(item.score for item in component_scores) == (
        Decimal("0.000000"),
        Decimal("50.000000"),
        Decimal("100.000000"),
        Decimal("49.500000"),
        Decimal("50.500000"),
    )
    assert service.score(strategy, _swing_snapshot("0"), policy) == service.score(
        strategy, replace(_swing_snapshot("0")), policy
    )


def test_swing_full_policy_has_a_golden_aggregate_and_byte_stable_identity() -> None:
    service = QuantScoreService()
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)

    first = service.score(strategy, _swing_snapshot("0"), policy)
    second = service.score(strategy, _swing_snapshot("0"), policy)

    assert first == second
    assert first.quant_score_id == second.quant_score_id
    assert first.total_score == Decimal("50.000000")
    assert tuple(item.score for item in first.components) == (
        Decimal("50.000000"),
    ) * 5


def test_shadow_score_is_independent_of_ambient_decimal_context() -> None:
    service = QuantScoreService()
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)
    snapshot = _swing_snapshot("0.123456789123456789")

    with localcontext(Context(prec=9, rounding=ROUND_DOWN)):
        rounded_down = service.score(strategy, snapshot, policy)
    with localcontext(Context(prec=60, rounding=ROUND_UP)):
        rounded_up = service.score(strategy, snapshot, policy)

    assert rounded_down == rounded_up
    assert rounded_down.quant_score_id == rounded_up.quant_score_id


def test_shadow_score_v1_rejects_a_legacy_feature_version_without_reinterpretation() -> None:
    with pytest.raises(ValueError, match="feature version must match score policy"):
        QuantScoreService().score(
            DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1),
            replace(_swing_snapshot("0"), feature_version="quant.features.v1"),
            shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR),
        )


def test_entry_thresholds_are_code_evaluated_and_fail_closed() -> None:
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.US)
    score = QuantScoreService().score(
        DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1),
        _swing_snapshot("0"),
        policy,
    )

    assert evaluate_shadow_entry_thresholds(
        policy,
        score,
        {
            "swing_v1.average_volume_20d_shares": Decimal("99999"),
            "swing_v1.atr_percent_14d": Decimal("8.1"),
            "swing_v1.breakout_distance_20d_percent": Decimal("0.49"),
        },
    ) == (
        "shadow_score_v1:swing_v1.entry_breakout_buffer",
        "shadow_score_v1:swing_v1.max_atr_percent",
        "shadow_score_v1:swing_v1.min_liquidity",
        "shadow_score_v1:swing_v1.min_quant_score",
    )

    assert evaluate_shadow_entry_thresholds(
        policy,
        replace(score, total_score=Decimal("65")),
        {
            "swing_v1.average_volume_20d_shares": Decimal("100000"),
            "swing_v1.atr_percent_14d": Decimal("8"),
            "swing_v1.breakout_distance_20d_percent": Decimal("0.5"),
        },
    ) == ()

    assert evaluate_shadow_entry_thresholds(policy, score, {}) == (
        "shadow_score_v1:missing:swing_v1.atr_percent_14d",
        "shadow_score_v1:missing:swing_v1.average_volume_20d_shares",
        "shadow_score_v1:missing:swing_v1.breakout_distance_20d_percent",
    )


def test_entry_thresholds_reject_a_legacy_score_version_without_reinterpretation() -> None:
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)
    score = QuantScoreService().score(
        DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1),
        _swing_snapshot("0"),
        policy,
    )

    with pytest.raises(ValueError, match="score version must match threshold policy"):
        evaluate_shadow_entry_thresholds(
            policy,
            replace(score, score_version="swing-score.shadow.v1"),
            {
                "swing_v1.average_volume_20d_shares": Decimal("100000"),
                "swing_v1.atr_percent_14d": Decimal("8"),
                "swing_v1.breakout_distance_20d_percent": Decimal("0.5"),
            },
        )


def _trend_snapshot() -> FeatureSnapshot:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.TREND_V1)
    return FeatureSnapshot(
        feature_snapshot_id=UUID("40000000-0000-0000-0000-000000000004"),
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SecurityId(
            value=UUID("50000000-0000-0000-0000-000000000005")
        ),
        data_snapshot_id=UUID("60000000-0000-0000-0000-000000000006"),
        as_of=datetime(2026, 7, 29, tzinfo=timezone.utc),
        feature_version="SHADOW_FEATURES_V1",
        values=(
            FeatureValue("trend_v1.price_above_200d", Decimal("30")),
            FeatureValue("trend_v1.moving_average_alignment", Decimal("-5")),
            FeatureValue(
                "trend_v1.benchmark_excess_return_60d_percentage_points",
                Decimal("30"),
            ),
            FeatureValue("trend_v1.earnings_trend", Decimal("25")),
            FeatureValue("trend_v1.industry_leadership", Decimal("100")),
            FeatureValue("trend_v1.regime_compatibility", Decimal("25")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def test_trend_full_policy_and_thresholds_have_golden_boundary_results() -> None:
    policy = shadow_score_v1_policy(StrategyId.TREND_V1, Market.US)
    score = QuantScoreService().score(
        DEFAULT_STRATEGY_REGISTRY.get(StrategyId.TREND_V1),
        _trend_snapshot(),
        policy,
    )

    assert tuple(item.score for item in score.components) == (
        Decimal("100.000000"),
        Decimal("0.000000"),
        Decimal("100.000000"),
        Decimal("50.000000"),
        Decimal("25.000000"),
    )
    assert score.total_score == Decimal("57.500000")
    assert evaluate_shadow_entry_thresholds(
        policy,
        score,
        {
            "trend_v1.average_volume_20d_shares": Decimal("99999"),
            "trend_v1.moving_average_alignment": Decimal("-0.1"),
            "trend_v1.distance_below_52_week_high_percent": Decimal("15.1"),
        },
    ) == (
        "shadow_score_v1:trend_v1.max_pullback_from_high",
        "shadow_score_v1:trend_v1.min_liquidity",
        "shadow_score_v1:trend_v1.min_quant_score",
        "shadow_score_v1:trend_v1.min_trend_strength",
    )
    assert evaluate_shadow_entry_thresholds(
        policy,
        replace(score, total_score=Decimal("65")),
        {
            "trend_v1.average_volume_20d_shares": Decimal("100000"),
            "trend_v1.moving_average_alignment": Decimal("0"),
            "trend_v1.distance_below_52_week_high_percent": Decimal("15"),
        },
    ) == ()
