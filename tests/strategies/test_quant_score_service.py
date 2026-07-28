from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    StrategyId,
)
from prism_core.strategies.quant_score import (
    QuantScorePolicy,
    QuantScoreRule,
    QuantScoreService,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY


AS_OF = datetime(2026, 7, 26, tzinfo=timezone.utc)


def _snapshot() -> FeatureSnapshot:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    return FeatureSnapshot(
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000103"),
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SecurityId(value=UUID("00000000-0000-0000-0000-000000000102")),
        data_snapshot_id=UUID("00000000-0000-0000-0000-000000000104"),
        as_of=AS_OF,
        feature_version="features.v1",
        values=(
            FeatureValue(name="swing_v1.price_momentum_5d", value=Decimal("0.05")),
            FeatureValue(name="swing_v1.regime_compatibility", value=Decimal("80")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def _policy() -> QuantScorePolicy:
    return QuantScorePolicy(
        strategy_id=StrategyId.SWING_V1,
        score_version="swing-score.shadow.v1",
        rules=(
            QuantScoreRule(
                feature_name="swing_v1.price_momentum_5d",
                lower_bound=Decimal("-0.10"),
                upper_bound=Decimal("0.10"),
                weight=Decimal("0.6"),
            ),
            QuantScoreRule(
                feature_name="swing_v1.regime_compatibility",
                lower_bound=Decimal("0"),
                upper_bound=Decimal("100"),
                weight=Decimal("0.4"),
            ),
        ),
    )


def test_quant_score_is_deterministic_bounded_and_uses_explicit_policy() -> None:
    service = QuantScoreService()
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

    first = service.score(strategy, _snapshot(), _policy())
    second = service.score(strategy, _snapshot(), _policy())

    assert first == second
    assert first.total_score == Decimal("77.000000")
    assert tuple(item.score for item in first.components) == (
        Decimal("75.000000"),
        Decimal("80.000000"),
    )
    assert first.score_version == "swing-score.shadow.v1"


def test_quant_score_rejects_non_accept_snapshot_and_missing_feature() -> None:
    service = QuantScoreService()
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    stale = _snapshot().__class__(
        **{
            **_snapshot().__dict__,
            "data_quality_status": DataQualityStatus.STALE,
            "quality_disposition": QualityDisposition.REPORT_ONLY,
        }
    )
    with pytest.raises(ValueError, match="FRESH ACCEPT"):
        service.score(strategy, stale, _policy())

    missing_policy = QuantScorePolicy(
        strategy_id=StrategyId.SWING_V1,
        score_version="swing-score.shadow.v1",
        rules=(
            QuantScoreRule(
                feature_name="swing_v1.volume_expansion_20d",
                lower_bound=Decimal("0"),
                upper_bound=Decimal("2"),
                weight=Decimal("1"),
            ),
        ),
    )
    with pytest.raises(ValueError, match="missing scored feature"):
        service.score(strategy, _snapshot(), missing_policy)


def test_policy_rejects_hidden_or_unnormalized_weights() -> None:
    with pytest.raises(ValueError, match="sum to exactly 1"):
        QuantScorePolicy(
            strategy_id=StrategyId.SWING_V1,
            score_version="swing-score.shadow.v1",
            rules=(
                QuantScoreRule(
                    feature_name="swing_v1.price_momentum_5d",
                    lower_bound=Decimal("0"),
                    upper_bound=Decimal("1"),
                    weight=Decimal("0.9"),
                ),
            ),
        )
