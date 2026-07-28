"""Thin Phase 1 KR product composition from prepared live inputs to SHADOW readback."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from cores.llm.ports import LLMBackend
from prism_app.daily_pipeline import (
    ApplicationCapabilities,
    DailyPipeline,
    DailyRunRequest,
    PersistedDailyAnalysis,
    PipelineSnapshot,
    SQLiteAppRunRepository,
)
from prism_app.market_snapshot_composer import KRProductSnapshotComposer
from prism_app.report_service import ReportService
from prism_app.shadow_report import (
    ShadowReadback,
    append_shadow_section,
    read_persisted_shadow,
    render_shadow_report,
)
from prism_app.single_runner import LeasedDailyPipeline
from prism_app.strategy_evaluator import (
    StrategyEvaluatorConfig,
    StructuredLLMStrategyEvaluator,
)
from prism_core.data.quality import DataQualityGate
from prism_core.feedback.repository import FeedbackRepository, canonical_json
from prism_core.llm.proposal_service import ProposalService
from prism_core.llm.trade_plan_prompts import get_trade_plan_prompt_contract
from prism_core.ops.job_runs import JobRunStore
from prism_core.policy import ProposalValidationPolicy, ProposalValidator
from prism_core.reporting.leadership_tracking import LeadershipRepository
from prism_core.runtime.settings import ProductMode, RuntimeSettings
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId


@dataclass(frozen=True)
class ProductRunConfig:
    evaluated_at: datetime
    run_type: str
    model_id: str
    model_version: str
    code_version: str
    owner_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.evaluated_at, datetime) or self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        for label in ("run_type", "model_id", "model_version", "code_version", "owner_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")


@dataclass(frozen=True)
class ProductRunResult:
    analysis: PersistedDailyAnalysis
    readback: ShadowReadback
    invocation_id: str
    output_path: Path
    idempotent_replay: bool


class _StaticSnapshotProvider:
    def __init__(self, snapshot: PipelineSnapshot) -> None:
        self._snapshot = snapshot

    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot:
        return self._snapshot


def product_invocation_id(
    snapshot: PipelineSnapshot,
    config: ProductRunConfig,
    evaluator_config: StrategyEvaluatorConfig,
    validation_policy: ProposalValidationPolicy,
    market: Market = Market.KR,
) -> str:
    """Bind app-level replay to all product evaluation provenance."""

    if not isinstance(snapshot, PipelineSnapshot):
        raise TypeError("snapshot must be PipelineSnapshot")
    if not isinstance(config, ProductRunConfig):
        raise TypeError("config must be ProductRunConfig")
    if not isinstance(evaluator_config, StrategyEvaluatorConfig):
        raise TypeError("evaluator_config must be StrategyEvaluatorConfig")
    if not isinstance(validation_policy, ProposalValidationPolicy):
        raise TypeError("validation_policy must be ProposalValidationPolicy")
    prompt_versions = {
        strategy_id.value: get_trade_plan_prompt_contract(
            strategy_id,
            market,
            language=evaluator_config.language,
        ).prompt_version
        for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1)
    }
    strategy_input_provenance = {
        strategy_id.value: {
            "feature_snapshot_id": item.feature_snapshot.feature_snapshot_id,
            "feature_version": item.feature_snapshot.feature_version,
            "quant_score_id": item.quant_score.quant_score_id,
            "score_version": item.quant_score.score_version,
        }
        for strategy_id, item in sorted(
            snapshot.strategy_inputs.items(), key=lambda pair: pair[0].value
        )
    }
    identity_payload = {
        "data_snapshot_id": snapshot.data_snapshot_id,
        "source_payload": dict(snapshot.source_payload),
        "evaluated_at": config.evaluated_at,
        "run_type": config.run_type,
        "strategy_inputs": strategy_input_provenance,
        "evaluator": {
            "model_provider": evaluator_config.model_provider,
            "model_id": evaluator_config.model_id,
            "model_version": evaluator_config.model_version,
            "sampling_version": evaluator_config.sampling_version,
            "sampling": dict(evaluator_config.sampling),
            "policy_version": evaluator_config.policy_version,
            "config_version": evaluator_config.config_version,
            "code_version": evaluator_config.code_version,
            "schema_version": evaluator_config.schema_version,
            "language": evaluator_config.language,
            "max_tokens": evaluator_config.max_tokens,
            "reasoning_effort": evaluator_config.reasoning_effort,
            "max_iterations": evaluator_config.max_iterations,
            "prompt_versions": prompt_versions,
        },
        "validation_policy": {
            "validator_version": validation_policy.validator_version,
            "max_snapshot_age_microseconds": int(
                validation_policy.max_snapshot_age.total_seconds() * 1_000_000
            ),
            "max_risk_multiplier": validation_policy.max_risk_multiplier,
            "max_llm_quant_score_gap": validation_policy.max_llm_quant_score_gap,
            "max_regime_divergence": validation_policy.max_regime_divergence,
        },
    }
    return hashlib.sha256(canonical_json(identity_payload).encode("utf-8")).hexdigest()


async def run_kr_shadow_product(
    *,
    composer: KRProductSnapshotComposer,
    backend: LLMBackend,
    settings: RuntimeSettings,
    config: ProductRunConfig,
    output_path: str | Path,
    base_report_path: str | Path | None = None,
    market: Market = Market.KR,
) -> ProductRunResult:
    """Run one explicit invocation; no scheduler, publisher, account, or broker exists."""

    if settings.product_mode is not ProductMode.SHADOW or settings.broker_enabled:
        raise ValueError("product composition requires SHADOW no-broker settings")
    snapshot = await composer.acquire(as_of=config.evaluated_at)
    validation_policy = ProposalValidationPolicy(
        validator_version="phase1-product-validator.v1",
        max_snapshot_age=timedelta(hours=2),
        max_risk_multiplier=Decimal("0.8"),
        max_llm_quant_score_gap=Decimal("25"),
        max_regime_divergence=Decimal("25"),
    )
    evaluator_config = StrategyEvaluatorConfig(
        model_provider="chatgpt_oauth",
        model_id=config.model_id,
        model_version=config.model_version,
        sampling_version="phase1-oauth-sampling.v2",
        sampling={"temperature": None},
        policy_version="phase1-shadow-policy.v1",
        config_version="phase1-product-composition.v2",
        code_version=config.code_version,
        schema_version="feedback.v1",
        max_tokens=8000,
        reasoning_effort="medium",
    )
    invocation_id = product_invocation_id(
        snapshot,
        config,
        evaluator_config,
        validation_policy,
        market,
    )
    request = DailyRunRequest(
        market=market,
        as_of_date=config.evaluated_at.date(),
        run_type=config.run_type,
        evaluated_at=config.evaluated_at,
        invocation_id=invocation_id,
    )

    with (
        open_database(settings.research_db_path) as research_connection,
        open_database(settings.ops_db_path) as ops_connection,
    ):
        migrate_database(research_connection, DatabaseKind.RESEARCH)
        migrate_database(ops_connection, DatabaseKind.OPS)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=ProposalValidator(validation_policy),
            repository=FeedbackRepository(research_connection),
            config=evaluator_config,
        )
        pipeline = DailyPipeline(
            settings=settings,
            capabilities=ApplicationCapabilities(
                publication_enabled=False,
                shadow_evaluation_enabled=True,
            ),
            snapshot_provider=_StaticSnapshotProvider(snapshot),
            quality_gate=DataQualityGate(),
            strategy_evaluator=evaluator,
            leadership_repository=LeadershipRepository(research_connection),
            run_repository=SQLiteAppRunRepository(ops_connection),
            report_service=ReportService(),
        )
        leased = LeasedDailyPipeline(
            pipeline=pipeline,
            store=JobRunStore(ops_connection),
            owner_id=config.owner_id,
        )
        daily_result = await leased.run(request)
        analysis = daily_result.analysis

    readback = read_persisted_shadow(settings.ops_db_path, job_key=request.job_key)
    if readback.analysis != analysis:
        raise RuntimeError("persisted SHADOW readback diverged from the completed analysis")
    destination = Path(output_path).expanduser()
    base = ""
    if base_report_path is not None:
        base = Path(base_report_path).expanduser().read_text(encoding="utf-8")
    rendered = append_shadow_section(base, render_shadow_report(analysis))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(rendered, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return ProductRunResult(
        analysis=analysis,
        readback=readback,
        invocation_id=invocation_id,
        output_path=destination,
        idempotent_replay=daily_result.idempotent_replay,
    )


async def run_us_shadow_product(
    *,
    composer: KRProductSnapshotComposer,
    backend: LLMBackend,
    settings: RuntimeSettings,
    config: ProductRunConfig,
    output_path: str | Path,
    base_report_path: str | Path | None = None,
) -> ProductRunResult:
    """Run the same no-broker SHADOW composition for the FMP-primary US market."""

    return await run_kr_shadow_product(
        composer=composer,
        backend=backend,
        settings=settings,
        config=config,
        output_path=output_path,
        base_report_path=base_report_path,
        market=Market.US,
    )
