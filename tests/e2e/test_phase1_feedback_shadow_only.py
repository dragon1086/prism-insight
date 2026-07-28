from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from prism_app.daily_pipeline import StrategyEvaluationRequest
from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.feedback.lessons import (
    LessonCandidate,
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.feedback.outcomes import OutcomeRecord, OutcomeRepository, OutcomeState
from prism_core.feedback.retrieval import retrieve_evaluation_lessons
from prism_core.feedback.retrospective import FeedbackProvenance
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId, StrategyVersion


AS_OF = datetime(2020, 7, 24, 20, 0, tzinfo=timezone.utc)
STRATEGY_VERSION = StrategyVersion("swing-v1.0.0")


def _timing(*, available_delta: timedelta = timedelta(0)) -> ObservationTime:
    available = AS_OF + available_delta
    return ObservationTime(
        observed_at=min(AS_OF, available),
        available_at=available,
        ingested_at=available + timedelta(minutes=1),
        as_of_date=max(AS_OF, available),
    )


def _provenance() -> FeedbackProvenance:
    return FeedbackProvenance(
        model_provider="fixture",
        model_id="lesson-model",
        model_version="fixture-v1",
        prompt_version="lesson-prompt.v1",
        config_version="lesson-config.v1",
        code_version="phase1-e2e",
        schema_version="feedback.v1",
    )


def _candidate() -> LessonCandidate:
    return LessonCandidate(
        lesson_candidate_event_id="lesson-candidate-1",
        lesson_id="lesson-1",
        strategy_id=StrategyId.SWING_V1,
        strategy_version=STRATEGY_VERSION,
        revision=0,
        market_scope=("US",),
        sector_scope=("technology",),
        regime_scope=("sideways",),
        condition="counter-evidence is incomplete",
        tentative_action="defer entry evaluation",
        uncertainty=Decimal("0.4"),
        provenance=_provenance(),
        timing=_timing(available_delta=timedelta(days=1)),
    )


def _seed_no_entry_proposal(connection) -> None:
    migrate_database(connection, DatabaseKind.RESEARCH)
    timestamp = AS_OF.isoformat(timespec="microseconds")
    connection.execute(
        "INSERT INTO feedback_runs "
        "(feedback_run_id, strategy_id, strategy_version, market, run_kind, "
        "config_version, code_version, schema_version, observed_at, available_at, "
        "ingested_at, as_of_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "run-SWING_V1", "SWING_V1", STRATEGY_VERSION.value, "US",
            "DAILY_RESEARCH", "config.v1", "phase1-e2e", "feedback.v1",
            timestamp, timestamp, timestamp, timestamp, "run-hash",
        ),
    )
    connection.execute(
        "INSERT INTO decision_snapshots "
        "(decision_snapshot_id, feedback_run_id, strategy_id, strategy_version, "
        "market, security_id, data_snapshot_id, feature_snapshot_id, feature_version, "
        "quant_score_id, quant_score_version, evidence_refs_json, snapshot_json, "
        "data_quality, quality_disposition, observed_at, available_at, ingested_at, "
        "as_of_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "decision-SWING_V1", "run-SWING_V1", "SWING_V1",
            STRATEGY_VERSION.value, "US", "security-1", "data-snapshot-1",
            "feature-snapshot-1", "features.v1", "quant-1", "quant.v1", "[]",
            "{}", "FRESH", "ACCEPT", timestamp, timestamp, timestamp, timestamp,
            "decision-hash",
        ),
    )
    connection.execute(
        "INSERT INTO trade_plan_proposals "
        "(proposal_record_id, proposal_key, proposal_id, revision, "
        "decision_snapshot_id, strategy_id, strategy_version, parse_status, "
        "validation_status, proposed_decision, raw_output_ref, raw_output, "
        "normalized_proposal_json, model_provider, model_id, model_version, "
        "prompt_version, sampling_version, sampling_json, validator_version, "
        "policy_version, observed_at, available_at, ingested_at, as_of_at, "
        "content_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "proposal-record-1", "proposal-natural-1", "proposal-1", 0,
            "decision-SWING_V1", "SWING_V1", STRATEGY_VERSION.value, "PARSED",
            "ACCEPTED", "NO_ENTRY", "raw-1", "fixture raw", "{}", "fixture",
            "fixture-model", "fixture-v1", "swing-prompt.v1", "sampling.v1", "{}",
            "validator.v1", "policy.v1", timestamp, timestamp, timestamp, timestamp,
            "proposal-hash",
        ),
    )


def test_no_entry_proposal_receives_a_symmetric_research_outcome(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        _seed_no_entry_proposal(connection)
        record = OutcomeRecord(
            outcome_event_id="phase1-no-entry-outcome",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=STRATEGY_VERSION,
            horizon_sessions=5,
            revision=0,
            outcome_state=OutcomeState.NO_ENTRY,
            quality=DataQualityStatus.FRESH,
            outcome_payload={"counterfactual_return": Decimal("0.02")},
            timing=_timing(available_delta=timedelta(days=5)),
            config_version="outcome-config.v1",
            code_version="phase1-e2e",
            schema_version="feedback.v1",
        )

        OutcomeRepository(connection).append(record)
        stored = OutcomeRepository(connection).outcomes_as_of(
            AS_OF + timedelta(days=5), strategy_id=StrategyId.SWING_V1
        )

    assert len(stored) == 1
    assert stored[0].outcome_state is OutcomeState.NO_ENTRY
    assert stored[0].proposal_record_id == "proposal-record-1"


def test_legacy_unvalidated_lesson_has_no_phase1_evaluation_or_score_input(
    tmp_path: Path,
) -> None:
    baseline_score = Decimal("60")
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        connection.execute(
            "INSERT INTO lessons "
            "(lesson_id, strategy_id, status, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "legacy-1",
                "SWING_V1",
                "LEGACY_UNVALIDATED",
                '{"activation_allowed":false,"score_adjustment":0}',
                AS_OF.isoformat(timespec="microseconds"),
            ),
        )
        retrieved = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=STRATEGY_VERSION,
            as_of=AS_OF,
        )

    score_after_retrieval = baseline_score + sum(
        (lesson.influence.score_delta for lesson in retrieved.lessons), Decimal("0")
    )
    evaluation_fields = {item.name for item in fields(StrategyEvaluationRequest)}
    assert retrieved.lessons == ()
    assert evaluation_fields == {
        "strategy",
        "market",
        "data_snapshot_id",
        "source_payload",
        "evaluated_at",
        "strategy_input",
    }
    assert "lessons" not in evaluation_fields
    assert "score_adjustment" not in evaluation_fields
    assert score_after_retrieval == baseline_score


def test_shadow_lesson_is_retrievable_only_with_zero_decision_influence(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        _seed_no_entry_proposal(connection)
        service = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        service.create_candidate(_candidate())
        for role in LessonEvidenceRole:
            service.append_evidence(
                LessonEvidence(
                    lesson_evidence_event_id=f"phase1-{role.value.lower()}",
                    lesson_candidate_event_id="lesson-candidate-1",
                    strategy_id=StrategyId.SWING_V1,
                    strategy_version=STRATEGY_VERSION,
                    role=role,
                    proposal_record_id="proposal-record-1",
                    timing=_timing(available_delta=timedelta(days=2)),
                )
            )
        service.transition(
            LessonTransition(
                lesson_candidate_event_id="phase1-shadow",
                lesson_id="lesson-1",
                strategy_id=StrategyId.SWING_V1,
                strategy_version=STRATEGY_VERSION,
                revision=1,
                to_status=LessonStatus.SHADOW,
                basis_candidate_event_id="lesson-candidate-1",
                reason="evaluate without decision influence",
                provenance=_provenance(),
                timing=_timing(available_delta=timedelta(days=3)),
            )
        )
        retrieved = retrieve_evaluation_lessons(
            connection,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=STRATEGY_VERSION,
            as_of=AS_OF + timedelta(days=3),
        )

    assert len(retrieved.lessons) == 1
    assert retrieved.lessons[0].status is LessonStatus.SHADOW
    assert retrieved.lessons[0].influence.score_delta == Decimal("0")
    assert retrieved.lessons[0].influence.policy_effect is False
    assert retrieved.lessons[0].influence.proposal_effect is False
    assert "PAPER_PROMOTED" not in {status.value for status in LessonStatus}
