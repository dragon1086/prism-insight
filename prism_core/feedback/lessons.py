"""Strategy-scoped, append-only lesson lifecycle for Phase 1 SHADOW evaluation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from prism_core.data.contracts import ObservationTime
from prism_core.feedback.repository import (
    AppendDisposition,
    FeedbackRepository,
    LessonCandidateRecord,
    LessonEvidenceRecord,
    _utc_text,
)
from prism_core.feedback.retrospective import FeedbackProvenance, _validate_text_tuple
from prism_core.strategies.contracts import StrategyId, StrategyVersion


class LessonStatus(str, Enum):
    LEGACY_UNVALIDATED = "LEGACY_UNVALIDATED"
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class LessonEvidenceRole(str, Enum):
    SUPPORT = "SUPPORT"
    CONTRA = "CONTRA"


@dataclass(frozen=True)
class LessonCandidate:
    lesson_candidate_event_id: str
    lesson_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    revision: int
    market_scope: tuple[str, ...]
    sector_scope: tuple[str, ...]
    regime_scope: tuple[str, ...]
    condition: str
    tentative_action: str
    uncertainty: Decimal
    provenance: FeedbackProvenance
    timing: ObservationTime


@dataclass(frozen=True)
class LessonValidationPolicy:
    minimum_support_count: int
    minimum_contra_count: int
    minimum_distinct_proposals: int
    minimum_separation: timedelta

    def __post_init__(self) -> None:
        for label in (
            "minimum_support_count",
            "minimum_contra_count",
            "minimum_distinct_proposals",
        ):
            value = getattr(self, label)
            if type(value) is not int or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if not isinstance(self.minimum_separation, timedelta) or self.minimum_separation < timedelta(0):
            raise ValueError("minimum_separation must be a non-negative timedelta")


@dataclass(frozen=True)
class LessonEvidence:
    lesson_evidence_event_id: str
    lesson_candidate_event_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    role: LessonEvidenceRole
    proposal_record_id: str
    timing: ObservationTime


@dataclass(frozen=True)
class LessonTransition:
    lesson_candidate_event_id: str
    lesson_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    revision: int
    to_status: LessonStatus
    basis_candidate_event_id: str
    reason: str
    provenance: FeedbackProvenance
    timing: ObservationTime


class LessonLifecycleService:
    """Domain validation above the Task 18 append-only event repository."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        policy: LessonValidationPolicy | None = None,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection
        self._repository = FeedbackRepository(connection)
        self._policy = policy

    def create_candidate(self, candidate: LessonCandidate) -> AppendDisposition:
        if not isinstance(candidate, LessonCandidate):
            raise TypeError("candidate must be LessonCandidate")
        _validate_candidate(candidate)
        if candidate.revision != 0:
            raise ValueError("candidate creation must use revision zero")
        return self._append_candidate(candidate)

    def revise_candidate(self, candidate: LessonCandidate) -> AppendDisposition:
        if not isinstance(candidate, LessonCandidate):
            raise TypeError("candidate must be LessonCandidate")
        _validate_candidate(candidate)
        if candidate.revision < 1:
            raise ValueError("candidate correction must use a positive revision")
        latest = self._connection.execute(
            "SELECT revision, status FROM lesson_candidates WHERE lesson_id = ? "
            "AND strategy_id = ? AND strategy_version = ? "
            "ORDER BY revision DESC LIMIT 1",
            (
                candidate.lesson_id,
                candidate.strategy_id.value,
                candidate.strategy_version.value,
            ),
        ).fetchone()
        if latest is None or latest[1] != LessonStatus.CANDIDATE.value:
            raise ValueError("only a current CANDIDATE may be corrected")
        if candidate.revision not in {latest[0], latest[0] + 1}:
            raise ValueError("candidate correction must append the next revision")
        return self._append_candidate(candidate)

    def _append_candidate(self, candidate: LessonCandidate) -> AppendDisposition:
        payload = {
            "status": LessonStatus.CANDIDATE,
            "strategy_id": candidate.strategy_id,
            "strategy_version": candidate.strategy_version.value,
            "market_scope": candidate.market_scope,
            "sector_scope": candidate.sector_scope,
            "regime_scope": candidate.regime_scope,
            "condition": candidate.condition,
            "tentative_action": candidate.tentative_action,
            "uncertainty": candidate.uncertainty,
            "activation_allowed": False,
            "score_adjustment": 0,
            "policy_effect": False,
            "provenance": candidate.provenance,
        }
        return self._repository.append_lesson_candidate(
            LessonCandidateRecord(
                lesson_candidate_event_id=candidate.lesson_candidate_event_id,
                lesson_id=candidate.lesson_id,
                strategy_id=candidate.strategy_id,
                strategy_version=candidate.strategy_version,
                revision=candidate.revision,
                status=LessonStatus.CANDIDATE.value,
                payload=payload,
                timing=candidate.timing,
            )
        )

    def append_evidence(self, evidence: LessonEvidence) -> AppendDisposition:
        if not isinstance(evidence, LessonEvidence):
            raise TypeError("evidence must be LessonEvidence")
        for label in (
            "lesson_evidence_event_id",
            "lesson_candidate_event_id",
            "proposal_record_id",
        ):
            value = getattr(evidence, label)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if not isinstance(evidence.role, LessonEvidenceRole):
            raise TypeError("role must be LessonEvidenceRole")
        return self._repository.append_lesson_evidence(
            LessonEvidenceRecord(
                lesson_evidence_event_id=evidence.lesson_evidence_event_id,
                lesson_candidate_event_id=evidence.lesson_candidate_event_id,
                strategy_id=evidence.strategy_id,
                strategy_version=evidence.strategy_version,
                evidence_role=evidence.role.value,
                proposal_record_id=evidence.proposal_record_id,
                observation_id=None,
                timing=evidence.timing,
            )
        )

    def transition(self, transition: LessonTransition) -> AppendDisposition:
        if not isinstance(transition, LessonTransition):
            raise TypeError("transition must be LessonTransition")
        if self._policy is None:
            raise RuntimeError("lesson validation policy is required for transitions")
        _validate_transition(transition)
        duplicate = self._duplicate_transition_disposition(transition)
        if duplicate is not None:
            return duplicate
        latest = self._connection.execute(
            "SELECT lesson_candidate_event_id, revision, status, available_at, "
            "as_of_at FROM lesson_candidates WHERE lesson_id = ? "
            "AND strategy_id = ? AND strategy_version = ? "
            "ORDER BY revision DESC LIMIT 1",
            (
                transition.lesson_id,
                transition.strategy_id.value,
                transition.strategy_version.value,
            ),
        ).fetchone()
        if latest is None:
            raise ValueError("lesson does not exist")
        if transition.revision != latest[1] + 1:
            raise ValueError("transition must append the next revision")
        from_status = LessonStatus(latest[2])
        legal_transitions = {
            (LessonStatus.CANDIDATE, LessonStatus.SHADOW),
            (LessonStatus.CANDIDATE, LessonStatus.RETIRED),
            (LessonStatus.SHADOW, LessonStatus.SUSPENDED),
            (LessonStatus.SHADOW, LessonStatus.RETIRED),
            (LessonStatus.SUSPENDED, LessonStatus.SHADOW),
            (LessonStatus.SUSPENDED, LessonStatus.RETIRED),
        }
        if (from_status, transition.to_status) not in legal_transitions:
            raise ValueError("illegal lesson transition")
        if transition.basis_candidate_event_id != latest[0]:
            raise ValueError("transition basis must be the latest lesson revision")
        basis = self._connection.execute(
            "SELECT lesson_id, strategy_id, strategy_version, status, as_of_at, "
            "available_at "
            "FROM lesson_candidates WHERE lesson_candidate_event_id = ?",
            (transition.basis_candidate_event_id,),
        ).fetchone()
        if basis is None:
            raise ValueError("basis candidate does not exist")
        if basis[:4] != (
            transition.lesson_id,
            transition.strategy_id.value,
            transition.strategy_version.value,
            from_status.value,
        ):
            raise ValueError("basis lesson strategy or status mismatch")
        evidence = []
        if transition.to_status is LessonStatus.SHADOW:
            evidence = self._connection.execute(
                "SELECT evidence_role, proposal_record_id, available_at, as_of_at "
                "FROM lesson_evidence_events WHERE lesson_candidate_event_id = ? "
                "AND strategy_id = ? AND strategy_version = ? "
                "AND available_at <= ? AND as_of_at <= ?",
                (
                    transition.basis_candidate_event_id,
                    transition.strategy_id.value,
                    transition.strategy_version.value,
                    _utc_text(transition.timing.as_of_date),
                    _utc_text(transition.timing.as_of_date),
                ),
            ).fetchall()
        support_count = sum(row[0] == LessonEvidenceRole.SUPPORT.value for row in evidence)
        contra_count = sum(row[0] == LessonEvidenceRole.CONTRA.value for row in evidence)
        if transition.to_status is LessonStatus.SHADOW:
            if (
                from_status is LessonStatus.SUSPENDED
                and not any(row[2] > basis[5] for row in evidence)
            ):
                raise ValueError("SUSPENDED lesson requires new evidence before SHADOW")
            if support_count < self._policy.minimum_support_count:
                raise ValueError("insufficient support evidence")
            if contra_count < self._policy.minimum_contra_count:
                raise ValueError("insufficient contra evidence")
            if len({row[1] for row in evidence}) < self._policy.minimum_distinct_proposals:
                raise ValueError("insufficient distinct proposal samples")
            candidate_as_of = datetime.fromisoformat(basis[4])
            if transition.timing.as_of_date - candidate_as_of < self._policy.minimum_separation:
                raise ValueError("minimum lesson time separation is not satisfied")
        payload = {
            "status": transition.to_status,
            "previous_status": from_status,
            "basis_candidate_event_id": transition.basis_candidate_event_id,
            "transition_reason": transition.reason,
            "activation_allowed": False,
            "score_adjustment": 0,
            "policy_effect": False,
            "support_count": support_count,
            "contra_count": contra_count,
            "distinct_proposal_count": len({row[1] for row in evidence}),
            "provenance": transition.provenance,
        }
        return self._repository.append_lesson_candidate(
            LessonCandidateRecord(
                lesson_candidate_event_id=transition.lesson_candidate_event_id,
                lesson_id=transition.lesson_id,
                strategy_id=transition.strategy_id,
                strategy_version=transition.strategy_version,
                revision=transition.revision,
                status=transition.to_status.value,
                payload=payload,
                timing=transition.timing,
            )
        )

    def _duplicate_transition_disposition(
        self, transition: LessonTransition
    ) -> AppendDisposition | None:
        existing = self._connection.execute(
            "SELECT lesson_id, strategy_id, strategy_version, revision, status, "
            "candidate_json, observed_at, available_at, ingested_at, as_of_at "
            "FROM lesson_candidates WHERE lesson_candidate_event_id = ?",
            (transition.lesson_candidate_event_id,),
        ).fetchone()
        if existing is None:
            return None
        payload = json.loads(existing[5])
        expected_identity = (
            transition.lesson_id,
            transition.strategy_id.value,
            transition.strategy_version.value,
            transition.revision,
            transition.to_status.value,
        )
        expected_payload = {
            "status": transition.to_status.value,
            "basis_candidate_event_id": transition.basis_candidate_event_id,
            "transition_reason": transition.reason,
            "provenance": dict(transition.provenance.__dict__),
        }
        expected_timing = (
            _utc_text(transition.timing.observed_at),
            _utc_text(transition.timing.available_at),
            _utc_text(transition.timing.ingested_at),
            _utc_text(transition.timing.as_of_date),
        )
        if (
            existing[:5] != expected_identity
            or any(payload.get(key) != value for key, value in expected_payload.items())
            or existing[6:] != expected_timing
        ):
            raise ValueError("lesson identity has divergent content")
        return AppendDisposition.DUPLICATE


def _validate_candidate(candidate: LessonCandidate) -> None:
    for label in (
        "lesson_candidate_event_id",
        "lesson_id",
        "condition",
        "tentative_action",
    ):
        value = getattr(candidate, label)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
    if not isinstance(candidate.strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(candidate.strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")
    if type(candidate.revision) is not int or candidate.revision < 0:
        raise ValueError("revision must be non-negative")
    _validate_text_tuple("market_scope", candidate.market_scope, required=True)
    _validate_text_tuple("sector_scope", candidate.sector_scope)
    _validate_text_tuple("regime_scope", candidate.regime_scope)
    if (
        not isinstance(candidate.uncertainty, Decimal)
        or not candidate.uncertainty.is_finite()
        or candidate.uncertainty < Decimal("0")
        or candidate.uncertainty > Decimal("1")
    ):
        raise ValueError("uncertainty must be a finite Decimal between zero and one")
    if not isinstance(candidate.provenance, FeedbackProvenance):
        raise TypeError("provenance must be FeedbackProvenance")
    if not isinstance(candidate.timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")


def _validate_transition(transition: LessonTransition) -> None:
    for label in (
        "lesson_candidate_event_id",
        "lesson_id",
        "basis_candidate_event_id",
        "reason",
    ):
        value = getattr(transition, label)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
    if not isinstance(transition.strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(transition.strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")
    if type(transition.revision) is not int or transition.revision < 1:
        raise ValueError("transition revision must be positive")
    if not isinstance(transition.to_status, LessonStatus) or transition.to_status in {
        LessonStatus.LEGACY_UNVALIDATED,
        LessonStatus.CANDIDATE,
    }:
        raise ValueError("unknown or unavailable transition status")
    if not isinstance(transition.provenance, FeedbackProvenance):
        raise TypeError("provenance must be FeedbackProvenance")
    if not isinstance(transition.timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")
