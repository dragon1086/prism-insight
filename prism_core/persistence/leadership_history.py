"""Append-only point-in-time leadership history for daily candidate feedback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from prism_core.candidates import CandidateChannel
from prism_core.data.contracts import ObservationTime
from prism_core.feedback.repository import AppendDisposition, _utc_text, canonical_json
from prism_core.reporting.leadership_tracking import (
    High52WeekState,
    MomentumState,
    PeakState,
)
from prism_core.storage.database import transaction
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


class GroupLeadershipState(str, Enum):
    LEADING = "LEADING"
    EMERGING = "EMERGING"
    NARROW = "NARROW"
    FADING = "FADING"


@dataclass(frozen=True)
class LeadershipHistoryRecord:
    leadership_event_id: str
    market: Market
    security_id: str
    provider_symbol: str
    session_date: date
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    candidate_channels: tuple[CandidateChannel, ...]
    relative_strength_5d: Decimal | None
    relative_strength_20d: Decimal | None
    relative_strength_60d: Decimal | None
    high_52_week_state: High52WeekState
    high_52_week_distance_pct: Decimal | None
    momentum_state: MomentumState
    momentum_score: Decimal | None
    peak_state: PeakState
    peak_score: Decimal | None
    group_id: str
    group_state: GroupLeadershipState
    source_snapshot_id: str
    evidence_ids: tuple[str, ...]
    timing: ObservationTime
    revision: int = 0


class LeadershipHistoryRepository:
    """Append immutable date/strategy rows and expose PIT-bounded history."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("foreign-key enforcement must be enabled")
        self._connection = connection

    def append(self, record: LeadershipHistoryRecord) -> AppendDisposition:
        _validate(record)
        semantic = _semantic(record)
        content_hash = "sha256:" + hashlib.sha256(
            canonical_json(semantic).encode("utf-8")
        ).hexdigest()
        natural = (
            record.market.value,
            record.security_id,
            record.session_date.isoformat(),
            record.strategy_id.value,
            record.strategy_version.value,
            record.revision,
        )
        with transaction(self._connection):
            by_event = self._connection.execute(
                "SELECT content_hash FROM leadership_history_events "
                "WHERE leadership_event_id = ?",
                (record.leadership_event_id,),
            ).fetchone()
            if by_event is not None:
                if by_event[0] != content_hash:
                    raise ValueError("leadership event identity has divergent content")
                return AppendDisposition.DUPLICATE
            existing = self._connection.execute(
                "SELECT content_hash FROM leadership_history_events WHERE market = ? "
                "AND security_id = ? AND session_date = ? AND strategy_id = ? "
                "AND strategy_version = ? AND revision = ?",
                natural,
            ).fetchone()
            if existing is not None:
                if existing[0] != content_hash:
                    raise ValueError("leadership natural identity has divergent content")
                return AppendDisposition.DUPLICATE
            if record.revision:
                previous = self._connection.execute(
                    "SELECT max(revision) FROM leadership_history_events WHERE market = ? "
                    "AND security_id = ? AND session_date = ? AND strategy_id = ? "
                    "AND strategy_version = ?",
                    natural[:-1],
                ).fetchone()[0]
                if previous is None or record.revision != previous + 1:
                    raise ValueError("leadership corrections must append the next revision")
            self._connection.execute(
                "INSERT INTO leadership_history_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.leadership_event_id,
                    record.market.value,
                    record.security_id,
                    record.provider_symbol,
                    record.session_date.isoformat(),
                    record.strategy_id.value,
                    record.strategy_version.value,
                    record.revision,
                    canonical_json(record.candidate_channels),
                    _decimal(record.relative_strength_5d),
                    _decimal(record.relative_strength_20d),
                    _decimal(record.relative_strength_60d),
                    record.high_52_week_state.value,
                    _decimal(record.high_52_week_distance_pct),
                    record.momentum_state.value,
                    _decimal(record.momentum_score),
                    record.peak_state.value,
                    _decimal(record.peak_score),
                    record.group_id,
                    record.group_state.value,
                    record.source_snapshot_id,
                    canonical_json(record.evidence_ids),
                    _utc_text(record.timing.observed_at),
                    _utc_text(record.timing.available_at),
                    _utc_text(record.timing.ingested_at),
                    _utc_text(record.timing.as_of_date),
                    content_hash,
                ),
            )
        return AppendDisposition.INSERTED

    def history_as_of(
        self,
        *,
        market: Market,
        security_id: str,
        strategy_id: StrategyId,
        strategy_version: StrategyVersion,
        as_of: datetime,
    ) -> tuple[LeadershipHistoryRecord, ...]:
        _validate_key(market, security_id, strategy_id, strategy_version)
        boundary = _utc_text(as_of)
        rows = self._connection.execute(
            """
            SELECT e.* FROM leadership_history_events AS e
            JOIN (
                SELECT market, security_id, session_date, strategy_id,
                       strategy_version, max(revision) AS revision
                FROM leadership_history_events
                WHERE market = ? AND security_id = ? AND strategy_id = ?
                  AND strategy_version = ? AND available_at <= ? AND as_of_at <= ?
                GROUP BY market, security_id, session_date, strategy_id, strategy_version
            ) AS latest
              ON latest.market = e.market AND latest.security_id = e.security_id
             AND latest.session_date = e.session_date
             AND latest.strategy_id = e.strategy_id
             AND latest.strategy_version = e.strategy_version
             AND latest.revision = e.revision
            ORDER BY e.session_date, e.revision
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
        return tuple(_stored(row) for row in rows)


def _semantic(record: LeadershipHistoryRecord) -> dict[str, object]:
    payload: dict[str, object] = {
        key: value
        for key, value in record.__dict__.items()
        if key not in {"timing", "session_date"}
    }
    payload.update({
        "session_date": record.session_date.isoformat(),
        "observed_at": record.timing.observed_at,
        "available_at": record.timing.available_at,
        "as_of_at": record.timing.as_of_date,
    })
    return payload


def _validate(record: LeadershipHistoryRecord) -> None:
    if not isinstance(record, LeadershipHistoryRecord):
        raise TypeError("record must be LeadershipHistoryRecord")
    _validate_key(record.market, record.security_id, record.strategy_id, record.strategy_version)
    for label in ("leadership_event_id", "provider_symbol", "group_id", "source_snapshot_id"):
        value = getattr(record, label)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
    if not isinstance(record.session_date, date):
        raise TypeError("session_date must be date")
    if type(record.revision) is not int or record.revision < 0:
        raise ValueError("revision must be non-negative")
    if not record.candidate_channels or len(set(record.candidate_channels)) != len(record.candidate_channels):
        raise ValueError("candidate_channels must be non-empty and unique")
    if any(not isinstance(item, CandidateChannel) for item in record.candidate_channels):
        raise TypeError("candidate_channels must contain CandidateChannel")
    if not record.evidence_ids or len(set(record.evidence_ids)) != len(record.evidence_ids):
        raise ValueError("evidence_ids must be non-empty and unique")
    if any(not isinstance(item, str) or not item.strip() for item in record.evidence_ids):
        raise ValueError("evidence_ids must contain non-empty strings")
    for label in (
        "relative_strength_5d", "relative_strength_20d", "relative_strength_60d",
        "high_52_week_distance_pct", "momentum_score", "peak_score",
    ):
        value = getattr(record, label)
        if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
            raise ValueError(f"{label} must be a finite Decimal or None")
    if not isinstance(record.high_52_week_state, High52WeekState):
        raise TypeError("high_52_week_state must be High52WeekState")
    if not isinstance(record.momentum_state, MomentumState):
        raise TypeError("momentum_state must be MomentumState")
    if not isinstance(record.peak_state, PeakState):
        raise TypeError("peak_state must be PeakState")
    if not isinstance(record.group_state, GroupLeadershipState):
        raise TypeError("group_state must be GroupLeadershipState")
    if not isinstance(record.timing, ObservationTime):
        raise TypeError("timing must be ObservationTime")
    if record.timing.available_at.date() < record.session_date:
        raise ValueError("leadership cannot be available before its session date")


def _validate_key(
    market: Market,
    security_id: str,
    strategy_id: StrategyId,
    strategy_version: StrategyVersion,
) -> None:
    if not isinstance(market, Market):
        raise TypeError("market must be Market")
    try:
        UUID(security_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("security_id must be a UUID string") from exc
    if not isinstance(strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _stored(row: tuple[object, ...]) -> LeadershipHistoryRecord:
    timing = ObservationTime(
        observed_at=datetime.fromisoformat(str(row[22])),
        available_at=datetime.fromisoformat(str(row[23])),
        ingested_at=datetime.fromisoformat(str(row[24])),
        as_of_date=datetime.fromisoformat(str(row[25])),
    )
    return LeadershipHistoryRecord(
        leadership_event_id=str(row[0]),
        market=Market(row[1]),
        security_id=str(row[2]),
        provider_symbol=str(row[3]),
        session_date=date.fromisoformat(str(row[4])),
        strategy_id=StrategyId(row[5]),
        strategy_version=StrategyVersion(str(row[6])),
        revision=int(str(row[7])),
        candidate_channels=tuple(CandidateChannel(item) for item in json.loads(str(row[8]))),
        relative_strength_5d=None if row[9] is None else Decimal(str(row[9])),
        relative_strength_20d=None if row[10] is None else Decimal(str(row[10])),
        relative_strength_60d=None if row[11] is None else Decimal(str(row[11])),
        high_52_week_state=High52WeekState(row[12]),
        high_52_week_distance_pct=None if row[13] is None else Decimal(str(row[13])),
        momentum_state=MomentumState(row[14]),
        momentum_score=None if row[15] is None else Decimal(str(row[15])),
        peak_state=PeakState(row[16]),
        peak_score=None if row[17] is None else Decimal(str(row[17])),
        group_id=str(row[18]),
        group_state=GroupLeadershipState(row[19]),
        source_snapshot_id=str(row[20]),
        evidence_ids=tuple(json.loads(str(row[21]))),
        timing=timing,
    )
