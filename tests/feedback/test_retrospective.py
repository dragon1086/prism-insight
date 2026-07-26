from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.feedback.repository import (
    AppendDisposition,
    FeedbackRepository,
    RetrospectiveRecord,
)
from prism_core.feedback.outcomes import OutcomeRepository
from prism_core.feedback.retrospective import (
    FeedbackProvenance,
    OutcomeReview,
    ProcessReview,
    RetrospectiveService,
)
from tests.feedback.test_outcomes import outcome
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId
from tests.feedback.test_repository import (
    AS_OF,
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
        model_id="retrospective-model",
        model_version="2026-07-01",
        prompt_version="retrospective-prompt.v1",
        config_version="feedback-config.v1",
        code_version="abc123",
        schema_version="feedback.v1",
    )


def test_process_review_is_available_before_outcome_and_uses_decision_evidence(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        review = ProcessReview(
            retrospective_event_id="process-review-1",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=0,
            evidence_ids=("ev-price-1", "ev-risk-1"),
            evidence_as_of=AS_OF,
            evidence_sufficiency="sufficient",
            policy_consistency="consistent",
            calibration="uncertain",
            ignored_counter_evidence=("ev-risk-1",),
            unsupported_confidence=(),
            data_quality_handling="fresh data accepted",
            provenance=provenance(),
            timing=timing(available_delta=timedelta(days=1)),
        )

        result = RetrospectiveService(connection).append_process(review)

        assert result is AppendDisposition.INSERTED
        assert connection.execute("SELECT count(*) FROM proposal_outcomes").fetchone() == (0,)
        stored = connection.execute(
            "SELECT review_kind, retrospective_json FROM retrospective_events"
        ).fetchone()
        assert stored[0] == "PROCESS"
        payload = json.loads(stored[1])
        assert payload["evidence_as_of"] == AS_OF.isoformat(timespec="microseconds")
        assert payload["evidence_ids"] == ["ev-price-1", "ev-risk-1"]
        assert "outcome_event_ids" not in payload


def test_outcome_review_requires_latest_pit_visible_outcome(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        OutcomeRepository(connection).append(outcome())
        review = OutcomeReview(
            retrospective_event_id="outcome-review-1",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=0,
            outcome_event_ids=("outcome-1",),
            realized_path=(Decimal("0.01"), Decimal("0.02")),
            maximum_favorable_excursion=Decimal("0.03"),
            maximum_adverse_excursion=Decimal("-0.01"),
            stop_target_events=("target_not_reached",),
            regime_transition="sideways_to_moderate_bull",
            rejected_candidate_counterfactual="eligible candidate was not executed",
            provenance=provenance(),
            timing=timing(available_delta=timedelta(days=5)),
        )

        with pytest.raises(ValueError, match="PIT-visible"):
            RetrospectiveService(connection).append_outcome(
                replace(review, timing=timing(available_delta=timedelta(days=4)))
            )

        assert RetrospectiveService(connection).append_outcome(review) is AppendDisposition.INSERTED
        payload = json.loads(
            connection.execute(
                "SELECT retrospective_json FROM retrospective_events "
                "WHERE review_kind = 'OUTCOME'"
            ).fetchone()[0]
        )
        assert payload["outcome_event_ids"] == ["outcome-1"]
        assert payload["outcome_horizons"] == [5]
        assert "policy_consistency" not in payload


def test_repository_rejects_outcome_review_that_bypasses_pit_outcome_validation(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        repository.append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )

        with pytest.raises(ValueError, match="outcome_event_ids"):
            repository.append_retrospective(
                RetrospectiveRecord(
                    retrospective_event_id="bypass-outcome-review",
                    proposal_record_id="proposal-record-1",
                    strategy_id=StrategyId.SWING_V1,
                    strategy_version=strategy_version(StrategyId.SWING_V1),
                    review_kind="OUTCOME",
                    revision=0,
                    payload={"summary": "no durable outcome reference"},
                    timing=timing(available_delta=timedelta(days=5)),
                )
            )


def test_process_review_rejects_evidence_outside_decision_snapshot(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        FeedbackRepository(connection).append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        review = ProcessReview(
            retrospective_event_id="process-review-invalid-evidence",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=0,
            evidence_ids=("future-or-orphan-evidence",),
            evidence_as_of=AS_OF,
            evidence_sufficiency="insufficient",
            policy_consistency="unknown",
            calibration="unknown",
            ignored_counter_evidence=(),
            unsupported_confidence=(),
            data_quality_handling="rejected",
            provenance=provenance(),
            timing=timing(available_delta=timedelta(days=1)),
        )

        with pytest.raises(ValueError, match="outside the decision snapshot"):
            RetrospectiveService(connection).append_process(review)

        assert connection.execute(
            "SELECT count(*) FROM retrospective_events"
        ).fetchone() == (0,)


def test_retrospective_correction_rejects_backdated_revision(tmp_path: Path) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        repository.append_proposal(
            run_record(), snapshot_record(), proposal_record(), dispositions()
        )
        first = RetrospectiveRecord(
            retrospective_event_id="process-revision-0",
            proposal_record_id="proposal-record-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            review_kind="PROCESS",
            revision=0,
            payload={"summary": "first"},
            timing=timing(available_delta=timedelta(days=2)),
        )
        repository.append_retrospective(first)

        with pytest.raises(ValueError, match="chronological"):
            repository.append_retrospective(
                replace(
                    first,
                    retrospective_event_id="process-revision-1",
                    revision=1,
                    payload={"summary": "backdated correction"},
                    timing=timing(available_delta=timedelta(days=1)),
                )
            )
