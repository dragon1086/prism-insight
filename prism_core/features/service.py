"""Deterministic point-in-time quantitative feature service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from typing import Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import (
    DataQualityGate,
    QualityDecision,
    QualityDisposition,
)
from prism_core.features.fundamental import (
    catalyst_recency,
    earnings_trend,
    industry_leadership,
)
from prism_core.features.liquidity import average_dollar_volume
from prism_core.features.regime import regime_compatibility
from prism_core.features.technical import (
    atr_percent,
    moving_average_alignment,
    percent_change,
    price_above_average,
    relative_strength,
    volume_expansion,
)
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    StrategyDefinition,
    StrategyId,
)


_QUANTUM = Decimal("0.000000000001")
_CORE_QUALITY_FIELDS = frozenset({"calendar", "evidence", "price", "regime"})
_REPORT_ONLY_QUALITY_FIELDS = frozenset({"fundamental"})
_QUALITY_PRECEDENCE = (
    DataQualityStatus.CONFLICT,
    DataQualityStatus.UNAVAILABLE,
    DataQualityStatus.STALE,
    DataQualityStatus.PARTIAL,
    DataQualityStatus.FRESH,
)


class FeatureComputationRejected(ValueError):
    """Fail-closed signal: no proposal-eligible feature snapshot was produced."""


class PriceBasis(str, Enum):
    """Explicitly prevents silent raw/adjusted price-series mixing."""

    RAW = "RAW"
    ADJUSTED = "ADJUSTED"


@dataclass(frozen=True)
class PricePoint:
    """One immutable PIT-adjusted input point, oldest-to-newest in a request."""

    observed_at: datetime
    available_at: datetime
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        for label, timestamp in (
            ("observed_at", self.observed_at),
            ("available_at", self.available_at),
        ):
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.observed_at > self.available_at:
            raise ValueError("observed_at must be at or before available_at")
        for label, value in (
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
            ("volume", self.volume),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{label} must be a finite Decimal")
        if self.low <= 0 or self.close <= 0 or self.high < max(self.low, self.close):
            raise ValueError("price point OHLC values are inconsistent")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")


@dataclass(frozen=True)
class NumericObservation:
    """A code-computed numeric input with an explicit availability boundary."""

    name: str
    value: Decimal
    available_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("observation name must be a non-empty string")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("observation value must be a finite Decimal")
        if not isinstance(self.available_at, datetime) or self.available_at.tzinfo is None:
            raise ValueError("observation available_at must be timezone-aware")


@dataclass(frozen=True)
class BenchmarkPoint:
    """One point-in-time benchmark close used for relative-strength features."""

    available_at: datetime
    close: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.available_at, datetime) or self.available_at.tzinfo is None:
            raise ValueError("benchmark available_at must be timezone-aware")
        if (
            not isinstance(self.close, Decimal)
            or not self.close.is_finite()
            or self.close <= 0
        ):
            raise ValueError("benchmark close must be a positive finite Decimal")


@dataclass(frozen=True)
class FeatureComputationInput:
    """Immutable input envelope for one security and one data snapshot."""

    data_snapshot_id: UUID
    market: Market
    security_id: SecurityId
    as_of: datetime
    price_basis: PriceBasis
    prices: tuple[PricePoint, ...]
    benchmark_points: tuple[BenchmarkPoint, ...]
    observations: tuple[NumericObservation, ...]
    field_quality: tuple[tuple[str, DataQualityStatus], ...]
    quality_decision: QualityDecision

    def __post_init__(self) -> None:
        if not isinstance(self.data_snapshot_id, UUID):
            raise TypeError("data_snapshot_id must be a UUID")
        if not isinstance(self.market, Market):
            raise TypeError("market must be a Market")
        if not isinstance(self.security_id, SecurityId):
            raise TypeError("security_id must be a SecurityId")
        if not isinstance(self.as_of, datetime) or self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not isinstance(self.price_basis, PriceBasis):
            raise TypeError("price_basis must be a PriceBasis")
        if not self.prices or any(not isinstance(point, PricePoint) for point in self.prices):
            raise ValueError("prices must contain PricePoint values")
        if tuple(point.observed_at for point in self.prices) != tuple(
            sorted(point.observed_at for point in self.prices)
        ):
            raise ValueError("prices must be ordered oldest to newest")
        if any(point.available_at > self.as_of for point in self.prices):
            raise ValueError("price input was unavailable at as_of")
        if not self.benchmark_points or any(
            not isinstance(point, BenchmarkPoint) for point in self.benchmark_points
        ):
            raise ValueError("benchmark_points must contain BenchmarkPoint values")
        if any(point.available_at > self.as_of for point in self.benchmark_points):
            raise ValueError("benchmark input was unavailable at as_of")
        benchmark_times = tuple(point.available_at for point in self.benchmark_points)
        if benchmark_times != tuple(sorted(benchmark_times)):
            raise ValueError("benchmark_points must be ordered oldest to newest")
        if len(self.prices) != len(self.benchmark_points):
            raise ValueError("price and benchmark sessions must align")
        if any(
            not isinstance(item, NumericObservation) or item.available_at > self.as_of
            for item in self.observations
        ):
            raise ValueError("observation input was unavailable at as_of")
        observation_names = tuple(item.name for item in self.observations)
        if len(observation_names) != len(set(observation_names)):
            raise ValueError("observation names must be unique")
        quality_names = tuple(item[0] for item in self.field_quality)
        if (
            not quality_names
            or len(quality_names) != len(set(quality_names))
            or tuple(sorted(quality_names)) != quality_names
        ):
            raise ValueError("field_quality names must be non-empty, unique, and sorted")
        if any(
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(status, DataQualityStatus)
            for name, status in self.field_quality
        ):
            raise TypeError("field_quality must pair names with DataQualityStatus values")
        if not isinstance(self.quality_decision, QualityDecision):
            raise TypeError("quality_decision must be a QualityDecision")


class QuantFeatureService:
    """Compute strategy-owned numeric features without LLM narrative or thresholds."""

    def __init__(self, *, feature_version: str) -> None:
        if not isinstance(feature_version, str) or not feature_version.strip():
            raise ValueError("feature_version must be a non-empty string")
        self._feature_version = feature_version

    def compute(
        self, strategy: StrategyDefinition, inputs: FeatureComputationInput
    ) -> FeatureSnapshot:
        """Return an immutable existing strategy contract or reject fail-closed."""
        if not isinstance(strategy, StrategyDefinition):
            raise TypeError("strategy must be a StrategyDefinition")
        if inputs.market not in strategy.supported_markets:
            raise FeatureComputationRejected("strategy does not support input market")
        self._validate_quality(inputs)

        closes = tuple(point.close for point in inputs.prices)
        benchmark_closes = tuple(point.close for point in inputs.benchmark_points)
        highs = tuple(point.high for point in inputs.prices)
        lows = tuple(point.low for point in inputs.prices)
        volumes = tuple(point.volume for point in inputs.prices)
        observations = {item.name: item.value for item in inputs.observations}

        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            if strategy.strategy_id is StrategyId.SWING_V1:
                values = {
                    "swing_v1.atr_percent_14d": atr_percent(highs, lows, closes),
                    "swing_v1.average_dollar_volume_20d": average_dollar_volume(
                        closes, volumes
                    ),
                    "swing_v1.catalyst_recency_sessions": catalyst_recency(
                        _required(observations, "catalyst_recency_sessions")
                    ),
                    "swing_v1.price_momentum_5d": percent_change(closes, 5),
                    "swing_v1.regime_compatibility": regime_compatibility(
                        _required(observations, "regime_swing_compatibility")
                    ),
                    "swing_v1.relative_strength_20d": relative_strength(
                        closes, benchmark_closes, 20
                    ),
                    "swing_v1.volume_expansion_20d": volume_expansion(volumes),
                }
            elif strategy.strategy_id is StrategyId.TREND_V1:
                values = {
                    "trend_v1.average_dollar_volume_20d": average_dollar_volume(
                        closes, volumes
                    ),
                    "trend_v1.earnings_trend": earnings_trend(
                        _required(observations, "earnings_current"),
                        _required(observations, "earnings_previous"),
                    ),
                    "trend_v1.industry_leadership": industry_leadership(
                        _required(observations, "industry_leadership")
                    ),
                    "trend_v1.moving_average_alignment": moving_average_alignment(closes),
                    "trend_v1.price_above_200d": price_above_average(closes, 200),
                    "trend_v1.regime_compatibility": regime_compatibility(
                        _required(observations, "regime_trend_compatibility")
                    ),
                    "trend_v1.relative_strength_60d": relative_strength(
                        closes, benchmark_closes, 60
                    ),
                }
            else:  # pragma: no cover - enum and registered contract bound this branch
                raise FeatureComputationRejected("unsupported strategy family")

            missing_required = sorted(
                set(strategy.entry_template.required_feature_names) - values.keys()
            )
            if missing_required:
                raise FeatureComputationRejected(
                    "missing strategy-required features: " + ", ".join(missing_required)
                )
            feature_values = tuple(
                FeatureValue(name=name, value=_normalize_decimal(value))
                for name, value in sorted(values.items())
            )
        status = _aggregate_quality(status for _, status in inputs.field_quality)
        identity_payload = _identity_payload(
            strategy=strategy,
            inputs=inputs,
            feature_version=self._feature_version,
            values=feature_values,
            status=status,
        )
        snapshot_id = uuid5(NAMESPACE_URL, identity_payload.decode("utf-8"))
        return FeatureSnapshot(
            feature_snapshot_id=snapshot_id,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            market=inputs.market,
            security_id=inputs.security_id,
            data_snapshot_id=inputs.data_snapshot_id,
            as_of=inputs.as_of,
            feature_version=self._feature_version,
            values=feature_values,
            data_quality_status=status,
            quality_disposition=inputs.quality_decision.disposition,
        )

    @staticmethod
    def _validate_quality(inputs: FeatureComputationInput) -> None:
        decision = inputs.quality_decision
        quality_names = {name for name, _ in inputs.field_quality}
        missing_core = sorted(_CORE_QUALITY_FIELDS - quality_names)
        if missing_core:
            raise FeatureComputationRejected(
                "missing core quality fields: " + ", ".join(missing_core)
            )
        expected_decision = DataQualityGate(
            core_fields=_CORE_QUALITY_FIELDS,
            report_only_fields=_REPORT_ONLY_QUALITY_FIELDS,
        ).evaluate(dict(inputs.field_quality))
        if decision != expected_decision:
            raise FeatureComputationRejected(
                "quality_decision does not match the field-level quality gate"
            )
        statuses = tuple(status for _, status in inputs.field_quality)
        all_fresh = all(status is DataQualityStatus.FRESH for status in statuses)
        if decision.disposition is QualityDisposition.REJECT:
            reasons = ",".join(decision.reasons) or "unspecified_quality_rejection"
            raise FeatureComputationRejected(reasons)
        if decision.disposition is QualityDisposition.ACCEPT and not all_fresh:
            raise FeatureComputationRejected("non-fresh inputs cannot be ACCEPT")
        if decision.disposition is QualityDisposition.REPORT_ONLY and all_fresh:
            raise FeatureComputationRejected("REPORT_ONLY requires a non-fresh input")
        if decision.disposition is not QualityDisposition.ACCEPT and not decision.reasons:
            raise FeatureComputationRejected("non-ACCEPT quality decision requires reasons")


def normalized_feature_snapshot(snapshot: FeatureSnapshot) -> bytes:
    """Serialize one feature envelope into byte-stable canonical JSON."""
    if not isinstance(snapshot, FeatureSnapshot):
        raise TypeError("snapshot must be a FeatureSnapshot")
    payload = {
        "as_of": snapshot.as_of.isoformat(timespec="microseconds"),
        "data_quality_status": snapshot.data_quality_status.value,
        "data_snapshot_id": str(snapshot.data_snapshot_id),
        "feature_snapshot_id": str(snapshot.feature_snapshot_id),
        "feature_version": snapshot.feature_version,
        "market": snapshot.market.value,
        "quality_disposition": snapshot.quality_disposition.value,
        "security_id": str(snapshot.security_id.value),
        "strategy_id": snapshot.strategy_id.value,
        "strategy_version": snapshot.strategy_version.value,
        "values": [
            {"name": item.name, "value": format(item.value, "f")}
            for item in snapshot.values
        ],
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _identity_payload(
    *,
    strategy: StrategyDefinition,
    inputs: FeatureComputationInput,
    feature_version: str,
    values: tuple[FeatureValue, ...],
    status: DataQualityStatus,
) -> bytes:
    payload = {
        "as_of": inputs.as_of.isoformat(timespec="microseconds"),
        "data_quality_status": status.value,
        "data_snapshot_id": str(inputs.data_snapshot_id),
        "feature_version": feature_version,
        "market": inputs.market.value,
        "price_basis": inputs.price_basis.value,
        "quality_disposition": inputs.quality_decision.disposition.value,
        "security_id": str(inputs.security_id.value),
        "strategy_id": strategy.strategy_id.value,
        "strategy_version": strategy.version.value,
        "values": [
            {"name": item.name, "value": format(item.value, "f")} for item in values
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")


def _required(values: dict[str, Decimal], name: str) -> Decimal:
    try:
        return values[name]
    except KeyError as exc:
        raise FeatureComputationRejected(f"missing numeric observation: {name}") from exc


def _normalize_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise FeatureComputationRejected("computed feature must be finite")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        return value.quantize(_QUANTUM)


def _aggregate_quality(statuses: Iterable[DataQualityStatus]) -> DataQualityStatus:
    present = set(statuses)
    for status in _QUALITY_PRECEDENCE:
        if status in present:
            return status
    raise FeatureComputationRejected("field quality must not be empty")
