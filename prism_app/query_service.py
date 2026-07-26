"""Read-only application queries over authoritative research repositories."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from prism_core.feedback.outcomes import OutcomeRepository, StoredOutcome
from prism_core.feedback.repository import FeedbackRepository, StoredProposal
from prism_core.feedback.retrieval import (
    EvaluationLessonSet,
    retrieve_evaluation_lessons,
)
from prism_core.reporting.leadership_tracking import (
    LeadershipRepository,
    StoredLeadershipRun,
)
from prism_core.strategies.contracts import StrategyId, StrategyVersion


@dataclass(frozen=True)
class StrategyEvaluationView:
    """Separate proposal history from inert SHADOW evaluation material."""

    proposals: tuple[StoredProposal, ...]
    shadow_evaluation: EvaluationLessonSet


class QueryService:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection
        self._feedback = FeedbackRepository(connection)
        self._outcomes = OutcomeRepository(connection)
        self._leadership = LeadershipRepository(connection)

    def leadership(self, snapshot_id: str) -> StoredLeadershipRun:
        return self._leadership.read(snapshot_id)

    def strategy_evaluation(
        self,
        *,
        as_of: datetime,
        strategy_id: StrategyId,
        strategy_version: StrategyVersion,
    ) -> StrategyEvaluationView:
        proposals = tuple(
            item
            for item in self._feedback.proposals_as_of(
                as_of,
                strategy_id=strategy_id,
            )
            if item.strategy_version == strategy_version
        )
        lessons = retrieve_evaluation_lessons(
            self._connection,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            as_of=as_of,
        )
        return StrategyEvaluationView(
            proposals=proposals,
            shadow_evaluation=lessons,
        )

    def outcomes_as_of(
        self,
        *,
        as_of: datetime,
        strategy_id: StrategyId,
        strategy_version: StrategyVersion,
    ) -> tuple[StoredOutcome, ...]:
        return tuple(
            item
            for item in self._outcomes.outcomes_as_of(
                as_of,
                strategy_id=strategy_id,
            )
            if item.strategy_version == strategy_version
        )
