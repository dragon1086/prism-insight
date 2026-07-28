"""Strict immutable point-in-time candidate contracts."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Mapping

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from prism_core.data.contracts import ContractModel, SecurityId
from prism_core.strategies.contracts import Market

NonEmptyStr = Annotated[str, Field(min_length=1)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class CandidateChannel(str, Enum):
    CORE_PRISM = "CORE_PRISM"
    SUPPLEMENTAL_LEADERSHIP = "SUPPLEMENTAL_LEADERSHIP"


class CandidateStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    REPORT_ONLY = "REPORT_ONLY"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class CandidateSnapshot(ContractModel):
    """One source/channel assertion that a security is a daily candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    market: Market
    security_id: SecurityId
    provider: NonEmptyStr
    provider_symbol: NonEmptyStr
    display_name: NonEmptyStr
    channel: CandidateChannel
    source_id: NonEmptyStr
    source_snapshot_id: NonEmptyStr
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    as_of: AwareDatetime
    trigger_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    raw_scores: Mapping[NonEmptyStr, FiniteDecimal]
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)
    status: CandidateStatus
    issues: tuple[NonEmptyStr, ...] = ()

    @field_validator("raw_scores", mode="after")
    @classmethod
    def freeze_raw_scores(
        cls, value: Mapping[str, Decimal]
    ) -> Mapping[str, Decimal]:
        return MappingProxyType(dict(sorted(value.items())))

    @field_serializer("raw_scores")
    def serialize_raw_scores(self, value: Mapping[str, Decimal]) -> dict[str, Decimal]:
        return dict(value)

    @field_validator("trigger_ids", "evidence_ids", "issues", mode="after")
    @classmethod
    def require_unique_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("candidate provenance IDs and issues must be unique")
        return value

    @model_validator(mode="after")
    def validate_point_in_time_clocks(self) -> CandidateSnapshot:
        if self.observed_at > self.available_at:
            raise ValueError("observed_at must be at or before available_at")
        if self.available_at > self.as_of:
            raise ValueError("available_at must be at or before as_of")
        if self.available_at > self.ingested_at:
            raise ValueError("available_at must be at or before ingested_at")
        if self.status is not CandidateStatus.ELIGIBLE and not self.issues:
            raise ValueError("non-eligible candidate status requires explicit issues")
        return self

    @property
    def identity(self) -> tuple[Market, SecurityId]:
        """Stable deduplication identity; provider symbols are aliases only."""

        return (self.market, self.security_id)

    @property
    def source_identity(
        self,
    ) -> tuple[Market, SecurityId, CandidateChannel, str, str, str]:
        """Identity of one source assertion, used to reject exact duplicates."""

        return (
            self.market,
            self.security_id,
            self.channel,
            self.provider,
            self.source_id,
            self.source_snapshot_id,
        )
