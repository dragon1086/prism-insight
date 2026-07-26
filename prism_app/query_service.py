"""Read-only application queries over authoritative research repositories."""

from __future__ import annotations

import re
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
    MARKET_OBSERVATION_KIND,
    PROVIDER as LEADERSHIP_PROVIDER,
    LeadershipRepository,
    StoredLeadershipRun,
)
from prism_core.reporting.daily import build_daily_report
from prism_core.reporting.models import DailyReport, LeadingSector
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


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


@dataclass(frozen=True)
class StoredReportSearchHit:
    """A persisted report match with its point-in-time identity."""

    report_id: str
    snapshot_id: str
    report_kind: str
    as_of: datetime
    content: str


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

    def latest_leadership(self, market: Market) -> StoredLeadershipRun:
        """Read the latest available persisted leadership run for one market."""

        if not isinstance(market, Market):
            raise TypeError("market must be a strategy Market")
        row = self._connection.execute(
            "SELECT o.snapshot_id FROM observations AS o "
            "JOIN market_snapshots AS s USING (snapshot_id) "
            "WHERE s.market = ? AND o.observation_kind = ? AND o.provider = ? "
            "ORDER BY o.available_at DESC, o.ingested_at DESC, o.snapshot_id DESC "
            "LIMIT 1",
            (market.value, MARKET_OBSERVATION_KIND, LEADERSHIP_PROVIDER),
        ).fetchone()
        if row is None:
            raise KeyError(f"stored leadership is unavailable for {market.value}")
        return self.leadership(row[0])

    def search_reports(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> tuple[StoredReportSearchHit, ...]:
        """Search persisted report text with bounded parameterized read-only SQL."""

        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("report query must contain 1 to 500 characters")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("report search limit must be between 1 and 10")
        if any(character in query for character in ("%", "'", ";")) or "--" in query:
            return ()
        tokens = tuple(
            dict.fromkeys(
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9가-힣.-]{3,}", query)
                if token.casefold()
                not in {"and", "did", "how", "the", "what", "why", "with"}
            )
        )[:8]
        if not tokens:
            return ()
        predicates = " AND ".join("LOWER(r.content) LIKE ?" for _ in tokens)
        rows = self._connection.execute(
            "SELECT r.report_id, r.snapshot_id, r.report_kind, "
            "s.as_of_date, r.content FROM reports AS r "
            "JOIN market_snapshots AS s USING (snapshot_id) "
            f"WHERE {predicates} "
            "ORDER BY s.as_of_date DESC, r.created_at DESC, r.report_id DESC "
            "LIMIT ?",
            (*tuple(f"%{token}%" for token in tokens), limit),
        ).fetchall()
        return tuple(
            StoredReportSearchHit(
                report_id=row[0],
                snapshot_id=row[1],
                report_kind=row[2],
                as_of=datetime.fromisoformat(row[3]),
                content=row[4],
            )
            for row in rows
        )

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
