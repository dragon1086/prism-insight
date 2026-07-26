from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from prism_app.outcome_tracker import OutcomeTracker
from prism_app.query_service import QueryService
from prism_core.data.contracts import DataQualityStatus
from prism_core.feedback.outcomes import OutcomeRecord, OutcomeState
from prism_core.feedback.lessons import (
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.feedback.repository import FeedbackRepository
from prism_core.reporting.leadership_tracking import LeadershipRepository
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId
from tests.feedback.test_lesson_lifecycle import candidate, provenance
from tests.feedback.test_repository import (
    AS_OF,
    dispositions,
    proposal_record,
    run_record,
    snapshot_record,
    strategy_version,
    timing,
)
from tests.app.test_daily_pipeline import _leadership_snapshot


def test_query_service_keeps_shadow_lessons_in_a_separate_inert_evaluation_view(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        version = strategy_version(StrategyId.SWING_V1)
        lifecycle = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        lifecycle.create_candidate(candidate())
        for role in LessonEvidenceRole:
            lifecycle.append_evidence(
                LessonEvidence(
                    f"query-{role.value.lower()}",
                    "lesson-candidate-1",
                    StrategyId.SWING_V1,
                    version,
                    role,
                    "proposal-record-1",
                    timing(available_delta=timedelta(days=2)),
                )
            )
        lifecycle.transition(
            LessonTransition(
                "query-shadow-1",
                "lesson-1",
                StrategyId.SWING_V1,
                version,
                1,
                LessonStatus.SHADOW,
                "lesson-candidate-1",
                "evaluate only",
                provenance(),
                timing(available_delta=timedelta(days=3)),
            )
        )

        result = QueryService(connection).strategy_evaluation(
            as_of=AS_OF + timedelta(days=3, hours=1),
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
        )

    assert len(result.proposals) == 1
    assert len(result.shadow_evaluation.lessons) == 1
    lesson = result.shadow_evaluation.lessons[0]
    assert lesson.status is LessonStatus.SHADOW
    assert lesson.influence.score_delta == Decimal("0")
    assert lesson.influence.policy_effect is False
    assert lesson.influence.proposal_effect is False
    assert not hasattr(result, "proposal_inputs")


def test_outcome_tracker_appends_and_query_service_reads_exact_strategy_version(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        version = strategy_version(StrategyId.SWING_V1)
        record = OutcomeRecord(
            outcome_event_id="app-outcome-1",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
            horizon_sessions=5,
            revision=0,
            outcome_state=OutcomeState.NO_ENTRY,
            quality=DataQualityStatus.FRESH,
            outcome_payload={"reason": "proposal remained no-entry"},
            timing=timing(available_delta=timedelta(days=5)),
            config_version="config.v1",
            code_version="abc123",
            schema_version="outcome.v1",
        )
        tracker = OutcomeTracker(connection)

        first = tracker.record(record)
        duplicate = tracker.record(record)
        outcomes = QueryService(connection).outcomes_as_of(
            as_of=AS_OF + timedelta(days=6),
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
        )

    assert first.value == "INSERTED"
    assert duplicate.value == "DUPLICATE"
    assert len(outcomes) == 1
    assert outcomes[0].outcome_event_id == "app-outcome-1"
    assert outcomes[0].strategy_version == version


def test_query_service_reads_persisted_leadership_report(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        ingested = LeadershipRepository(connection).ingest(_leadership_snapshot())

        stored = QueryService(connection).leadership(ingested.snapshot_id)

    assert stored.snapshot.run_id == "kr-20200726-19"
    assert stored.report_id == ingested.report_id
    assert stored.rendered_markdown == ingested.rendered_markdown
