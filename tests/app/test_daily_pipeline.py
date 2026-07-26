from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from prism_app.daily_pipeline import (
    ApplicationCapabilities,
    DailyPipeline,
    DailyRunRequest,
    PipelineSnapshot,
    SQLiteAppRunRepository,
    StrategyAnalysis,
)
from prism_app.report_service import ReportService
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import DataQualityGate
from prism_core.reporting.leadership_tracking import (
    ConfirmationState,
    LeadershipRepository,
    Market as LeadershipMarket,
    MarketRegime,
    MarketStage,
    MarketTrackingSnapshot,
)
from prism_core.runtime.settings import RuntimeSettings
from prism_core.runtime.settings import ProductMode
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market


NOW = datetime(2020, 7, 26, 1, 0, tzinfo=timezone.utc)
SNAPSHOT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _leadership_snapshot() -> MarketTrackingSnapshot:
    return MarketTrackingSnapshot.model_validate(
        {
            "schema_version": "market_tracking_v1",
            "run_id": "kr-20200726-19",
            "revision": 0,
            "market": LeadershipMarket.KR,
            "kst_slot": 19,
            "stage": MarketStage.CLOSE,
            "confirmation_state": ConfirmationState.CONFIRMED,
            "as_of": NOW,
            "observed_at": NOW,
            "available_at": NOW,
            "ingested_at": NOW,
            "source": {
                "report_path": "reports/kr-close.md",
                "report_sha256": "a" * 64,
                "source_urls": (),
                "evidence_refs": ("market-evidence",),
            },
            "quality": DataQualityStatus.FRESH,
            "quality_reasons": (),
            "core_evidence_usable": True,
            "leader_universe_complete": True,
            "market_state": {
                "regime": MarketRegime.MODERATE_BULL,
                "summary": "Constructive close.",
                "evidence_refs": ("market-evidence",),
            },
            "events": (),
            "leaders": (),
        }
    )


class SnapshotProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot:
        self.calls += 1
        return PipelineSnapshot(
            data_snapshot_id=SNAPSHOT_ID,
            field_quality={
                "calendar": DataQualityStatus.FRESH,
                "evidence": DataQualityStatus.FRESH,
                "price": DataQualityStatus.FRESH,
                "regime": DataQualityStatus.FRESH,
            },
            leadership_snapshot=_leadership_snapshot(),
            source_payload={"provider": "fixture", "snapshot": "complete"},
        )


class StrategyEvaluator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def evaluate(self, request):
        identity = (request.strategy.strategy_id.value, request.strategy.version.value)
        self.calls.append(identity)
        return StrategyAnalysis(
            strategy_id=request.strategy.strategy_id,
            strategy_version=request.strategy.version,
            output_payload={"decision": "NO_ENTRY", "strategy": identity[0]},
            evidence_refs=(f"evidence-{identity[0]}",),
        )


class StaleSnapshotProvider(SnapshotProvider):
    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot:
        snapshot = await super().acquire(request)
        return PipelineSnapshot(
            data_snapshot_id=snapshot.data_snapshot_id,
            field_quality={**snapshot.field_quality, "price": DataQualityStatus.STALE},
            leadership_snapshot=snapshot.leadership_snapshot,
            source_payload=snapshot.source_payload,
        )


class Publisher:
    def __init__(self, ops_connection, research_connection) -> None:
        self.calls = 0
        self._ops_connection = ops_connection
        self._research_connection = research_connection

    async def publish(self, analysis) -> None:
        self.calls += 1
        assert self._ops_connection.execute(
            "SELECT status FROM job_runs WHERE run_id = ?", (analysis.run_id,)
        ).fetchone() == ("ANALYSIS_PERSISTED",)
        assert self._research_connection.execute(
            "SELECT COUNT(*) FROM reports WHERE report_id = ?",
            (analysis.leadership_report_id,),
        ).fetchone() == (1,)


class FailingPublisher:
    async def publish(self, analysis) -> None:
        raise TimeoutError("fixture publication timeout")


class FailOnceRunRepository:
    def __init__(self, repository: SQLiteAppRunRepository) -> None:
        self._repository = repository
        self._failed = False

    def get(self, job_key: str):
        return self._repository.get(job_key)

    def save(self, analysis):
        if not self._failed:
            self._failed = True
            raise OSError("fixture ops persistence failure")
        return self._repository.save(analysis)


class WrongIdentityEvaluator(StrategyEvaluator):
    async def evaluate(self, request):
        output = await super().evaluate(request)
        if request.strategy.strategy_id.value == "SWING_V1":
            return StrategyAnalysis(
                strategy_id=request.strategy.strategy_id,
                strategy_version=type(request.strategy.version)("wrong-v9.9.9"),
                output_payload=output.output_payload,
                evidence_refs=output.evidence_refs,
            )
        return output


@pytest.mark.asyncio
async def test_pipeline_persists_complete_analysis_before_publication(tmp_path: Path):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    provider = SnapshotProvider()
    evaluator = StrategyEvaluator()

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        publisher = Publisher(ops_connection, research_connection)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(publication_enabled=True),
            snapshot_provider=provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(publisher),
        )

        result = await pipeline.run(
            DailyRunRequest(
                market=Market.KR,
                as_of_date=date(2020, 7, 26),
                run_type="daily-close",
                evaluated_at=NOW,
            )
        )

    assert result.analysis.quality_decision.disposition.value == "ACCEPT"
    assert [item.strategy_id.value for item in result.analysis.strategies] == [
        "SWING_V1",
        "TREND_V1",
    ]
    assert evaluator.calls == [("SWING_V1", "1.0.0"), ("TREND_V1", "1.0.0")]
    assert result.publication.succeeded is True
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_pipeline_rerun_returns_persisted_job_without_repeating_work(
    tmp_path: Path,
):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    provider = SnapshotProvider()
    evaluator = StrategyEvaluator()
    request = DailyRunRequest(
        market=Market.KR,
        as_of_date=date(2020, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        publisher = Publisher(ops_connection, research_connection)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(publication_enabled=True),
            snapshot_provider=provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(publisher),
        )

        first = await pipeline.run(request)
        replay = await pipeline.run(request)

    assert replay.analysis == first.analysis
    assert replay.idempotent_replay is True
    assert replay.publication.attempted is False
    assert provider.calls == 1
    assert len(evaluator.calls) == 2
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_retry_after_ops_save_failure_reuses_leadership_identity(tmp_path: Path):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    provider = SnapshotProvider()
    evaluator = StrategyEvaluator()
    request = DailyRunRequest(
        market=Market.KR,
        as_of_date=date(2020, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        publisher = Publisher(ops_connection, research_connection)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(publication_enabled=True),
            snapshot_provider=provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=FailOnceRunRepository(
                SQLiteAppRunRepository(ops_connection)
            ),
            report_service=ReportService(publisher),
        )

        with pytest.raises(OSError, match="ops persistence failure"):
            await pipeline.run(request)
        result = await pipeline.run(request)
        report_count = research_connection.execute(
            "SELECT COUNT(*) FROM reports WHERE report_id = ?",
            (result.analysis.leadership_report_id,),
        ).fetchone()[0]

    assert report_count == 1
    assert provider.calls == 2
    assert len(evaluator.calls) == 4
    assert result.publication.succeeded is True
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_strategy_evaluator_cannot_cross_exact_version_boundary(tmp_path: Path):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(),
            snapshot_provider=SnapshotProvider(),
            quality_gate=DataQualityGate(),
            strategy_evaluator=WrongIdentityEvaluator(),
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(),
        )

        with pytest.raises(ValueError, match="mismatched exact identity"):
            await pipeline.run(
                DailyRunRequest(
                    market=Market.KR,
                    as_of_date=date(2020, 7, 26),
                    run_type="daily-close",
                    evaluated_at=NOW,
                )
            )
        persisted_count = ops_connection.execute(
            "SELECT COUNT(*) FROM job_runs"
        ).fetchone()[0]

    assert persisted_count == 0


@pytest.mark.asyncio
async def test_stale_core_data_persists_leadership_and_skips_all_proposals(
    tmp_path: Path,
):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    provider = StaleSnapshotProvider()
    evaluator = StrategyEvaluator()

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(),
            snapshot_provider=provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(),
        )

        result = await pipeline.run(
            DailyRunRequest(
                market=Market.KR,
                as_of_date=date(2020, 7, 26),
                run_type="daily-close",
                evaluated_at=NOW,
            )
        )
        report_count = research_connection.execute(
            "SELECT COUNT(*) FROM reports WHERE report_id = ?",
            (result.analysis.leadership_report_id,),
        ).fetchone()[0]

    assert result.analysis.quality_decision.disposition.value == "REJECT"
    assert result.analysis.quality_skip is not None
    assert result.analysis.quality_skip.stale_fields == ("price",)
    assert result.analysis.strategies == ()
    assert evaluator.calls == []
    assert report_count == 1
    assert result.publication.attempted is False


@pytest.mark.asyncio
async def test_publication_failure_cannot_erase_persisted_analysis(tmp_path: Path):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(publication_enabled=True),
            snapshot_provider=SnapshotProvider(),
            quality_gate=DataQualityGate(),
            strategy_evaluator=StrategyEvaluator(),
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(FailingPublisher()),
        )

        result = await pipeline.run(
            DailyRunRequest(
                market=Market.KR,
                as_of_date=date(2020, 7, 26),
                run_type="daily-close",
                evaluated_at=NOW,
            )
        )
        stored = ops_connection.execute(
            "SELECT status FROM job_runs WHERE run_id = ?", (result.analysis.run_id,)
        ).fetchone()

    assert stored == ("ANALYSIS_PERSISTED",)
    assert result.publication.attempted is True
    assert result.publication.succeeded is False
    assert result.publication.error_type == "TimeoutError"


def test_enabled_publication_requires_an_explicit_injected_transport(tmp_path: Path):
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        with pytest.raises(ValueError, match="publication capability"):
            DailyPipeline(
                settings=settings,
                capabilities=ApplicationCapabilities(publication_enabled=True),
                snapshot_provider=SnapshotProvider(),
                quality_gate=DataQualityGate(),
                strategy_evaluator=StrategyEvaluator(),
                leadership_repository=LeadershipRepository(research_connection),
                run_repository=SQLiteAppRunRepository(ops_connection),
                report_service=ReportService(),
            )


def test_broker_paper_capability_is_rejected_at_the_application_boundary(tmp_path: Path):
    settings = RuntimeSettings(
        product_mode=ProductMode.BROKER_PAPER,
        broker_enabled=True,
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        with pytest.raises(ValueError, match="Phase 1 no-broker"):
            DailyPipeline(
                settings=settings,
                capabilities=ApplicationCapabilities(),
                snapshot_provider=SnapshotProvider(),
                quality_gate=DataQualityGate(),
                strategy_evaluator=StrategyEvaluator(),
                leadership_repository=LeadershipRepository(research_connection),
                run_repository=SQLiteAppRunRepository(ops_connection),
                report_service=ReportService(),
            )
