"""Immutable contracts shared by strategy definitions and later feature services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition


class StrategyId(str, Enum):
    """Approved strategy-family identities."""

    SWING_V1 = "SWING_V1"
    TREND_V1 = "TREND_V1"


class Market(str, Enum):
    """Markets approved for the Phase 1 strategy families."""

    KR = "KR"
    US = "US"


class LessonScope(str, Enum):
    """Default lesson reuse boundary."""

    STRATEGY = "STRATEGY"


@dataclass(frozen=True)
class StrategyVersion:
    """Explicit strategy version, separate from the family identity."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("strategy version must be a non-empty string")


@dataclass(frozen=True, order=True)
class OutcomeHorizon:
    """Research outcome window; never a forced holding-period rule."""

    trading_sessions: int

    def __post_init__(self) -> None:
        if type(self.trading_sessions) is not int or self.trading_sessions <= 0:
            raise ValueError("outcome horizon must be a positive integer")


@dataclass(frozen=True)
class EntryTemplate:
    """Names the deterministic inputs owned by one strategy family.

    Numeric threshold values remain research configuration and are intentionally
    absent from this foundation contract.
    """

    template_id: str
    required_feature_names: tuple[str, ...]
    threshold_names: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_names("template_id", (self.template_id,))
        _validate_names("required_feature_names", self.required_feature_names)
        _validate_names("threshold_names", self.threshold_names)


@dataclass(frozen=True)
class StrategyDefinition:
    """Versioned, strategy-owned contract registered for proposal research."""

    strategy_id: StrategyId
    version: StrategyVersion
    supported_markets: tuple[Market, ...]
    entry_template: EntryTemplate
    outcome_horizons: tuple[OutcomeHorizon, ...]
    default_lesson_scope: LessonScope = LessonScope.STRATEGY

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, StrategyId):
            raise TypeError("strategy_id must be a StrategyId")
        if not isinstance(self.version, StrategyVersion):
            raise TypeError("version must be a StrategyVersion")
        if not isinstance(self.entry_template, EntryTemplate):
            raise TypeError("entry_template must be an EntryTemplate")
        if not isinstance(self.default_lesson_scope, LessonScope):
            raise TypeError("default_lesson_scope must be a LessonScope")
        if not self.supported_markets or len(set(self.supported_markets)) != len(
            self.supported_markets
        ):
            raise ValueError("supported_markets must be non-empty and unique")
        if any(not isinstance(market, Market) for market in self.supported_markets):
            raise TypeError("supported_markets values must be Market members")
        if not self.outcome_horizons or len(set(self.outcome_horizons)) != len(
            self.outcome_horizons
        ):
            raise ValueError("outcome_horizons must be non-empty and unique")
        if any(
            not isinstance(horizon, OutcomeHorizon)
            for horizon in self.outcome_horizons
        ):
            raise TypeError("outcome_horizons values must be OutcomeHorizon members")
        if tuple(sorted(self.outcome_horizons)) != self.outcome_horizons:
            raise ValueError("outcome_horizons must be in ascending order")
        ownership_prefix = f"{self.strategy_id.value.lower()}."
        owned_names = (
            self.entry_template.template_id,
            *self.entry_template.required_feature_names,
            *self.entry_template.threshold_names,
        )
        if any(not name.startswith(ownership_prefix) for name in owned_names):
            raise ValueError(
                "entry template, features, and thresholds must be owned by "
                f"{self.strategy_id.value}"
            )


@dataclass(frozen=True)
class FeatureValue:
    """One deterministic, strategy-owned quantitative feature value."""

    name: str
    value: Decimal

    def __post_init__(self) -> None:
        _validate_names("feature name", (self.name,))
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("feature value must be a finite Decimal")


@dataclass(frozen=True)
class FeatureSnapshot:
    """Immutable PIT feature envelope without any feature-computation service.

    Observed data status and policy disposition are separate fields. In
    particular, non-fresh data cannot be represented as proposal-eligible.
    """

    feature_snapshot_id: UUID
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    security_id: SecurityId
    data_snapshot_id: UUID
    as_of: datetime
    feature_version: str
    values: tuple[FeatureValue, ...]
    data_quality_status: DataQualityStatus
    quality_disposition: QualityDisposition

    def __post_init__(self) -> None:
        _require_type("feature_snapshot_id", self.feature_snapshot_id, UUID)
        _require_type("strategy_id", self.strategy_id, StrategyId)
        _require_type("strategy_version", self.strategy_version, StrategyVersion)
        _require_type("market", self.market, Market)
        _require_type("security_id", self.security_id, SecurityId)
        _require_type("data_snapshot_id", self.data_snapshot_id, UUID)
        _require_type("as_of", self.as_of, datetime)
        _require_type(
            "data_quality_status", self.data_quality_status, DataQualityStatus
        )
        _require_type(
            "quality_disposition", self.quality_disposition, QualityDisposition
        )
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        _validate_names("feature_version", (self.feature_version,))
        if not self.values:
            raise ValueError("feature values must be non-empty")
        names = tuple(item.name for item in self.values)
        _validate_names("feature names", names)
        prefix = f"{self.strategy_id.value.lower()}."
        if any(not name.startswith(prefix) for name in names):
            raise ValueError("feature names must be owned by the snapshot strategy")
        if (
            self.data_quality_status is not DataQualityStatus.FRESH
            and self.quality_disposition is QualityDisposition.ACCEPT
        ):
            raise ValueError("non-fresh data quality cannot have ACCEPT disposition")


@dataclass(frozen=True)
class QuantScoreComponent:
    """One bounded deterministic score component."""

    name: str
    score: Decimal

    def __post_init__(self) -> None:
        _validate_names("score component name", (self.name,))
        _validate_score("component score", self.score)


@dataclass(frozen=True)
class QuantScoreBreakdown:
    """Versioned quantitative score, separate from any LLM score or narrative."""

    quant_score_id: UUID
    feature_snapshot_id: UUID
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    security_id: SecurityId
    score_version: str
    total_score: Decimal
    components: tuple[QuantScoreComponent, ...]

    def __post_init__(self) -> None:
        _require_type("quant_score_id", self.quant_score_id, UUID)
        _require_type("feature_snapshot_id", self.feature_snapshot_id, UUID)
        _require_type("strategy_id", self.strategy_id, StrategyId)
        _require_type("strategy_version", self.strategy_version, StrategyVersion)
        _require_type("market", self.market, Market)
        _require_type("security_id", self.security_id, SecurityId)
        _validate_names("score_version", (self.score_version,))
        _validate_score("total_score", self.total_score)
        if not self.components:
            raise ValueError("score components must be non-empty")
        names = tuple(item.name for item in self.components)
        _validate_names("score component names", names)
        prefix = f"{self.strategy_id.value.lower()}."
        if any(not name.startswith(prefix) for name in names):
            raise ValueError("score component names must be owned by the score strategy")


def _validate_names(label: str, values: tuple[str, ...]) -> None:
    if not values:
        raise ValueError(f"{label} must be non-empty")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} values must be non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} values must be unique")


def _validate_score(label: str, value: Decimal) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < Decimal("0")
        or value > Decimal("100")
    ):
        raise ValueError(f"{label} must be a finite Decimal between 0 and 100")


def _require_type(label: str, value: object, expected_type: type[object]) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(f"{label} must be a {expected_type.__name__}")
