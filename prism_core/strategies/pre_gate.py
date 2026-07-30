"""Typed deterministic outcomes emitted before any strategy LLM invocation."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from prism_core.data.contracts import ContractModel, SecurityId
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


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
