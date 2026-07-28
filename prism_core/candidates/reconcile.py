"""Deterministic, uncapped reconciliation for candidate source assertions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Literal, TypeAlias

from pydantic import Field, ValidationError, model_validator

from prism_core.candidates.contracts import (
    CandidateChannel,
    CandidateSnapshot,
    CandidateStatus,
)
from prism_core.data.contracts import ContractModel, SecurityId
from prism_core.strategies.contracts import Market

_CHANNEL_ORDER = {
    CandidateChannel.CORE_PRISM: 0,
    CandidateChannel.SUPPLEMENTAL_LEADERSHIP: 1,
}
_STATUS_ORDER = {
    CandidateStatus.ELIGIBLE: 0,
    CandidateStatus.REPORT_ONLY: 1,
    CandidateStatus.DATA_UNAVAILABLE: 2,
}


class ReconciledCandidate(ContractModel):
    """All source assertions for one stable market/security identity.

    ``status`` describes candidate-selection eligibility only.  Data quality for
    scenario/proposal authority is re-evaluated downstream from the preserved
    source snapshots and issues; reconciliation never upgrades source evidence.
    """

    market: Market
    security_id: SecurityId
    status: CandidateStatus
    channels: tuple[CandidateChannel, ...]
    provider_symbols: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_snapshot_ids: tuple[str, ...]
    trigger_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    issues: tuple[str, ...]
    snapshots: tuple[CandidateSnapshot, ...]


class CandidateExclusionCode(str, Enum):
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


class ExcludedCandidate(ContractModel):
    """A source record or stable candidate group withheld with an explicit reason."""

    market: Market | None = None
    security_id: SecurityId | None = None
    input_index: int | None = Field(default=None, ge=0)
    exclusion_code: CandidateExclusionCode
    exclusion_reason: str
    candidate: ReconciledCandidate | None = None


class CandidateReconciliation(ContractModel):
    """Complete reconciliation result; no count or score cap exists."""

    score_policy: Literal["TRIGGER_LOCAL_UNRANKED"] = "TRIGGER_LOCAL_UNRANKED"
    ordering_policy: Literal["CHANNEL_THEN_STABLE_IDENTITY"] = (
        "CHANNEL_THEN_STABLE_IDENTITY"
    )
    input_count: int = Field(ge=0)
    unique_identity_count: int = Field(ge=0)
    included_identity_count: int = Field(ge=0)
    excluded_identity_count: int = Field(ge=0)
    included_source_count: int = Field(ge=0)
    excluded_source_count: int = Field(ge=0)
    duplicate_source_count: int = Field(ge=0)
    invalid_record_count: int = Field(ge=0)
    truncated_candidate_count: Literal[0] = 0
    included: tuple[ReconciledCandidate, ...]
    excluded: tuple[ExcludedCandidate, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> CandidateReconciliation:
        excluded_groups = tuple(
            item.candidate for item in self.excluded if item.candidate is not None
        )
        if self.included_identity_count != len(self.included) or (
            self.excluded_identity_count != len(excluded_groups)
        ):
            raise ValueError("identity counts must match included and excluded records")
        if self.unique_identity_count != (
            self.included_identity_count + self.excluded_identity_count
        ):
            raise ValueError("identity counts must reconcile to unique_identity_count")
        if self.included_source_count != sum(
            len(item.snapshots) for item in self.included
        ) or self.excluded_source_count != sum(
            len(item.snapshots) for item in excluded_groups
        ):
            raise ValueError("source counts must match reconciled snapshots")
        invalid_records = sum(
            item.exclusion_code is CandidateExclusionCode.INVALID_CANDIDATE
            for item in self.excluded
        )
        if self.invalid_record_count != invalid_records:
            raise ValueError("invalid record count must match invalid exclusions")
        if self.input_count != (
            self.included_source_count
            + self.excluded_source_count
            + self.duplicate_source_count
            + self.invalid_record_count
        ):
            raise ValueError("source counts must reconcile to input_count")
        return self


CandidateInput: TypeAlias = CandidateSnapshot | Mapping[str, object]


def reconcile_candidates(
    candidates: Iterable[CandidateInput],
) -> CandidateReconciliation:
    """Merge candidates by stable identity without ranking unlike trigger scores.

    Any non-conflicting eligible channel keeps the identity available for the
    downstream quality gate.  A contradictory assertion under one complete
    source identity fails the whole security closed while retaining every sibling
    assertion for audit.
    """

    candidate_records = tuple(candidates)
    grouped: dict[tuple[Market, SecurityId], list[CandidateSnapshot]] = {}
    source_assertions: dict[tuple[object, ...], list[CandidateSnapshot]] = {}
    conflicting_identities: set[tuple[Market, SecurityId]] = set()
    duplicate_source_count = 0
    invalid_exclusions: list[ExcludedCandidate] = []
    for input_index, raw_candidate in enumerate(candidate_records):
        try:
            candidate = (
                raw_candidate
                if isinstance(raw_candidate, CandidateSnapshot)
                else CandidateSnapshot.model_validate(raw_candidate)
            )
        except ValidationError as exc:
            invalid_exclusions.append(
                ExcludedCandidate(
                    input_index=input_index,
                    exclusion_code=CandidateExclusionCode.INVALID_CANDIDATE,
                    exclusion_reason=_validation_reason(exc),
                )
            )
            continue
        prior_assertions = source_assertions.setdefault(candidate.source_identity, [])
        if any(prior == candidate for prior in prior_assertions):
            duplicate_source_count += 1
            continue
        if prior_assertions:
            conflicting_identities.add(candidate.identity)
        prior_assertions.append(candidate)
        grouped.setdefault(candidate.identity, []).append(candidate)

    included: list[ReconciledCandidate] = []
    excluded: list[ExcludedCandidate] = list(invalid_exclusions)
    for identity, snapshots in grouped.items():
        source_conflict = identity in conflicting_identities
        merged = _merge_group(snapshots, source_conflict=source_conflict)
        if merged.status is CandidateStatus.DATA_UNAVAILABLE:
            excluded.append(
                ExcludedCandidate(
                    market=merged.market,
                    security_id=merged.security_id,
                    exclusion_code=(
                        CandidateExclusionCode.SOURCE_CONFLICT
                        if source_conflict
                        else CandidateExclusionCode.DATA_UNAVAILABLE
                    ),
                    exclusion_reason=(
                        "SOURCE_CONFLICT: " if source_conflict else "DATA_UNAVAILABLE: "
                    )
                    + "; ".join(merged.issues),
                    candidate=merged,
                )
            )
        else:
            included.append(merged)

    included.sort(key=_candidate_order_key)
    excluded.sort(key=_excluded_order_key)
    return CandidateReconciliation(
        input_count=len(candidate_records),
        unique_identity_count=len(grouped),
        included_identity_count=len(included),
        excluded_identity_count=sum(item.candidate is not None for item in excluded),
        included_source_count=sum(len(item.snapshots) for item in included),
        excluded_source_count=sum(
            len(item.candidate.snapshots)
            for item in excluded
            if item.candidate is not None
        ),
        duplicate_source_count=duplicate_source_count,
        invalid_record_count=len(invalid_exclusions),
        included=tuple(included),
        excluded=tuple(excluded),
    )


def _merge_group(
    snapshots: list[CandidateSnapshot], *, source_conflict: bool = False
) -> ReconciledCandidate:
    ordered = tuple(sorted(snapshots, key=_snapshot_order_key))
    first = ordered[0]
    status = (
        CandidateStatus.DATA_UNAVAILABLE
        if source_conflict
        else min((item.status for item in ordered), key=_STATUS_ORDER.__getitem__)
    )
    issues = _unique(value for item in ordered for value in item.issues)
    if source_conflict:
        issues = (*issues, "CONFLICTING_SOURCE_ASSERTION")
    return ReconciledCandidate(
        market=first.market,
        security_id=first.security_id,
        status=status,
        channels=_unique(item.channel for item in ordered),
        provider_symbols=_unique(item.provider_symbol for item in ordered),
        source_ids=_unique(item.source_id for item in ordered),
        source_snapshot_ids=_unique(item.source_snapshot_id for item in ordered),
        trigger_ids=_unique(value for item in ordered for value in item.trigger_ids),
        evidence_ids=_unique(value for item in ordered for value in item.evidence_ids),
        issues=issues,
        snapshots=ordered,
    )


def _snapshot_order_key(candidate: CandidateSnapshot) -> tuple[object, ...]:
    return (
        _CHANNEL_ORDER[candidate.channel],
        candidate.source_id,
        candidate.source_snapshot_id,
        candidate.provider,
        candidate.provider_symbol,
        candidate.model_dump_json(),
    )


def _candidate_order_key(candidate: ReconciledCandidate) -> tuple[object, ...]:
    return (
        min(_CHANNEL_ORDER[channel] for channel in candidate.channels),
        candidate.market.value,
        str(candidate.security_id.value),
    )


def _excluded_order_key(item: ExcludedCandidate) -> tuple[object, ...]:
    if item.candidate is not None:
        return (0, *_candidate_order_key(item.candidate))
    return (1, item.input_index if item.input_index is not None else -1)


def _validation_reason(exc: ValidationError) -> str:
    """Return validation shape without echoing rejected input values."""

    details = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error["loc"]) or "candidate"
        details.append(f"{location}:{error['type']}")
    return "INVALID_CANDIDATE: " + "; ".join(details)


def _unique(values: Iterable[object]) -> tuple:
    return tuple(dict.fromkeys(values))
