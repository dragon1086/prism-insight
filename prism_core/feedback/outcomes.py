"""Research-only proposal outcome events with no broker execution semantics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.feedback.repository import (
    AppendDisposition,
    _utc_text,
    canonical_json,
)
from prism_core.storage.database import transaction
from prism_core.strategies.contracts import StrategyId, StrategyVersion


class OutcomeState(str, Enum):
    NO_ENTRY = "NO_ENTRY"
    REJECTED = "REJECTED"
    ELIGIBLE_NOT_EXECUTED = "ELIGIBLE_NOT_EXECUTED"
    INTERNALLY_SIMULATED = "INTERNALLY_SIMULATED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


_OUTCOME_HORIZONS = {
    StrategyId.SWING_V1: frozenset({5, 10, 20}),
    StrategyId.TREND_V1: frozenset({20, 60, 120}),
}


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    horizon_sessions: int
    revision: int
    outcome_state: OutcomeState
    quality: DataQualityStatus
    outcome_payload: Mapping[str, Any]
    timing: ObservationTime
    config_version: str
    code_version: str
    schema_version: str


@dataclass(frozen=True)
class StoredOutcome:
    outcome_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    horizon_sessions: int
    revision: int
    outcome_state: OutcomeState
    quality: DataQualityStatus
    outcome_json: str
    available_at: datetime


class OutcomeRepository:
    """Append outcome observations and read only what was PIT-available."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("foreign-key enforcement must be enabled")
        self._connection = connection

    def append(self, record: OutcomeRecord) -> AppendDisposition:
        self._validate(record)
        parent = self._connection.execute(
            "SELECT strategy_id, strategy_version, as_of_at FROM trade_plan_proposals "
            "WHERE proposal_record_id = ?",
            (record.proposal_record_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist")
        if parent[:2] != (record.strategy_id.value, record.strategy_version.value):
            raise ValueError("strategy mismatch between proposal and outcome")
        available_at = _utc_text(record.timing.available_at)
        as_of_at = _utc_text(record.timing.as_of_date)
        if available_at < parent[2] or as_of_at < parent[2]:
            raise ValueError("outcome cannot precede the proposal decision boundary")
        outcome_json = canonical_json(record.outcome_payload)
        semantic = {
            "proposal_record_id": record.proposal_record_id,
            "strategy_id": record.strategy_id,
            "strategy_version": record.strategy_version.value,
            "horizon_sessions": record.horizon_sessions,
            "revision": record.revision,
            "outcome_state": record.outcome_state,
            "quality": record.quality,
            "outcome_payload": record.outcome_payload,
            "observed_at": record.timing.observed_at,
            "available_at": record.timing.available_at,
            "as_of_at": record.timing.as_of_date,
            "config_version": record.config_version,
            "code_version": record.code_version,
            "schema_version": record.schema_version,
        }
        content_hash = "sha256:" + hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest()
        natural = (
            record.proposal_record_id,
            record.horizon_sessions,
            record.revision,
        )
        with transaction(self._connection):
            existing = self._connection.execute(
                "SELECT content_hash FROM proposal_outcomes "
                "WHERE proposal_record_id = ? AND horizon_sessions = ? AND revision = ?",
                natural,
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("duplicate natural identity has divergent content")
                return AppendDisposition.DUPLICATE
            if record.revision:
                previous = self._connection.execute(
                    "SELECT max(revision) FROM proposal_outcomes "
                    "WHERE proposal_record_id = ? AND horizon_sessions = ?",
                    natural[:2],
                ).fetchone()[0]
                if previous is None or record.revision != previous + 1:
                    raise ValueError("outcome corrections must append the next revision")
            self._connection.execute(
                "INSERT INTO proposal_outcomes VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.outcome_event_id,
                    record.proposal_record_id,
                    record.strategy_id.value,
                    record.strategy_version.value,
                    record.horizon_sessions,
                    record.revision,
                    record.outcome_state.value,
                    record.quality.value,
                    outcome_json,
                    _utc_text(record.timing.observed_at),
                    available_at,
                    _utc_text(record.timing.ingested_at),
                    as_of_at,
                    content_hash,
                    record.config_version,
                    record.code_version,
                    record.schema_version,
                ),
            )
        return AppendDisposition.INSERTED

    def outcomes_as_of(
        self, as_of: datetime, *, strategy_id: StrategyId
    ) -> tuple[StoredOutcome, ...]:
        boundary = _utc_text(as_of)
        if not isinstance(strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        rows = self._connection.execute(
            """
            SELECT o.outcome_event_id, o.proposal_record_id, o.strategy_id,
                   o.strategy_version, o.horizon_sessions, o.revision,
                   o.outcome_state, o.quality, o.outcome_json, o.available_at
            FROM proposal_outcomes AS o
            JOIN (
                SELECT proposal_record_id, horizon_sessions, max(revision) AS revision
                FROM proposal_outcomes
                WHERE strategy_id = ? AND available_at <= ? AND as_of_at <= ?
                GROUP BY proposal_record_id, horizon_sessions
            ) AS latest
              ON latest.proposal_record_id = o.proposal_record_id
             AND latest.horizon_sessions = o.horizon_sessions
             AND latest.revision = o.revision
            ORDER BY o.proposal_record_id, o.horizon_sessions
            """,
            (strategy_id.value, boundary, boundary),
        ).fetchall()
        return tuple(
            StoredOutcome(
                outcome_event_id=row[0],
                proposal_record_id=row[1],
                strategy_id=StrategyId(row[2]),
                strategy_version=StrategyVersion(row[3]),
                horizon_sessions=row[4],
                revision=row[5],
                outcome_state=OutcomeState(row[6]),
                quality=DataQualityStatus(row[7]),
                outcome_json=row[8],
                available_at=datetime.fromisoformat(row[9]),
            )
            for row in rows
        )

    @staticmethod
    def _validate(record: OutcomeRecord) -> None:
        if not isinstance(record, OutcomeRecord):
            raise TypeError("record must be OutcomeRecord")
        if not isinstance(record.strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        if not isinstance(record.strategy_version, StrategyVersion):
            raise TypeError("strategy_version must be StrategyVersion")
        if not isinstance(record.outcome_state, OutcomeState):
            raise TypeError("outcome_state must be OutcomeState")
        if not isinstance(record.quality, DataQualityStatus):
            raise TypeError("quality must be DataQualityStatus")
        if (
            type(record.horizon_sessions) is not int
            or record.horizon_sessions not in _OUTCOME_HORIZONS[record.strategy_id]
        ):
            raise ValueError("horizon_sessions is not valid for the strategy")
        if type(record.revision) is not int or record.revision < 0:
            raise ValueError("revision must be non-negative")
        for label, value in (
            ("outcome_event_id", record.outcome_event_id),
            ("proposal_record_id", record.proposal_record_id),
            ("config_version", record.config_version),
            ("code_version", record.code_version),
            ("schema_version", record.schema_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if not isinstance(record.timing, ObservationTime):
            raise TypeError("timing must be ObservationTime")
        _utc_text(record.timing.observed_at)
        _utc_text(record.timing.available_at)
        _utc_text(record.timing.ingested_at)
        _utc_text(record.timing.as_of_date)
