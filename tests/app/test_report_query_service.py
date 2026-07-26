from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from prism_app.daily_pipeline import (
    PersistedDailyAnalysis,
    SQLiteAppRunRepository,
    StrategyAnalysis,
)
from prism_app.query_service import (
    QueryService,
    ReportUnavailableError,
    WeeklyReportReadiness,
)
from prism_core.data.quality import (
    QualityDecision,
    QualityDisposition,
    QualitySkipRecord,
)
from prism_core.feedback.lessons import (
    LessonEvidence,
    LessonEvidenceRole,
    LessonLifecycleService,
    LessonStatus,
    LessonTransition,
    LessonValidationPolicy,
)
from prism_core.feedback.repository import FeedbackRepository
from prism_core.llm.trade_plan import TradePlanProposal
from prism_core.reporting.leadership_tracking import LeadershipRepository
from prism_core.reporting.models import LeadingSector
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId
from tests.app.test_daily_pipeline import _leadership_snapshot
from tests.feedback.test_repository import (
    AS_OF,
    dispositions,
    proposal_record,
    run_record,
    snapshot_record,
    strategy_version,
    timing,
)
from tests.feedback.test_lesson_lifecycle import candidate, provenance
from tests.llm.test_trade_plan_schema import valid_proposal_payload


DATA_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000104")


def _seed_strategy(connection, strategy_id: StrategyId) -> None:
    version = strategy_version(strategy_id)
    payload = valid_proposal_payload()
    payload["strategy_id"] = strategy_id
    payload["strategy_version"] = version.value
    payload["market"] = Market.KR
    payload["proposal_id"] = UUID(
        "00000000-0000-0000-0000-000000000201"
        if strategy_id is StrategyId.SWING_V1
        else "00000000-0000-0000-0000-000000000202"
    )
    payload["feature_provenance"]["data_snapshot_id"] = DATA_SNAPSHOT_ID
    proposal = TradePlanProposal.model_validate(payload)
    FeedbackRepository(connection).append_proposal(
        replace(run_record(strategy_id), market=Market.KR),
        replace(snapshot_record(strategy_id), market=Market.KR),
        proposal_record(
            record_id=f"proposal-{strategy_id.value}",
            key=f"proposal-key-{strategy_id.value}",
            normalized=proposal,
            strategy=strategy_id,
        ),
        dispositions(),
    )


def _seed_shadow_lesson(connection) -> None:
    service = LessonLifecycleService(
        connection,
        policy=LessonValidationPolicy(1, 1, 1, timedelta(days=1)),
    )
    service.create_candidate(candidate())
    for role in LessonEvidenceRole:
        service.append_evidence(
            LessonEvidence(
                lesson_evidence_event_id=f"report-{role.value.lower()}",
                lesson_candidate_event_id="lesson-candidate-1",
                strategy_id=StrategyId.SWING_V1,
                strategy_version=strategy_version(StrategyId.SWING_V1),
                role=role,
                proposal_record_id="proposal-SWING_V1",
                timing=timing(available_delta=timedelta(days=2)),
            )
        )
    service.transition(
        LessonTransition(
            lesson_candidate_event_id="report-shadow-1",
            lesson_id="lesson-1",
            strategy_id=StrategyId.SWING_V1,
            strategy_version=strategy_version(StrategyId.SWING_V1),
            revision=1,
            to_status=LessonStatus.SHADOW,
            basis_candidate_event_id="lesson-candidate-1",
            reason="evaluate only",
            provenance=provenance(),
            timing=timing(available_delta=timedelta(days=3)),
        )
    )


def test_query_service_builds_daily_report_from_persisted_read_sides(tmp_path: Path):
    with (
        open_database(tmp_path / "research.sqlite") as research,
        open_database(tmp_path / "ops.sqlite") as ops,
    ):
        migrate_database(research, DatabaseKind.RESEARCH)
        migrate_database(ops, DatabaseKind.OPS)
        leadership = LeadershipRepository(research).ingest(_leadership_snapshot())
        for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1):
            _seed_strategy(research, strategy_id)
        _seed_shadow_lesson(research)
        analysis = PersistedDailyAnalysis(
            job_key="daily:KR:2026-07-24:daily-close",
            run_id=SQLiteAppRunRepository.run_id_for(
                "daily:KR:2026-07-24:daily-close"
            ),
            market=Market.KR,
            as_of_date=date(2026, 7, 24),
            run_type="daily-close",
            evaluated_at=AS_OF + timedelta(days=4),
            data_snapshot_id=DATA_SNAPSHOT_ID,
            leadership_snapshot_id=leadership.snapshot_id,
            leadership_report_id=leadership.report_id,
            quality_decision=QualityDecision(
                QualityDisposition.ACCEPT, (), (), ()
            ),
            quality_skip=None,
            source_payload={"provider": "persisted-fixture"},
            strategies=tuple(
                StrategyAnalysis(
                    strategy_id,
                    strategy_version(strategy_id),
                    {"decision": "NO_ENTRY", "summary": strategy_id.value},
                    (f"analysis-{strategy_id.value}",),
                )
                for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1)
            ),
        )
        run_repository = SQLiteAppRunRepository(ops)
        run_repository.save(analysis)

        report = QueryService(
            research,
            run_repository=run_repository,
        ).daily_report(
            job_key=analysis.job_key,
            leading_sectors=(
                LeadingSector(
                    market=Market.KR,
                    name="Semiconductors",
                    evidence_refs=("sector-evidence",),
                ),
            ),
        )

    assert report.data_snapshot_id == str(DATA_SNAPSHOT_ID)
    assert report.leadership_snapshot_id == leadership.snapshot_id
    assert tuple(section.strategy_id for section in report.strategies) == (
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    )
    assert all(len(section.proposals) == 1 for section in report.strategies)
    assert report.strategies[0].shadow_lesson_ids == ("lesson-1",)
    assert report.strategies[1].shadow_lesson_ids == ()
    assert report.leading_sectors[0].name == "Semiconductors"
    assert report.shadow_status.evaluation_only is True
    assert report.shadow_status.proposal_effect is False


def test_daily_report_fails_visibly_when_persisted_analysis_is_absent(tmp_path: Path):
    with (
        open_database(tmp_path / "research.sqlite") as research,
        open_database(tmp_path / "ops.sqlite") as ops,
    ):
        migrate_database(research, DatabaseKind.RESEARCH)
        migrate_database(ops, DatabaseKind.OPS)

        with pytest.raises(
            ReportUnavailableError,
            match="persisted daily analysis is unavailable",
        ):
            QueryService(
                research,
                run_repository=SQLiteAppRunRepository(ops),
            ).daily_report(
                job_key="daily:KR:2026-07-24:daily-close",
                leading_sectors=(),
            )


def test_daily_report_requires_an_explicit_analysis_read_repository(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as research:
        migrate_database(research, DatabaseKind.RESEARCH)

        with pytest.raises(
            RuntimeError,
            match="daily analysis read repository is not configured",
        ):
            QueryService(research).daily_report(
                job_key="daily:KR:2026-07-24:daily-close",
                leading_sectors=(),
            )


def test_daily_report_fails_with_report_unavailable_when_leadership_is_absent(
    tmp_path: Path,
):
    with (
        open_database(tmp_path / "research.sqlite") as research,
        open_database(tmp_path / "ops.sqlite") as ops,
    ):
        migrate_database(research, DatabaseKind.RESEARCH)
        migrate_database(ops, DatabaseKind.OPS)
        job_key = "daily:KR:2026-07-24:daily-close"
        run_repository = SQLiteAppRunRepository(ops)
        run_repository.save(
            PersistedDailyAnalysis(
                job_key=job_key,
                run_id=SQLiteAppRunRepository.run_id_for(job_key),
                market=Market.KR,
                as_of_date=date(2026, 7, 24),
                run_type="daily-close",
                evaluated_at=AS_OF,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                leadership_snapshot_id="missing-leadership-snapshot",
                leadership_report_id="missing-leadership-report",
                quality_decision=QualityDecision(
                    QualityDisposition.ACCEPT, (), (), ()
                ),
                quality_skip=None,
                source_payload={"provider": "persisted-fixture"},
                strategies=(),
            )
        )

        with pytest.raises(
            ReportUnavailableError,
            match="persisted leadership is unavailable: missing-leadership-snapshot",
        ):
            QueryService(
                research,
                run_repository=run_repository,
            ).daily_report(job_key=job_key, leading_sectors=())


def test_daily_report_preserves_non_accept_quality_skip_without_strategy_data(
    tmp_path: Path,
):
    with (
        open_database(tmp_path / "research.sqlite") as research,
        open_database(tmp_path / "ops.sqlite") as ops,
    ):
        migrate_database(research, DatabaseKind.RESEARCH)
        migrate_database(ops, DatabaseKind.OPS)
        leadership = LeadershipRepository(research).ingest(_leadership_snapshot())
        job_key = "daily:KR:2026-07-24:daily-close"
        skip = QualitySkipRecord(
            request_id=job_key,
            snapshot_id=DATA_SNAPSHOT_ID,
            evaluated_at=AS_OF,
            disposition=QualityDisposition.REPORT_ONLY,
            reasons=("core field is stale",),
            missing_fields=(),
            stale_fields=("price.close",),
        )
        run_repository = SQLiteAppRunRepository(ops)
        run_repository.save(
            PersistedDailyAnalysis(
                job_key=job_key,
                run_id=SQLiteAppRunRepository.run_id_for(job_key),
                market=Market.KR,
                as_of_date=date(2026, 7, 24),
                run_type="daily-close",
                evaluated_at=AS_OF,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                leadership_snapshot_id=leadership.snapshot_id,
                leadership_report_id=leadership.report_id,
                quality_decision=QualityDecision(
                    QualityDisposition.REPORT_ONLY,
                    ("core field is stale",),
                    (),
                    ("price.close",),
                ),
                quality_skip=skip,
                source_payload={"provider": "persisted-fixture"},
                strategies=(),
            )
        )

        report = QueryService(
            research,
            run_repository=run_repository,
        ).daily_report(job_key=job_key, leading_sectors=())

    assert report.analysis_quality.disposition is QualityDisposition.REPORT_ONLY
    assert report.analysis_quality.skipped is True
    assert report.analysis_quality.stale_fields == ("price.close",)
    assert report.strategies == ()


def test_weekly_report_readiness_is_explicitly_unavailable_without_persisted_scenarios(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as research:
        migrate_database(research, DatabaseKind.RESEARCH)

        readiness = QueryService(research).weekly_report_readiness()

    assert readiness == WeeklyReportReadiness(
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
