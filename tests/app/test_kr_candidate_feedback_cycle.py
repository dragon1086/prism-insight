from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from prism_app.outcome_tracker import OutcomeTracker
from prism_app.query_service import QueryService
from prism_core.candidates import CandidateChannel
from prism_core.data.contracts import ObservationTime
from prism_core.feedback.repository import FeedbackRepository
from prism_core.llm.trade_plan import ProposedDecision
from prism_core.policy.proposal_validator import ProposalValidationStatus
from prism_core.feedback.lessons import (
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.persistence.leadership_history import (
    GroupLeadershipState,
    LeadershipHistoryRecord,
)
from prism_core.persistence.feedback_cycle import (
    MarketOutcomeInput,
    MarketOutcomeMeasurementStatus,
    PendingMarketOutcomeInput,
    PriceBasis,
    ProcessQuality,
    ProcessQualityRecord,
)
from prism_core.reporting.leadership_tracking import (
    High52WeekState,
    MomentumState,
    PeakState,
)
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion
from tests.feedback.test_repository import (
    AS_OF,
    dispositions,
    proposal_model,
    proposal_record,
    run_record,
    snapshot_record,
    timing,
)
from tests.feedback.test_lesson_lifecycle import candidate, provenance


DAY_N = datetime(2026, 7, 28, 6, 30, tzinfo=timezone.utc)
SECURITY_ID = "7445cf82-2c34-51ba-ab77-26f69f80a14b"
PROPOSAL_SECURITY_ID = "00000000-0000-0000-0000-000000000102"
SWING_VERSION = StrategyVersion("swing-v1.0.0")
SWING_SCORE_VERSION = "SHADOW_SCORE_V1.SWING_V1"


def _timing(at: datetime) -> ObservationTime:
    return ObservationTime(
        observed_at=at - timedelta(minutes=2),
        available_at=at - timedelta(minutes=1),
        ingested_at=at,
        as_of_date=at,
    )


def _history(*, event_id: str, session: date, at: datetime) -> LeadershipHistoryRecord:
    return LeadershipHistoryRecord(
        leadership_event_id=event_id,
        market=Market.KR,
        security_id=SECURITY_ID,
        provider_symbol="271560",
        session_date=session,
        strategy_id=StrategyId.SWING_V1,
        strategy_version=SWING_VERSION,
        candidate_channels=(CandidateChannel.CORE_PRISM,),
        relative_strength_5d=Decimal("91.2"),
        relative_strength_20d=Decimal("88.7"),
        relative_strength_60d=None,
        high_52_week_state=High52WeekState.NEAR_HIGH,
        high_52_week_distance_pct=Decimal("1.8"),
        momentum_state=MomentumState.ADVANCING,
        momentum_score=Decimal("72.5"),
        peak_state=PeakState.APPROACHING,
        peak_score=Decimal("64"),
        group_id="KR-SEMICONDUCTORS",
        group_state=GroupLeadershipState.LEADING,
        source_snapshot_id="fixture-kr-day-n-snapshot",
        evidence_ids=("fixture:kis:271560:2026-07-28", "fixture:agentnews:kr:2026-07-28"),
        timing=_timing(at),
    )


def test_daily_leadership_history_is_append_only_strategy_scoped_and_pit_readable(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        tracker = OutcomeTracker(connection)
        day_n = _history(event_id="leadership-day-n", session=date(2026, 7, 28), at=DAY_N)
        day_n1 = _history(
            event_id="leadership-day-n1",
            session=date(2026, 7, 29),
            at=DAY_N + timedelta(days=1),
        )

        assert tracker.record_leadership(day_n).value == "INSERTED"
        assert tracker.record_leadership(day_n).value == "DUPLICATE"
        assert tracker.record_leadership(day_n1).value == "INSERTED"
        history = QueryService(connection).candidate_leadership_history(
            market=Market.KR,
            security_id=SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            as_of=DAY_N + timedelta(days=1, minutes=1),
        )

        assert tuple(item.session_date for item in history) == (
            date(2026, 7, 28),
            date(2026, 7, 29),
        )
        assert history[0].candidate_channels == (CandidateChannel.CORE_PRISM,)
        assert history[0].relative_strength_5d == Decimal("91.2")
        assert history[0].high_52_week_state is High52WeekState.NEAR_HIGH
        assert history[0].momentum_state is MomentumState.ADVANCING
        assert history[0].peak_state is PeakState.APPROACHING
        assert history[0].group_state is GroupLeadershipState.LEADING
        assert QueryService(connection).candidate_leadership_history(
            market=Market.KR,
            security_id=SECURITY_ID,
            strategy_id=StrategyId.TREND_V1,
            strategy_version=StrategyVersion("trend-v1.0.0"),
            as_of=DAY_N + timedelta(days=2),
        ) == ()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE leadership_history_events SET group_state = 'FADING' "
                "WHERE leadership_event_id = 'leadership-day-n'"
            )


def test_later_completed_session_records_process_and_market_outcomes_without_lookahead(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        run = run_record(StrategyId.SWING_V1)
        run = run.__class__(
            **{
                **run.__dict__,
                "market": Market.KR,
                "strategy_version": SWING_VERSION,
            }
        )
        snapshot = snapshot_record(StrategyId.SWING_V1)
        snapshot = snapshot.__class__(
            **{
                **snapshot.__dict__,
                "market": Market.KR,
                "security_id": PROPOSAL_SECURITY_ID,
                "strategy_version": SWING_VERSION,
            }
        )
        proposal = proposal_record(strategy=StrategyId.SWING_V1)
        proposal = proposal.__class__(
            **{**proposal.__dict__, "strategy_version": SWING_VERSION}
        )
        FeedbackRepository(connection).append_proposal(
            run, snapshot, proposal, dispositions()
        )
        tracker = OutcomeTracker(connection)
        process = ProcessQualityRecord(
            process_event_id="process-day-n",
            proposal_record_id=proposal.proposal_record_id,
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            data_quality=ProcessQuality.PASS,
            evidence_quality=ProcessQuality.PASS,
            predicate_quality=ProcessQuality.WARN,
            validator_quality=ProcessQuality.PASS,
            evidence_ids=("ev-price-1", "ev-risk-1"),
            timing=_timing(DAY_N + timedelta(days=5)),
        )
        market_outcome = MarketOutcomeInput(
            outcome_event_id="market-outcome-day-n-5",
            proposal_record_id=proposal.proposal_record_id,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            horizon_sessions=5,
            revision=0,
            decision_session=date(2026, 7, 24),
            completed_sessions=(
                date(2026, 7, 27),
                date(2026, 7, 28),
                date(2026, 7, 29),
                date(2026, 7, 30),
                date(2026, 7, 31),
            ),
            decision_close=Decimal("100"),
            completed_close=Decimal("108"),
            price_basis=PriceBasis.ADJUSTED,
            source_snapshot_ids=(
                "00000000-0000-0000-0000-000000000104",
                "kis-close-2026-07-27",
                "kis-close-2026-07-28",
                "kis-close-2026-07-29",
                "kis-close-2026-07-30",
                "kis-close-2026-07-31",
            ),
            timing=_timing(DAY_N + timedelta(days=5)),
            config_version="kr-feedback.v1",
            code_version="task-10",
            schema_version="feedback.v1",
        )

        assert tracker.record_process_quality(process).value == "INSERTED"
        assert tracker.record_process_quality(process).value == "DUPLICATE"
        with pytest.raises(ValueError, match="UUID"):
            tracker.record_process_quality(
                replace(process, process_event_id="unstable-security", security_id="271560")
            )
        assert tracker.record_market_outcome(market_outcome).value == "INSERTED"
        before_completion = QueryService(connection).candidate_feedback_cycle(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            as_of=DAY_N + timedelta(days=4),
        )
        cycle = QueryService(connection).candidate_feedback_cycle(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            as_of=DAY_N + timedelta(days=6),
        )

        assert before_completion.process_outcomes == ()
        assert before_completion.market_outcomes == ()
        assert cycle.process_outcomes == (process,)
        assert len(cycle.market_outcomes) == 1
        assert cycle.market_outcomes[0].horizon_sessions == 5
        assert cycle.market_outcomes[0].outcome_json == (
            '{"completed_close":"108","completed_session":"2026-07-31",'
            '"completed_sessions":["2026-07-27","2026-07-28","2026-07-29",'
            '"2026-07-30","2026-07-31"],"completed_sessions_elapsed":5,'
            '"decision_close":"100",'
            '"decision_session":"2026-07-24","forward_return":"0.08",'
            '"price_basis":"ADJUSTED","source_snapshot_ids":'
            '["00000000-0000-0000-0000-000000000104",'
            '"kis-close-2026-07-27","kis-close-2026-07-28",'
            '"kis-close-2026-07-29","kis-close-2026-07-30",'
            '"kis-close-2026-07-31"]}'
        )
        with pytest.raises(ValueError, match="official exchange sessions"):
            tracker.record_market_outcome(
                replace(
                    market_outcome,
                    outcome_event_id="non-session-invalid",
                    revision=1,
                    completed_sessions=(
                        date(2026, 7, 25),
                        *market_outcome.completed_sessions[1:],
                    ),
                    source_snapshot_ids=(
                        market_outcome.source_snapshot_ids[0],
                        "kis-close-2026-07-25",
                        *market_outcome.source_snapshot_ids[2:],
                    ),
                )
            )
        with pytest.raises(ValueError, match="completed horizon"):
            tracker.record_market_outcome(
                replace(
                    market_outcome,
                    outcome_event_id="lookahead-invalid",
                    completed_sessions=market_outcome.completed_sessions[:-1],
                )
            )
        with pytest.raises(ValueError, match="original decision data snapshot"):
            tracker.record_market_outcome(
                replace(
                    market_outcome,
                    outcome_event_id="decision-snapshot-invalid",
                    source_snapshot_ids=(
                        "unrelated-day-n-snapshot",
                        *market_outcome.source_snapshot_ids[1:],
                    ),
                )
            )

        rejected_proposal = replace(
            proposal,
            proposal_record_id="rejected-proposal-record",
            proposal_key="rejected-proposal-key",
            validation_status=ProposalValidationStatus.REJECTED,
            normalized_proposal=proposal_model(ProposedDecision.WATCH).model_copy(
                update={"proposal_id": UUID("00000000-0000-0000-0000-000000000777")}
            ),
        )
        FeedbackRepository(connection).append_proposal(
            run, snapshot, rejected_proposal, dispositions()
        )
        rejected_outcome = replace(
            market_outcome,
            outcome_event_id="rejected-market-outcome-day-n-5",
            proposal_record_id=rejected_proposal.proposal_record_id,
        )

        assert tracker.record_market_outcome(rejected_outcome).value == "INSERTED"
        rejected_stored = QueryService(connection).candidate_feedback_cycle(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            as_of=DAY_N + timedelta(days=6),
        ).market_outcomes
        assert rejected_stored[-1].outcome_state.value == "REJECTED"


def test_immature_horizon_persists_explicit_pending_outcome_without_future_data(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        run = replace(
            run_record(StrategyId.SWING_V1),
            market=Market.KR,
            strategy_version=SWING_VERSION,
        )
        snapshot = replace(
            snapshot_record(StrategyId.SWING_V1),
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_version=SWING_VERSION,
        )
        proposal = replace(
            proposal_record(strategy=StrategyId.SWING_V1),
            strategy_version=SWING_VERSION,
        )
        FeedbackRepository(connection).append_proposal(
            run, snapshot, proposal, dispositions()
        )
        pending = PendingMarketOutcomeInput(
            outcome_event_id="market-outcome-pending-day-n",
            proposal_record_id=proposal.proposal_record_id,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            required_session_count=5,
            completed_sessions=(),
            next_eligible_session=date(2026, 7, 31),
            timing=_timing(DAY_N),
            config_version="kr-feedback.v1",
            code_version="task-10",
            schema_version="feedback.v1",
        )

        tracker = OutcomeTracker(connection)
        assert tracker.record_pending_market_outcome(pending).value == "INSERTED"
        assert tracker.record_pending_market_outcome(pending).value == "DUPLICATE"
        stored = QueryService(connection).candidate_feedback_cycle(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            as_of=DAY_N + timedelta(minutes=1),
        ).market_outcomes

        assert len(stored) == 1
        assert stored[0].horizon_sessions == 5
        assert stored[0].outcome_state.value == "UNKNOWN"
        assert stored[0].outcome_json == (
            '{"measurement_reason":"NOT_YET_MEASURABLE",'
            '"measurement_status":"PENDING","next_eligible_session":"2026-07-31",'
            '"observed_session_count":0,"required_session_count":5,'
            '"source_snapshot_ids":["00000000-0000-0000-0000-000000000104"]}'
        )
        assert MarketOutcomeMeasurementStatus.PENDING.value == "PENDING"
        with pytest.raises(ValueError, match="remaining official horizon"):
            tracker.record_pending_market_outcome(
                replace(
                    pending,
                    outcome_event_id="invalid-pending-boundary",
                    revision=1,
                    next_eligible_session=date(2026, 7, 30),
                )
            )
        with pytest.raises(ValueError, match="must remain below"):
            tracker.record_pending_market_outcome(
                replace(
                    pending,
                    outcome_event_id="invalid-mature-pending",
                    completed_sessions=tuple(
                        date(2026, 7, 25) + timedelta(days=offset)
                        for offset in range(5)
                    ),
                    completed_session_snapshot_ids=tuple(
                        f"kis-close-{offset}" for offset in range(5)
                    ),
                )
            )


def test_next_run_retrieves_shadow_lesson_by_exact_candidate_key_with_zero_influence(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        run = replace(
            run_record(StrategyId.SWING_V1),
            market=Market.KR,
            strategy_version=SWING_VERSION,
        )
        snapshot = replace(
            snapshot_record(StrategyId.SWING_V1),
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_version=SWING_VERSION,
            quant_score_version=SWING_SCORE_VERSION,
        )
        proposal = replace(
            proposal_record(strategy=StrategyId.SWING_V1),
            strategy_version=SWING_VERSION,
        )
        FeedbackRepository(connection).append_proposal(
            run, snapshot, proposal, dispositions()
        )
        lifecycle = LessonLifecycleService(
            connection,
            policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
        )
        lifecycle.create_candidate(
            replace(
                candidate(),
                market_scope=("KR",),
                regime_scope=("MODERATE_BULL",),
            )
        )
        for role in LessonEvidenceRole:
            lifecycle.append_evidence(
                LessonEvidence(
                    lesson_evidence_event_id=f"kr-cycle-{role.value.lower()}",
                    lesson_candidate_event_id="lesson-candidate-1",
                    strategy_id=StrategyId.SWING_V1,
                    strategy_version=SWING_VERSION,
                    role=role,
                    proposal_record_id=proposal.proposal_record_id,
                    timing=timing(available_delta=timedelta(days=2)),
                )
            )
        lifecycle.transition(
            LessonTransition(
                lesson_candidate_event_id="kr-cycle-shadow",
                lesson_id="lesson-1",
                strategy_id=StrategyId.SWING_V1,
                strategy_version=SWING_VERSION,
                revision=1,
                to_status=LessonStatus.SHADOW,
                basis_candidate_event_id="lesson-candidate-1",
                reason="next-run evaluation only",
                provenance=provenance(),
                timing=timing(available_delta=timedelta(days=3)),
            )
        )
        service = QueryService(connection)

        exact = service.candidate_shadow_feedback(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            quant_score_version=SWING_SCORE_VERSION,
            regime="MODERATE_BULL",
            as_of=AS_OF + timedelta(days=4),
        )
        wrong_score_version = service.candidate_shadow_feedback(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            quant_score_version="SHADOW_SCORE_V0.SWING_V1",
            regime="MODERATE_BULL",
            as_of=AS_OF + timedelta(days=4),
        )
        wrong_regime = service.candidate_shadow_feedback(
            market=Market.KR,
            security_id=PROPOSAL_SECURITY_ID,
            strategy_id=StrategyId.SWING_V1,
            strategy_version=SWING_VERSION,
            quant_score_version=SWING_SCORE_VERSION,
            regime="STRONG_BEAR",
            as_of=AS_OF + timedelta(days=4),
        )

        assert len(exact.lessons) == 1
        assert exact.lessons[0].status is LessonStatus.SHADOW
        assert exact.lessons[0].influence.score_delta == Decimal("0")
        assert exact.lessons[0].influence.policy_effect is False
        assert exact.lessons[0].influence.proposal_effect is False
        assert exact.quant_score_version == SWING_SCORE_VERSION
        assert wrong_score_version.lessons == ()
        assert wrong_regime.lessons == ()
