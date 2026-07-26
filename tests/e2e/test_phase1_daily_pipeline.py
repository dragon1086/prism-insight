from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

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
from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import DataQualityGate, QualityDisposition
from prism_core.llm.proposal_service import ProposalParseStatus, ProposalService
from prism_core.policy import (
    ProposalValidationPolicy,
    ProposalValidationStatus,
    ProposalValidator,
)
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
    StrategyVersion,
)


NOW = datetime(2020, 7, 26, 20, 0, tzinfo=timezone.utc)
EVIDENCE_IDS = frozenset({"fixture-price", "fixture-risk"})


def _feature_snapshot() -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000103"),
        strategy_id=StrategyId.SWING_V1,
        strategy_version=StrategyVersion("1.0.0"),
        market=Market.US,
        security_id=SecurityId(
            value=UUID("00000000-0000-0000-0000-000000000102")
        ),
        data_snapshot_id=UUID("00000000-0000-0000-0000-000000000104"),
        as_of=NOW,
        feature_version="features.v1",
        values=(
            FeatureValue(name="swing_v1.price_momentum_5d", value=Decimal("0.05")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def _quant_score(snapshot: FeatureSnapshot) -> QuantScoreBreakdown:
    return QuantScoreBreakdown(
        quant_score_id=UUID("00000000-0000-0000-0000-000000000105"),
        feature_snapshot_id=snapshot.feature_snapshot_id,
        strategy_id=snapshot.strategy_id,
        strategy_version=snapshot.strategy_version,
        market=snapshot.market,
        security_id=snapshot.security_id,
        score_version="score.v1",
        total_score=Decimal("70"),
        components=(
            QuantScoreComponent(name="swing_v1.quant_total", score=Decimal("70")),
        ),
    )


class FixtureSnapshotProvider:
    def __init__(self, *, market: Market, provider: str, stale: bool = False) -> None:
        self.market = market
        self.provider = provider
        self.stale = stale
        self.calls = 0

    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot:
        self.calls += 1
        assert request.market is self.market
        quality = DataQualityStatus.STALE if self.stale else DataQualityStatus.FRESH
        snapshot_id = uuid5(NAMESPACE_URL, f"phase1-e2e:{self.market.value}:{self.provider}")
        leadership = MarketTrackingSnapshot.model_validate(
            {
                "schema_version": "market_tracking_v1",
                "run_id": f"fixture-{self.market.value.lower()}-daily",
                "revision": 0,
                "market": LeadershipMarket(self.market.value),
                "kst_slot": 19 if self.market is Market.KR else 7,
                "stage": MarketStage.CLOSE,
                "confirmation_state": ConfirmationState.CONFIRMED,
                "as_of": NOW,
                "observed_at": NOW,
                "available_at": NOW,
                "ingested_at": NOW,
                "source": {
                    "report_path": f"fixtures/{self.market.value.lower()}-daily.md",
                    "report_sha256": "a" * 64,
                    "source_urls": (),
                    "evidence_refs": (f"{self.provider.lower()}-fixture",),
                },
                "quality": quality,
                "quality_reasons": ("fixture stale price",) if self.stale else (),
                "core_evidence_usable": not self.stale,
                "leader_universe_complete": not self.stale,
                "market_state": {
                    "regime": MarketRegime.MODERATE_BULL,
                    "summary": f"{self.market.value} fixture close",
                    "evidence_refs": (f"{self.provider.lower()}-fixture",),
                },
                "events": (),
                "leaders": (),
            }
        )
        return PipelineSnapshot(
            data_snapshot_id=snapshot_id,
            field_quality={
                "calendar": DataQualityStatus.FRESH,
                "evidence": DataQualityStatus.FRESH,
                "price": quality,
                "regime": DataQualityStatus.FRESH,
            },
            leadership_snapshot=leadership,
            source_payload={
                "provider": self.provider,
                "evidence_level": "FIXTURE_CONTRACT",
                "live_transport_exercised": False,
                "runtime_provider_wiring_verified": False,
            },
        )


class FixtureStrategyEvaluator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(self, request) -> StrategyAnalysis:
        self.calls.append(request.strategy.strategy_id.value)
        return StrategyAnalysis(
            strategy_id=request.strategy.strategy_id,
            strategy_version=request.strategy.version,
            output_payload={"decision": "NO_ENTRY", "source": "fixture"},
            evidence_refs=(f"fixture-{request.strategy.strategy_id.value}",),
        )


async def _run_daily_fixture(
    tmp_path: Path, *, market: Market, provider: str, stale: bool = False
):
    settings = RuntimeSettings(
        research_db_path=tmp_path / f"{market.value}-research.sqlite",
        ops_db_path=tmp_path / f"{market.value}-ops.sqlite",
    )
    snapshot_provider = FixtureSnapshotProvider(
        market=market, provider=provider, stale=stale
    )
    evaluator = FixtureStrategyEvaluator()
    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(),
            snapshot_provider=snapshot_provider,
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(),
        )
        result = await pipeline.run(
            DailyRunRequest(
                market=market,
                as_of_date=NOW.date(),
                run_type="daily-close",
                evaluated_at=NOW,
            )
        )
        report_count = research_connection.execute(
            "SELECT count(*) FROM reports WHERE report_id = ?",
            (result.analysis.leadership_report_id,),
        ).fetchone()[0]
    return result, snapshot_provider, evaluator, report_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("market", "provider"),
    ((Market.KR, "KIS_FIXTURE"), (Market.US, "FMP_FIXTURE")),
)
async def test_kr_us_daily_reports_are_generated_from_explicit_provider_fixtures(
    tmp_path: Path, market: Market, provider: str
) -> None:
    result, snapshot_provider, evaluator, report_count = await _run_daily_fixture(
        tmp_path, market=market, provider=provider
    )

    assert result.analysis.market is market
    assert result.analysis.source_payload == {
        "provider": provider,
        "evidence_level": "FIXTURE_CONTRACT",
        "live_transport_exercised": False,
        "runtime_provider_wiring_verified": False,
    }
    assert result.analysis.quality_decision.disposition.value == "ACCEPT"
    assert [item.strategy_id.value for item in result.analysis.strategies] == [
        "SWING_V1",
        "TREND_V1",
    ]
    assert report_count == 1
    assert snapshot_provider.calls == 1
    assert evaluator.calls == ["SWING_V1", "TREND_V1"]
    assert result.publication.attempted is False


@pytest.mark.asyncio
async def test_stale_fmp_fixture_persists_report_only_evidence_and_blocks_proposals(
    tmp_path: Path,
) -> None:
    result, _, evaluator, report_count = await _run_daily_fixture(
        tmp_path, market=Market.US, provider="FMP_FIXTURE", stale=True
    )

    assert result.analysis.quality_decision.disposition.value == "REJECT"
    assert result.analysis.quality_skip is not None
    assert result.analysis.quality_skip.stale_fields == ("price",)
    assert result.analysis.strategies == ()
    assert evaluator.calls == []
    assert report_count == 1
    assert result.publication.attempted is False


def test_malformed_llm_output_is_rejected_before_deterministic_policy() -> None:
    snapshot = _feature_snapshot()
    parsed = ProposalService().parse(
        raw_response='{"proposal_id":',
        feature_snapshot=snapshot,
        available_evidence_ids=EVIDENCE_IDS,
    )
    validated = ProposalValidator(
        ProposalValidationPolicy(
            validator_version="phase1-e2e.v1",
            max_snapshot_age=timedelta(hours=2),
            max_risk_multiplier=Decimal("0.8"),
            max_llm_quant_score_gap=Decimal("20"),
            max_regime_divergence=Decimal("15"),
        )
    ).validate(
        parse_result=parsed,
        feature_snapshot=snapshot,
        quant_score=_quant_score(snapshot),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=NOW + timedelta(hours=1),
    )

    assert parsed.status is ProposalParseStatus.REJECTED
    assert parsed.proposal is None
    assert parsed.raw_response == '{"proposal_id":'
    assert validated.status is ProposalValidationStatus.REJECTED
    assert validated.proposal is None
    assert validated.reasons == parsed.errors
