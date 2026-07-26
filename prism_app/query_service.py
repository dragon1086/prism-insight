"""Read-only application queries over authoritative research repositories."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from prism_app.daily_pipeline import AppRunRepository
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
from prism_core.reporting.daily import build_daily_report
from prism_core.reporting.models import DailyReport, LeadingSector
from prism_core.strategies.contracts import StrategyId, StrategyVersion


class ReportUnavailableError(LookupError):
    """Raised when a required persisted report input cannot be read."""


@dataclass(frozen=True)
class StrategyEvaluationView:
    """Separate proposal history from inert SHADOW evaluation material."""

    proposals: tuple[StoredProposal, ...]
    shadow_evaluation: EvaluationLessonSet


@dataclass(frozen=True)
class WeeklyReportReadiness:
    """Visible readiness state while weekly persisted inputs are not defined."""

    available: bool
    missing_inputs: tuple[str, ...]
    reason: str


class QueryService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        run_repository: AppRunRepository | None = None,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection
        self._feedback = FeedbackRepository(connection)
        self._outcomes = OutcomeRepository(connection)
        self._leadership = LeadershipRepository(connection)
        self._run_repository = run_repository

    def leadership(self, snapshot_id: str) -> StoredLeadershipRun:
        return self._leadership.read(snapshot_id)

    def daily_report(
        self,
        *,
        job_key: str,
        leading_sectors: tuple[LeadingSector, ...],
    ) -> DailyReport:
        """Build a report only from persisted, PIT-bounded read-side records."""

        if self._run_repository is None:
            raise RuntimeError("daily analysis read repository is not configured")
        analysis = self._run_repository.get(job_key)
        if analysis is None:
            raise ReportUnavailableError(
                f"persisted daily analysis is unavailable: {job_key}"
            )
        try:
            leadership = self.leadership(analysis.leadership_snapshot_id)
        except KeyError as exc:
            raise ReportUnavailableError(
                "persisted leadership is unavailable: "
                f"{analysis.leadership_snapshot_id}"
            ) from exc
        evaluations = tuple(
            self.strategy_evaluation(
                as_of=analysis.evaluated_at,
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
            )
            for item in analysis.strategies
        )
        return build_daily_report(
            analysis=analysis,
            leadership=leadership,
            strategy_evaluations=evaluations,
            leading_sectors=leading_sectors,
        )

    def weekly_report_readiness(self) -> WeeklyReportReadiness:
        """Refuse to fabricate the not-yet-persisted weekly scenario read sides."""

        return WeeklyReportReadiness(
            available=False,
            missing_inputs=(
                "KR weekly scenario read side",
                "US weekly scenario read side",
            ),
            reason=(
                "weekly scenario, calendar, and context-board inputs have no "
                "persisted application read contract"
            ),
        )

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
