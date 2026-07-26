from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.feedback.lessons import (
    LessonCandidate,
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.feedback.repository import AppendDisposition, FeedbackRepository
from prism_core.feedback.retrospective import FeedbackProvenance
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId
from tests.feedback.test_repository import (
    dispositions,
    proposal_record,
    run_record,
    snapshot_record,
    strategy_version,
    timing,
)


def provenance() -> FeedbackProvenance:
    return FeedbackProvenance(
        model_provider="fixture",
        model_id="lesson-model",
        model_version="2026-07-01",
        prompt_version="lesson-prompt.v1",
        config_version="lesson-config.v1",
        code_version="abc123",
        schema_version="feedback.v1",
    )


def candidate(*, event_id: str = "lesson-candidate-1", revision: int = 0) -> LessonCandidate:
    return LessonCandidate(
        lesson_candidate_event_id=event_id,
        lesson_id="lesson-1",
        strategy_id=StrategyId.SWING_V1,
        strategy_version=strategy_version(StrategyId.SWING_V1),
        revision=revision,
        market_scope=("US",),
        sector_scope=("technology",),
        regime_scope=("sideways",),
        condition="counter-evidence is incomplete",
        tentative_action="defer entry evaluation",
        uncertainty=Decimal("0.4"),
        provenance=provenance(),
        timing=timing(available_delta=timedelta(days=1)),
    )


def test_candidate_is_append_only_inactive_and_provenance_complete(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )

        result = LessonLifecycleService(connection).create_candidate(candidate())

        assert result is AppendDisposition.INSERTED
        row = connection.execute(
            "SELECT status, candidate_json FROM lesson_candidates"
        ).fetchone()
        assert row[0] == LessonStatus.CANDIDATE.value
        payload = json.loads(row[1])
        assert payload["activation_allowed"] is False
        assert payload["score_adjustment"] == 0
        assert payload["strategy_id"] == "SWING_V1"
        assert payload["strategy_version"] == "swing-v1.0.0"
        assert payload["provenance"]["prompt_version"] == "lesson-prompt.v1"


def test_candidate_correction_appends_revision_and_rejects_divergence(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        service = LessonLifecycleService(connection)
        service.create_candidate(candidate())
        correction = replace(
            candidate(),
            lesson_candidate_event_id="lesson-candidate-2",
            revision=1,
            condition="counter-evidence remains unresolved",
            timing=timing(available_delta=timedelta(days=2)),
        )

        assert service.revise_candidate(correction) is AppendDisposition.INSERTED
        assert connection.execute(
            "SELECT revision FROM lesson_candidates ORDER BY revision"
        ).fetchall() == [(0,), (1,)]
        with pytest.raises(ValueError, match="divergent"):
            service.revise_candidate(replace(correction, condition="different content"))


def test_candidate_to_shadow_requires_support_contra_and_time_separation(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        policy = LessonValidationPolicy(
            minimum_support_count=1,
            minimum_contra_count=1,
            minimum_distinct_proposals=1,
            minimum_separation=timedelta(days=10),
        )
        service = LessonLifecycleService(connection, policy=policy)
        service.create_candidate(candidate())
        service.append_evidence(
            LessonEvidence(
                lesson_evidence_event_id="lesson-support-1",
                lesson_candidate_event_id="lesson-candidate-1",
                strategy_id=StrategyId.SWING_V1,
                strategy_version=strategy_version(StrategyId.SWING_V1),
                role=LessonEvidenceRole.SUPPORT,
                proposal_record_id="proposal-record-1",
                timing=timing(available_delta=timedelta(days=2)),
            )
        )
        transition = LessonTransition(
            lesson_candidate_event_id="lesson-shadow-1",
            lesson_id="lesson-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=1,
            to_status=LessonStatus.SHADOW,
            basis_candidate_event_id="lesson-candidate-1",
            reason="evaluate without production influence",
            provenance=provenance(),
            timing=timing(available_delta=timedelta(days=11)),
        )

        with pytest.raises(ValueError, match="contra"):
            service.transition(transition)

        service.append_evidence(
            LessonEvidence(
                lesson_evidence_event_id="lesson-contra-1",
                lesson_candidate_event_id="lesson-candidate-1",
                strategy_id=StrategyId.SWING_V1,
                strategy_version=strategy_version(StrategyId.SWING_V1),
                role=LessonEvidenceRole.CONTRA,
                proposal_record_id="proposal-record-1",
                timing=timing(available_delta=timedelta(days=3)),
            )
        )
        with pytest.raises(ValueError, match="separation"):
            service.transition(
                replace(transition, timing=timing(available_delta=timedelta(days=10)))
            )

        assert service.transition(transition) is AppendDisposition.INSERTED
        assert connection.execute(
            "SELECT status FROM lesson_candidates ORDER BY revision"
        ).fetchall() == [("CANDIDATE",), ("SHADOW",)]


def test_shadow_can_suspend_resume_only_on_new_evidence_and_retire_terminally(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(candidate())
        for role in LessonEvidenceRole:
            service.append_evidence(
                LessonEvidence(
                    lesson_evidence_event_id=f"initial-{role.value.lower()}",
                    lesson_candidate_event_id="lesson-candidate-1",
                    strategy_id=StrategyId.SWING_V1,
                    strategy_version=strategy_version(StrategyId.SWING_V1),
                    role=role,
                    proposal_record_id="proposal-record-1",
                    timing=timing(available_delta=timedelta(days=2)),
                )
            )
        service.transition(
            LessonTransition(
                "lesson-shadow-1", "lesson-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), 1, LessonStatus.SHADOW,
                "lesson-candidate-1", "start shadow evaluation", provenance(),
                timing(available_delta=timedelta(days=3)),
            )
        )
        service.transition(
            LessonTransition(
                "lesson-suspended-1", "lesson-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), 2, LessonStatus.SUSPENDED,
                "lesson-shadow-1", "conflicting evidence", provenance(),
                timing(available_delta=timedelta(days=4)),
            )
        )
        resume = LessonTransition(
            "lesson-shadow-2", "lesson-1", StrategyId.SWING_V1,
            strategy_version(StrategyId.SWING_V1), 3, LessonStatus.SHADOW,
            "lesson-suspended-1", "new support observed", provenance(),
            timing(available_delta=timedelta(days=6)),
        )

        with pytest.raises(ValueError, match="new evidence"):
            service.transition(resume)

        service.append_evidence(
            LessonEvidence(
                "resume-support", "lesson-suspended-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), LessonEvidenceRole.SUPPORT,
                "proposal-record-1", timing(available_delta=timedelta(days=5)),
            )
        )

        with pytest.raises(ValueError, match="contra"):
            service.transition(resume)

        service.append_evidence(
            LessonEvidence(
                "resume-contra", "lesson-suspended-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), LessonEvidenceRole.CONTRA,
                "proposal-record-1", timing(available_delta=timedelta(days=5)),
            )
        )
        assert service.transition(resume) is AppendDisposition.INSERTED
        service.transition(
            LessonTransition(
                "lesson-retired-1", "lesson-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), 4, LessonStatus.RETIRED,
                "lesson-shadow-2", "evaluation closed", provenance(),
                timing(available_delta=timedelta(days=7)),
            )
        )
        with pytest.raises(ValueError, match="illegal"):
            service.transition(
                replace(
                    resume,
                    lesson_candidate_event_id="lesson-shadow-after-retire",
                    revision=5,
                    timing=timing(available_delta=timedelta(days=8)),
                )
            )


def test_candidate_can_retire_without_shadow_promotion_evidence(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=10)),
        )
        service.create_candidate(candidate())

        result = service.transition(
            LessonTransition(
                "lesson-retired-directly", "lesson-1", StrategyId.SWING_V1,
                strategy_version(StrategyId.SWING_V1), 1, LessonStatus.RETIRED,
                "lesson-candidate-1", "candidate rejected during review", provenance(),
                timing(available_delta=timedelta(days=2)),
            )
        )

        assert result is AppendDisposition.INSERTED
        assert connection.execute(
            "SELECT status FROM lesson_candidates ORDER BY revision"
        ).fetchall() == [("CANDIDATE",), ("RETIRED",)]


def test_lesson_revisions_reject_backdated_timing(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(candidate())

        with pytest.raises(ValueError, match="chronological"):
            service.revise_candidate(
                replace(
                    candidate(),
                    lesson_candidate_event_id="lesson-candidate-backdated",
                    revision=1,
                    timing=timing(),
                )
            )

        with pytest.raises(ValueError, match="chronological"):
            service.transition(
                LessonTransition(
                    "lesson-retired-backdated", "lesson-1", StrategyId.SWING_V1,
                    strategy_version(StrategyId.SWING_V1), 1, LessonStatus.RETIRED,
                    "lesson-candidate-1", "invalid chronology", provenance(), timing(),
                )
            )


def test_shadow_transition_excludes_future_evidence(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(candidate())
        for role, days in (
            (LessonEvidenceRole.SUPPORT, 2),
            (LessonEvidenceRole.CONTRA, 12),
        ):
            service.append_evidence(
                LessonEvidence(
                    f"future-{role.value.lower()}", "lesson-candidate-1",
                    StrategyId.SWING_V1, strategy_version(StrategyId.SWING_V1),
                    role, "proposal-record-1",
                    timing(available_delta=timedelta(days=days)),
                )
            )

        with pytest.raises(ValueError, match="contra"):
            service.transition(
                LessonTransition(
                    "lesson-shadow-future", "lesson-1", StrategyId.SWING_V1,
                    strategy_version(StrategyId.SWING_V1), 1, LessonStatus.SHADOW,
                    "lesson-candidate-1", "future evidence is unavailable", provenance(),
                    timing(available_delta=timedelta(days=11)),
                )
            )


def test_transition_exact_reingest_is_idempotent_and_divergence_is_rejected(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(candidate())
        transition = LessonTransition(
            "lesson-retired-idempotent", "lesson-1", StrategyId.SWING_V1,
            strategy_version(StrategyId.SWING_V1), 1, LessonStatus.RETIRED,
            "lesson-candidate-1", "candidate rejected", provenance(),
            timing(available_delta=timedelta(days=2)),
        )

        assert service.transition(transition) is AppendDisposition.INSERTED
        assert service.transition(transition) is AppendDisposition.DUPLICATE
        with pytest.raises(ValueError, match="divergent"):
            service.transition(replace(transition, reason="different reason"))
