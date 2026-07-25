"""TREND_V1 contract tests."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from prism_core.data.contracts import SecurityId
from prism_core.strategies import (
    LessonScope,
    Market,
    QuantScoreBreakdown,
    QuantScoreComponent,
    SWING_V1,
    TREND_V1,
    StrategyId,
)


def test_trend_definition_owns_medium_horizon_features_thresholds_and_lessons() -> None:
    assert TREND_V1.strategy_id is StrategyId.TREND_V1
    assert tuple(item.trading_sessions for item in TREND_V1.outcome_horizons) == (20, 60, 120)
    assert TREND_V1.default_lesson_scope is LessonScope.STRATEGY
    assert all(name.startswith("trend_v1.") for name in TREND_V1.entry_template.required_feature_names)
    assert all(name.startswith("trend_v1.") for name in TREND_V1.entry_template.threshold_names)
    assert set(TREND_V1.entry_template.required_feature_names).isdisjoint(
        SWING_V1.entry_template.required_feature_names
    )
    assert set(TREND_V1.entry_template.threshold_names).isdisjoint(
        SWING_V1.entry_template.threshold_names
    )


def test_trend_quant_score_remains_strategy_and_market_scoped() -> None:
    security_id = SecurityId(value=uuid4())
    breakdown = QuantScoreBreakdown(
        quant_score_id=uuid4(),
        feature_snapshot_id=uuid4(),
        strategy_id=StrategyId.TREND_V1,
        strategy_version=TREND_V1.version,
        market=Market.KR,
        security_id=security_id,
        score_version="trend.quant.v1",
        total_score=Decimal("72.5"),
        components=(
            QuantScoreComponent(name="trend_v1.trend_strength", score=Decimal("72.5")),
        ),
    )

    assert breakdown.strategy_id is StrategyId.TREND_V1
    assert breakdown.market is Market.KR
    assert breakdown.security_id == security_id


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("quant_score_id", "score-id", "quant_score_id must be a UUID"),
        ("feature_snapshot_id", "snapshot-id", "feature_snapshot_id must be a UUID"),
        ("strategy_id", "TREND_V1", "strategy_id must be a StrategyId"),
        ("strategy_version", "1.0.0", "strategy_version must be a StrategyVersion"),
        ("market", "KR", "market must be a Market"),
        ("security_id", uuid4(), "security_id must be a SecurityId"),
    ],
)
def test_quant_score_rejects_untyped_identity_fields(
    field_name: str, invalid_value: object, message: str
) -> None:
    breakdown = QuantScoreBreakdown(
        quant_score_id=uuid4(),
        feature_snapshot_id=uuid4(),
        strategy_id=StrategyId.TREND_V1,
        strategy_version=TREND_V1.version,
        market=Market.KR,
        security_id=SecurityId(value=uuid4()),
        score_version="trend.quant.v1",
        total_score=Decimal("72.5"),
        components=(
            QuantScoreComponent(name="trend_v1.trend_strength", score=Decimal("72.5")),
        ),
    )

    with pytest.raises(TypeError, match=message):
        replace(breakdown, **{field_name: invalid_value})
