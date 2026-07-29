"""Immutable deterministic Korean market-context contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import AwareDatetime, Field, field_validator, model_validator

from prism_core.data.contracts import ContractModel, DataQualityStatus


NonEmptyStr = Annotated[str, Field(min_length=1)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class SessionState(str, Enum):
    COMPLETE = "COMPLETE"
    PRIOR_CLOSE = "PRIOR_CLOSE"
    INTRADAY = "INTRADAY"
    UNKNOWN = "UNKNOWN"


class KRMarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    MODERATE_BULL = "MODERATE_BULL"
    SIDEWAYS = "SIDEWAYS"
    MODERATE_BEAR = "MODERATE_BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    UNKNOWN = "UNKNOWN"


class ContextDisposition(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"


class SourceRole(str, Enum):
    PRIMARY = "PRIMARY"
    SUPPLEMENTAL = "SUPPLEMENTAL"


class MarketContextTiming(ContractModel):
    market: Annotated[str, Field(pattern="^KR$")] = "KR"
    session_date: date
    session_state: SessionState
    as_of: AwareDatetime
    ingested_at: AwareDatetime

    @model_validator(mode="after")
    def validate_timing(self) -> "MarketContextTiming":
        if self.as_of > self.ingested_at:
            raise ValueError("context as_of must be at or before ingested_at")
        return self


class SourceClock(ContractModel):
    source: NonEmptyStr
    role: SourceRole
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    quality: DataQualityStatus
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence IDs must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_timing(self) -> "SourceClock":
        if not self.observed_at <= self.available_at <= self.ingested_at:
            raise ValueError("source timing must be observed <= available <= ingested")
        return self


class DeterministicMetric(ContractModel):
    name: NonEmptyStr
    value: FiniteDecimal
    unit: NonEmptyStr
    source: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("metric evidence IDs must be unique")
        return tuple(sorted(value))


class GroupLeadership(ContractModel):
    group_id: NonEmptyStr
    rank: Annotated[int, Field(ge=1)]
    concentration_pct: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
    source: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("leadership evidence IDs must be unique")
        return tuple(sorted(value))


class RegimeFeature(ContractModel):
    name: NonEmptyStr
    value: FiniteDecimal
    unit: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("regime feature evidence IDs must be unique")
        return tuple(sorted(value))


class RegimeAssessment(ContractModel):
    regime: KRMarketRegime
    version: NonEmptyStr
    features: tuple[RegimeFeature, ...]
    reason_codes: tuple[NonEmptyStr, ...] = Field(min_length=1)
    missing_features: tuple[NonEmptyStr, ...] = ()

    @classmethod
    def unknown(cls, *, missing_features: tuple[str, ...]) -> "RegimeAssessment":
        if not missing_features:
            raise ValueError("UNKNOWN regime requires missing features")
        return cls(
            regime=KRMarketRegime.UNKNOWN,
            version="kr_regime_v1",
            features=(),
            reason_codes=("REQUIRED_REGIME_FEATURES_MISSING",),
            missing_features=tuple(sorted(set(missing_features))),
        )

    @model_validator(mode="after")
    def validate_regime(self) -> "RegimeAssessment":
        if self.regime is KRMarketRegime.UNKNOWN:
            if not self.missing_features:
                raise ValueError("UNKNOWN regime requires missing features")
            if (
                self.version != _REGIME_VERSION
                or self.features
                or self.reason_codes != ("REQUIRED_REGIME_FEATURES_MISSING",)
                or self.missing_features != tuple(sorted(set(self.missing_features)))
                or not set(self.missing_features).issubset(_REGIME_FEATURE_NAMES)
            ):
                raise ValueError("UNKNOWN regime must use governed kr_regime_v1 fields")
            return self
        try:
            expected_regime, expected_reason = _classify_complete_regime(self.features)
        except ValueError:
            raise ValueError("known regime must match kr_regime_v1 classifier") from None
        if (
            self.version != _REGIME_VERSION
            or self.missing_features
            or self.features != tuple(sorted(self.features, key=lambda item: item.name))
            or self.regime is not expected_regime
            or self.reason_codes != (expected_reason,)
        ):
            raise ValueError("known regime must match kr_regime_v1 classifier")
        return self


_REGIME_FEATURE_NAMES = frozenset(
    {"kospi_close", "kospi_ma20", "kospi_return_10d_pct"}
)
_REGIME_FEATURE_UNITS = {
    "kospi_close": "index_points",
    "kospi_ma20": "index_points",
    "kospi_return_10d_pct": "percent",
}
_REGIME_VERSION = "kr_regime_v1"


def _classify_complete_regime(
    features: tuple[RegimeFeature, ...],
) -> tuple[KRMarketRegime, str]:
    by_name = {feature.name: feature for feature in features}
    if len(by_name) != len(features) or set(by_name) != _REGIME_FEATURE_NAMES:
        raise ValueError("complete unique regime features are required")
    if any(
        feature.unit != _REGIME_FEATURE_UNITS[feature.name]
        for feature in features
    ):
        raise ValueError("regime feature units do not match kr_regime_v1")
    close = by_name["kospi_close"].value
    moving_average = by_name["kospi_ma20"].value
    return_10d = by_name["kospi_return_10d_pct"].value
    if close > moving_average and return_10d >= Decimal("5"):
        return (
            KRMarketRegime.STRONG_BULL,
            "ABOVE_MA20_AND_RETURN_10D_AT_LEAST_5",
        )
    if close > moving_average and return_10d > Decimal("0"):
        return KRMarketRegime.MODERATE_BULL, "ABOVE_MA20_AND_POSITIVE_RETURN_10D"
    if close < moving_average and return_10d <= Decimal("-5"):
        return (
            KRMarketRegime.STRONG_BEAR,
            "BELOW_MA20_AND_RETURN_10D_AT_MOST_MINUS_5",
        )
    if close < moving_average and return_10d < Decimal("0"):
        return KRMarketRegime.MODERATE_BEAR, "BELOW_MA20_AND_NEGATIVE_RETURN_10D"
    return (
        KRMarketRegime.SIDEWAYS,
        "PRICE_AND_RETURN_SIGNALS_NOT_DIRECTIONALLY_ALIGNED",
    )


def classify_kr_regime(features: tuple[RegimeFeature, ...]) -> RegimeAssessment:
    """Classify the versioned KR regime without fabricating missing observations."""
    if any(not isinstance(feature, RegimeFeature) for feature in features):
        raise TypeError("features must contain RegimeFeature records")
    by_name = {feature.name: feature for feature in features}
    if len(by_name) != len(features):
        raise ValueError("regime feature names must be unique")
    unsupported = sorted(set(by_name) - _REGIME_FEATURE_NAMES)
    if unsupported:
        raise ValueError("unsupported regime features: " + ", ".join(unsupported))
    missing = tuple(sorted(_REGIME_FEATURE_NAMES - set(by_name)))
    if missing:
        return RegimeAssessment.unknown(missing_features=missing)

    regime, reason = _classify_complete_regime(features)
    return RegimeAssessment(
        regime=regime,
        version=_REGIME_VERSION,
        features=tuple(sorted(features, key=lambda feature: feature.name)),
        reason_codes=(reason,),
        missing_features=(),
    )


def derive_context_quality(
    *,
    source_clocks: tuple[SourceClock, ...],
    conflicts: tuple[str, ...],
    missing_fields: tuple[str, ...],
) -> DataQualityStatus:
    """Aggregate quality monotonically using CONFLICT > UNAVAILABLE > STALE > PARTIAL."""
    if not source_clocks:
        return DataQualityStatus.UNAVAILABLE
    if conflicts or any(
        source.quality is DataQualityStatus.CONFLICT for source in source_clocks
    ):
        return DataQualityStatus.CONFLICT
    if missing_fields or any(
        source.quality is DataQualityStatus.UNAVAILABLE for source in source_clocks
    ):
        return DataQualityStatus.UNAVAILABLE
    for status in (DataQualityStatus.STALE, DataQualityStatus.PARTIAL):
        if any(source.quality is status for source in source_clocks):
            return status
    return DataQualityStatus.FRESH


class SupplementalEvidence(ContractModel):
    evidence_id: NonEmptyStr
    provider: NonEmptyStr
    role: NonEmptyStr
    trust: NonEmptyStr
    executable: bool
    quality: DataQualityStatus

    @model_validator(mode="after")
    def prohibit_action_authority(self) -> "SupplementalEvidence":
        if self.executable:
            raise ValueError("supplemental evidence cannot be executable")
        return self


class KRMarketContext(ContractModel):
    schema_version: Annotated[str, Field(pattern="^kr_market_context_v1$")] = (
        "kr_market_context_v1"
    )
    timing: MarketContextTiming
    source_clocks: tuple[SourceClock, ...] = Field(min_length=1)
    index_state: tuple[DeterministicMetric, ...]
    breadth: tuple[DeterministicMetric, ...]
    investor_flows: tuple[DeterministicMetric, ...]
    macro_indicators: tuple[DeterministicMetric, ...]
    group_leadership: tuple[GroupLeadership, ...]
    regime: RegimeAssessment
    supplemental_evidence: tuple[SupplementalEvidence, ...] = ()
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    quality: DataQualityStatus
    conflicts: tuple[NonEmptyStr, ...]
    missing_fields: tuple[NonEmptyStr, ...]

    @field_validator(
        "source_clocks",
        "index_state",
        "breadth",
        "investor_flows",
        "macro_indicators",
        "group_leadership",
        "supplemental_evidence",
        mode="after",
    )
    @classmethod
    def require_stable_order(cls, value: tuple[object, ...]) -> tuple[object, ...]:
        keys: list[tuple[str, ...]] = []
        for item in value:
            if isinstance(item, SourceClock):
                keys.append((item.source,))
            elif isinstance(item, DeterministicMetric):
                keys.append((item.name, item.source))
            elif isinstance(item, GroupLeadership):
                keys.append((item.group_id, item.source))
            elif isinstance(item, SupplementalEvidence):
                keys.append((item.evidence_id,))
            else:
                keys.append((type(item).__name__,))
        if len(set(keys)) != len(keys):
            raise ValueError("market context collection keys must be unique")
        if keys != sorted(keys):
            raise ValueError("market context collections must use deterministic order")
        return value

    @field_validator("evidence_ids", "conflicts", "missing_fields", mode="after")
    @classmethod
    def require_sorted_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("context identifiers and issues must be unique")
        if value != tuple(sorted(value)):
            raise ValueError("context identifiers and issues must be sorted")
        return value

    @model_validator(mode="after")
    def validate_context(self) -> "KRMarketContext":
        for source in self.source_clocks:
            if source.available_at > self.timing.as_of:
                raise ValueError("available_at must be at or before context as_of")
            if source.ingested_at > self.timing.ingested_at:
                raise ValueError("source ingestion cannot follow context ingestion")
        nested_evidence = {
            evidence_id
            for source in self.source_clocks
            for evidence_id in source.evidence_ids
        }
        for collection in (
            self.index_state,
            self.breadth,
            self.investor_flows,
            self.macro_indicators,
            self.group_leadership,
            self.regime.features,
        ):
            nested_evidence.update(
                evidence_id
                for item in collection
                for evidence_id in item.evidence_ids
            )
        nested_evidence.update(
            item.evidence_id for item in self.supplemental_evidence
        )
        if not set(self.evidence_ids).issuperset(nested_evidence):
            raise ValueError("context evidence IDs must include every nested evidence ID")
        if self.conflicts and self.quality is not DataQualityStatus.CONFLICT:
            raise ValueError("conflicts require CONFLICT context quality")
        if self.quality is DataQualityStatus.CONFLICT and not self.conflicts:
            raise ValueError("CONFLICT context quality requires explicit conflicts")
        primary_clocks = tuple(
            source
            for source in self.source_clocks
            if source.role is SourceRole.PRIMARY
        )
        if not primary_clocks:
            raise ValueError("market context requires at least one primary source")
        derived_quality = derive_context_quality(
            source_clocks=primary_clocks,
            conflicts=self.conflicts,
            missing_fields=self.missing_fields,
        )
        if self.quality is not derived_quality:
            raise ValueError("context quality must equal derived primary quality")
        if self.missing_fields and self.quality is DataQualityStatus.FRESH:
            raise ValueError("missing fields cannot have FRESH context quality")
        if self.regime.regime is KRMarketRegime.UNKNOWN and self.quality is DataQualityStatus.FRESH:
            raise ValueError("UNKNOWN regime cannot have FRESH context quality")
        return self

    @property
    def disposition(self) -> ContextDisposition:
        if (
            self.quality is DataQualityStatus.FRESH
            and self.regime.regime is not KRMarketRegime.UNKNOWN
            and not self.conflicts
            and not self.missing_fields
        ):
            return ContextDisposition.ELIGIBLE
        return ContextDisposition.ANALYSIS_INCOMPLETE

    @property
    def action_eligible(self) -> bool:
        return self.disposition is ContextDisposition.ELIGIBLE

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
