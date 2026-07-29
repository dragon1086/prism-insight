"""Deterministic, versioned quantitative scoring for strategy feature snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    Market,
    QuantScoreBreakdown,
    QuantScoreComponent,
    StrategyDefinition,
    StrategyId,
)


_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class EntryThresholdPolicy:
    """Versioned, code-owned research entry thresholds and their explicit units."""

    version: str
    values: tuple[tuple[str, Decimal, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("threshold version must be a non-empty string")
        names = tuple(item[0] for item in self.values)
        if not names or len(names) != len(set(names)):
            raise ValueError("threshold names must be non-empty and unique")
        for name, value, unit in self.values:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("threshold name must be a non-empty string")
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("threshold value must be a finite Decimal")
            if not isinstance(unit, str) or not unit.strip():
                raise ValueError("threshold unit must be a non-empty string")


@dataclass(frozen=True)
class QuantScoreRule:
    """One explicit linear normalization rule; no hidden thresholds."""

    feature_name: str
    lower_bound: Decimal
    upper_bound: Decimal
    weight: Decimal
    higher_is_better: bool = True
    component_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.feature_name, str) or not self.feature_name.strip():
            raise ValueError("feature_name must be a non-empty string")
        for label, value in (
            ("lower_bound", self.lower_bound),
            ("upper_bound", self.upper_bound),
            ("weight", self.weight),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{label} must be a finite Decimal")
        if self.upper_bound <= self.lower_bound:
            raise ValueError("upper_bound must be greater than lower_bound")
        if self.weight <= 0 or self.weight > 1:
            raise ValueError("weight must be greater than 0 and at most 1")
        if type(self.higher_is_better) is not bool:
            raise TypeError("higher_is_better must be bool")
        if self.component_name is not None and (
            not isinstance(self.component_name, str) or not self.component_name.strip()
        ):
            raise ValueError("component_name must be a non-empty string")


@dataclass(frozen=True)
class QuantScorePolicy:
    """Strategy-owned scoring policy whose weights must be fully explicit."""

    strategy_id: StrategyId
    score_version: str
    rules: tuple[QuantScoreRule, ...]
    expected_feature_version: str | None = None
    thresholds: EntryThresholdPolicy | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        if not isinstance(self.score_version, str) or not self.score_version.strip():
            raise ValueError("score_version must be a non-empty string")
        if not self.rules or any(not isinstance(rule, QuantScoreRule) for rule in self.rules):
            raise TypeError("rules must contain QuantScoreRule values")
        names = tuple(rule.feature_name for rule in self.rules)
        if len(set(names)) != len(names):
            raise ValueError("score rule feature names must be unique")
        prefix = f"{self.strategy_id.value.lower()}."
        if any(not name.startswith(prefix) for name in names):
            raise ValueError("score rules must be owned by the policy strategy")
        component_names = tuple(rule.component_name or rule.feature_name for rule in self.rules)
        if len(set(component_names)) != len(component_names):
            raise ValueError("score component names must be unique")
        if any(not name.startswith(prefix) for name in component_names):
            raise ValueError("score components must be owned by the policy strategy")
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            total_weight = sum((rule.weight for rule in self.rules), Decimal("0"))
        if total_weight != Decimal("1"):
            raise ValueError("score rule weights must sum to exactly 1")
        if self.expected_feature_version is not None and (
            not isinstance(self.expected_feature_version, str)
            or not self.expected_feature_version.strip()
        ):
            raise ValueError("expected_feature_version must be a non-empty string")
        if self.thresholds is not None and not isinstance(
            self.thresholds, EntryThresholdPolicy
        ):
            raise TypeError("thresholds must be an EntryThresholdPolicy")


_SWING_SHADOW_SCORE_V1 = QuantScorePolicy(
    strategy_id=StrategyId.SWING_V1,
    score_version="SHADOW_SCORE_V1.SWING_V1",
    rules=(
        QuantScoreRule(
            "swing_v1.price_return_5d_percent",
            Decimal("-10"),
            Decimal("10"),
            Decimal("0.30"),
            component_name="swing_v1.momentum_state_score",
        ),
        QuantScoreRule(
            "swing_v1.benchmark_excess_return_20d_percentage_points",
            Decimal("-10"),
            Decimal("20"),
            Decimal("0.20"),
            component_name="swing_v1.relative_strength_state_score",
        ),
        QuantScoreRule(
            "swing_v1.volume_expansion_20d_percent",
            Decimal("50"),
            Decimal("200"),
            Decimal("0.20"),
            component_name="swing_v1.volume_state_score",
        ),
        QuantScoreRule(
            "swing_v1.atr_percent_14d",
            Decimal("2"),
            Decimal("8"),
            Decimal("0.10"),
            higher_is_better=False,
            component_name="swing_v1.volatility_state_score",
        ),
        QuantScoreRule(
            "swing_v1.regime_compatibility",
            Decimal("0"),
            Decimal("100"),
            Decimal("0.20"),
            component_name="swing_v1.regime_state_score",
        ),
    ),
    expected_feature_version="SHADOW_FEATURES_V1",
    thresholds=EntryThresholdPolicy(
        version="SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1",
        values=(
            ("swing_v1.min_liquidity", Decimal("100000"), "shares_per_session"),
            ("swing_v1.min_quant_score", Decimal("65"), "score_0_100"),
            ("swing_v1.max_atr_percent", Decimal("8"), "percent"),
            ("swing_v1.entry_breakout_buffer", Decimal("0.5"), "percent"),
        ),
    ),
)

_TREND_SHADOW_SCORE_V1 = QuantScorePolicy(
    strategy_id=StrategyId.TREND_V1,
    score_version="SHADOW_SCORE_V1.TREND_V1",
    rules=(
        QuantScoreRule(
            "trend_v1.price_above_200d",
            Decimal("-10"),
            Decimal("30"),
            Decimal("0.25"),
            component_name="trend_v1.price_structure_state_score",
        ),
        QuantScoreRule(
            "trend_v1.moving_average_alignment",
            Decimal("-5"),
            Decimal("20"),
            Decimal("0.20"),
            component_name="trend_v1.trend_strength_state_score",
        ),
        QuantScoreRule(
            "trend_v1.benchmark_excess_return_60d_percentage_points",
            Decimal("-15"),
            Decimal("30"),
            Decimal("0.20"),
            component_name="trend_v1.relative_strength_state_score",
        ),
        QuantScoreRule(
            "trend_v1.earnings_trend",
            Decimal("0"),
            Decimal("50"),
            Decimal("0.15"),
            component_name="trend_v1.earnings_state_score",
        ),
        QuantScoreRule(
            "trend_v1.regime_compatibility",
            Decimal("0"),
            Decimal("100"),
            Decimal("0.20"),
            component_name="trend_v1.regime_state_score",
        ),
    ),
    expected_feature_version="SHADOW_FEATURES_V1",
    thresholds=EntryThresholdPolicy(
        version="SHADOW_ENTRY_THRESHOLDS_V1.TREND_V1",
        values=(
            ("trend_v1.min_liquidity", Decimal("100000"), "shares_per_session"),
            ("trend_v1.min_quant_score", Decimal("65"), "score_0_100"),
            ("trend_v1.min_trend_strength", Decimal("0"), "percent"),
            ("trend_v1.max_pullback_from_high", Decimal("15"), "percent_below_high"),
        ),
    ),
)


def shadow_score_v1_policy(
    strategy_id: StrategyId, market: Market
) -> QuantScorePolicy:
    """Return the shared KR/US V1 research policy for one strategy family."""

    if not isinstance(market, Market):
        raise TypeError("market must be Market")
    if strategy_id is StrategyId.SWING_V1:
        return _SWING_SHADOW_SCORE_V1
    if strategy_id is StrategyId.TREND_V1:
        return _TREND_SHADOW_SCORE_V1
    raise TypeError("strategy_id must be StrategyId")


def evaluate_shadow_entry_thresholds(
    policy: QuantScorePolicy,
    score: QuantScoreBreakdown,
    observations: Mapping[str, Decimal],
) -> tuple[str, ...]:
    """Return deterministic research-entry vetoes; missing inputs fail closed."""

    if policy.thresholds is None:
        raise ValueError("entry thresholds are required")
    if score.strategy_id is not policy.strategy_id:
        raise ValueError("score and threshold policy identities must match")
    if score.score_version != policy.score_version:
        raise ValueError("score version must match threshold policy")
    thresholds = {name: value for name, value, _ in policy.thresholds.values}
    if policy.strategy_id is StrategyId.SWING_V1:
        required = (
            "swing_v1.average_volume_20d_shares",
            "swing_v1.atr_percent_14d",
            "swing_v1.breakout_distance_20d_percent",
        )
        checks = (
            ("swing_v1.min_liquidity", observations.get(required[0]), "min"),
            ("swing_v1.max_atr_percent", observations.get(required[1]), "max"),
            ("swing_v1.entry_breakout_buffer", observations.get(required[2]), "min"),
        )
        min_score_name = "swing_v1.min_quant_score"
    else:
        required = (
            "trend_v1.average_volume_20d_shares",
            "trend_v1.moving_average_alignment",
            "trend_v1.distance_below_52_week_high_percent",
        )
        checks = (
            ("trend_v1.min_liquidity", observations.get(required[0]), "min"),
            ("trend_v1.min_trend_strength", observations.get(required[1]), "min"),
            ("trend_v1.max_pullback_from_high", observations.get(required[2]), "max"),
        )
        min_score_name = "trend_v1.min_quant_score"

    missing = tuple(
        f"shadow_score_v1:missing:{name}" for name in required if name not in observations
    )
    if missing:
        return tuple(sorted(missing))
    vetoes = []
    for name, value, direction in checks:
        if value is None or (
            direction == "min" and value < thresholds[name]
        ) or (
            direction == "max" and value > thresholds[name]
        ):
            vetoes.append(f"shadow_score_v1:{name}")
    if score.total_score < thresholds[min_score_name]:
        vetoes.append(f"shadow_score_v1:{min_score_name}")
    return tuple(sorted(vetoes))


class QuantScoreService:
    """Normalize an eligible feature snapshot under one explicit score policy."""

    def score(
        self,
        strategy: StrategyDefinition,
        snapshot: FeatureSnapshot,
        policy: QuantScorePolicy,
    ) -> QuantScoreBreakdown:
        if not isinstance(strategy, StrategyDefinition):
            raise TypeError("strategy must be StrategyDefinition")
        if not isinstance(snapshot, FeatureSnapshot):
            raise TypeError("snapshot must be FeatureSnapshot")
        if not isinstance(policy, QuantScorePolicy):
            raise TypeError("policy must be QuantScorePolicy")
        if (
            strategy.strategy_id is not snapshot.strategy_id
            or strategy.version != snapshot.strategy_version
            or policy.strategy_id is not snapshot.strategy_id
        ):
            raise ValueError("strategy, snapshot, and score policy identities must match")
        if (
            policy.expected_feature_version is not None
            and snapshot.feature_version != policy.expected_feature_version
        ):
            raise ValueError("feature version must match score policy")
        if (
            snapshot.data_quality_status is not DataQualityStatus.FRESH
            or snapshot.quality_disposition is not QualityDisposition.ACCEPT
        ):
            raise ValueError("quant scoring requires a FRESH ACCEPT feature snapshot")

        values = {item.name: item.value for item in snapshot.values}
        components: list[QuantScoreComponent] = []
        weighted_total = Decimal("0")
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            for rule in policy.rules:
                try:
                    raw_value = values[rule.feature_name]
                except KeyError as exc:
                    raise ValueError(
                        f"missing scored feature: {rule.feature_name}"
                    ) from exc
                normalized = (raw_value - rule.lower_bound) / (
                    rule.upper_bound - rule.lower_bound
                )
                normalized = min(Decimal("1"), max(Decimal("0"), normalized))
                if not rule.higher_is_better:
                    normalized = Decimal("1") - normalized
                score = (normalized * Decimal("100")).quantize(_QUANTUM)
                components.append(
                    QuantScoreComponent(
                        name=rule.component_name or rule.feature_name, score=score
                    )
                )
                weighted_total += score * rule.weight
            total_score = weighted_total.quantize(_QUANTUM)

        identity = json.dumps(
            {
                "feature_snapshot_id": str(snapshot.feature_snapshot_id),
                "score_version": policy.score_version,
                "rules": [
                    {
                        "feature_name": rule.feature_name,
                        "higher_is_better": rule.higher_is_better,
                        "component_name": rule.component_name or rule.feature_name,
                        "lower_bound": format(rule.lower_bound, "f"),
                        "upper_bound": format(rule.upper_bound, "f"),
                        "weight": format(rule.weight, "f"),
                    }
                    for rule in policy.rules
                ],
                "total_score": format(total_score, "f"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return QuantScoreBreakdown(
            quant_score_id=uuid5(NAMESPACE_URL, identity),
            feature_snapshot_id=snapshot.feature_snapshot_id,
            strategy_id=snapshot.strategy_id,
            strategy_version=snapshot.strategy_version,
            market=snapshot.market,
            security_id=snapshot.security_id,
            score_version=policy.score_version,
            total_score=total_score,
            components=tuple(components),
        )
