"""Typed observation-universe and analysis-cohort contracts."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from prism_core.candidates.contracts import CandidateStatus
from prism_core.candidates.reconcile import (
    CandidateReconciliation,
    ReconciledCandidate,
)
from prism_core.data.contracts import ContractModel


class CandidateSourceState(str, Enum):
    """State of a candidate source for one cohort-selection run."""

    NOT_INGESTED = "NOT_INGESTED"
    FRESH = "FRESH"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


class CohortDisposition(str, Enum):
    ANALYZE = "ANALYZE"
    OBSERVE_ONLY = "OBSERVE_ONLY"


class CohortSelectionReason(str, Enum):
    """Deterministic reason codes used by the pure selection policy."""

    CROSS_CONFIRMED = "CROSS_CONFIRMED"
    CORE_FALLBACK_SUPPLEMENT_NOT_INGESTED = (
        "CORE_FALLBACK_SUPPLEMENT_NOT_INGESTED"
    )
    CORE_FALLBACK_SUPPLEMENT_UNAVAILABLE = (
        "CORE_FALLBACK_SUPPLEMENT_UNAVAILABLE"
    )
    CORE_ONLY_SUPPLEMENT_REQUIRED = "CORE_ONLY_SUPPLEMENT_REQUIRED"
    SUPPLEMENT_ONLY_CORE_REQUIRED = "SUPPLEMENT_ONLY_CORE_REQUIRED"
    CORE_INELIGIBLE = "CORE_INELIGIBLE"
    SUPPLEMENT_NOT_FRESH = "SUPPLEMENT_NOT_FRESH"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"


class ObservationUniverse(ContractModel):
    """Complete Core union supplement identity inventory retained for audit."""

    reconciliation: CandidateReconciliation
    members: tuple[ReconciledCandidate, ...]
    truncated_candidate_count: Literal[0] = 0

    @classmethod
    def from_reconciliation(
        cls, reconciliation: CandidateReconciliation
    ) -> ObservationUniverse:
        """Project every valid stable identity without ranking or truncation."""

        members = tuple(
            sorted(
                (
                    *reconciliation.included,
                    *(
                        item.candidate
                        for item in reconciliation.excluded
                        if item.candidate is not None
                    ),
                ),
                key=_identity_key,
            )
        )
        return cls(reconciliation=reconciliation, members=members)

    @model_validator(mode="after")
    def validate_members(self) -> ObservationUniverse:
        expected = ObservationUniverse.from_reconciliation_members(self.reconciliation)
        if self.members != expected:
            raise ValueError(
                "observation members must contain every reconciled stable identity once"
            )
        return self

    @staticmethod
    def from_reconciliation_members(
        reconciliation: CandidateReconciliation,
    ) -> tuple[ReconciledCandidate, ...]:
        return tuple(
            sorted(
                (
                    *reconciliation.included,
                    *(
                        item.candidate
                        for item in reconciliation.excluded
                        if item.candidate is not None
                    ),
                ),
                key=_identity_key,
            )
        )

    @property
    def identity_count(self) -> int:
        return len(self.members)


class AnalysisCohortMember(ContractModel):
    """One explicit analyze/observe-only decision for an observed identity."""

    candidate: ReconciledCandidate
    disposition: CohortDisposition
    selection_reasons: tuple[CohortSelectionReason, ...] = Field(min_length=1)
    core_state: CandidateSourceState
    supplement_state: CandidateSourceState

    @model_validator(mode="after")
    def validate_decision(self) -> AnalysisCohortMember:
        if len(set(self.selection_reasons)) != len(self.selection_reasons):
            raise ValueError("selection reasons must be unique")
        if (
            self.disposition is CohortDisposition.ANALYZE
            and self.candidate.status is CandidateStatus.DATA_UNAVAILABLE
        ):
            raise ValueError("data-unavailable candidates cannot enter analysis cohort")
        return self


class AnalysisCohort(ContractModel):
    """Separate fanout cohort with a decision for every observed identity."""

    observation_universe: ObservationUniverse
    members: tuple[AnalysisCohortMember, ...]
    truncated_candidate_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_members(self) -> AnalysisCohort:
        universe_candidates = self.observation_universe.members
        cohort_candidates = tuple(item.candidate for item in self.members)
        if cohort_candidates != universe_candidates:
            raise ValueError(
                "analysis cohort requires exactly one decision per observed identity"
            )
        return self

    @property
    def analysis_members(self) -> tuple[AnalysisCohortMember, ...]:
        return tuple(
            item for item in self.members if item.disposition is CohortDisposition.ANALYZE
        )

    @property
    def observation_only_members(self) -> tuple[AnalysisCohortMember, ...]:
        return tuple(
            item
            for item in self.members
            if item.disposition is CohortDisposition.OBSERVE_ONLY
        )


def _identity_key(candidate: ReconciledCandidate) -> tuple[str, str]:
    return (candidate.market.value, str(candidate.security_id.value))
