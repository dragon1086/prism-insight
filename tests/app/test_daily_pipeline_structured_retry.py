from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from cores.llm.fakes import FakeLLMBackend
from cores.llm.ports import LLMResult
from prism_app.daily_pipeline import (
    ApplicationCapabilities,
    DailyPipeline,
    DailyRunRequest,
    PipelineSnapshot,
    SQLiteAppRunRepository,
    StrategyEvaluationInput,
)
from prism_app.report_service import ReportService
from prism_app.strategy_evaluator import (
    StrategyEvaluatorConfig,
    StructuredLLMStrategyEvaluator,
)
from prism_core.data.contracts import (
    DataQualityStatus,
    ObservationTime,
    SecurityId,
)
from prism_core.data.quality import DataQualityGate, QualityDisposition
from prism_core.feedback.repository import FeedbackRepository
from prism_core.llm.proposal_service import ProposalService
from prism_core.policy import ProposalValidationPolicy, ProposalValidator
from prism_core.reporting.leadership_tracking import (
    ConfirmationState,
    LeadershipRepository,
    Market as LeadershipMarket,
    MarketRegime,
    MarketStage,
    MarketTrackingSnapshot,
)
from prism_core.runtime.settings import RuntimeSettings
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    QuantScoreBreakdown,
    QuantScoreComponent,
    StrategyId,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
DATA_SNAPSHOT_ID = UUID("10000000-0000-0000-0000-000000000001")
SECURITY_ID = SecurityId(value=UUID("10000000-0000-0000-0000-000000000002"))


def _strategy_input(strategy_id: StrategyId) -> StrategyEvaluationInput:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(strategy_id)
    suffix = 3 if strategy_id is StrategyId.SWING_V1 else 4
    feature_id = UUID(f"10000000-0000-0000-0000-{suffix:012d}")
    score_id = UUID(f"10000000-0000-0000-0000-{suffix + 10:012d}")
    prefix = strategy_id.value.lower()
    feature = FeatureSnapshot(
        feature_snapshot_id=feature_id,
        strategy_id=strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SECURITY_ID,
        data_snapshot_id=DATA_SNAPSHOT_ID,
        as_of=NOW,
        feature_version=f"{prefix}.features.v1",
        values=(FeatureValue(name=f"{prefix}.fixture", value=Decimal("1")),),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )
    score = QuantScoreBreakdown(
        quant_score_id=score_id,
        feature_snapshot_id=feature_id,
        strategy_id=strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SECURITY_ID,
        score_version=f"{prefix}.score.v1",
        total_score=Decimal("50"),
        components=(
            QuantScoreComponent(name=f"{prefix}.fixture", score=Decimal("50")),
        ),
    )
    return StrategyEvaluationInput(
        feature_snapshot=feature,
        quant_score=score,
        available_evidence_ids=frozenset({f"evidence-{prefix}"}),
        evidence_payload={f"evidence-{prefix}": {"kind": "fixture"}},
        timing=ObservationTime(
            observed_at=NOW,
            available_at=NOW,
            ingested_at=NOW + timedelta(seconds=1),
            as_of_date=NOW,
        ),
    )


def _leadership_snapshot() -> MarketTrackingSnapshot:
    return MarketTrackingSnapshot.model_validate(
        {
            "schema_version": "market_tracking_v1",
            "run_id": "us-20260726-close",
            "revision": 0,
            "market": LeadershipMarket.US,
            "kst_slot": 7,
            "stage": MarketStage.CLOSE,
            "confirmation_state": ConfirmationState.CONFIRMED,
            "as_of": NOW,
            "observed_at": NOW,
            "available_at": NOW,
            "ingested_at": NOW,
            "source": {
                "report_path": "reports/us-close.md",
                "report_sha256": "b" * 64,
                "source_urls": (),
                "evidence_refs": ("market-evidence",),
            },
            "quality": DataQualityStatus.FRESH,
            "quality_reasons": (),
            "core_evidence_usable": True,
            "leader_universe_complete": True,
            "market_state": {
                "regime": MarketRegime.MODERATE_BULL,
                "summary": "Fixture close.",
                "evidence_refs": ("market-evidence",),
            },
            "events": (),
            "leaders": (),
        }
    )


class StableSnapshotProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot:
        self.calls += 1
        return PipelineSnapshot(
            data_snapshot_id=DATA_SNAPSHOT_ID,
            field_quality={
                "calendar": DataQualityStatus.FRESH,
                "evidence": DataQualityStatus.FRESH,
                "price": DataQualityStatus.FRESH,
                "regime": DataQualityStatus.FRESH,
            },
            leadership_snapshot=_leadership_snapshot(),
            source_payload={"provider": "FMP_FIXTURE", "snapshot": "stable"},
            strategy_inputs={
                strategy_id: _strategy_input(strategy_id)
                for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1)
            },
        )


class FailOnceRunRepository:
    def __init__(self, delegate: SQLiteAppRunRepository) -> None:
        self._delegate = delegate
        self._failed = False

    def get(self, job_key: str):
        return self._delegate.get(job_key)

    def save(self, analysis):
        if not self._failed:
            self._failed = True
            raise OSError("injected ops save failure")
        return self._delegate.save(analysis)


class FailSecondStrategyOnce:
    def __init__(self, delegate: StructuredLLMStrategyEvaluator) -> None:
        self._delegate = delegate
        self._failed = False

    async def evaluate(self, request):
        if request.strategy.strategy_id is StrategyId.TREND_V1 and not self._failed:
            self._failed = True
            raise OSError("injected second strategy failure")
        return await self._delegate.evaluate(request)


def _evaluator(repository: FeedbackRepository, backend: FakeLLMBackend):
    return StructuredLLMStrategyEvaluator(
        backend=backend,
        proposal_service=ProposalService(),
        validator=ProposalValidator(
            ProposalValidationPolicy(
                validator_version="validator.v1",
                max_snapshot_age=timedelta(hours=2),
                max_risk_multiplier=Decimal("0.8"),
                max_llm_quant_score_gap=Decimal("20"),
                max_regime_divergence=Decimal("20"),
            )
        ),
        repository=repository,
        config=StrategyEvaluatorConfig(
            model_provider="fixture",
            model_id="fixture-model",
            model_version="fixture-v1",
            sampling_version="sampling.v1",
            sampling={"temperature": Decimal("0")},
            policy_version="policy.v1",
            config_version="config.v1",
            code_version="test-sha",
            schema_version="feedback.v1",
        ),
    )


def _request() -> DailyRunRequest:
    return DailyRunRequest(
        market=Market.US,
        as_of_date=date(2026, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW + timedelta(minutes=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["second_strategy", "ops_save"])
async def test_daily_pipeline_retry_reuses_persisted_proposals_without_llm_recall(
    tmp_path: Path, failure_point: str
) -> None:
    settings = RuntimeSettings(
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    provider = StableSnapshotProvider()
    backend = FakeLLMBackend(
        [LLMResult(text='{"swing":'), LLMResult(text='{"trend":')]
    )

    with (
        open_database(settings.research_db_path) as research,
        open_database(settings.ops_db_path) as ops,
    ):
        migrate_database(research, DatabaseKind.RESEARCH)
        migrate_database(ops, DatabaseKind.OPS)
        structured = _evaluator(FeedbackRepository(research), backend)
        evaluator = (
            FailSecondStrategyOnce(structured)
            if failure_point == "second_strategy"
            else structured
        )
        base_run_repository = SQLiteAppRunRepository(ops)
        run_repository = (
            FailOnceRunRepository(base_run_repository)
            if failure_point == "ops_save"
            else base_run_repository
        )
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(),
            snapshot_provider=provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research),
            run_repository=run_repository,
            report_service=ReportService(),
        )

        with pytest.raises(OSError, match=failure_point.replace("_", " ")):
            await pipeline.run(_request())
        result = await pipeline.run(_request())

        proposal_count = research.execute(
            "SELECT COUNT(*) FROM trade_plan_proposals"
        ).fetchone()[0]
        report_count = research.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        ops_count = ops.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0]

    assert len(backend.calls) == 2
    assert proposal_count == 2
    assert report_count == 1
    assert ops_count == 1
    assert tuple(item.output_payload["status"] for item in result.analysis.strategies) == (
        "REJECTED",
        "REJECTED",
    )
    assert provider.calls == 2
