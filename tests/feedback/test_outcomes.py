from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.feedback.outcomes import OutcomeRecord, OutcomeRepository, OutcomeState
from prism_core.feedback.repository import AppendDisposition, FeedbackRepository
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import StrategyId
from tests.feedback.test_repository import (
    AS_OF,
    dispositions,
    proposal_record,
    run_record,
    snapshot_record,
    timing,
)


def seeded(tmp_path: Path):
    return open_database(tmp_path / "research.sqlite")


def outcome(
    *,
    event_id: str = "outcome-1",
    state: OutcomeState = OutcomeState.ELIGIBLE_NOT_EXECUTED,
    revision: int = 0,
    strategy: StrategyId = StrategyId.SWING_V1,
    available_delta: timedelta = timedelta(days=5),
    payload=None,
) -> OutcomeRecord:
    return OutcomeRecord(
        outcome_event_id=event_id,
        proposal_record_id="proposal-record-1",
        strategy_id=strategy,
        strategy_version=run_record(strategy).strategy_version,
        horizon_sessions=5,
        revision=revision,
        outcome_state=state,
        quality=DataQualityStatus.FRESH,
        outcome_payload={"return": Decimal("0.02")} if payload is None else payload,
        timing=timing(available_delta=available_delta),
        config_version="outcome-config.v1",
        code_version="abc123",
        schema_version="feedback.v1",
    )


def seed_proposal(connection) -> None:
    migrate_database(connection, DatabaseKind.RESEARCH)
    FeedbackRepository(connection).append_proposal(
        run_record(), snapshot_record(), proposal_record(), dispositions()
    )


@pytest.mark.parametrize("state", list(OutcomeState))
def test_all_non_broker_outcome_states_are_preserved(tmp_path: Path, state: OutcomeState):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        record = outcome(event_id=f"outcome-{state.value}", state=state)

        assert repo.append(record) is AppendDisposition.INSERTED
        stored = repo.outcomes_as_of(
            AS_OF + timedelta(days=5), strategy_id=StrategyId.SWING_V1
        )
        assert stored[0].outcome_state is state


def test_exact_reingest_dedupes_and_correction_appends_revision(tmp_path: Path):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        base = outcome()
        assert repo.append(base) is AppendDisposition.INSERTED
        assert repo.append(
            replace(base, timing=timing(available_delta=timedelta(days=5), ingested_delta=timedelta(hours=3)))
        ) is AppendDisposition.DUPLICATE
        correction = replace(
            base,
            outcome_event_id="outcome-2",
            revision=1,
            outcome_payload={"return": Decimal("0.03")},
        )
        assert repo.append(correction) is AppendDisposition.INSERTED
        assert connection.execute(
            "SELECT revision FROM proposal_outcomes ORDER BY revision"
        ).fetchall() == [(0,), (1,)]


def test_divergent_duplicate_and_non_contiguous_revision_fail_closed(tmp_path: Path):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        base = outcome()
        repo.append(base)
        with pytest.raises(ValueError, match="divergent content"):
            repo.append(replace(base, outcome_payload={"return": Decimal("0.5")}))
        with pytest.raises(ValueError, match="next revision"):
            repo.append(replace(base, outcome_event_id="outcome-3", revision=2))


def test_pit_read_hides_future_available_outcome(tmp_path: Path):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        repo.append(outcome(available_delta=timedelta(days=5)))

        assert repo.outcomes_as_of(
            AS_OF + timedelta(days=4), strategy_id=StrategyId.SWING_V1
        ) == ()
        assert len(
            repo.outcomes_as_of(
                AS_OF + timedelta(days=5), strategy_id=StrategyId.SWING_V1
            )
        ) == 1
        assert repo.outcomes_as_of(
            AS_OF + timedelta(days=5), strategy_id=StrategyId.TREND_V1
        ) == ()


def test_orphan_cross_strategy_and_predecision_outcomes_are_rejected(tmp_path: Path):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        with pytest.raises(ValueError, match="proposal does not exist"):
            repo.append(replace(outcome(), proposal_record_id="missing"))
        with pytest.raises(ValueError, match="strategy mismatch"):
            repo.append(
                replace(
                    outcome(),
                    strategy_id=StrategyId.TREND_V1,
                    horizon_sessions=20,
                )
            )
        with pytest.raises(ValueError, match="decision boundary"):
            repo.append(outcome(available_delta=timedelta(days=-1)))
        assert connection.execute("SELECT count(*) FROM proposal_outcomes").fetchone() == (0,)


def test_outcome_payload_rejects_nonfinite_and_execution_fields(tmp_path: Path):
    with seeded(tmp_path) as connection:
        seed_proposal(connection)
        repo = OutcomeRepository(connection)
        with pytest.raises(ValueError, match="non-finite"):
            repo.append(outcome(payload={"mfe": Decimal("Infinity")}))
        with pytest.raises(ValueError, match="execution field"):
            repo.append(outcome(payload={"broker": "external"}))
        with pytest.raises(ValueError, match="horizon"):
            repo.append(replace(outcome(), horizon_sessions=7))
