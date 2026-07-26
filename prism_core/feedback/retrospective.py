"""Point-in-time two-pass retrospective contracts and validation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from prism_core.data.contracts import ObservationTime
from prism_core.feedback.repository import (
    AppendDisposition,
    FeedbackRepository,
    RetrospectiveRecord,
    _utc_text,
)
from prism_core.strategies.contracts import StrategyId, StrategyVersion


@dataclass(frozen=True)
class FeedbackProvenance:
    """Version identity required for every generated feedback artifact."""

    model_provider: str
    model_id: str
    model_version: str
    prompt_version: str
    config_version: str
    code_version: str
    schema_version: str

    def __post_init__(self) -> None:
        for label, value in self.__dict__.items():
            _require_text(label, value)


@dataclass(frozen=True)
class ProcessReview:
    """Decision-time review that cannot carry realized outcome fields."""

    retrospective_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    revision: int
    evidence_ids: tuple[str, ...]
    evidence_as_of: datetime
    evidence_sufficiency: str
    policy_consistency: str
    calibration: str
    ignored_counter_evidence: tuple[str, ...]
    unsupported_confidence: tuple[str, ...]
    data_quality_handling: str
    provenance: FeedbackProvenance
    timing: ObservationTime


@dataclass(frozen=True)
class OutcomeReview:
    """Progressive per-proposal review of PIT-visible outcome horizons."""

    retrospective_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    revision: int
    outcome_event_ids: tuple[str, ...]
    realized_path: tuple[Decimal, ...]
    maximum_favorable_excursion: Decimal
    maximum_adverse_excursion: Decimal
    stop_target_events: tuple[str, ...]
    regime_transition: str
    rejected_candidate_counterfactual: str
    provenance: FeedbackProvenance
    timing: ObservationTime


class RetrospectiveService:
    """Validate review isolation before using the append-only repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection
        self._repository = FeedbackRepository(connection)

    def append_process(self, review: ProcessReview) -> AppendDisposition:
        if not isinstance(review, ProcessReview):
            raise TypeError("review must be ProcessReview")
        _validate_common(review)
        parent = self._connection.execute(
            """
            SELECT p.strategy_id, p.strategy_version, p.as_of_at,
                   s.evidence_refs_json
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS s
              ON s.decision_snapshot_id = p.decision_snapshot_id
             AND s.strategy_id = p.strategy_id
             AND s.strategy_version = p.strategy_version
            WHERE p.proposal_record_id = ?
            """,
            (review.proposal_record_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist")
        _require_strategy(parent, review.strategy_id, review.strategy_version)
        if _utc_text(review.evidence_as_of) != parent[2]:
            raise ValueError("process evidence_as_of must equal the decision boundary")
        allowed_evidence = set(json.loads(parent[3]))
        cited_evidence = set(review.evidence_ids)
        if not cited_evidence or not cited_evidence <= allowed_evidence:
            raise ValueError("process review cites evidence outside the decision snapshot")
        if not set(review.ignored_counter_evidence) <= allowed_evidence:
            raise ValueError("ignored counter-evidence is outside the decision snapshot")
        payload = {
            "review_kind": "PROCESS",
            "evidence_ids": review.evidence_ids,
            "evidence_as_of": review.evidence_as_of,
            "evidence_sufficiency": review.evidence_sufficiency,
            "policy_consistency": review.policy_consistency,
            "calibration": review.calibration,
            "ignored_counter_evidence": review.ignored_counter_evidence,
            "unsupported_confidence": review.unsupported_confidence,
            "data_quality_handling": review.data_quality_handling,
            "provenance": review.provenance,
        }
        return self._repository.append_retrospective(
            RetrospectiveRecord(
                retrospective_event_id=review.retrospective_event_id,
                proposal_record_id=review.proposal_record_id,
                strategy_id=review.strategy_id,
                strategy_version=review.strategy_version,
                review_kind="PROCESS",
                revision=review.revision,
                payload=payload,
                timing=review.timing,
            )
        )

    def append_outcome(self, review: OutcomeReview) -> AppendDisposition:
        if not isinstance(review, OutcomeReview):
            raise TypeError("review must be OutcomeReview")
        _validate_outcome_common(review)
        boundary = _utc_text(review.timing.as_of_date)
        available_boundary = _utc_text(review.timing.available_at)
        outcomes = []
        for event_id in review.outcome_event_ids:
            row = self._connection.execute(
                """
                SELECT outcome_event_id, proposal_record_id, strategy_id,
                       strategy_version, horizon_sessions, revision,
                       available_at, as_of_at
                FROM proposal_outcomes WHERE outcome_event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if row is None:
                raise ValueError("outcome review requires a PIT-visible outcome")
            if row[1] != review.proposal_record_id:
                raise ValueError("outcome belongs to another proposal")
            _require_strategy(row[2:], review.strategy_id, review.strategy_version)
            latest = self._connection.execute(
                """
                SELECT outcome_event_id FROM proposal_outcomes
                WHERE proposal_record_id = ? AND horizon_sessions = ?
                  AND available_at <= ? AND as_of_at <= ?
                ORDER BY revision DESC LIMIT 1
                """,
                (review.proposal_record_id, row[4], boundary, boundary),
            ).fetchone()
            if (
                latest is None
                or latest[0] != event_id
                or row[6] > available_boundary
                or row[7] > boundary
            ):
                raise ValueError("outcome review requires a latest PIT-visible outcome")
            outcomes.append(row)
        horizons = tuple(row[4] for row in outcomes)
        if len(set(horizons)) != len(horizons) or tuple(sorted(horizons)) != horizons:
            raise ValueError("outcome horizons must be unique and ascending")
        payload = {
            "review_kind": "OUTCOME",
            "outcome_event_ids": review.outcome_event_ids,
            "outcome_horizons": horizons,
            "realized_path": review.realized_path,
            "maximum_favorable_excursion": review.maximum_favorable_excursion,
            "maximum_adverse_excursion": review.maximum_adverse_excursion,
            "stop_target_events": review.stop_target_events,
            "regime_transition": review.regime_transition,
            "rejected_candidate_counterfactual": review.rejected_candidate_counterfactual,
            "provenance": review.provenance,
        }
        return self._repository.append_retrospective(
            RetrospectiveRecord(
                retrospective_event_id=review.retrospective_event_id,
                proposal_record_id=review.proposal_record_id,
                strategy_id=review.strategy_id,
                strategy_version=review.strategy_version,
                review_kind="OUTCOME",
                revision=review.revision,
                payload=payload,
                timing=review.timing,
            )
        )


def _validate_common(review: ProcessReview) -> None:
    for label in (
        "retrospective_event_id",
        "proposal_record_id",
        "evidence_sufficiency",
        "policy_consistency",
        "calibration",
        "data_quality_handling",
    ):
        _require_text(label, getattr(review, label))
    if not isinstance(review.strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(review.strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")
    if type(review.revision) is not int or review.revision < 0:
        raise ValueError("revision must be non-negative")
    if not isinstance(review.provenance, FeedbackProvenance):
        raise TypeError("provenance must be FeedbackProvenance")
    if not isinstance(review.timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")
    _validate_text_tuple("evidence_ids", review.evidence_ids, required=True)
    _validate_text_tuple("ignored_counter_evidence", review.ignored_counter_evidence)
    _validate_text_tuple("unsupported_confidence", review.unsupported_confidence)
    _utc_text(review.evidence_as_of)


def _validate_outcome_common(review: OutcomeReview) -> None:
    for label in (
        "retrospective_event_id",
        "proposal_record_id",
        "regime_transition",
        "rejected_candidate_counterfactual",
    ):
        _require_text(label, getattr(review, label))
    if not isinstance(review.strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(review.strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")
    if type(review.revision) is not int or review.revision < 0:
        raise ValueError("revision must be non-negative")
    if not isinstance(review.provenance, FeedbackProvenance):
        raise TypeError("provenance must be FeedbackProvenance")
    if not isinstance(review.timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")
    _validate_text_tuple("outcome_event_ids", review.outcome_event_ids, required=True)
    _validate_text_tuple("stop_target_events", review.stop_target_events)
    if not isinstance(review.realized_path, tuple) or not review.realized_path:
        raise ValueError("realized_path must be a non-empty tuple")
    numeric = (
        *review.realized_path,
        review.maximum_favorable_excursion,
        review.maximum_adverse_excursion,
    )
    if any(not isinstance(item, Decimal) or not item.is_finite() for item in numeric):
        raise ValueError("outcome metrics must be finite Decimal values")


def _require_strategy(parent, strategy_id: StrategyId, strategy_version: StrategyVersion) -> None:
    if parent[:2] != (strategy_id.value, strategy_version.value):
        raise ValueError("strategy mismatch")


def _validate_text_tuple(label: str, value: tuple[str, ...], *, required: bool = False) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be a tuple")
    if required and not value:
        raise ValueError(f"{label} must be non-empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} values must be non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} values must be unique")


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
