"""Typed deterministic outcomes emitted before any strategy LLM invocation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from prism_core.data.contracts import ContractModel, SecurityId
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    Market,
    QuantScoreBreakdown,
    StrategyId,
    StrategyVersion,
)
from prism_core.strategies.quant_score import QuantScorePolicy


NonEmptyStr = Annotated[str, Field(min_length=1)]
Score = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
SanitizedErrorCode = Annotated[
    str,
    Field(min_length=1, max_length=120, pattern=r"^[A-Z][A-Z0-9_]*$"),
]


class PreGateStatus(str, Enum):
    """Investment-policy result of deterministic pre-LLM evaluation."""

    PASS = "PASS"
    PRE_GATE_REJECTED = "PRE_GATE_REJECTED"


class PreGateDecision(str, Enum):
    """Only decisions that a successful deterministic evaluation may emit."""

    PROCEED_TO_LLM = "PROCEED_TO_LLM"
    NO_ENTRY = "NO_ENTRY"


class PreGateErrorClass(str, Enum):
    """Non-investment failures kept outside the PASS/NO_ENTRY result domain."""

    PARSER_ERROR = "PARSER_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"


class PreGateOutcome(ContractModel):
    """Strategy-scoped investment decision made before an LLM can be called.

    PASS means the caller may proceed to an LLM. PRE_GATE_REJECTED is an
    investment NO_ENTRY backed by one or more deterministic vetoes. Parser and
    infrastructure failures are represented by :class:`PreGateError` instead.
    """

    status: PreGateStatus
    decision: PreGateDecision
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    security_id: SecurityId
    data_snapshot_id: UUID
    feature_snapshot_id: UUID
    quant_score_id: UUID
    score_version: NonEmptyStr
    threshold_version: NonEmptyStr
    score: Score
    threshold: Score
    hard_vetoes: tuple[NonEmptyStr, ...]
    evaluated_at: AwareDatetime
    llm_called: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision_state(self) -> PreGateOutcome:
        if len(set(self.hard_vetoes)) != len(self.hard_vetoes):
            raise ValueError("hard_vetoes must be unique")
        if self.hard_vetoes != tuple(sorted(self.hard_vetoes)):
            raise ValueError("hard_vetoes must use canonical sorted order")
        if self.status is PreGateStatus.PASS:
            if self.decision is not PreGateDecision.PROCEED_TO_LLM:
                raise ValueError("PASS requires PROCEED_TO_LLM")
            if self.hard_vetoes:
                raise ValueError("PASS cannot contain hard vetoes")
            if self.score < self.threshold:
                raise ValueError("PASS requires score at or above threshold")
        else:
            if self.decision is not PreGateDecision.NO_ENTRY:
                raise ValueError("PRE_GATE_REJECTED requires NO_ENTRY")
            if not self.hard_vetoes:
                raise ValueError("PRE_GATE_REJECTED requires hard vetoes")
        return self


class PreGateError(ContractModel):
    """Sanitized non-investment failure from pre-gate parsing or infrastructure.

    This contract intentionally has no investment ``decision`` field, so an
    operational failure cannot be serialized or consumed as NO_ENTRY.
    """

    status: Literal["PRE_GATE_ERROR"] = "PRE_GATE_ERROR"
    error_class: PreGateErrorClass
    error_code: SanitizedErrorCode
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    security_id: SecurityId
    data_snapshot_id: UUID
    evaluated_at: AwareDatetime
    llm_called: Literal[False] = False


def evaluate_pre_gate(
    *,
    feature_snapshot: FeatureSnapshot,
    quant_score: QuantScoreBreakdown,
    policy: QuantScorePolicy,
    hard_vetoes: tuple[str, ...],
    evaluated_at: datetime,
) -> PreGateOutcome:
    """Bind one deterministic score and veto set to its exact PIT identities."""

    if not isinstance(feature_snapshot, FeatureSnapshot):
        raise TypeError("feature_snapshot must be a FeatureSnapshot")
    if not isinstance(quant_score, QuantScoreBreakdown):
        raise TypeError("quant_score must be a QuantScoreBreakdown")
    if not isinstance(policy, QuantScorePolicy):
        raise TypeError("policy must be a QuantScorePolicy")
    if (
        quant_score.feature_snapshot_id != feature_snapshot.feature_snapshot_id
        or quant_score.strategy_id is not feature_snapshot.strategy_id
        or quant_score.strategy_version != feature_snapshot.strategy_version
        or quant_score.market is not feature_snapshot.market
        or quant_score.security_id != feature_snapshot.security_id
        or quant_score.score_version != policy.score_version
        or policy.strategy_id is not feature_snapshot.strategy_id
    ):
        raise ValueError("pre-gate inputs must share exact strategy and snapshot identities")
    if policy.thresholds is None:
        raise ValueError("pre-gate policy requires versioned entry thresholds")
    threshold_name = f"{feature_snapshot.strategy_id.value.lower()}.min_quant_score"
    threshold_matches = tuple(
        value for name, value, _unit in policy.thresholds.values if name == threshold_name
    )
    if len(threshold_matches) != 1:
        raise ValueError("pre-gate policy requires exactly one minimum quant score")
    if any(not isinstance(item, str) or not item.strip() for item in hard_vetoes):
        raise TypeError("hard_vetoes must contain non-empty strings")
    threshold = threshold_matches[0]
    vetoes = tuple(sorted(set(hard_vetoes)))
    rejected = bool(vetoes)
    return PreGateOutcome(
        status=(PreGateStatus.PRE_GATE_REJECTED if rejected else PreGateStatus.PASS),
        decision=(PreGateDecision.NO_ENTRY if rejected else PreGateDecision.PROCEED_TO_LLM),
        strategy_id=feature_snapshot.strategy_id,
        strategy_version=feature_snapshot.strategy_version,
        market=feature_snapshot.market,
        security_id=feature_snapshot.security_id,
        data_snapshot_id=feature_snapshot.data_snapshot_id,
        feature_snapshot_id=feature_snapshot.feature_snapshot_id,
        quant_score_id=quant_score.quant_score_id,
        score_version=quant_score.score_version,
        threshold_version=policy.thresholds.version,
        score=quant_score.total_score,
        threshold=threshold,
        hard_vetoes=vetoes,
        evaluated_at=evaluated_at,
    )
