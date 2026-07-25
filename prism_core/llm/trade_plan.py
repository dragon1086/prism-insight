"""Strict, proposal-only LLM trade-plan contracts.

These models describe candidates for later deterministic validation. They carry no
execution approval, quantity, order intent, portfolio limit, or broker capability.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PlainSerializer,
    BeforeValidator,
    WithJsonSchema,
    model_validator,
)

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.features.service import PriceBasis
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


NonEmptyStr = Annotated[str, Field(min_length=1)]
Probability = Annotated[Decimal, Field(ge=0, le=1, allow_inf_nan=False)]
PositivePrice = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Score = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
RiskMultiplier = Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)]


def _strategy_version(value: object) -> StrategyVersion:
    if isinstance(value, StrategyVersion):
        return value
    if isinstance(value, str):
        try:
            return StrategyVersion(value)
        except ValueError as exc:
            raise ValueError("strategy version must be a non-empty string") from exc
    raise ValueError("strategy version must be a string")


StrategyVersionValue = Annotated[
    StrategyVersion,
    BeforeValidator(_strategy_version),
    PlainSerializer(lambda value: value.value, return_type=str),
    WithJsonSchema({"type": "string", "minLength": 1}),
]


class ProposalContract(BaseModel):
    """Strict immutable base for all LLM-supplied structures."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ProposedDecision(str, Enum):
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"
    WATCH = "WATCH"
    NO_ENTRY = "NO_ENTRY"
    REPORT_ONLY = "REPORT_ONLY"


class PredicateOperator(str, Enum):
    GREATER_THAN = "GREATER_THAN"
    GREATER_THAN_OR_EQUAL = "GREATER_THAN_OR_EQUAL"
    LESS_THAN = "LESS_THAN"
    LESS_THAN_OR_EQUAL = "LESS_THAN_OR_EQUAL"
    EQUAL = "EQUAL"
    BETWEEN_INCLUSIVE = "BETWEEN_INCLUSIVE"


class CandidateBasis(str, Enum):
    STRUCTURE = "STRUCTURE"
    ATR = "ATR"
    EVIDENCE = "EVIDENCE"


class MarketSession(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"


class MissingDataStatus(str, Enum):
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICT = "CONFLICT"


class ScoreComponentName(str, Enum):
    TREND_STRUCTURE = "trend_structure"
    RELATIVE_STRENGTH = "relative_strength"
    VOLUME_FLOW = "volume_flow"
    VOLATILITY_LIQUIDITY = "volatility_liquidity"
    FUNDAMENTALS_EARNINGS = "fundamentals_earnings"
    CATALYST_NEWS = "catalyst_news"
    SECTOR_LEADERSHIP = "sector_leadership"
    REGIME_FIT = "regime_fit"
    COUNTER_EVIDENCE_UNCERTAINTY = "counter_evidence_uncertainty"


class FeatureProvenance(ProposalContract):
    """Identity and quality fields copied from the Task 12 feature snapshot."""

    feature_snapshot_id: UUID
    data_snapshot_id: UUID
    as_of: AwareDatetime
    feature_version: NonEmptyStr
    data_quality_status: DataQualityStatus
    quality_disposition: QualityDisposition


class ScoreComponent(ProposalContract):
    name: ScoreComponentName
    score: Score
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)


class RegimeProbabilities(ProposalContract):
    strong_bull: Probability
    moderate_bull: Probability
    sideways: Probability
    moderate_bear: Probability
    strong_bear: Probability

    @model_validator(mode="after")
    def validate_sum(self) -> RegimeProbabilities:
        values = (
            self.strong_bull,
            self.moderate_bull,
            self.sideways,
            self.moderate_bear,
            self.strong_bear,
        )
        if sum(values, Decimal("0")) != Decimal("1"):
            raise ValueError("regime probabilities must sum exactly to 1")
        return self


class RegimeProposal(ProposalContract):
    probabilities: RegimeProbabilities
    confidence: Probability
    drivers: tuple[NonEmptyStr, ...] = Field(min_length=1)
    falsifiers: tuple[NonEmptyStr, ...] = Field(min_length=1)


class EntryPredicate(ProposalContract):
    feature_name: NonEmptyStr
    operator: PredicateOperator
    comparison_value: Decimal = Field(allow_inf_nan=False)
    upper_value: Decimal | None = Field(allow_inf_nan=False)
    reference_price: PositivePrice
    reference_price_basis: PriceBasis
    market_session: MarketSession
    valid_until: AwareDatetime
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operator_shape(self) -> EntryPredicate:
        if self.operator is PredicateOperator.BETWEEN_INCLUSIVE:
            if self.upper_value is None or self.upper_value < self.comparison_value:
                raise ValueError("BETWEEN_INCLUSIVE requires an ordered upper_value")
        elif self.upper_value is not None:
            raise ValueError("upper_value is supported only by BETWEEN_INCLUSIVE")
        return self


class PriceCandidate(ProposalContract):
    price: PositivePrice
    basis: CandidateBasis
    price_basis: PriceBasis
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)


class RiskMultiplierCandidate(ProposalContract):
    value: RiskMultiplier
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)


class ReentryCandidate(ProposalContract):
    conditions: tuple[NonEmptyStr, ...] = Field(min_length=1)
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)


class PyramidingCandidate(ProposalContract):
    conditions: tuple[NonEmptyStr, ...] = Field(min_length=1)
    requires_profitable_position: Literal[True]
    rationale: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)


class MissingOrStaleData(ProposalContract):
    field: NonEmptyStr
    status: MissingDataStatus
    critical: bool
    detail: NonEmptyStr


class UncertaintyDeclaration(ProposalContract):
    level: Probability
    known_unknowns: tuple[NonEmptyStr, ...] = Field(min_length=1)
    assumptions: tuple[NonEmptyStr, ...]


class ModelIdentity(ProposalContract):
    provider: NonEmptyStr
    model_id: NonEmptyStr
    model_version: NonEmptyStr


class SamplingSettings(ProposalContract):
    version: NonEmptyStr
    temperature: Annotated[Decimal, Field(ge=0, le=2, allow_inf_nan=False)]
    top_p: Annotated[Decimal, Field(gt=0, le=1, allow_inf_nan=False)]
    seed: NonNegativeInt | None


class TradePlanProposal(ProposalContract):
    """A strict auditable candidate that cannot itself authorize execution."""

    proposal_id: UUID
    proposal_version: NonEmptyStr
    strategy_id: StrategyId
    strategy_version: StrategyVersionValue
    market: Market
    security_id: SecurityId
    feature_provenance: FeatureProvenance
    decision: ProposedDecision
    llm_score: Score
    score_breakdown: tuple[ScoreComponent, ...] = Field(min_length=1)
    regime: RegimeProposal
    entry_predicates: tuple[EntryPredicate, ...]
    stop_candidates: tuple[PriceCandidate, ...]
    target_candidates: tuple[PriceCandidate, ...]
    risk_multiplier_candidate: RiskMultiplierCandidate
    reentry_candidates: tuple[ReentryCandidate, ...]
    pyramiding_candidates: tuple[PyramidingCandidate, ...]
    bull_evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    bear_evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    missing_or_stale_data: tuple[MissingOrStaleData, ...]
    uncertainty: UncertaintyDeclaration
    model: ModelIdentity
    prompt_version: NonEmptyStr
    sampling: SamplingSettings

    @model_validator(mode="after")
    def validate_fail_closed_semantics(self) -> TradePlanProposal:
        if len(set(self.bull_evidence_ids)) != len(self.bull_evidence_ids):
            raise ValueError("bull evidence IDs must be unique")
        if len(set(self.bear_evidence_ids)) != len(self.bear_evidence_ids):
            raise ValueError("bear evidence IDs must be unique")
        component_names = tuple(item.name for item in self.score_breakdown)
        if len(set(component_names)) != len(component_names):
            raise ValueError("score component names must be unique")
        if any(item.valid_until <= self.feature_provenance.as_of for item in self.entry_predicates):
            raise ValueError("entry predicates must remain valid after feature as_of")
        price_bases = {
            *(item.reference_price_basis for item in self.entry_predicates),
            *(item.price_basis for item in self.stop_candidates),
            *(item.price_basis for item in self.target_candidates),
        }
        if len(price_bases) > 1:
            raise ValueError("entry, stop, and target candidates must use one price basis")
        has_declared_data_issue = bool(self.missing_or_stale_data)
        proposal_eligible_quality = (
            self.feature_provenance.data_quality_status is DataQualityStatus.FRESH
            and self.feature_provenance.quality_disposition is QualityDisposition.ACCEPT
        )
        if (has_declared_data_issue or not proposal_eligible_quality) and self.decision not in {
            ProposedDecision.NO_ENTRY,
            ProposedDecision.REPORT_ONLY,
        }:
            raise ValueError("missing, stale, conflicting, or non-eligible data must fail closed")
        if self.decision is ProposedDecision.ENTRY_CANDIDATE:
            missing_candidates = tuple(
                label
                for label, values in (
                    ("entry predicate", self.entry_predicates),
                    ("stop candidate", self.stop_candidates),
                    ("target candidate", self.target_candidates),
                )
                if not values
            )
            if missing_candidates:
                raise ValueError(
                    "ENTRY_CANDIDATE requires at least one "
                    + ", ".join(missing_candidates)
                )
        return self
