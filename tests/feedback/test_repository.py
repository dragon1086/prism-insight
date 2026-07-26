from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.data.quality import QualityDisposition
from prism_core.feedback.repository import (
    AppendDisposition,
    DecisionSnapshotRecord,
    FeedbackRepository,
    FeedbackRunRecord,
    LessonCandidateRecord,
    LessonEvidenceRecord,
    ProposalRecord,
    RetrospectiveRecord,
    canonical_json,
)
from prism_core.llm.trade_plan import ProposedDecision, TradePlanProposal
from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.policy.proposal_validator import ProposalValidationStatus
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion
from tests.llm.test_trade_plan_schema import valid_proposal_payload

AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)


def strategy_version(strategy: StrategyId) -> StrategyVersion:
    return StrategyVersion(
        "swing-v1.0.0" if strategy is StrategyId.SWING_V1 else "trend-v1.0.0"
    )


def timing(*, available_delta: timedelta = timedelta(0), ingested_delta: timedelta = timedelta(minutes=1)) -> ObservationTime:
    available = AS_OF + available_delta
    return ObservationTime(
        observed_at=min(AS_OF, available),
        available_at=available,
        ingested_at=available + ingested_delta,
        as_of_date=max(AS_OF, available),
    )


def run_record(strategy: StrategyId = StrategyId.SWING_V1) -> FeedbackRunRecord:
    return FeedbackRunRecord(
        feedback_run_id=f"run-{strategy.value}",
        strategy_id=strategy,
        strategy_version=strategy_version(strategy),
        market=Market.US,
        run_kind="DAILY_RESEARCH",
        config_version="config.v1",
        code_version="abc123",
        schema_version="feedback.v1",
        timing=timing(),
    )


def snapshot_record(strategy: StrategyId = StrategyId.SWING_V1) -> DecisionSnapshotRecord:
    version = strategy_version(strategy)
    return DecisionSnapshotRecord(
        decision_snapshot_id=f"decision-{strategy.value}",
        feedback_run_id=f"run-{strategy.value}",
        strategy_id=strategy,
        strategy_version=version,
        market=Market.US,
        security_id="00000000-0000-0000-0000-000000000102",
        data_snapshot_id="00000000-0000-0000-0000-000000000104",
        feature_snapshot_id="00000000-0000-0000-0000-000000000103",
        feature_version="features.v1",
        quant_score_id="quant-1",
        quant_score_version="quant.v1",
        evidence_refs=("ev-price-1", "ev-risk-1"),
        snapshot_payload={"quant_score": Decimal("60.0"), "feature": Decimal("0.05")},
        data_quality=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
        timing=timing(),
    )


def proposal_model(decision: ProposedDecision = ProposedDecision.NO_ENTRY) -> TradePlanProposal:
    payload = valid_proposal_payload()
    payload["decision"] = decision
    return TradePlanProposal.model_validate(payload)


def proposal_record(
    *,
    record_id: str = "proposal-record-1",
    key: str = "proposal-natural-1",
    revision: int = 0,
    normalized: TradePlanProposal | None = None,
    raw_output: str = "raw-model-output",
    strategy: StrategyId = StrategyId.SWING_V1,
    available_delta: timedelta = timedelta(0),
) -> ProposalRecord:
    normalized = proposal_model() if normalized is None else normalized
    return ProposalRecord(
        proposal_record_id=record_id,
        proposal_key=key,
        revision=revision,
        decision_snapshot_id=f"decision-{strategy.value}",
        strategy_id=strategy,
        strategy_version=strategy_version(strategy),
        parse_status="PARSED",
        validation_status=ProposalValidationStatus.ACCEPTED,
        raw_output_ref="llm-response-1",
        raw_output=raw_output,
        normalized_proposal=normalized,
        model_provider="fixture",
        model_id="fixture-model",
        model_version="2026-07-01",
        prompt_version="swing-prompt.v1",
        sampling_version="sampling.v1",
        sampling={
            "temperature": Decimal("0.2"),
            "top_p": Decimal("0.9"),
            "seed": 7,
        },
        validator_version="validator.v1",
        policy_version="policy.v1",
        timing=timing(available_delta=available_delta),
    )


def dispositions() -> tuple[FieldDisposition, ...]:
    return (
        FieldDisposition(
            field_path="risk_multiplier_candidate.value",
            action=DispositionAction.CLAMP,
            reason="policy_max",
            proposed_value="0.8",
            resolved_value="0.5",
            evidence_ids=("ev-risk-1",),
        ),
        FieldDisposition(
            field_path="quant_score.total_score",
            action=DispositionAction.RECALCULATE,
            reason="deterministic",
            resolved_value="60",
        ),
    )


def repository(tmp_path: Path):
    return open_database(tmp_path / "research.sqlite")


def test_append_persists_no_entry_proposal_and_field_dispositions_atomically(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)

        result = repo.append_proposal(
            run=run_record(),
            snapshot=snapshot_record(),
            proposal=proposal_record(),
            dispositions=dispositions(),
        )

        assert result is AppendDisposition.INSERTED
        stored = repo.proposals_as_of(AS_OF, strategy_id=StrategyId.SWING_V1)
        assert len(stored) == 1
        assert stored[0].proposed_decision is ProposedDecision.NO_ENTRY
        assert tuple(item.action for item in stored[0].dispositions) == (
            DispositionAction.CLAMP,
            DispositionAction.RECALCULATE,
        )
        assert stored[0].normalized_proposal_json == proposal_model().model_dump_json()


def test_exact_reingest_is_idempotent_but_divergent_natural_revision_is_rejected(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        args = dict(
            run=run_record(), snapshot=snapshot_record(), proposal=proposal_record(), dispositions=dispositions()
        )
        assert repo.append_proposal(**args) is AppendDisposition.INSERTED

        later_ingestion = replace(
            args["proposal"],
            timing=timing(ingested_delta=timedelta(hours=2)),
        )
        assert repo.append_proposal(**{**args, "proposal": later_ingestion}) is AppendDisposition.DUPLICATE
        with pytest.raises(ValueError, match="divergent content"):
            repo.append_proposal(
                **{**args, "proposal": replace(args["proposal"], raw_output="changed")}
            )
        assert connection.execute("SELECT count(*) FROM trade_plan_proposals").fetchone() == (1,)


def test_correction_appends_new_revision_without_mutating_prior_record(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        base = proposal_record()
        repo.append_proposal(run_record(), snapshot_record(), base, dispositions())
        corrected = replace(
            base,
            proposal_record_id="proposal-record-2",
            revision=1,
            raw_output="corrected",
            raw_output_ref="llm-response-2",
        )

        assert repo.append_proposal(run_record(), snapshot_record(), corrected, dispositions()) is AppendDisposition.INSERTED
        assert connection.execute(
            "SELECT revision, raw_output FROM trade_plan_proposals ORDER BY revision"
        ).fetchall() == [(0, "raw-model-output"), (1, "corrected")]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE trade_plan_proposals SET raw_output = 'mutated' WHERE revision = 0"
            )


def test_rejected_malformed_output_is_preserved_without_proposal_uuid(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        rejected = replace(
            proposal_record(),
            parse_status="REJECTED",
            validation_status=ProposalValidationStatus.REJECTED,
            normalized_proposal=None,
            raw_output="{malformed",
        )

        repo.append_proposal(run_record(), snapshot_record(), rejected, dispositions())

        row = connection.execute(
            "SELECT proposal_id, parse_status, raw_output FROM trade_plan_proposals"
        ).fetchone()
        assert row == (None, "REJECTED", "{malformed")


def test_atomic_rollback_on_cross_strategy_proposal_mismatch(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)

        with pytest.raises(ValueError, match="strategy mismatch"):
            repo.append_proposal(
                run_record(),
                snapshot_record(),
                proposal_record(strategy=StrategyId.TREND_V1),
                dispositions(),
            )

        assert connection.execute("SELECT count(*) FROM feedback_runs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM decision_snapshots").fetchone() == (0,)


def test_strategy_isolation_and_future_available_proposal_filtering(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        repo.append_proposal(run_record(), snapshot_record(), proposal_record(), dispositions())
        trend_model = proposal_model().model_copy(
            update={
                "proposal_id": UUID("00000000-0000-0000-0000-000000000202"),
                "strategy_id": StrategyId.TREND_V1,
                "strategy_version": strategy_version(StrategyId.TREND_V1),
            }
        )
        future = proposal_record(
            record_id="trend-record",
            key="proposal-natural-1",
            strategy=StrategyId.TREND_V1,
            normalized=trend_model,
            available_delta=timedelta(days=1),
        )
        future_snapshot = replace(snapshot_record(StrategyId.TREND_V1), timing=timing(available_delta=timedelta(days=1)))
        future_run = replace(run_record(StrategyId.TREND_V1), timing=timing(available_delta=timedelta(days=1)))
        repo.append_proposal(future_run, future_snapshot, future, dispositions())

        assert len(repo.proposals_as_of(AS_OF, strategy_id=StrategyId.SWING_V1)) == 1
        assert repo.proposals_as_of(AS_OF, strategy_id=StrategyId.TREND_V1) == ()
        assert len(
            repo.proposals_as_of(AS_OF + timedelta(days=1), strategy_id=StrategyId.TREND_V1)
        ) == 1


def test_pit_read_preserves_latest_revision_for_each_strategy_version(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        base = proposal_record()
        repo.append_proposal(run_record(), snapshot_record(), base, dispositions())
        repo.append_proposal(
            run_record(),
            snapshot_record(),
            replace(
                base,
                proposal_record_id="proposal-record-v1-revision-1",
                revision=1,
                raw_output_ref="llm-response-v1-revision-1",
            ),
            dispositions(),
        )

        version_two = StrategyVersion("swing-v2.0.0")
        model_two = proposal_model().model_copy(
            update={
                "proposal_id": UUID("00000000-0000-0000-0000-000000000302"),
                "strategy_version": version_two,
            }
        )
        run_two = replace(
            run_record(),
            feedback_run_id="run-SWING_V1-v2",
            strategy_version=version_two,
        )
        snapshot_two = replace(
            snapshot_record(),
            decision_snapshot_id="decision-SWING_V1-v2",
            feedback_run_id=run_two.feedback_run_id,
            strategy_version=version_two,
        )
        proposal_two = replace(
            proposal_record(normalized=model_two),
            proposal_record_id="proposal-record-v2",
            decision_snapshot_id=snapshot_two.decision_snapshot_id,
            strategy_version=version_two,
        )
        repo.append_proposal(run_two, snapshot_two, proposal_two, dispositions())

        stored = repo.proposals_as_of(AS_OF, strategy_id=StrategyId.SWING_V1)
        assert {(item.strategy_version.value, item.revision) for item in stored} == {
            ("swing-v1.0.0", 1),
            ("swing-v2.0.0", 0),
        }


def test_canonical_serialization_rejects_nonfinite_and_execution_fields(tmp_path: Path):
    assert canonical_json({"b": Decimal("2.00"), "a": 1}) == canonical_json(
        {"a": 1, "b": Decimal("2.00")}
    )
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        with pytest.raises(ValueError, match="non-finite"):
            repo.append_proposal(
                run_record(),
                replace(snapshot_record(), snapshot_payload={"score": Decimal("NaN")}),
                proposal_record(),
                dispositions(),
            )
        with pytest.raises(ValueError, match="execution field"):
            repo.append_proposal(
                run_record(),
                replace(snapshot_record(), snapshot_payload={"order_intent": "forbidden"}),
                proposal_record(),
                dispositions(),
            )
        assert connection.execute("SELECT count(*) FROM feedback_runs").fetchone() == (0,)


def test_proposal_requires_complete_evidence_and_model_provenance(tmp_path: Path):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        with pytest.raises(ValueError, match="evidence references"):
            repo.append_proposal(
                run_record(),
                replace(snapshot_record(), evidence_refs=("ev-price-1",)),
                proposal_record(),
                dispositions(),
            )
        with pytest.raises(ValueError, match="model provenance mismatch"):
            repo.append_proposal(
                run_record(),
                snapshot_record(),
                replace(proposal_record(), model_id="different-model"),
                dispositions(),
            )
        bad_disposition = replace(dispositions()[0], evidence_ids=("missing-evidence",))
        with pytest.raises(ValueError, match="disposition has missing evidence references"):
            repo.append_proposal(
                run_record(), snapshot_record(), proposal_record(), (bad_disposition,)
            )
        rejected = replace(
            proposal_record(),
            parse_status="REJECTED",
            validation_status=ProposalValidationStatus.REJECTED,
            normalized_proposal=None,
        )
        with pytest.raises(ValueError, match="disposition has missing evidence references"):
            repo.append_proposal(
                run_record(), snapshot_record(), rejected, (bad_disposition,)
            )
        with pytest.raises(ValueError, match="rejected parse"):
            repo.append_proposal(
                run_record(),
                snapshot_record(),
                replace(rejected, validation_status=ProposalValidationStatus.ACCEPTED),
                dispositions(),
            )
        with pytest.raises(ValueError, match="revision"):
            repo.append_proposal(
                run_record(),
                snapshot_record(),
                replace(proposal_record(), revision=True),
                dispositions(),
            )
        repo.append_proposal(run_record(), snapshot_record(), proposal_record(), dispositions())
        with pytest.raises(ValueError, match="feedback_runs identity has divergent content"):
            repo.append_proposal(
                replace(run_record(), run_kind="BACKFILL"),
                replace(snapshot_record(), decision_snapshot_id="decision-other"),
                replace(
                    proposal_record(),
                    proposal_record_id="proposal-other",
                    proposal_key="proposal-other",
                    decision_snapshot_id="decision-other",
                ),
                dispositions(),
            )


def test_naive_and_future_inconsistent_timestamps_fail_closed() -> None:
    aware = timing()
    with pytest.raises(Exception):
        replace(aware, observed_at=datetime(2026, 7, 24, 20, 0))
    with pytest.raises(Exception):
        ObservationTime(
            observed_at=AS_OF + timedelta(minutes=2),
            available_at=AS_OF,
            ingested_at=AS_OF + timedelta(minutes=3),
            as_of_date=AS_OF + timedelta(minutes=3),
        )


def test_minimal_retrospective_lesson_and_evidence_storage_is_append_only_and_scoped(
    tmp_path: Path,
):
    with repository(tmp_path) as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repo = FeedbackRepository(connection)
        repo.append_proposal(run_record(), snapshot_record(), proposal_record(), dispositions())
        later = timing(available_delta=timedelta(days=1))
        retrospective = RetrospectiveRecord(
            retrospective_event_id="retro-1",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            review_kind="PROCESS",
            revision=0,
            payload={"summary": "stored only"},
            timing=later,
        )
        lesson = LessonCandidateRecord(
            lesson_candidate_event_id="lesson-event-1",
            lesson_id="lesson-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=0,
            status="CANDIDATE",
            payload={"hypothesis": "inactive"},
            timing=later,
        )
        evidence = LessonEvidenceRecord(
            lesson_evidence_event_id="lesson-evidence-1",
            lesson_candidate_event_id="lesson-event-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            evidence_role="SUPPORT",
            proposal_record_id="proposal-record-1",
            observation_id=None,
            timing=later,
        )

        assert repo.append_retrospective(retrospective) is AppendDisposition.INSERTED
        assert repo.append_lesson_candidate(lesson) is AppendDisposition.INSERTED
        assert repo.append_lesson_evidence(evidence) is AppendDisposition.INSERTED
        assert repo.append_lesson_evidence(evidence) is AppendDisposition.DUPLICATE
        assert repo.append_lesson_evidence(
            replace(evidence, lesson_evidence_event_id="lesson-evidence-alias")
        ) is AppendDisposition.DUPLICATE
        with pytest.raises(ValueError, match="divergent content"):
            repo.append_lesson_evidence(
                replace(
                    evidence,
                    lesson_evidence_event_id="lesson-evidence-divergent",
                    timing=timing(available_delta=timedelta(days=2)),
                )
            )
        assert repo.retrospective_ids_as_of(
            AS_OF, strategy_id=StrategyId.SWING_V1
        ) == ()
        assert repo.lesson_evidence_ids_as_of(
            AS_OF, strategy_id=StrategyId.SWING_V1
        ) == ()
        assert repo.retrospective_ids_as_of(
            AS_OF + timedelta(days=1), strategy_id=StrategyId.SWING_V1
        ) == ("retro-1",)
        assert repo.lesson_evidence_ids_as_of(
            AS_OF + timedelta(days=1), strategy_id=StrategyId.SWING_V1
        ) == ("lesson-evidence-1",)
        correction = replace(
            retrospective,
            retrospective_event_id="retro-2",
            revision=1,
            payload={"summary": "corrected"},
        )
        assert repo.append_retrospective(correction) is AppendDisposition.INSERTED
        assert repo.retrospective_ids_as_of(
            AS_OF + timedelta(days=1), strategy_id=StrategyId.SWING_V1
        ) == ("retro-2",)
        assert connection.execute("SELECT status FROM lesson_candidates").fetchone() == (
            "CANDIDATE",
        )
        with pytest.raises(ValueError, match="unavailable lesson status"):
            repo.append_lesson_candidate(replace(lesson, status="PAPER_PROMOTED"))
        with pytest.raises(ValueError, match="start at CANDIDATE"):
            repo.append_lesson_candidate(replace(lesson, status="SHADOW"))
        with pytest.raises(ValueError, match="exactly one source"):
            repo.append_lesson_evidence(replace(evidence, observation_id="also-set"))
        with pytest.raises(ValueError, match="strategy mismatch"):
            repo.append_lesson_evidence(
                replace(
                    evidence,
                    lesson_evidence_event_id="lesson-evidence-wrong-strategy",
                    strategy_id=StrategyId.TREND_V1,
                    strategy_version=strategy_version(StrategyId.TREND_V1),
                )
            )
        with pytest.raises(ValueError, match="cannot precede"):
            repo.append_lesson_evidence(
                replace(
                    evidence,
                    lesson_evidence_event_id="lesson-evidence-too-early",
                    timing=timing(),
                )
            )
        trend_lesson = replace(
            lesson,
            lesson_candidate_event_id="trend-lesson-event",
            strategy_id=StrategyId.TREND_V1,
            strategy_version=strategy_version(StrategyId.TREND_V1),
        )
        assert repo.append_lesson_candidate(trend_lesson) is AppendDisposition.INSERTED
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM lesson_candidates")
