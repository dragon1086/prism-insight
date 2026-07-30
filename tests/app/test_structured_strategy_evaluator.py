from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

import pytest

from cores.llm.backends.openai_agents_backend import build_agent
from cores.llm.fakes import FakeLLMBackend
from cores.llm.ports import AgentSpec, DeferredValidationSchema, LLMResult
from prism_app.daily_pipeline import StrategyEvaluationInput, StrategyEvaluationRequest
from prism_app.strategy_evaluator import StructuredLLMStrategyEvaluator, StrategyEvaluatorConfig
from prism_core.data.contracts import DataQualityStatus, ObservationTime, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.feedback.repository import FeedbackRepository, canonical_json
from prism_core.llm.proposal_service import ProposalService
from prism_core.llm.trade_plan import TradePlanProposal
from prism_core.llm.trade_plan_prompts import get_trade_plan_prompt_contract
from prism_core.policy import ProposalValidationPolicy, ProposalValidator
from prism_core.policy.dispositions import DispositionAction
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
from prism_core.strategies.quant_score import QuantScoreService, shadow_score_v1_policy
from tests.llm.test_trade_plan_schema import valid_proposal_payload


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000102"))
DATA_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000104")
FEATURE_SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000103")
QUANT_SCORE_ID = UUID("00000000-0000-0000-0000-000000000105")


def _strategy_input() -> StrategyEvaluationInput:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    feature_snapshot = FeatureSnapshot(
        feature_snapshot_id=FEATURE_SNAPSHOT_ID,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SECURITY_ID,
        data_snapshot_id=DATA_SNAPSHOT_ID,
        as_of=NOW,
        feature_version="features.v1",
        values=(
            FeatureValue(name="swing_v1.price_momentum_5d", value=Decimal("0.05")),
            FeatureValue(name="swing_v1.regime_compatibility", value=Decimal("60")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )
    quant_score = QuantScoreBreakdown(
        quant_score_id=QUANT_SCORE_ID,
        feature_snapshot_id=FEATURE_SNAPSHOT_ID,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.US,
        security_id=SECURITY_ID,
        score_version="score.v1",
        total_score=Decimal("70"),
        components=(
            QuantScoreComponent(name="swing_v1.quant_total", score=Decimal("70")),
        ),
    )
    return StrategyEvaluationInput(
        feature_snapshot=feature_snapshot,
        quant_score=quant_score,
        available_evidence_ids=frozenset({"ev-price-1", "ev-risk-1"}),
        evidence_payload={
            "ev-price-1": {"kind": "price", "summary": "fixture price evidence"},
            "ev-risk-1": {"kind": "risk", "summary": "fixture risk evidence"},
        },
        timing=ObservationTime(
            observed_at=NOW,
            available_at=NOW,
            ingested_at=NOW + timedelta(seconds=1),
            as_of_date=NOW,
        ),
    )


def _shadow_strategy_input() -> StrategyEvaluationInput:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
    feature_snapshot = FeatureSnapshot(
        feature_snapshot_id=FEATURE_SNAPSHOT_ID,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.version,
        market=Market.KR,
        security_id=SECURITY_ID,
        data_snapshot_id=DATA_SNAPSHOT_ID,
        as_of=NOW,
        feature_version="SHADOW_FEATURES_V1",
        values=(
            FeatureValue("swing_v1.price_return_5d_percent", Decimal("10")),
            FeatureValue(
                "swing_v1.benchmark_excess_return_20d_percentage_points",
                Decimal("20"),
            ),
            FeatureValue("swing_v1.volume_expansion_20d_percent", Decimal("200")),
            FeatureValue("swing_v1.atr_percent_14d", Decimal("2")),
            FeatureValue("swing_v1.regime_compatibility", Decimal("100")),
            FeatureValue("swing_v1.average_volume_20d_shares", Decimal("100000")),
            FeatureValue("swing_v1.breakout_distance_20d_percent", Decimal("0.5")),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )
    policy = shadow_score_v1_policy(StrategyId.SWING_V1, Market.KR)
    return StrategyEvaluationInput(
        feature_snapshot=feature_snapshot,
        quant_score=QuantScoreService().score(strategy, feature_snapshot, policy),
        available_evidence_ids=frozenset({"ev-price-1", "ev-risk-1"}),
        evidence_payload={
            "ev-price-1": {"kind": "price", "summary": "fixture price evidence"},
            "ev-risk-1": {"kind": "risk", "summary": "fixture risk evidence"},
        },
        timing=ObservationTime(
            observed_at=NOW,
            available_at=NOW,
            ingested_at=NOW + timedelta(seconds=1),
            as_of_date=NOW,
        ),
        hard_vetoes=("risk:fixture-veto",),
    )


def _config() -> StrategyEvaluatorConfig:
    return StrategyEvaluatorConfig(
        model_provider="fixture",
        model_id="fixture-model",
        model_version="fixture-v1",
        sampling_version="sampling.v1",
        sampling={"temperature": Decimal("0")},
        policy_version="policy.v1",
        config_version="config.v1",
        code_version="test-sha",
        schema_version="feedback.v1",
        language="ko",
    )


def _validator() -> ProposalValidator:
    return ProposalValidator(
        ProposalValidationPolicy(
            validator_version="validator.v1",
            max_snapshot_age=timedelta(hours=2),
            max_risk_multiplier=Decimal("0.8"),
            max_llm_quant_score_gap=Decimal("20"),
            max_regime_divergence=Decimal("20"),
        )
    )


def _accepted_proposal() -> TradePlanProposal:
    strategy_input = _strategy_input()
    feature = strategy_input.feature_snapshot
    config = _config()
    payload = valid_proposal_payload()
    payload["strategy_version"] = feature.strategy_version
    payload["market"] = feature.market
    payload["security_id"] = feature.security_id
    payload["feature_provenance"] = {
        "feature_snapshot_id": feature.feature_snapshot_id,
        "data_snapshot_id": feature.data_snapshot_id,
        "as_of": feature.as_of,
        "feature_version": feature.feature_version,
        "data_quality_status": feature.data_quality_status,
        "quality_disposition": feature.quality_disposition,
    }
    payload["model"] = {
        "provider": config.model_provider,
        "model_id": config.model_id,
        "model_version": config.model_version,
    }
    payload["prompt_version"] = get_trade_plan_prompt_contract(
        feature.strategy_id, feature.market, language=config.language
    ).prompt_version
    payload["sampling"] = {
        "version": config.sampling_version,
        "temperature": config.sampling["temperature"],
        "top_p": None,
        "seed": None,
    }
    return TradePlanProposal.model_validate(payload)


def _model_json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    model_value = getattr(value, "value", None)
    if isinstance(model_value, str):
        return model_value
    raise TypeError(f"unsupported model fixture value: {type(value).__name__}")


def test_full_trade_plan_deferred_schema_accepts_numeric_json_shape() -> None:
    agent = build_agent(
        AgentSpec(
            name="trade-plan-shape-contract",
            instructions="Return one structured trade plan.",
            model="gpt-5.6-sol",
            output_schema=DeferredValidationSchema(TradePlanProposal),
        ),
        [],
    )
    raw_response = json.dumps(
        _accepted_proposal().model_dump(mode="python"),
        default=_model_json_default,
    )

    decoded = agent.output_type.validate_json(raw_response)

    assert isinstance(decoded, dict)
    assert isinstance(decoded["regime"]["probabilities"]["strong_bull"], Decimal)
    assert "minimum" not in agent.output_type.json_schema()["$defs"]["RegimeProbabilities"][
        "properties"
    ]["strong_bull"]
    assert agent.output_type.json_schema()["$defs"]["PyramidingCandidate"]["properties"][
        "requires_profitable_position"
    ]["type"] == "boolean"


@pytest.mark.asyncio
async def test_accepted_proposal_persists_real_call_provenance_and_replays(
    tmp_path: Path,
) -> None:
    backend = FakeLLMBackend([LLMResult(structured=_accepted_proposal())])
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )

        first = await evaluator.evaluate(request)
        replay = await evaluator.evaluate(request)

        assert first.output_payload["status"] == "ACCEPTED"
        assert first.output_payload["decision"] == "ENTRY_CANDIDATE"
        assert first.output_payload["quant_score"] == {
            "score_version": "score.v1",
            "total_score": "70",
            "components": {"swing_v1.quant_total": "70"},
        }
        assert replay == first
        assert len(backend.calls) == 1
        stored = repository.proposals_as_of(
            request.evaluated_at, strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 1
        assert first.output_payload["proposal_record_id"] == stored[0].proposal_record_id
        assert stored[0].model_provider == _config().model_provider
        assert stored[0].model_id == _config().model_id
        assert stored[0].model_version == _config().model_version
        assert canonical_json(stored[0].sampling) == canonical_json(
            {"temperature": Decimal("0"), "top_p": None, "seed": None}
        )


@pytest.mark.asyncio
async def test_deterministic_code_recalculates_model_echoed_identity_and_preserves_raw_output(
    tmp_path: Path,
) -> None:
    payload = json.loads(_accepted_proposal().model_dump_json())
    payload["strategy_id"] = "TREND_V1"
    payload["market"] = "KR"
    payload["security_id"] = "00000000-0000-0000-0000-000000000999"
    payload["feature_provenance"]["as_of"] = "2026-07-26T08:00:00+09:00"
    payload["model"]["provider"] = "model-echoed-provider"
    payload["prompt_version"] = "model-echoed-prompt"
    payload["sampling"]["version"] = "model-echoed-sampling"
    backend = FakeLLMBackend([LLMResult(structured=payload)])

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )

        result = await evaluator.evaluate(request)
        stored = repository.proposals_as_of(
            request.evaluated_at, strategy_id=StrategyId.SWING_V1
        )[0]

    assert result.output_payload["status"] == "ACCEPTED", result.output_payload["scenario_reasons"]
    assert '"model-echoed-provider"' in stored.raw_output
    assert stored.normalized_proposal_json is not None
    normalized = json.loads(stored.normalized_proposal_json)
    assert normalized["strategy_id"] == "SWING_V1"
    assert normalized["market"] == "US"
    assert normalized["security_id"] == {"value": str(SECURITY_ID.value)}
    assert normalized["feature_provenance"]["as_of"] == NOW.isoformat().replace("+00:00", "Z")
    assert normalized["model"]["provider"] == _config().model_provider
    assert normalized["prompt_version"] != "model-echoed-prompt"
    assert normalized["sampling"]["version"] == _config().sampling_version
    recalculated = {
        item.field_path
        for item in stored.dispositions
        if item.action is DispositionAction.RECALCULATE
    }
    assert {
        "strategy_id",
        "market",
        "security_id",
        "feature_provenance.as_of",
        "model.provider",
        "prompt_version",
        "sampling.version",
    } <= recalculated


def test_sampling_provenance_rejects_parameters_the_backend_port_cannot_apply() -> None:
    with pytest.raises(ValueError, match="explicit temperature"):
        replace(_config(), sampling={})
    oauth_config = replace(_config(), sampling={"temperature": None})
    assert oauth_config.sampling == {"temperature": None}
    with pytest.raises(ValueError, match="unsupported sampling"):
        replace(_config(), sampling={"temperature": 0, "top_p": 0.9})

    with pytest.raises(TypeError, match="temperature must be numeric"):
        replace(_config(), sampling={"temperature": True})


@pytest.mark.asyncio
async def test_malformed_model_output_is_persisted_rejected_without_actionable_decision(
    tmp_path: Path,
) -> None:
    backend = FakeLLMBackend([LLMResult(text='{"proposal_id":')])
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )

        assert result.output_payload["status"] == "REJECTED"
        assert result.output_payload["decision"] is None
        assert result.output_payload["scenario_state"] == "INVALID_PROPOSAL"
        assert result.output_payload["scenario_complete"] is False
        assert result.output_payload["scenario_reasons"]
        assert result.output_payload["shadow_only"] is True
        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 1
        assert stored[0].raw_output == '{"proposal_id":'
        assert stored[0].normalized_proposal_json is None

    assert len(backend.calls) == 1
    spec, user_input = backend.calls[0]
    assert spec.mcp_servers == ()
    assert isinstance(spec.output_schema, DeferredValidationSchema)
    assert spec.output_schema.model_type is TradePlanProposal
    assert "SWING_V1" in spec.instructions
    assert "order_intent" not in user_input.lower()
    assert "ev-price-1" in user_input
    decoded_input = json.loads(user_input)
    assert decoded_input["contract"]["model"] == {
        "provider": "fixture",
        "model_id": "fixture-model",
        "model_version": "fixture-v1",
    }
    assert decoded_input["contract"]["sampling"] == {
        "version": "sampling.v1",
        "temperature": "0",
        "top_p": None,
        "seed": None,
    }
    assert decoded_input["allowed_evidence_ids"] == ["ev-price-1", "ev-risk-1"]
    assert decoded_input["allowed_predicate_features"] == {
        "swing_v1.price_momentum_5d": "0.05",
        "swing_v1.regime_compatibility": "60",
    }
    assert "reference data only" in user_input.lower()
    assert "not instructions" in user_input.lower()


@pytest.mark.asyncio
async def test_shadow_score_audit_is_persisted_with_the_same_snapshot_used_by_output(
    tmp_path: Path,
) -> None:
    backend = FakeLLMBackend([LLMResult(text="not-json")])
    strategy_input = _shadow_strategy_input()
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=FeedbackRepository(connection),
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.KR,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "KIS_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=strategy_input,
            )
        )
        row = connection.execute(
            "SELECT data_snapshot_id, feature_snapshot_id, quant_score_version, snapshot_json "
            "FROM decision_snapshots"
        ).fetchone()

    assert result.output_payload["quant_score"]["recomposition_matches"] is True
    assert result.output_payload["quant_score"]["threshold_version"] == (
        "SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1"
    )
    assert result.output_payload["quant_score"]["thresholds"]
    assert result.output_payload["hard_vetoes"] == ["risk:fixture-veto"]
    assert row[:3] == (
        str(DATA_SNAPSHOT_ID),
        str(FEATURE_SNAPSHOT_ID),
        "SHADOW_SCORE_V1.SWING_V1",
    )
    persisted_score = json.loads(row[3])["quant_score"]
    assert persisted_score == result.output_payload["quant_score"]
    assert persisted_score["feature_snapshot_id"] == str(FEATURE_SNAPSHOT_ID)
    prompt_score = json.loads(backend.calls[0][1])["quant_score"]
    assert prompt_score == {
        "score_id": str(strategy_input.quant_score.quant_score_id),
        "score_version": "SHADOW_SCORE_V1.SWING_V1",
        "total_score": persisted_score["total_score"],
        "components": persisted_score["components"],
    }


@pytest.mark.asyncio
async def test_semantically_invalid_structured_output_is_policy_rejected_not_backend_failure(
    tmp_path: Path,
) -> None:
    payload = json.loads(_accepted_proposal().model_dump_json())
    payload["regime"]["probabilities"]["strong_bull"] = 0.3
    backend = FakeLLMBackend([LLMResult(structured=payload)])

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )

        assert result.output_payload["status"] == "REJECTED"
        assert result.output_payload["decision"] is None
        assert result.output_payload["scenario_state"] == "INVALID_PROPOSAL"
        assert result.output_payload["scenario_complete"] is False
        assert any(
            "regime.probabilities" in reason
            for reason in result.output_payload["scenario_reasons"]
        )
        assert result.output_payload["backend_error_type"] is None
        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 1
        assert "regime probabilities must sum exactly to 1" in stored[0].dispositions[0].reason
        assert stored[0].raw_output.startswith("{")


@pytest.mark.asyncio
async def test_backend_failure_is_redacted_persisted_and_fail_closed(tmp_path: Path) -> None:
    def fail_backend(spec, user_input):
        raise RuntimeError("provider detail that must not be persisted")

    backend = FakeLLMBackend(fail_backend)
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )
        replay = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )

        assert replay == result
        assert len(backend.calls) == 1
        assert result.output_payload["status"] == "REJECTED"
        assert result.output_payload["decision"] is None
        assert result.output_payload["backend_error_type"] == "LLM_BACKEND_FAILURE"
        assert "provider detail" not in str(result.output_payload)
        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 1
        assert stored[0].raw_output == "[LLM_BACKEND_FAILURE]"
        assert "provider detail" not in stored[0].raw_output


@pytest.mark.asyncio
async def test_default_evaluator_delegates_timeout_to_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden_wait_for(*args, **kwargs):
        raise AssertionError("default evaluator must not impose an application timeout")

    monkeypatch.setattr("prism_app.strategy_evaluator.asyncio.wait_for", forbidden_wait_for)
    backend = FakeLLMBackend([LLMResult(structured=_accepted_proposal())])
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=FeedbackRepository(connection),
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )

    assert _config().timeout_seconds is None
    assert result.output_payload["status"] == "ACCEPTED"
    assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_backend_timeout_is_bounded_persisted_and_distinct_from_generic_failure(
    tmp_path: Path,
) -> None:
    class HangingBackend(FakeLLMBackend):
        def __init__(self) -> None:
            super().__init__([])

        async def run(self, spec, user_input):
            self.calls.append((spec, user_input))
            await asyncio.sleep(60)
            raise AssertionError("deadline must cancel the hanging backend")

    backend = HangingBackend()
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=replace(_config(), timeout_seconds=0.01),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )

        result = await evaluator.evaluate(request)
        replay = await evaluator.evaluate(request)
        stored = repository.proposals_as_of(
            request.evaluated_at, strategy_id=StrategyId.SWING_V1
        )

    assert replay == result
    assert len(backend.calls) == 1
    assert result.output_payload["status"] == "REJECTED"
    assert result.output_payload["decision"] is None
    assert result.output_payload["scenario_state"] == "ANALYSIS_INCOMPLETE"
    assert result.output_payload["backend_error_type"] == "LLM_BACKEND_TIMEOUT"
    assert stored[0].raw_output == "[LLM_BACKEND_TIMEOUT]"


def test_evaluation_request_rejects_cross_market_feature_provenance() -> None:
    strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

    with pytest.raises(ValueError, match="strategy input market"):
        StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.KR,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )


@pytest.mark.asyncio
async def test_empty_backend_result_is_model_output_rejection_not_backend_failure(
    tmp_path: Path,
) -> None:
    backend = FakeLLMBackend(lambda spec, user_input: LLMResult())
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )

        assert result.output_payload["status"] == "REJECTED"
        assert result.output_payload["backend_error_type"] is None
        assert result.output_payload["model_output_error_type"] == "LLM_OUTPUT_INVALID"
        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 1
        assert stored[0].raw_output == "[NO_MODEL_OUTPUT]"


@pytest.mark.asyncio
async def test_non_finite_structured_model_value_is_redacted_as_invalid_output(
    tmp_path: Path,
) -> None:
    backend = FakeLLMBackend([LLMResult(structured={"score": float("nan")})])
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)

        result = await evaluator.evaluate(
            StrategyEvaluationRequest(
                strategy=strategy,
                market=Market.US,
                data_snapshot_id=DATA_SNAPSHOT_ID,
                source_payload={"provider": "FMP_FIXTURE"},
                evaluated_at=NOW + timedelta(minutes=1),
                strategy_input=_strategy_input(),
            )
        )
        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )

    assert result.output_payload["backend_error_type"] is None
    assert result.output_payload["model_output_error_type"] == "LLM_OUTPUT_INVALID"
    assert stored[0].raw_output == "[INVALID_MODEL_OUTPUT]"


@pytest.mark.asyncio
async def test_rejected_parsed_proposal_replay_keeps_decision_fail_closed(
    tmp_path: Path,
) -> None:
    strategy_input = _strategy_input()
    strategy_input = replace(
        strategy_input,
        feature_snapshot=replace(
            strategy_input.feature_snapshot,
            values=(strategy_input.feature_snapshot.values[0],),
        ),
    )
    backend = FakeLLMBackend([LLMResult(structured=_accepted_proposal())])
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=strategy_input,
        )
        evaluator = StructuredLLMStrategyEvaluator(
            backend=backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )

        first = await evaluator.evaluate(request)
        replay = await evaluator.evaluate(request)

        assert first.output_payload["status"] == "REJECTED"
        assert first.output_payload["decision"] is None
        assert replay == first
        assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_retry_reuses_persisted_invocation_without_calling_backend(
    tmp_path: Path,
) -> None:
    first_backend = FakeLLMBackend([LLMResult(text='{"proposal_id":')])

    def unexpected_backend_call(spec, user_input):
        raise AssertionError("persisted invocation must be reused before the LLM call")

    replay_backend = FakeLLMBackend(unexpected_backend_call)
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )
        first_evaluator = StructuredLLMStrategyEvaluator(
            backend=first_backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )
        replay_evaluator = StructuredLLMStrategyEvaluator(
            backend=replay_backend,
            proposal_service=ProposalService(),
            validator=_validator(),
            repository=repository,
            config=_config(),
        )

        first = await first_evaluator.evaluate(request)
        replay = await replay_evaluator.evaluate(request)

        assert replay == first
        assert len(first_backend.calls) == 1
        assert replay_backend.calls == []
        assert len(
            repository.proposals_as_of(
                request.evaluated_at, strategy_id=StrategyId.SWING_V1
            )
        ) == 1


@pytest.mark.asyncio
async def test_proposal_identity_includes_model_and_evaluation_provenance(
    tmp_path: Path,
) -> None:
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = FeedbackRepository(connection)
        strategy = DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1)
        request = StrategyEvaluationRequest(
            strategy=strategy,
            market=Market.US,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            source_payload={"provider": "FMP_FIXTURE"},
            evaluated_at=NOW + timedelta(minutes=1),
            strategy_input=_strategy_input(),
        )

        for raw, model_version in (
            ('{"one":', "fixture-v1"),
            ('{"two":', "fixture-v2"),
        ):
            evaluator = StructuredLLMStrategyEvaluator(
                backend=FakeLLMBackend([LLMResult(text=raw)]),
                proposal_service=ProposalService(),
                validator=_validator(),
                repository=repository,
                config=replace(_config(), model_version=model_version),
            )
            await evaluator.evaluate(request)

        stored = repository.proposals_as_of(
            NOW + timedelta(minutes=1), strategy_id=StrategyId.SWING_V1
        )
        assert len(stored) == 2
        assert len({item.proposal_key for item in stored}) == 2
