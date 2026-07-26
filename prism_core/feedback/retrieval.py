"""Read-only, PIT-correct SHADOW lesson retrieval for offline evaluation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from prism_core.feedback.lessons import LessonStatus
from prism_core.feedback.repository import _utc_text
from prism_core.strategies.contracts import StrategyId, StrategyVersion


@dataclass(frozen=True)
class LessonInfluence:
    """Explicit proof that Phase 1 evaluation lessons cannot affect decisions."""

    score_delta: Decimal = Decimal("0")
    policy_effect: bool = False
    proposal_effect: bool = False


@dataclass(frozen=True)
class EvaluationLesson:
    lesson_id: str
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    status: LessonStatus
    condition: str
    tentative_action: str
    market_scope: tuple[str, ...]
    sector_scope: tuple[str, ...]
    regime_scope: tuple[str, ...]
    uncertainty: Decimal
    candidate_event_id: str
    status_event_id: str
    influence: LessonInfluence


@dataclass(frozen=True)
class EvaluationLessonSet:
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    as_of: datetime
    lessons: tuple[EvaluationLesson, ...]


def retrieve_evaluation_lessons(
    connection: sqlite3.Connection,
    *,
    strategy_id: StrategyId,
    strategy_version: StrategyVersion,
    as_of: datetime,
) -> EvaluationLessonSet:
    """Return only the latest PIT-visible SHADOW revision for an exact strategy version."""

    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    if not isinstance(strategy_id, StrategyId):
        raise TypeError("strategy_id must be StrategyId")
    if not isinstance(strategy_version, StrategyVersion):
        raise TypeError("strategy_version must be StrategyVersion")
    boundary = _utc_text(as_of)
    rows = connection.execute(
        """
        SELECT current.lesson_candidate_event_id, current.lesson_id,
               current.candidate_json
        FROM lesson_candidates AS current
        JOIN (
            SELECT lesson_id, max(revision) AS revision
            FROM lesson_candidates
            WHERE strategy_id = ? AND strategy_version = ?
              AND available_at <= ? AND as_of_at <= ?
            GROUP BY lesson_id
        ) AS latest
          ON latest.lesson_id = current.lesson_id
         AND latest.revision = current.revision
        WHERE current.strategy_id = ? AND current.strategy_version = ?
          AND current.status = 'SHADOW'
        ORDER BY current.lesson_id
        """,
        (
            strategy_id.value,
            strategy_version.value,
            boundary,
            boundary,
            strategy_id.value,
            strategy_version.value,
        ),
    ).fetchall()
    lessons = []
    for status_event_id, lesson_id, status_json in rows:
        status_payload = json.loads(status_json)
        candidate_event_id, candidate_payload = _resolve_candidate_payload(
            connection,
            status_event_id,
            status_payload,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            boundary=boundary,
        )
        lessons.append(
            EvaluationLesson(
                lesson_id=lesson_id,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                status=LessonStatus.SHADOW,
                condition=candidate_payload["condition"],
                tentative_action=candidate_payload["tentative_action"],
                market_scope=tuple(candidate_payload["market_scope"]),
                sector_scope=tuple(candidate_payload["sector_scope"]),
                regime_scope=tuple(candidate_payload["regime_scope"]),
                uncertainty=Decimal(candidate_payload["uncertainty"]),
                candidate_event_id=candidate_event_id,
                status_event_id=status_event_id,
                influence=LessonInfluence(),
            )
        )
    return EvaluationLessonSet(strategy_id, strategy_version, as_of, tuple(lessons))


def _resolve_candidate_payload(
    connection: sqlite3.Connection,
    event_id: str,
    payload: dict,
    *,
    strategy_id: StrategyId,
    strategy_version: StrategyVersion,
    boundary: str,
) -> tuple[str, dict]:
    visited = set()
    current_id = event_id
    current_payload = payload
    while current_payload.get("status") != LessonStatus.CANDIDATE.value:
        if current_id in visited:
            raise ValueError("lesson basis chain contains a cycle")
        visited.add(current_id)
        basis_id = current_payload.get("basis_candidate_event_id")
        if not isinstance(basis_id, str) or not basis_id:
            raise ValueError("lesson status revision has no candidate basis")
        row = connection.execute(
            "SELECT candidate_json FROM lesson_candidates "
            "WHERE lesson_candidate_event_id = ? AND strategy_id = ? "
            "AND strategy_version = ? AND available_at <= ? AND as_of_at <= ?",
            (
                basis_id,
                strategy_id.value,
                strategy_version.value,
                boundary,
                boundary,
            ),
        ).fetchone()
        if row is None:
            raise ValueError("lesson candidate basis is not PIT-visible")
        current_id = basis_id
        current_payload = json.loads(row[0])
    return current_id, current_payload
