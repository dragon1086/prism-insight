"""Deterministic, versioned quantitative scoring for strategy feature snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from uuid import NAMESPACE_URL, uuid5

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    QuantScoreBreakdown,
    QuantScoreComponent,
    StrategyDefinition,
    StrategyId,
)


_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class QuantScoreRule:
    """One explicit linear normalization rule; no hidden thresholds."""

    feature_name: str
    lower_bound: Decimal
    upper_bound: Decimal
    weight: Decimal
    higher_is_better: bool = True

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


@dataclass(frozen=True)
class QuantScorePolicy:
    """Strategy-owned scoring policy whose weights must be fully explicit."""

    strategy_id: StrategyId
    score_version: str
    rules: tuple[QuantScoreRule, ...]

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
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            total_weight = sum((rule.weight for rule in self.rules), Decimal("0"))
        if total_weight != Decimal("1"):
            raise ValueError("score rule weights must sum to exactly 1")


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
                    QuantScoreComponent(name=rule.feature_name, score=score)
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
