"""Strict contracts for sanitized supplemental leadership evidence."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from prism_core.data.contracts import (
    ContractModel,
    DataQualityStatus,
    FiniteDecimal,
    NonEmptyStr,
    ObservationTime,
    SecurityId,
    Sha256,
)


class LeadershipSection(str, Enum):
    """Generic internal section identities safe for downstream projection."""

    MARKET_BREADTH = "MARKET_BREADTH"
    INVESTOR_FLOWS = "INVESTOR_FLOWS"
    LEADING_GROUPS = "LEADING_GROUPS"
    SECURITY_LEADERSHIP = "SECURITY_LEADERSHIP"


class LeadershipCaptureMethod(str, Enum):
    APPROVED_UI = "APPROVED_UI"
    APPROVED_EXPORT = "APPROVED_EXPORT"


class LeadershipScope(str, Enum):
    MARKET = "MARKET"
    GROUP = "GROUP"
    SECURITY = "SECURITY"


class LeadershipObservationKind(str, Enum):
    MARKET_BREADTH = "MARKET_BREADTH"
    INVESTOR_FLOW = "INVESTOR_FLOW"
    LEADING_GROUP = "LEADING_GROUP"
    RELATIVE_STRENGTH_RANK = "RELATIVE_STRENGTH_RANK"
    HIGH_52_WEEK_STATE = "HIGH_52_WEEK_STATE"
    TURNOVER_RANK = "TURNOVER_RANK"
    TURNOVER_VALUE = "TURNOVER_VALUE"
    SESSION_RETURN = "SESSION_RETURN"
    MOMENTUM_RANK = "MOMENTUM_RANK"
    PEAK_STATE = "PEAK_STATE"


class LeadershipObservation(ContractModel):
    """Non-authoritative normalized observation from an approved snapshot."""

    observation_id: NonEmptyStr
    kind: LeadershipObservationKind
    scope: LeadershipScope
    security_id: SecurityId | None = None
    provider_symbol: NonEmptyStr | None = None
    group_id: NonEmptyStr | None = None
    value: FiniteDecimal | NonEmptyStr
    unit: NonEmptyStr
    evidence_id: NonEmptyStr

    @model_validator(mode="after")
    def validate_scope_identity(self) -> "LeadershipObservation":
        if self.scope is LeadershipScope.MARKET:
            if any((self.security_id, self.provider_symbol, self.group_id)):
                raise ValueError("market observations cannot carry security or group identity")
        elif self.scope is LeadershipScope.GROUP:
            if self.group_id is None or self.security_id is not None or self.provider_symbol is not None:
                raise ValueError("group observations require only group_id")
        elif self.security_id is None or self.provider_symbol is None or self.group_id is not None:
            raise ValueError("security observations require security_id and provider_symbol")
        return self

    @property
    def comparison_key(self) -> str:
        security_id = self.security_id
        identity = (
            "MARKET"
            if self.scope is LeadershipScope.MARKET
            else self.group_id
            if self.scope is LeadershipScope.GROUP
            else str(security_id.value) if security_id is not None else "INVALID"
        )
        return f"{self.kind.value}:{identity}"


class LeadershipCandidateNomination(ContractModel):
    security_id: SecurityId
    provider_symbol: Annotated[str, Field(pattern=r"^\d{6}$")]
    display_name: NonEmptyStr
    trigger_id: NonEmptyStr
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> "LeadershipCandidateNomination":
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("candidate evidence IDs must be unique")
        return self


class LeadershipSupplementSnapshot(ContractModel):
    """Content-addressed, bounded, sanitized evidence; never a price authority."""

    schema_version: Literal["leadership_supplement_v1"] = "leadership_supplement_v1"
    snapshot_id: NonEmptyStr
    provider: Literal["STOCKEASY_SANITIZED_UI_EXPORT"]
    section: LeadershipSection
    source_scope_id: NonEmptyStr
    source_snapshot_id: NonEmptyStr
    capture_method: LeadershipCaptureMethod
    permission_record_id: NonEmptyStr
    timing: ObservationTime
    content_hash: Sha256
    image_hash: Sha256 | None = None
    quality: DataQualityStatus
    observations: tuple[LeadershipObservation, ...]
    candidate_nominations: tuple[LeadershipCandidateNomination, ...]
    issues: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def validate_snapshot(self) -> "LeadershipSupplementSnapshot":
        if self.quality is DataQualityStatus.UNAVAILABLE:
            raise ValueError("unavailable imports must not fabricate a snapshot")
        if self.quality is not DataQualityStatus.FRESH and not self.issues:
            raise ValueError("non-fresh supplemental snapshots require issues")
        observation_ids = tuple(item.observation_id for item in self.observations)
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("observation IDs must be unique")
        evidence_ids = {item.evidence_id for item in self.observations}
        if any(
            not set(candidate.evidence_ids).issubset(evidence_ids)
            for candidate in self.candidate_nominations
        ):
            raise ValueError("candidate nominations must bind snapshot evidence")
        return self
