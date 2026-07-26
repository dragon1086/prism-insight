"""Append-only, point-in-time proposal and feedback persistence."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping
from uuid import UUID

from pydantic import BaseModel

from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.data.quality import QualityDisposition
from prism_core.llm.trade_plan import ProposedDecision, TradePlanProposal
from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.policy.proposal_validator import ProposalValidationStatus
from prism_core.storage.database import transaction
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


class AppendDisposition(str, Enum):
    INSERTED = "INSERTED"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True)
class FeedbackRunRecord:
    feedback_run_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    run_kind: str
    config_version: str
    code_version: str
    schema_version: str
    timing: ObservationTime


@dataclass(frozen=True)
class DecisionSnapshotRecord:
    decision_snapshot_id: str
    feedback_run_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    market: Market
    security_id: str
    data_snapshot_id: str
    feature_snapshot_id: str
    feature_version: str
    quant_score_id: str
    quant_score_version: str
    evidence_refs: tuple[str, ...]
    snapshot_payload: Mapping[str, Any]
    data_quality: DataQualityStatus
    quality_disposition: QualityDisposition
    timing: ObservationTime


@dataclass(frozen=True)
class ProposalRecord:
    proposal_record_id: str
    proposal_key: str
    revision: int
    decision_snapshot_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    parse_status: str
    validation_status: ProposalValidationStatus
    raw_output_ref: str
    raw_output: str
    normalized_proposal: TradePlanProposal | None
    model_provider: str
    model_id: str
    model_version: str
    prompt_version: str
    sampling_version: str
    sampling: Mapping[str, Any]
    validator_version: str
    policy_version: str
    timing: ObservationTime


@dataclass(frozen=True)
class RetrospectiveRecord:
    retrospective_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    review_kind: str
    revision: int
    payload: Mapping[str, Any]
    timing: ObservationTime


@dataclass(frozen=True)
class LessonCandidateRecord:
    lesson_candidate_event_id: str
    lesson_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    revision: int
    status: str
    payload: Mapping[str, Any]
    timing: ObservationTime


@dataclass(frozen=True)
class LessonEvidenceRecord:
    lesson_evidence_event_id: str
    lesson_candidate_event_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    evidence_role: str
    proposal_record_id: str | None
    observation_id: str | None
    timing: ObservationTime


@dataclass(frozen=True)
class StoredProposal:
    proposal_record_id: str
    proposal_key: str
    revision: int
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    proposed_decision: ProposedDecision | None
    raw_output: str
    normalized_proposal_json: str | None
    available_at: datetime
    dispositions: tuple[FieldDisposition, ...]


_FORBIDDEN_EXECUTION_KEYS = frozenset(
    {
        "broker",
        "broker_id",
        "fill",
        "fill_id",
        "order",
        "order_id",
        "order_intent",
        "quantity",
        "qty",
    }
)


def canonical_json(value: Any) -> str:
    """Serialize deterministic evidence JSON while rejecting unsafe values."""

    normalized = _normalize(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("canonical JSON keys must be non-empty strings")
            if key.lower() in _FORBIDDEN_EXECUTION_KEYS:
                raise ValueError(f"execution field is prohibited: {key}")
            result[key] = _normalize(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_normalize(item) for item in value]
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite numeric value is prohibited")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numeric value is prohibited")
        raise TypeError("float values are prohibited; use Decimal")
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(label: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{label} must be a non-empty string")


def _timing_values(timing: ObservationTime) -> tuple[str, str, str, str]:
    if not isinstance(timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")
    return (
        _utc_text(timing.observed_at),
        _utc_text(timing.available_at),
        _utc_text(timing.ingested_at),
        _utc_text(timing.as_of_date),
    )


class FeedbackRepository:
    """One-transaction writer and PIT reader for Task 18 feedback evidence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("foreign-key enforcement must be enabled")
        self._connection = connection

    def append_proposal(
        self,
        run: FeedbackRunRecord,
        snapshot: DecisionSnapshotRecord,
        proposal: ProposalRecord,
        dispositions: tuple[FieldDisposition, ...],
    ) -> AppendDisposition:
        self._validate_bundle(run, snapshot, proposal, dispositions)
        prepared = self._prepare_bundle(run, snapshot, proposal, dispositions)
        with transaction(self._connection):
            self._insert_identity(
                "feedback_runs",
                "feedback_run_id",
                run.feedback_run_id,
                prepared["run_hash"],
                prepared["run_insert"],
            )
            self._insert_identity(
                "decision_snapshots",
                "decision_snapshot_id",
                snapshot.decision_snapshot_id,
                prepared["snapshot_hash"],
                prepared["snapshot_insert"],
            )
            existing = self._connection.execute(
                "SELECT content_hash FROM trade_plan_proposals "
                "WHERE proposal_key = ? AND strategy_id = ? AND "
                "strategy_version = ? AND revision = ?",
                (
                    proposal.proposal_key,
                    proposal.strategy_id.value,
                    proposal.strategy_version.value,
                    proposal.revision,
                ),
            ).fetchone()
            if existing is not None:
                if existing[0] != prepared["proposal_hash"]:
                    raise ValueError("duplicate natural identity has divergent content")
                return AppendDisposition.DUPLICATE
            if proposal.revision:
                previous = self._connection.execute(
                    "SELECT max(revision) FROM trade_plan_proposals "
                    "WHERE proposal_key = ? AND strategy_id = ? AND strategy_version = ?",
                    (
                        proposal.proposal_key,
                        proposal.strategy_id.value,
                        proposal.strategy_version.value,
                    ),
                ).fetchone()[0]
                if previous is None or proposal.revision != previous + 1:
                    raise ValueError("proposal corrections must append the next revision")
            self._connection.execute(prepared["proposal_insert"][0], prepared["proposal_insert"][1])
            for statement, values in prepared["disposition_inserts"]:
                self._connection.execute(statement, values)
        return AppendDisposition.INSERTED

    def proposals_as_of(
        self, as_of: datetime, *, strategy_id: StrategyId
    ) -> tuple[StoredProposal, ...]:
        boundary = _utc_text(as_of)
        if not isinstance(strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        rows = self._connection.execute(
            """
            SELECT p.proposal_record_id, p.proposal_key, p.revision, p.strategy_id,
                   p.strategy_version, p.proposed_decision, p.raw_output,
                   p.normalized_proposal_json, p.available_at
            FROM trade_plan_proposals AS p
            JOIN (
                SELECT proposal_key, strategy_version, max(revision) AS revision
                FROM trade_plan_proposals
                WHERE strategy_id = ? AND available_at <= ? AND as_of_at <= ?
                GROUP BY proposal_key, strategy_version
            ) AS latest
              ON latest.proposal_key = p.proposal_key
             AND latest.strategy_version = p.strategy_version
             AND latest.revision = p.revision
            WHERE p.strategy_id = ?
            ORDER BY p.proposal_key
            """,
            (strategy_id.value, boundary, boundary, strategy_id.value),
        ).fetchall()
        stored: list[StoredProposal] = []
        for row in rows:
            disposition_rows = self._connection.execute(
                """
                SELECT field_path, action, reason, proposed_value_json,
                       resolved_value_json, evidence_refs_json
                FROM proposal_disposition_events
                WHERE proposal_record_id = ? AND available_at <= ? AND as_of_at <= ?
                ORDER BY sequence_no
                """,
                (row[0], boundary, boundary),
            ).fetchall()
            disposition_values = tuple(
                FieldDisposition(
                    field_path=item[0],
                    action=DispositionAction(item[1]),
                    reason=item[2],
                    proposed_value=None if item[3] is None else json.loads(item[3]),
                    resolved_value=None if item[4] is None else json.loads(item[4]),
                    evidence_ids=tuple(json.loads(item[5])),
                )
                for item in disposition_rows
            )
            stored.append(
                StoredProposal(
                    proposal_record_id=row[0],
                    proposal_key=row[1],
                    revision=row[2],
                    strategy_id=StrategyId(row[3]),
                    strategy_version=StrategyVersion(row[4]),
                    proposed_decision=None if row[5] is None else ProposedDecision(row[5]),
                    raw_output=row[6],
                    normalized_proposal_json=row[7],
                    available_at=datetime.fromisoformat(row[8]),
                    dispositions=disposition_values,
                )
            )
        return tuple(stored)

    def append_retrospective(self, record: RetrospectiveRecord) -> AppendDisposition:
        if not isinstance(record, RetrospectiveRecord):
            raise TypeError("record must be RetrospectiveRecord")
        if record.review_kind not in {"PROCESS", "OUTCOME"}:
            raise ValueError("unknown retrospective review kind")
        parent = self._proposal_parent(record.proposal_record_id)
        self._require_parent_strategy(parent, record.strategy_id, record.strategy_version)
        self._require_not_before_parent(record.timing, parent[2], "retrospective")
        if record.review_kind == "OUTCOME":
            self._validate_outcome_retrospective(record)
        payload_json = canonical_json(record.payload)
        semantic = {
            "proposal_record_id": record.proposal_record_id,
            "strategy_id": record.strategy_id,
            "strategy_version": record.strategy_version.value,
            "review_kind": record.review_kind,
            "revision": record.revision,
            "payload": record.payload,
            "observed_at": record.timing.observed_at,
            "available_at": record.timing.available_at,
            "as_of_at": record.timing.as_of_date,
        }
        content_hash = _hash(semantic)
        times = _timing_values(record.timing)
        return self._append_revision_event(
            table="retrospective_events",
            natural_where="proposal_record_id = ? AND review_kind = ?",
            natural_values=(record.proposal_record_id, record.review_kind),
            revision=record.revision,
            content_hash=content_hash,
            timing=record.timing,
            insert=(
                "INSERT INTO retrospective_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.retrospective_event_id, record.proposal_record_id,
                    record.strategy_id.value, record.strategy_version.value,
                    record.review_kind, record.revision, payload_json, *times, content_hash,
                ),
            ),
        )

    def _validate_outcome_retrospective(self, record: RetrospectiveRecord) -> None:
        if not isinstance(record.payload, Mapping):
            raise TypeError("retrospective payload must be a mapping")
        event_ids = record.payload.get("outcome_event_ids")
        if (
            not isinstance(event_ids, (tuple, list))
            or not event_ids
            or any(not isinstance(item, str) or not item.strip() for item in event_ids)
            or len(set(event_ids)) != len(event_ids)
        ):
            raise ValueError("OUTCOME retrospective requires unique outcome_event_ids")
        review_available = _utc_text(record.timing.available_at)
        review_as_of = _utc_text(record.timing.as_of_date)
        horizons: list[int] = []
        for event_id in event_ids:
            outcome = self._connection.execute(
                "SELECT proposal_record_id, strategy_id, strategy_version, "
                "horizon_sessions, revision, available_at, as_of_at "
                "FROM proposal_outcomes WHERE outcome_event_id = ?",
                (event_id,),
            ).fetchone()
            if outcome is None:
                raise ValueError("OUTCOME retrospective references an unknown outcome")
            if outcome[0] != record.proposal_record_id:
                raise ValueError("OUTCOME retrospective references another proposal")
            self._require_parent_strategy(
                outcome[1:], record.strategy_id, record.strategy_version
            )
            if outcome[5] > review_available or outcome[6] > review_as_of:
                raise ValueError("OUTCOME retrospective references future outcome evidence")
            latest = self._connection.execute(
                "SELECT outcome_event_id FROM proposal_outcomes "
                "WHERE proposal_record_id = ? AND horizon_sessions = ? "
                "AND available_at <= ? AND as_of_at <= ? "
                "ORDER BY revision DESC LIMIT 1",
                (record.proposal_record_id, outcome[3], review_as_of, review_as_of),
            ).fetchone()
            if latest is None or latest[0] != event_id:
                raise ValueError("OUTCOME retrospective requires latest PIT outcome revisions")
            horizons.append(outcome[3])
        if horizons != sorted(set(horizons)):
            raise ValueError("OUTCOME retrospective horizons must be unique and ascending")
        declared_horizons = record.payload.get("outcome_horizons")
        if declared_horizons is not None and list(declared_horizons) != horizons:
            raise ValueError("OUTCOME retrospective horizon provenance mismatch")

    def append_lesson_candidate(self, record: LessonCandidateRecord) -> AppendDisposition:
        if not isinstance(record, LessonCandidateRecord):
            raise TypeError("record must be LessonCandidateRecord")
        allowed_statuses = {"CANDIDATE", "SHADOW", "SUSPENDED", "RETIRED"}
        if record.status not in allowed_statuses:
            raise ValueError("unknown or unavailable lesson status")
        if type(record.revision) is not int or record.revision < 0:
            raise ValueError("revision must be non-negative")
        if record.revision == 0 and record.status != "CANDIDATE":
            raise ValueError("lesson creation must start at CANDIDATE")
        payload_json = canonical_json(record.payload)
        semantic = {
            "lesson_id": record.lesson_id,
            "strategy_id": record.strategy_id,
            "strategy_version": record.strategy_version.value,
            "revision": record.revision,
            "status": record.status,
            "payload": record.payload,
            "observed_at": record.timing.observed_at,
            "available_at": record.timing.available_at,
            "as_of_at": record.timing.as_of_date,
        }
        content_hash = _hash(semantic)
        times = _timing_values(record.timing)
        natural_values = (
            record.lesson_id,
            record.strategy_id.value,
            record.strategy_version.value,
        )
        with transaction(self._connection):
            existing = self._connection.execute(
                "SELECT content_hash FROM lesson_candidates WHERE "
                "lesson_candidate_event_id = ? OR "
                "(lesson_id = ? AND strategy_id = ? AND strategy_version = ? "
                "AND revision = ?)",
                (record.lesson_candidate_event_id, *natural_values, record.revision),
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("lesson identity has divergent content")
                return AppendDisposition.DUPLICATE
            previous = self._connection.execute(
                "SELECT revision, status, available_at, as_of_at "
                "FROM lesson_candidates WHERE "
                "lesson_id = ? AND strategy_id = ? AND strategy_version = ? "
                "ORDER BY revision DESC LIMIT 1",
                natural_values,
            ).fetchone()
            if record.revision == 0:
                if previous is not None:
                    raise ValueError("lesson revision zero already has a successor")
            else:
                if previous is None or record.revision != previous[0] + 1:
                    raise ValueError("corrections must append the next revision")
                self._require_chronological_revision(
                    record.timing, previous[2], previous[3]
                )
                legal_pairs = {
                    ("CANDIDATE", "CANDIDATE"),
                    ("CANDIDATE", "SHADOW"),
                    ("CANDIDATE", "RETIRED"),
                    ("SHADOW", "SHADOW"),
                    ("SHADOW", "SUSPENDED"),
                    ("SHADOW", "RETIRED"),
                    ("SUSPENDED", "SUSPENDED"),
                    ("SUSPENDED", "SHADOW"),
                    ("SUSPENDED", "RETIRED"),
                }
                if (previous[1], record.status) not in legal_pairs:
                    raise ValueError("illegal lesson status transition")
            self._connection.execute(
                "INSERT INTO lesson_candidates VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.lesson_candidate_event_id, record.lesson_id,
                    record.strategy_id.value, record.strategy_version.value,
                    record.revision, record.status, payload_json, *times, content_hash,
                ),
            )
        return AppendDisposition.INSERTED

    def append_lesson_evidence(self, record: LessonEvidenceRecord) -> AppendDisposition:
        if not isinstance(record, LessonEvidenceRecord):
            raise TypeError("record must be LessonEvidenceRecord")
        if record.evidence_role not in {"SUPPORT", "CONTRA"}:
            raise ValueError("unknown lesson evidence role")
        if (record.proposal_record_id is None) == (record.observation_id is None):
            raise ValueError("lesson evidence requires exactly one source reference")
        candidate = self._connection.execute(
            "SELECT strategy_id, strategy_version, as_of_at FROM lesson_candidates "
            "WHERE lesson_candidate_event_id = ?",
            (record.lesson_candidate_event_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError("lesson candidate does not exist")
        self._require_parent_strategy(candidate, record.strategy_id, record.strategy_version)
        self._require_not_before_parent(record.timing, candidate[2], "lesson evidence")
        if record.proposal_record_id is not None:
            proposal = self._proposal_parent(record.proposal_record_id)
            self._require_parent_strategy(proposal, record.strategy_id, record.strategy_version)
        elif self._connection.execute(
            "SELECT 1 FROM observations WHERE observation_id = ?", (record.observation_id,)
        ).fetchone() is None:
            raise ValueError("observation evidence does not exist")
        semantic = {
            "lesson_candidate_event_id": record.lesson_candidate_event_id,
            "strategy_id": record.strategy_id,
            "strategy_version": record.strategy_version.value,
            "evidence_role": record.evidence_role,
            "proposal_record_id": record.proposal_record_id,
            "observation_id": record.observation_id,
            "observed_at": record.timing.observed_at,
            "available_at": record.timing.available_at,
            "as_of_at": record.timing.as_of_date,
        }
        content_hash = _hash(semantic)
        times = _timing_values(record.timing)
        with transaction(self._connection):
            existing = self._connection.execute(
                "SELECT content_hash FROM lesson_evidence_events WHERE "
                "lesson_evidence_event_id = ? OR "
                "(lesson_candidate_event_id = ? AND evidence_role = ? AND "
                "proposal_record_id IS ? AND observation_id IS ?)",
                (
                    record.lesson_evidence_event_id,
                    record.lesson_candidate_event_id,
                    record.evidence_role,
                    record.proposal_record_id,
                    record.observation_id,
                ),
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("lesson evidence identity has divergent content")
                return AppendDisposition.DUPLICATE
            self._connection.execute(
                "INSERT INTO lesson_evidence_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.lesson_evidence_event_id, record.lesson_candidate_event_id,
                    record.strategy_id.value, record.strategy_version.value,
                    record.evidence_role, record.proposal_record_id, record.observation_id,
                    *times, content_hash,
                ),
            )
        return AppendDisposition.INSERTED

    def retrospective_ids_as_of(
        self, as_of: datetime, *, strategy_id: StrategyId
    ) -> tuple[str, ...]:
        boundary = _utc_text(as_of)
        rows = self._connection.execute(
            "SELECT r.retrospective_event_id FROM retrospective_events AS r "
            "JOIN ("
            "SELECT proposal_record_id, review_kind, max(revision) AS revision "
            "FROM retrospective_events "
            "WHERE strategy_id = ? AND available_at <= ? AND as_of_at <= ? "
            "GROUP BY proposal_record_id, review_kind"
            ") AS latest ON latest.proposal_record_id = r.proposal_record_id "
            "AND latest.review_kind = r.review_kind AND latest.revision = r.revision "
            "ORDER BY r.proposal_record_id, r.review_kind",
            (strategy_id.value, boundary, boundary),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def lesson_evidence_ids_as_of(
        self, as_of: datetime, *, strategy_id: StrategyId
    ) -> tuple[str, ...]:
        boundary = _utc_text(as_of)
        rows = self._connection.execute(
            "SELECT e.lesson_evidence_event_id FROM lesson_evidence_events AS e "
            "JOIN lesson_candidates AS c "
            "ON c.lesson_candidate_event_id = e.lesson_candidate_event_id "
            "WHERE e.strategy_id = ? AND e.available_at <= ? AND e.as_of_at <= ? "
            "AND c.available_at <= ? AND c.as_of_at <= ? "
            "ORDER BY e.lesson_evidence_event_id",
            (strategy_id.value, boundary, boundary, boundary, boundary),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def _proposal_parent(self, proposal_record_id: str):
        parent = self._connection.execute(
            "SELECT strategy_id, strategy_version, as_of_at FROM trade_plan_proposals "
            "WHERE proposal_record_id = ?", (proposal_record_id,)
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist")
        return parent

    @staticmethod
    def _require_parent_strategy(parent, strategy_id, strategy_version) -> None:
        if parent[:2] != (strategy_id.value, strategy_version.value):
            raise ValueError("strategy mismatch")

    @staticmethod
    def _require_not_before_parent(timing, parent_as_of: str, label: str) -> None:
        if _utc_text(timing.available_at) < parent_as_of or _utc_text(timing.as_of_date) < parent_as_of:
            raise ValueError(f"{label} cannot precede its parent boundary")

    @staticmethod
    def _require_chronological_revision(
        timing: ObservationTime, previous_available_at: str, previous_as_of_at: str
    ) -> None:
        if (
            _utc_text(timing.available_at) < previous_available_at
            or _utc_text(timing.as_of_date) < previous_as_of_at
        ):
            raise ValueError("revisions must be chronological")

    def _append_revision_event(
        self, *, table, natural_where, natural_values, revision, content_hash, timing, insert
    ) -> AppendDisposition:
        if type(revision) is not int or revision < 0:
            raise ValueError("revision must be non-negative")
        with transaction(self._connection):
            existing = self._connection.execute(
                f"SELECT content_hash FROM {table} WHERE {natural_where} AND revision = ?",
                (*natural_values, revision),
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("duplicate natural identity has divergent content")
                return AppendDisposition.DUPLICATE
            if revision:
                previous = self._connection.execute(
                    f"SELECT revision, available_at, as_of_at FROM {table} "
                    f"WHERE {natural_where} ORDER BY revision DESC LIMIT 1",
                    natural_values,
                ).fetchone()
                if previous is None or revision != previous[0] + 1:
                    raise ValueError("corrections must append the next revision")
                self._require_chronological_revision(timing, previous[1], previous[2])
            self._connection.execute(insert[0], insert[1])
        return AppendDisposition.INSERTED

    def _validate_bundle(
        self,
        run: FeedbackRunRecord,
        snapshot: DecisionSnapshotRecord,
        proposal: ProposalRecord,
        dispositions: tuple[FieldDisposition, ...],
    ) -> None:
        if not isinstance(run, FeedbackRunRecord):
            raise TypeError("run must be FeedbackRunRecord")
        if not isinstance(snapshot, DecisionSnapshotRecord):
            raise TypeError("snapshot must be DecisionSnapshotRecord")
        if not isinstance(proposal, ProposalRecord):
            raise TypeError("proposal must be ProposalRecord")
        if not isinstance(dispositions, tuple) or any(
            not isinstance(item, FieldDisposition) for item in dispositions
        ):
            raise TypeError("dispositions must contain FieldDisposition values")
        identities = (
            (run.strategy_id, snapshot.strategy_id, proposal.strategy_id),
            (run.strategy_version, snapshot.strategy_version, proposal.strategy_version),
        )
        if any(len(set(items)) != 1 for items in identities):
            raise ValueError("strategy mismatch in proposal bundle")
        if run.market is not snapshot.market:
            raise ValueError("market mismatch in proposal bundle")
        if run.feedback_run_id != snapshot.feedback_run_id:
            raise ValueError("feedback run mismatch")
        if snapshot.decision_snapshot_id != proposal.decision_snapshot_id:
            raise ValueError("decision snapshot mismatch")
        if type(proposal.revision) is not int or proposal.revision < 0:
            raise ValueError("revision must be non-negative")
        if proposal.parse_status not in {"PARSED", "REJECTED"}:
            raise ValueError("unknown parse status")
        if proposal.parse_status == "PARSED" and proposal.normalized_proposal is None:
            raise ValueError("parsed proposal requires normalized content")
        if proposal.parse_status == "REJECTED" and proposal.normalized_proposal is not None:
            raise ValueError("rejected parse cannot carry normalized content")
        if (
            proposal.parse_status == "REJECTED"
            and proposal.validation_status is not ProposalValidationStatus.REJECTED
        ):
            raise ValueError("rejected parse requires rejected validation status")
        if proposal.normalized_proposal is not None:
            model = proposal.normalized_proposal
            if (
                model.strategy_id is not proposal.strategy_id
                or model.strategy_version != proposal.strategy_version
                or str(model.security_id.value) != snapshot.security_id
                or str(model.feature_provenance.feature_snapshot_id) != snapshot.feature_snapshot_id
                or str(model.feature_provenance.data_snapshot_id) != snapshot.data_snapshot_id
            ):
                raise ValueError("strategy mismatch or snapshot provenance mismatch")
            referenced_evidence = _referenced_evidence(model)
            if not referenced_evidence <= set(snapshot.evidence_refs):
                raise ValueError("proposal has missing evidence references")
            expected_sampling = {
                "temperature": model.sampling.temperature,
                "top_p": model.sampling.top_p,
                "seed": model.sampling.seed,
            }
            if (
                proposal.model_provider != model.model.provider
                or proposal.model_id != model.model.model_id
                or proposal.model_version != model.model.model_version
                or proposal.prompt_version != model.prompt_version
                or proposal.sampling_version != model.sampling.version
                or canonical_json(proposal.sampling) != canonical_json(expected_sampling)
            ):
                raise ValueError("model provenance mismatch")
        if any(
            not set(item.evidence_ids) <= set(snapshot.evidence_refs)
            for item in dispositions
        ):
            raise ValueError("disposition has missing evidence references")
        if not snapshot.evidence_refs or len(set(snapshot.evidence_refs)) != len(
            snapshot.evidence_refs
        ):
            raise ValueError("snapshot evidence references must be non-empty and unique")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in snapshot.evidence_refs
        ):
            raise ValueError("snapshot evidence references must be non-empty strings")
        for label, value in (
            ("feedback_run_id", run.feedback_run_id),
            ("decision_snapshot_id", snapshot.decision_snapshot_id),
            ("proposal_record_id", proposal.proposal_record_id),
            ("proposal_key", proposal.proposal_key),
            ("raw_output_ref", proposal.raw_output_ref),
            ("raw_output", proposal.raw_output),
            ("model_provider", proposal.model_provider),
            ("model_id", proposal.model_id),
            ("model_version", proposal.model_version),
            ("prompt_version", proposal.prompt_version),
            ("sampling_version", proposal.sampling_version),
        ):
            _require_text(label, value)
        _timing_values(run.timing)
        _timing_values(snapshot.timing)
        _timing_values(proposal.timing)

    def _prepare_bundle(self, run, snapshot, proposal, dispositions):
        run_semantic = {
            "feedback_run_id": run.feedback_run_id,
            "strategy_id": run.strategy_id,
            "strategy_version": run.strategy_version.value,
            "market": run.market,
            "run_kind": run.run_kind,
            "config_version": run.config_version,
            "code_version": run.code_version,
            "schema_version": run.schema_version,
            "observed_at": run.timing.observed_at,
            "available_at": run.timing.available_at,
            "as_of_at": run.timing.as_of_date,
        }
        run_hash = _hash(run_semantic)
        run_times = _timing_values(run.timing)
        run_insert = (
            "INSERT INTO feedback_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.feedback_run_id, run.strategy_id.value, run.strategy_version.value,
                run.market.value, run.run_kind, run.config_version, run.code_version,
                run.schema_version, *run_times, run_hash,
            ),
        )
        evidence_json = canonical_json(snapshot.evidence_refs)
        snapshot_json = canonical_json(snapshot.snapshot_payload)
        snapshot_semantic = {
            "decision_snapshot_id": snapshot.decision_snapshot_id,
            "feedback_run_id": snapshot.feedback_run_id,
            "strategy_id": snapshot.strategy_id,
            "strategy_version": snapshot.strategy_version.value,
            "market": snapshot.market,
            "security_id": snapshot.security_id,
            "data_snapshot_id": snapshot.data_snapshot_id,
            "feature_snapshot_id": snapshot.feature_snapshot_id,
            "feature_version": snapshot.feature_version,
            "quant_score_id": snapshot.quant_score_id,
            "quant_score_version": snapshot.quant_score_version,
            "evidence_refs": snapshot.evidence_refs,
            "snapshot_payload": snapshot.snapshot_payload,
            "data_quality": snapshot.data_quality,
            "quality_disposition": snapshot.quality_disposition,
            "observed_at": snapshot.timing.observed_at,
            "available_at": snapshot.timing.available_at,
            "as_of_at": snapshot.timing.as_of_date,
        }
        snapshot_hash = _hash(snapshot_semantic)
        snapshot_times = _timing_values(snapshot.timing)
        snapshot_insert = (
            "INSERT INTO decision_snapshots VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.decision_snapshot_id, snapshot.feedback_run_id,
                snapshot.strategy_id.value, snapshot.strategy_version.value,
                snapshot.market.value, snapshot.security_id, snapshot.data_snapshot_id,
                snapshot.feature_snapshot_id, snapshot.feature_version,
                snapshot.quant_score_id, snapshot.quant_score_version, evidence_json,
                snapshot_json, snapshot.data_quality.value,
                snapshot.quality_disposition.value, *snapshot_times, snapshot_hash,
            ),
        )
        normalized_json = (
            None if proposal.normalized_proposal is None
            else proposal.normalized_proposal.model_dump_json()
        )
        disposition_semantics = [
            {
                "sequence_no": index,
                "field_path": item.field_path,
                "action": item.action,
                "reason": item.reason,
                "proposed_value": item.proposed_value,
                "resolved_value": item.resolved_value,
                "evidence_ids": item.evidence_ids,
            }
            for index, item in enumerate(dispositions)
        ]
        proposal_semantic = {
            "proposal_key": proposal.proposal_key,
            "revision": proposal.revision,
            "decision_snapshot_id": proposal.decision_snapshot_id,
            "strategy_id": proposal.strategy_id,
            "strategy_version": proposal.strategy_version.value,
            "parse_status": proposal.parse_status,
            "validation_status": proposal.validation_status,
            "raw_output_ref": proposal.raw_output_ref,
            "raw_output": proposal.raw_output,
            "normalized_proposal": None if normalized_json is None else json.loads(normalized_json),
            "model_provider": proposal.model_provider,
            "model_id": proposal.model_id,
            "model_version": proposal.model_version,
            "prompt_version": proposal.prompt_version,
            "sampling_version": proposal.sampling_version,
            "sampling": proposal.sampling,
            "validator_version": proposal.validator_version,
            "policy_version": proposal.policy_version,
            "observed_at": proposal.timing.observed_at,
            "available_at": proposal.timing.available_at,
            "as_of_at": proposal.timing.as_of_date,
            "dispositions": disposition_semantics,
        }
        proposal_hash = _hash(proposal_semantic)
        proposal_times = _timing_values(proposal.timing)
        proposal_id = None if proposal.normalized_proposal is None else str(proposal.normalized_proposal.proposal_id)
        proposed_decision = None if proposal.normalized_proposal is None else proposal.normalized_proposal.decision.value
        proposal_insert = (
            "INSERT INTO trade_plan_proposals VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal.proposal_record_id, proposal.proposal_key, proposal_id,
                proposal.revision, proposal.decision_snapshot_id, proposal.strategy_id.value,
                proposal.strategy_version.value, proposal.parse_status,
                proposal.validation_status.value,
                proposed_decision, proposal.raw_output_ref, proposal.raw_output,
                normalized_json, proposal.model_provider, proposal.model_id,
                proposal.model_version, proposal.prompt_version, proposal.sampling_version,
                canonical_json(proposal.sampling), proposal.validator_version,
                proposal.policy_version, *proposal_times, proposal_hash,
            ),
        )
        disposition_inserts = []
        for index, item in enumerate(dispositions):
            semantic = disposition_semantics[index]
            event_hash = _hash(semantic)
            event_id = f"{proposal.proposal_record_id}:disposition:{index}"
            disposition_inserts.append(
                (
                    "INSERT INTO proposal_disposition_events VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id, proposal.proposal_record_id, proposal.strategy_id.value,
                        proposal.strategy_version.value, index, item.field_path,
                        item.action.value, item.reason,
                        None if item.proposed_value is None else canonical_json(item.proposed_value),
                        None if item.resolved_value is None else canonical_json(item.resolved_value),
                        canonical_json(item.evidence_ids), proposal.validator_version,
                        proposal.policy_version, *proposal_times, event_hash,
                    ),
                )
            )
        return {
            "run_hash": run_hash,
            "run_insert": run_insert,
            "snapshot_hash": snapshot_hash,
            "snapshot_insert": snapshot_insert,
            "proposal_hash": proposal_hash,
            "proposal_insert": proposal_insert,
            "disposition_inserts": disposition_inserts,
        }

    def _insert_identity(self, table, id_column, identity, content_hash, insert):
        existing = self._connection.execute(
            f"SELECT content_hash FROM {table} WHERE {id_column} = ?", (identity,)
        ).fetchone()
        if existing is not None:
            if existing[0] != content_hash:
                raise ValueError(f"{table} identity has divergent content")
            return
        self._connection.execute(insert[0], insert[1])


def _referenced_evidence(proposal: TradePlanProposal) -> set[str]:
    evidence = set(proposal.bull_evidence_ids) | set(proposal.bear_evidence_ids)
    for item in proposal.score_breakdown:
        evidence.update(item.evidence_ids)
    evidence.update(proposal.risk_multiplier_candidate.evidence_ids)
    for item in (
        *proposal.entry_predicates,
        *proposal.stop_candidates,
        *proposal.target_candidates,
        *proposal.reentry_candidates,
        *proposal.pyramiding_candidates,
    ):
        evidence.update(item.evidence_ids)
    return evidence
