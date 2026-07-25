"""SWING_V1 contract tests."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.strategies import (
    FeatureSnapshot,
    FeatureValue,
    LessonScope,
    Market,
    SWING_V1,
    StrategyId,
)


UTC = timezone.utc


def test_swing_definition_owns_short_horizon_features_thresholds_and_lessons() -> None:
    assert SWING_V1.strategy_id is StrategyId.SWING_V1
    assert tuple(item.trading_sessions for item in SWING_V1.outcome_horizons) == (5, 10, 20)
    assert SWING_V1.default_lesson_scope is LessonScope.STRATEGY
    assert all(name.startswith("swing_v1.") for name in SWING_V1.entry_template.required_feature_names)
    assert all(name.startswith("swing_v1.") for name in SWING_V1.entry_template.threshold_names)


def test_swing_feature_snapshot_keeps_strategy_market_and_quality_fields_explicit() -> None:
    snapshot = FeatureSnapshot(
        feature_snapshot_id=uuid4(),
        strategy_id=StrategyId.SWING_V1,
        strategy_version=SWING_V1.version,
        market=Market.US,
        security_id=SecurityId(value=uuid4()),
        data_snapshot_id=uuid4(),
        as_of=datetime(2026, 7, 24, tzinfo=UTC),
        feature_version="swing.features.v1",
        values=(FeatureValue(name="swing_v1.price_momentum_5d", value=Decimal("4.2")),),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )

    assert snapshot.strategy_id is StrategyId.SWING_V1
    assert snapshot.market is Market.US
    assert snapshot.data_quality_status is DataQualityStatus.FRESH
    assert snapshot.quality_disposition is QualityDisposition.ACCEPT


def test_non_fresh_feature_snapshot_cannot_claim_accept_disposition() -> None:
    with pytest.raises(ValueError, match="non-fresh data quality"):
        FeatureSnapshot(
            feature_snapshot_id=uuid4(),
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_V1.version,
            market=Market.KR,
            security_id=SecurityId(value=uuid4()),
            data_snapshot_id=uuid4(),
            as_of=datetime(2026, 7, 24, tzinfo=UTC),
            feature_version="swing.features.v1",
            values=(
                FeatureValue(
                    name="swing_v1.price_momentum_5d", value=Decimal("1")
                ),
            ),
            data_quality_status=DataQualityStatus.STALE,
            quality_disposition=QualityDisposition.ACCEPT,
        )
