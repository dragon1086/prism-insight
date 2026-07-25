"""Strategy registry identity and isolation tests."""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition

from prism_core.strategies import (
    DEFAULT_STRATEGY_REGISTRY,
    FeatureSnapshot,
    FeatureValue,
    Market,
    SWING_V1,
    TREND_V1,
    StrategyId,
    StrategyRegistry,
)


def test_default_registry_exposes_both_strategy_families_by_distinct_id() -> None:
    assert DEFAULT_STRATEGY_REGISTRY.strategy_ids == (
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    )
    assert DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1).strategy_id is StrategyId.SWING_V1
    assert DEFAULT_STRATEGY_REGISTRY.get(StrategyId.TREND_V1).strategy_id is StrategyId.TREND_V1


def test_registry_rejects_duplicate_strategy_ids() -> None:
    swing = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

    with pytest.raises(ValueError, match="duplicate strategy_id"):
        StrategyRegistry((swing, swing))


def test_strategy_definition_rejects_cross_family_feature_and_threshold_ownership() -> None:
    with pytest.raises(ValueError, match="owned by TREND_V1"):
        replace(TREND_V1, entry_template=SWING_V1.entry_template)


def test_strategy_definition_requires_typed_identity() -> None:
    with pytest.raises(TypeError, match="strategy_id must be a StrategyId"):
        replace(SWING_V1, strategy_id="SWING_V1")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("feature_snapshot_id", "snapshot-id", "feature_snapshot_id must be a UUID"),
        ("strategy_id", "SWING_V1", "strategy_id must be a StrategyId"),
        ("strategy_version", "1.0.0", "strategy_version must be a StrategyVersion"),
        ("market", "US", "market must be a Market"),
        ("security_id", uuid4(), "security_id must be a SecurityId"),
        ("data_snapshot_id", "data-id", "data_snapshot_id must be a UUID"),
        ("data_quality_status", "FRESH", "data_quality_status must be a DataQualityStatus"),
        ("quality_disposition", "ACCEPT", "quality_disposition must be a QualityDisposition"),
    ],
)
def test_feature_snapshot_rejects_untyped_identity_and_quality_fields(
    field_name: str, invalid_value: object, message: str
) -> None:
    values = {
        "feature_snapshot_id": uuid4(),
        "strategy_id": StrategyId.SWING_V1,
        "strategy_version": SWING_V1.version,
        "market": Market.US,
        "security_id": SecurityId(value=uuid4()),
        "data_snapshot_id": uuid4(),
        "as_of": datetime(2026, 7, 24, tzinfo=timezone.utc),
        "feature_version": "swing.features.v1",
        "values": (FeatureValue(name="swing_v1.momentum", value=Decimal("1")),),
        "data_quality_status": DataQualityStatus.FRESH,
        "quality_disposition": QualityDisposition.ACCEPT,
    }
    values[field_name] = invalid_value

    with pytest.raises(TypeError, match=message):
        FeatureSnapshot(**values)  # type: ignore[arg-type]


def test_same_security_and_market_can_have_separate_strategy_identities() -> None:
    security_id = SecurityId(value=uuid4())
    data_snapshot_id = uuid4()
    common = {
        "market": Market.US,
        "security_id": security_id,
        "data_snapshot_id": data_snapshot_id,
        "as_of": datetime(2026, 7, 24, tzinfo=timezone.utc),
        "data_quality_status": DataQualityStatus.FRESH,
        "quality_disposition": QualityDisposition.ACCEPT,
    }

    swing_snapshot = FeatureSnapshot(
        feature_snapshot_id=uuid4(),
        strategy_id=StrategyId.SWING_V1,
        strategy_version=SWING_V1.version,
        feature_version="swing.features.v1",
        values=(FeatureValue(name="swing_v1.momentum", value=Decimal("1")),),
        **common,
    )
    trend_snapshot = FeatureSnapshot(
        feature_snapshot_id=uuid4(),
        strategy_id=StrategyId.TREND_V1,
        strategy_version=TREND_V1.version,
        feature_version="trend.features.v1",
        values=(FeatureValue(name="trend_v1.strength", value=Decimal("1")),),
        **common,
    )

    assert swing_snapshot.security_id == trend_snapshot.security_id
    assert (swing_snapshot.strategy_id, swing_snapshot.market) != (
        trend_snapshot.strategy_id,
        trend_snapshot.market,
    )


@pytest.mark.parametrize("market", [Market.KR, Market.US])
def test_both_strategies_are_enabled_independently_for_each_market(market: Market) -> None:
    assert tuple(
        definition.strategy_id
        for definition in DEFAULT_STRATEGY_REGISTRY.enabled_for(market)
    ) == (StrategyId.SWING_V1, StrategyId.TREND_V1)
