"""Process-quality and no-look-ahead market-outcome persistence services."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.data.exchange_calendar import (
    ExchangeMarket,
    is_exchange_session,
    latest_completed_session,
)
from prism_core.feedback.outcomes import (
    OutcomeRecord,
    OutcomeRepository,
    OutcomeState,
    StoredOutcome,
)
from prism_core.feedback.repository import AppendDisposition, _utc_text, canonical_json
from prism_core.policy.proposal_validator import ProposalValidationStatus
from prism_core.storage.database import transaction
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


class ProcessQuality(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"


class PriceBasis(str, Enum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"


class MarketOutcomeMeasurementStatus(str, Enum):
    PENDING = "PENDING"


@dataclass(frozen=True)
class ProcessQualityRecord:
    process_event_id: str
    proposal_record_id: str
    market: Market
    security_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    data_quality: ProcessQuality
    evidence_quality: ProcessQuality
    predicate_quality: ProcessQuality
    validator_quality: ProcessQuality
    evidence_ids: tuple[str, ...]
    timing: ObservationTime
    revision: int = 0


@dataclass(frozen=True)
class MarketOutcomeInput:
    outcome_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    horizon_sessions: int
    revision: int
    decision_session: date
    completed_sessions: tuple[date, ...]
    decision_close: Decimal
    completed_close: Decimal
    price_basis: PriceBasis
    source_snapshot_ids: tuple[str, ...]
    timing: ObservationTime
    config_version: str
    code_version: str
    schema_version: str


@dataclass(frozen=True)
class PendingMarketOutcomeInput:
    outcome_event_id: str
    proposal_record_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    required_session_count: int
    completed_sessions: tuple[date, ...]
    next_eligible_session: date
    timing: ObservationTime
    config_version: str
    code_version: str
    schema_version: str
    completed_session_snapshot_ids: tuple[str, ...] = ()
    revision: int = 0


class ProcessQualityRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("foreign-key enforcement must be enabled")
        self._connection = connection

    def append(self, record: ProcessQualityRecord) -> AppendDisposition:
        _validate_process(record)
        parent = self._connection.execute(
            """
            SELECT p.strategy_id, p.strategy_version, p.as_of_at,
                   d.market, d.security_id, d.evidence_refs_json
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d
              ON d.decision_snapshot_id = p.decision_snapshot_id
            WHERE p.proposal_record_id = ?
            """,
            (record.proposal_record_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist")
        if parent[:2] != (record.strategy_id.value, record.strategy_version.value):
            raise ValueError("strategy mismatch")
        if parent[3:5] != (record.market.value, record.security_id):
            raise ValueError("market or security mismatch")
        if _utc_text(record.timing.available_at) < parent[2]:
            raise ValueError("process outcome cannot precede the decision boundary")
        if not set(record.evidence_ids) <= set(json.loads(parent[5])):
            raise ValueError("process outcome cites evidence outside the decision snapshot")
        semantic = {
            key: value
            for key, value in record.__dict__.items()
            if key != "timing"
        } | {
            "observed_at": record.timing.observed_at,
            "available_at": record.timing.available_at,
            "as_of_at": record.timing.as_of_date,
        }
        content_hash = "sha256:" + hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest()
        natural = (record.proposal_record_id, record.revision)
        with transaction(self._connection):
            existing = self._connection.execute(
                "SELECT content_hash FROM process_quality_outcomes "
                "WHERE proposal_record_id = ? AND revision = ?",
                natural,
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("process outcome identity has divergent content")
                return AppendDisposition.DUPLICATE
            if record.revision:
                previous = self._connection.execute(
                    "SELECT max(revision) FROM process_quality_outcomes "
                    "WHERE proposal_record_id = ?",
                    (record.proposal_record_id,),
                ).fetchone()[0]
                if previous is None or record.revision != previous + 1:
                    raise ValueError("process corrections must append the next revision")
            self._connection.execute(
                "INSERT INTO process_quality_outcomes VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.process_event_id,
                    record.proposal_record_id,
                    record.market.value,
                    record.security_id,
                    record.strategy_id.value,
                    record.strategy_version.value,
                    record.revision,
                    record.data_quality.value,
                    record.evidence_quality.value,
                    record.predicate_quality.value,
                    record.validator_quality.value,
                    canonical_json(record.evidence_ids),
                    _utc_text(record.timing.observed_at),
                    _utc_text(record.timing.available_at),
                    _utc_text(record.timing.ingested_at),
                    _utc_text(record.timing.as_of_date),
                    content_hash,
                ),
            )
        return AppendDisposition.INSERTED

    def records_as_of(
        self,
        *,
        market: Market,
        security_id: str,
        strategy_id: StrategyId,
        strategy_version: StrategyVersion,
        as_of: datetime,
    ) -> tuple[ProcessQualityRecord, ...]:
        boundary = _utc_text(as_of)
        rows = self._connection.execute(
            """
            SELECT q.* FROM process_quality_outcomes AS q
            JOIN (
                SELECT proposal_record_id, max(revision) AS revision
                FROM process_quality_outcomes
                WHERE market = ? AND security_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND available_at <= ? AND as_of_at <= ?
                GROUP BY proposal_record_id
            ) latest ON latest.proposal_record_id = q.proposal_record_id
                    AND latest.revision = q.revision
            ORDER BY q.available_at, q.proposal_record_id
            """,
            (
                market.value,
                security_id,
                strategy_id.value,
                strategy_version.value,
                boundary,
                boundary,
            ),
        ).fetchall()
        return tuple(_stored_process(row) for row in rows)


class MarketOutcomeService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._repository = OutcomeRepository(connection)

    def record(self, item: MarketOutcomeInput) -> AppendDisposition:
        _validate_market_outcome(item)
        parent = self._connection.execute(
            """
            SELECT p.validation_status, p.proposed_decision, p.as_of_at,
                   d.data_snapshot_id, d.market
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d
              ON d.decision_snapshot_id = p.decision_snapshot_id
            WHERE p.proposal_record_id = ? AND p.strategy_id = ?
              AND p.strategy_version = ?
            """,
            (
                item.proposal_record_id,
                item.strategy_id.value,
                item.strategy_version.value,
            ),
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist or strategy mismatch")
        decision_boundary = datetime.fromisoformat(parent[2])
        if item.decision_session != decision_boundary.date():
            raise ValueError("decision session must equal the original decision boundary")
        if len(item.completed_sessions) != item.horizon_sessions:
            raise ValueError("completed horizon does not match strategy horizon")
        exchange_market = _exchange_market(Market(parent[4]))
        expected_sessions = _next_exchange_sessions(
            exchange_market,
            after=item.decision_session,
            count=item.horizon_sessions,
        )
        if item.completed_sessions != expected_sessions:
            raise ValueError(
                "completed horizon must contain consecutive official exchange sessions"
            )
        if any(
            current <= previous
            for previous, current in zip(
                (item.decision_session, *item.completed_sessions[:-1]),
                item.completed_sessions,
            )
        ):
            raise ValueError("completed horizon sessions must be strictly increasing")
        if len(item.source_snapshot_ids) != item.horizon_sessions + 1:
            raise ValueError("source snapshot evidence must cover every horizon session")
        if item.source_snapshot_ids[0] != parent[3]:
            raise ValueError("decision close must use the original decision data snapshot")
        if item.timing.available_at <= decision_boundary:
            raise ValueError("market outcome must be available after the decision boundary")
        completed_session = item.completed_sessions[-1]
        if completed_session > latest_completed_session(
            exchange_market, item.timing.observed_at
        ):
            raise ValueError(
                "market outcome session was not completed by the observation boundary"
            )
        forward_return = item.completed_close / item.decision_close - Decimal("1")
        payload = {
            "decision_session": item.decision_session.isoformat(),
            "completed_session": completed_session.isoformat(),
            "completed_sessions": tuple(
                session.isoformat() for session in item.completed_sessions
            ),
            "completed_sessions_elapsed": len(item.completed_sessions),
            "decision_close": item.decision_close,
            "completed_close": item.completed_close,
            "price_basis": item.price_basis,
            "forward_return": forward_return,
            "source_snapshot_ids": item.source_snapshot_ids,
        }
        if parent[0] == ProposalValidationStatus.REJECTED.value:
            state = OutcomeState.REJECTED
        elif parent[1] == "NO_ENTRY":
            state = OutcomeState.NO_ENTRY
        else:
            state = OutcomeState.ELIGIBLE_NOT_EXECUTED
        return self._repository.append(
            OutcomeRecord(
                outcome_event_id=item.outcome_event_id,
                proposal_record_id=item.proposal_record_id,
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
                horizon_sessions=item.horizon_sessions,
                revision=item.revision,
                outcome_state=state,
                quality=DataQualityStatus.FRESH,
                outcome_payload=payload,
                timing=item.timing,
                config_version=item.config_version,
                code_version=item.code_version,
                schema_version=item.schema_version,
            )
        )

    def record_pending(self, item: PendingMarketOutcomeInput) -> AppendDisposition:
        _validate_pending_market_outcome(item)
        parent = self._connection.execute(
            """
            SELECT p.as_of_at, d.data_snapshot_id, d.market
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d
              ON d.decision_snapshot_id = p.decision_snapshot_id
            WHERE p.proposal_record_id = ? AND p.strategy_id = ?
              AND p.strategy_version = ?
            """,
            (
                item.proposal_record_id,
                item.strategy_id.value,
                item.strategy_version.value,
            ),
        ).fetchone()
        if parent is None:
            raise ValueError("proposal does not exist or strategy mismatch")
        decision_boundary = datetime.fromisoformat(parent[0])
        observed_count = len(item.completed_sessions)
        if observed_count >= item.required_session_count:
            raise ValueError("pending observed sessions must remain below required sessions")
        exchange_market = _exchange_market(Market(parent[2]))
        expected_sessions = _next_exchange_sessions(
            exchange_market,
            after=decision_boundary.date(),
            count=item.required_session_count,
        )
        if item.completed_sessions != expected_sessions[:observed_count]:
            raise ValueError(
                "pending observations must contain consecutive official exchange sessions"
            )
        if item.next_eligible_session != expected_sessions[-1]:
            raise ValueError(
                "next eligible session must equal the remaining official horizon boundary"
            )
        if any(
            current <= previous
            for previous, current in zip(
                (decision_boundary.date(), *item.completed_sessions[:-1]),
                item.completed_sessions,
            )
        ):
            raise ValueError("completed sessions must be strictly after the decision")
        latest_boundary = (
            item.completed_sessions[-1]
            if item.completed_sessions
            else decision_boundary.date()
        )
        if item.next_eligible_session <= latest_boundary:
            raise ValueError("next eligible session must follow observed sessions")
        if (
            item.completed_sessions
            and item.completed_sessions[-1]
            > latest_completed_session(exchange_market, item.timing.observed_at)
        ):
            raise ValueError(
                "pending outcome session was not completed by the observation boundary"
            )
        source_snapshot_ids = (parent[1], *item.completed_session_snapshot_ids)
        return self._repository.append(
            OutcomeRecord(
                outcome_event_id=item.outcome_event_id,
                proposal_record_id=item.proposal_record_id,
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
                horizon_sessions=item.required_session_count,
                revision=item.revision,
                outcome_state=OutcomeState.UNKNOWN,
                quality=DataQualityStatus.FRESH,
                outcome_payload={
                    "measurement_reason": "NOT_YET_MEASURABLE",
                    "measurement_status": MarketOutcomeMeasurementStatus.PENDING,
                    "next_eligible_session": item.next_eligible_session.isoformat(),
                    "observed_session_count": observed_count,
                    "required_session_count": item.required_session_count,
                    "source_snapshot_ids": source_snapshot_ids,
                },
                timing=item.timing,
                config_version=item.config_version,
                code_version=item.code_version,
                schema_version=item.schema_version,
            )
        )


def candidate_market_outcomes_as_of(
    connection: sqlite3.Connection,
    *,
    market: Market,
    security_id: str,
    strategy_id: StrategyId,
    strategy_version: StrategyVersion,
    as_of: datetime,
) -> tuple[StoredOutcome, ...]:
    proposal_ids = {
        row[0]
        for row in connection.execute(
            """
            SELECT p.proposal_record_id
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d ON d.decision_snapshot_id = p.decision_snapshot_id
            WHERE d.market = ? AND d.security_id = ? AND p.strategy_id = ?
              AND p.strategy_version = ?
            """,
            (market.value, security_id, strategy_id.value, strategy_version.value),
        )
    }
    return tuple(
        item
        for item in OutcomeRepository(connection).outcomes_as_of(
            as_of, strategy_id=strategy_id
        )
        if item.strategy_version == strategy_version
        and item.proposal_record_id in proposal_ids
    )


def _validate_process(record: ProcessQualityRecord) -> None:
    if not isinstance(record, ProcessQualityRecord):
        raise TypeError("record must be ProcessQualityRecord")
    for label in ("process_event_id", "proposal_record_id", "security_id"):
        value = getattr(record, label)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
    try:
        UUID(record.security_id)
    except ValueError as exc:
        raise ValueError("security_id must be a UUID stable identity") from exc
    if any(
        not isinstance(getattr(record, label), ProcessQuality)
        for label in ("data_quality", "evidence_quality", "predicate_quality", "validator_quality")
    ):
        raise TypeError("process quality fields must be ProcessQuality")
    if not record.evidence_ids or len(set(record.evidence_ids)) != len(record.evidence_ids):
        raise ValueError("evidence_ids must be non-empty and unique")
    if type(record.revision) is not int or record.revision < 0:
        raise ValueError("revision must be non-negative")


def _validate_market_outcome(item: MarketOutcomeInput) -> None:
    if not isinstance(item, MarketOutcomeInput):
        raise TypeError("item must be MarketOutcomeInput")
    if not isinstance(item.price_basis, PriceBasis):
        raise TypeError("price_basis must be PriceBasis")
    if any(
        not isinstance(value, Decimal) or not value.is_finite() or value <= 0
        for value in (item.decision_close, item.completed_close)
    ):
        raise ValueError("market outcome prices must be positive finite Decimals")
    if not item.source_snapshot_ids or len(set(item.source_snapshot_ids)) != len(item.source_snapshot_ids):
        raise ValueError("source_snapshot_ids must be non-empty and unique")
    if not isinstance(item.completed_sessions, tuple) or any(
        not isinstance(session, date) for session in item.completed_sessions
    ):
        raise TypeError("completed_sessions must contain date values")


def _validate_pending_market_outcome(item: PendingMarketOutcomeInput) -> None:
    if not isinstance(item, PendingMarketOutcomeInput):
        raise TypeError("item must be PendingMarketOutcomeInput")
    if not isinstance(item.next_eligible_session, date):
        raise TypeError("next_eligible_session must be date")
    if not isinstance(item.completed_sessions, tuple) or any(
        not isinstance(session, date) for session in item.completed_sessions
    ):
        raise TypeError("completed_sessions must contain date values")
    if len(item.completed_session_snapshot_ids) != len(item.completed_sessions):
        raise ValueError("completed session evidence must cover every observed session")
    if len(set(item.completed_session_snapshot_ids)) != len(
        item.completed_session_snapshot_ids
    ):
        raise ValueError("completed session snapshot identities must be unique")


def _exchange_market(market: Market) -> ExchangeMarket:
    return ExchangeMarket.KRX if market is Market.KR else ExchangeMarket.NYSE


def _next_exchange_sessions(
    market: ExchangeMarket,
    *,
    after: date,
    count: int,
) -> tuple[date, ...]:
    sessions: list[date] = []
    candidate = after
    for _ in range(730):
        candidate += timedelta(days=1)
        if is_exchange_session(market, candidate):
            sessions.append(candidate)
            if len(sessions) == count:
                return tuple(sessions)
    raise ValueError("exchange calendar cannot resolve the requested outcome horizon")


def _stored_process(row: tuple[object, ...]) -> ProcessQualityRecord:
    return ProcessQualityRecord(
        process_event_id=str(row[0]),
        proposal_record_id=str(row[1]),
        market=Market(row[2]),
        security_id=str(row[3]),
        strategy_id=StrategyId(row[4]),
        strategy_version=StrategyVersion(str(row[5])),
        revision=int(str(row[6])),
        data_quality=ProcessQuality(row[7]),
        evidence_quality=ProcessQuality(row[8]),
        predicate_quality=ProcessQuality(row[9]),
        validator_quality=ProcessQuality(row[10]),
        evidence_ids=tuple(json.loads(str(row[11]))),
        timing=ObservationTime(
            observed_at=datetime.fromisoformat(str(row[12])),
            available_at=datetime.fromisoformat(str(row[13])),
            ingested_at=datetime.fromisoformat(str(row[14])),
            as_of_date=datetime.fromisoformat(str(row[15])),
        ),
    )
