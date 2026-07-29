from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.feedback.lessons import (
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.feedback.repository import FeedbackRepository
from prism_core.feedback.retrieval import retrieve_evaluation_lessons
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion
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


def test_retrieval_is_pit_version_scoped_shadow_only_and_inert(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        version = strategy_version(StrategyId.SWING_V1)
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(candidate())
        for role in LessonEvidenceRole:
            service.append_evidence(
                LessonEvidence(
                    f"retrieval-{role.value.lower()}", "lesson-candidate-1",
                    StrategyId.SWING_V1, version, role, "proposal-record-1",
                    timing(available_delta=timedelta(days=2)),
                )
            )
        service.transition(
            LessonTransition(
                "lesson-shadow-1", "lesson-1", StrategyId.SWING_V1, version, 1,
                LessonStatus.SHADOW, "lesson-candidate-1", "evaluate", provenance(),
                timing(available_delta=timedelta(days=3)),
            )
        )
        service.transition(
            LessonTransition(
                "lesson-suspended-1", "lesson-1", StrategyId.SWING_V1, version, 2,
                LessonStatus.SUSPENDED, "lesson-shadow-1", "conflict", provenance(),
                timing(available_delta=timedelta(days=4)),
            )
        )

        before_suspension = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
            as_of=AS_OF + timedelta(days=3, hours=1),
        )
        after_suspension = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
            as_of=AS_OF + timedelta(days=4, hours=1),
        )
        wrong_version = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=StrategyVersion("swing-v9.9.9"),
            as_of=AS_OF + timedelta(days=3, hours=1),
        )
        exact_candidate = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=version,
            as_of=AS_OF + timedelta(days=3, hours=1),
            market=Market.US,
            security_id="00000000-0000-0000-0000-000000000102",
            regime="sideways",
        )
        with pytest.raises(ValueError, match="UUID"):
            retrieve_evaluation_lessons(
                connection,
                strategy_id=StrategyId.SWING_V1,
                strategy_version=version,
                as_of=AS_OF + timedelta(days=3, hours=1),
                market=Market.US,
                security_id="symbol-is-not-stable-identity",
                regime="sideways",
            )
        with pytest.raises(ValueError, match="requires the exact"):
            retrieve_evaluation_lessons(
                connection,
                strategy_id=StrategyId.SWING_V1,
                strategy_version=version,
                as_of=AS_OF + timedelta(days=3, hours=1),
                quant_score_version="SHADOW_SCORE_V1.SWING_V1",
            )

        assert len(before_suspension.lessons) == 1
        lesson = before_suspension.lessons[0]
        assert lesson.status is LessonStatus.SHADOW
        assert lesson.condition == "counter-evidence is incomplete"
        assert lesson.influence.score_delta == Decimal("0")
        assert lesson.influence.policy_effect is False
        assert lesson.influence.proposal_effect is False
        assert after_suspension.lessons == ()
        assert wrong_version.lessons == ()
        assert tuple(item.lesson_id for item in exact_candidate.lessons) == ("lesson-1",)


def test_retrieval_fails_closed_when_shadow_basis_is_not_pit_visible(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        version = strategy_version(StrategyId.SWING_V1)
        LessonLifecycleService(connection).create_candidate(
            replace(
                candidate(event_id="future-candidate"),
                timing=timing(available_delta=timedelta(days=10)),
            )
        )
        shadow_timing = timing(available_delta=timedelta(days=5))
        connection.execute(
            "INSERT INTO lesson_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "malformed-shadow", "lesson-1", StrategyId.SWING_V1.value,
                version.value, 1, LessonStatus.SHADOW.value,
                json.dumps(
                    {
                        "status": LessonStatus.SHADOW.value,
                        "basis_candidate_event_id": "future-candidate",
                    }
                ),
                shadow_timing.observed_at.isoformat(timespec="microseconds"),
                shadow_timing.available_at.isoformat(timespec="microseconds"),
                shadow_timing.ingested_at.isoformat(timespec="microseconds"),
                shadow_timing.as_of_date.isoformat(timespec="microseconds"),
                "malformed-fixture-hash",
            ),
        )

        with pytest.raises(ValueError, match="not PIT-visible"):
            retrieve_evaluation_lessons(
                connection,
                strategy_id=StrategyId.SWING_V1,
                strategy_version=version,
                as_of=AS_OF + timedelta(days=6),
            )
