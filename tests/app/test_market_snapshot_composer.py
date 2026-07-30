from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from cores.llm.fakes import FakeLLMBackend
from cores.llm.ports import LLMBackend, LLMResult
from prism_app.daily_pipeline import SQLiteAppRunRepository
from prism_app.market_snapshot_composer import KRPITEvidence, KRProductSnapshotComposer
from prism_app.outcome_tracker import OutcomeTracker
from prism_app.product_composition import (
    ProductRunConfig,
    product_invocation_id,
    run_kr_shadow_product,
)
from prism_app.strategy_evaluator import StrategyEvaluatorConfig
from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import (
    KISInstrument,
    KISMarketDataProvider,
    ProviderPayload,
)
from prism_core.ops.job_runs import JobRunStore
from prism_core.policy import ProposalValidationPolicy
from prism_core.runtime.settings import ProductMode, RuntimeSettings
from prism_core.strategies.contracts import StrategyId


KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 7, 24, 18, 0, tzinfo=KST)
INGESTED = AS_OF + timedelta(minutes=1)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000082"))


def _sessions(count: int):
    result = []
    cursor = AS_OF.date()
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(result))


class HistoricalTransport:
    def __init__(self, count: int = 260) -> None:
        self._count = count

    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
        rows = []
        for index, session in enumerate(_sessions(self._count)):
            for symbol, base, step in (
                ("005930", 50000, 100),
                ("069500", 30000, 40),
            ):
                close = base + index * step
                rows.append(
                    {
                        "provider_symbol": symbol,
                        "trade_date": session.isoformat(),
                        "open": str(close - 50),
                        "high": str(close + 100),
                        "low": str(close - 100),
                        "close": str(close),
                        "volume": str(1_000_000 + index * 1_000),
                    }
                )
        return ProviderPayload(
            provider="KIS",
            source_record_id="kis:historical:fixture",
            revision=0,
            observed_at=datetime.combine(AS_OF.date(), time(15, 30), tzinfo=KST),
            available_at=datetime.combine(AS_OF.date(), time(15, 31), tzinfo=KST),
            payload={"prices": rows},
        )


def _evidence() -> KRPITEvidence:
    return KRPITEvidence(
        observed_at=AS_OF - timedelta(minutes=2),
        available_at=AS_OF - timedelta(minutes=1),
        ingested_at=AS_OF,
        observations={
            "catalyst_recency_sessions": Decimal("2"),
            "regime_swing_compatibility": Decimal("70"),
            "earnings_current": Decimal("12"),
            "earnings_previous": Decimal("10"),
            "industry_leadership": Decimal("75"),
            "regime_trend_compatibility": Decimal("68"),
        },
        evidence_payload={
            "manual:regime:2026-07-24": {
                "source": "user-supplied-pit-evidence",
                "available_at": (AS_OF - timedelta(minutes=1)).isoformat(),
            }
        },
    )


@pytest.mark.asyncio
async def test_kr_composer_builds_two_exact_strategy_inputs_from_kis_and_pit_evidence() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    )

    snapshot = await composer.acquire(as_of=AS_OF)

    assert snapshot.data_snapshot_id == next(
        iter(snapshot.strategy_inputs.values())
    ).feature_snapshot.data_snapshot_id
    assert set(snapshot.strategy_inputs) == {StrategyId.SWING_V1, StrategyId.TREND_V1}
    assert all(
        value.feature_snapshot.data_quality_status is DataQualityStatus.FRESH
        for value in snapshot.strategy_inputs.values()
    )
    assert {
        strategy_id: value.quant_score.score_version
        for strategy_id, value in snapshot.strategy_inputs.items()
    } == {
        StrategyId.SWING_V1: "SHADOW_SCORE_V1.SWING_V1",
        StrategyId.TREND_V1: "SHADOW_SCORE_V1.TREND_V1",
    }
    assert all(
        value.feature_snapshot.feature_version == "SHADOW_FEATURES_V1"
        for value in snapshot.strategy_inputs.values()
    )
    assert tuple(
        component.name
        for component in snapshot.strategy_inputs[
            StrategyId.SWING_V1
        ].quant_score.components
    ) == (
        "swing_v1.momentum_state_score",
        "swing_v1.relative_strength_state_score",
        "swing_v1.volume_state_score",
        "swing_v1.volatility_state_score",
        "swing_v1.regime_state_score",
    )
    for value in snapshot.strategy_inputs.values():
        assert value.scenario_input_pack is not None
        assert value.scenario_input_pack.entry_vetoes
        assert set(value.scenario_input_pack.entry_vetoes) <= set(value.hard_vetoes)
        assert any(
            veto.startswith("shadow_score_v1:") for veto in value.hard_vetoes
        )
    assert snapshot.source_payload["provider"] == "KIS"
    assert snapshot.source_payload["evidence_level"] == "STATIC_PIT_OVERRIDE"
    assert snapshot.source_payload["price_basis"] == "RAW"
    assert snapshot.source_payload["price_adjustment_semantics"] == "RAW_UNADJUSTED"
    assert snapshot.source_payload["corporate_action_count"] == 0
    assert snapshot.source_payload["corporate_action_coverage_status"] == "UNVERIFIED"
    assert snapshot.source_payload["corporate_action_coverage_scope"] == "UNVERIFIED"
    assert snapshot.source_payload["corporate_action_covered_symbols"] == []
    assert snapshot.source_payload["excluded_incomplete_sessions"] == []
    assert snapshot.source_payload["incomplete_session_filter"] == "SCENARIO_PACK"
    assert snapshot.source_payload["broker_called"] is False
    assert snapshot.leadership_snapshot.market.value == "KR"
    assert snapshot.leadership_snapshot.observed_at == _evidence().observed_at
    assert snapshot.leadership_snapshot.available_at == _evidence().available_at


@pytest.mark.asyncio
async def test_kr_composer_preserves_swing_when_trend_history_is_insufficient() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(count=100),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    )

    snapshot = await composer.acquire(as_of=AS_OF)

    assert set(snapshot.strategy_inputs) == {StrategyId.SWING_V1}
    assert snapshot.strategy_inputs[
        StrategyId.SWING_V1
    ].quant_score.score_version == "SHADOW_SCORE_V1.SWING_V1"


@pytest.mark.asyncio
async def test_kr_composer_rejects_stale_evidence_built_after_live_market_fetch() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )

    class EvidenceProvider:
        calls = []

        async def build(self, *, snapshot, stock_id, benchmark_id, as_of):
            self.calls.append((snapshot, stock_id, benchmark_id, as_of))
            evidence = _evidence()
            return replace(
                evidence,
                observed_at=evidence.observed_at - timedelta(days=3),
                available_at=evidence.available_at - timedelta(days=3),
            )

    evidence_provider = EvidenceProvider()
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence_provider=evidence_provider,
    )

    with pytest.raises(ValueError, match="evidence is stale"):
        await composer.acquire(as_of=AS_OF)

    assert len(evidence_provider.calls) == 1
    assert evidence_provider.calls[0][1:] == (STOCK_ID, BENCHMARK_ID, AS_OF)


@pytest.mark.asyncio
async def test_kr_composer_does_not_upgrade_stale_price_leadership_quality() -> None:
    class StaleHistoricalTransport(HistoricalTransport):
        async def fetch(
            self, provider: str, *, as_of_date: datetime
        ) -> ProviderPayload:
            payload = await super().fetch(provider, as_of_date=as_of_date)
            return replace(payload, quality=DataQualityStatus.STALE)

    provider = KISMarketDataProvider(
        transport=StaleHistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    snapshot = await KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    ).acquire(as_of=AS_OF)

    assert snapshot.field_quality["price"] is DataQualityStatus.STALE
    assert snapshot.leadership_snapshot.quality is DataQualityStatus.STALE
    assert snapshot.leadership_snapshot.core_evidence_usable is False


def test_kr_evidence_rejects_missing_or_post_as_of_inputs() -> None:
    with pytest.raises(ValueError, match="missing required observations"):
        KRPITEvidence(
            observed_at=AS_OF,
            available_at=AS_OF,
            ingested_at=AS_OF,
            observations={"catalyst_recency_sessions": Decimal("1")},
            evidence_payload={"evidence-1": {}},
        )

    evidence = _evidence()
    provider = object()
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=evidence,
    )
    with pytest.raises(ValueError, match="available after evaluation"):
        composer.validate_as_of(evidence.available_at - timedelta(seconds=1))

    with pytest.raises(ValueError, match="evidence is stale"):
        composer.validate_as_of(evidence.available_at + timedelta(days=2))


@pytest.mark.asyncio
async def test_product_composition_persists_and_reads_back_shadow_report(
    tmp_path,
    monkeypatch,
) -> None:
    app_connection_ids: list[int] = []
    job_connection_ids: list[int] = []
    original_app_init = SQLiteAppRunRepository.__init__
    original_job_init = JobRunStore.__init__

    def record_app_connection(self, connection):
        app_connection_ids.append(id(connection))
        original_app_init(self, connection)

    def record_job_connection(self, connection):
        job_connection_ids.append(id(connection))
        original_job_init(self, connection)

    monkeypatch.setattr(SQLiteAppRunRepository, "__init__", record_app_connection)
    monkeypatch.setattr(JobRunStore, "__init__", record_job_connection)
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    )
    backend = FakeLLMBackend(
        [LLMResult(text="not-json"), LLMResult(text="not-json")]
    )
    output = tmp_path / "phase1-shadow.md"
    base = tmp_path / "existing.md"
    base.write_text("# 기존 PRISM 리포트\n\n기존 내용\n", encoding="utf-8")
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    run_config = ProductRunConfig(
        evaluated_at=AS_OF,
        run_type="daily-close",
        model_id="fixture-model",
        model_version="fixture-v1",
        code_version="test-tree",
        owner_id="fixture-runner",
    )
    result = await run_kr_shadow_product(
        composer=composer,
        backend=backend,
        settings=settings,
        config=run_config,
        output_path=output,
        base_report_path=base,
    )
    replay = await run_kr_shadow_product(
        composer=composer,
        backend=backend,
        settings=settings,
        config=run_config,
        output_path=output,
        base_report_path=base,
    )
    second_backend = FakeLLMBackend(
        [LLMResult(text="not-json"), LLMResult(text="not-json")]
    )
    second = await run_kr_shadow_product(
        composer=composer,
        backend=second_backend,
        settings=settings,
        config=replace(run_config, model_version="fixture-v2"),
        output_path=tmp_path / "phase1-shadow-v2.md",
        base_report_path=base,
    )

    assert len(backend.calls) == 2
    assert replay.idempotent_replay is True
    assert second.invocation_id != result.invocation_id
    assert len(second_backend.calls) == 2
    assert all(spec.params.temperature is None for spec, _user_input in backend.calls)
    assert result.analysis.job_key.endswith(result.invocation_id)
    assert result.readback.analysis == result.analysis
    assert result.readback.markdown in output.read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8").startswith("# 기존 PRISM 리포트")
    import sqlite3

    with sqlite3.connect(settings.research_db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM trade_plan_proposals"
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT count(*) FROM process_quality_outcomes"
        ).fetchone()[0] == 4
        pending = connection.execute(
            "SELECT strategy_id, horizon_sessions, outcome_state, outcome_json "
            "FROM proposal_outcomes "
            "ORDER BY strategy_id, horizon_sessions"
        ).fetchall()
        assert len(pending) == 12
        assert {
            (strategy_id, horizon)
            for strategy_id, horizon, _state, _payload in pending
        } == {
            ("SWING_V1", 5),
            ("SWING_V1", 10),
            ("SWING_V1", 20),
            ("TREND_V1", 20),
            ("TREND_V1", 60),
            ("TREND_V1", 120),
        }
        assert all(state == "UNKNOWN" for _strategy, _horizon, state, _payload in pending)
        assert all(
            '"measurement_status":"PENDING"' in payload
            for _strategy, _horizon, _state, payload in pending
        )
        assert connection.execute(
            "SELECT count(*) FROM retrospective_events"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM lesson_candidates"
        ).fetchone()[0] == 0
    with sqlite3.connect(settings.ops_db_path) as connection:
        statuses = {
            row[0] for row in connection.execute("SELECT status FROM job_runs")
        }
    assert {"ANALYSIS_PERSISTED", "SUCCESS"} <= statuses
    assert app_connection_ids[0] != job_connection_ids[0]


@pytest.mark.asyncio
async def test_product_total_deadline_records_error_and_releases_lease(tmp_path) -> None:
    class HangingBackend(LLMBackend):
        name = "hanging"

        async def run(self, spec, user_input):
            await asyncio.sleep(60)
            raise AssertionError("product deadline must cancel the backend")

    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    with pytest.raises(TimeoutError):
        await run_kr_shadow_product(
            composer=KRProductSnapshotComposer(
                provider=provider,
                stock_id=STOCK_ID,
                benchmark_id=BENCHMARK_ID,
                stock_symbol="005930",
                benchmark_symbol="069500",
                evidence=_evidence(),
            ),
            backend=HangingBackend(),
            settings=settings,
            config=ProductRunConfig(
                evaluated_at=AS_OF,
                run_type="daily-close",
                model_id="fixture-model",
                model_version="fixture-v1",
                code_version="test-tree",
                owner_id="fixture-runner",
                total_timeout_seconds=0.01,
            ),
            output_path=tmp_path / "must-not-exist.md",
        )

    import sqlite3

    with sqlite3.connect(settings.ops_db_path) as connection:
        assert connection.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM job_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0] == "ERROR"
    assert not (tmp_path / "must-not-exist.md").exists()


@pytest.mark.asyncio
async def test_partial_feedback_failure_is_error_and_same_invocation_replay_reconciles(
    tmp_path, monkeypatch
) -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    )
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )
    config = ProductRunConfig(
        evaluated_at=AS_OF,
        run_type="daily-close",
        model_id="fixture-model",
        model_version="fixture-v1",
        code_version="test-tree",
        owner_id="fixture-runner",
    )
    original_pending = OutcomeTracker.record_pending_market_outcome
    failed = False

    def fail_first_pending(self, item):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected pending persistence failure")
        return original_pending(self, item)

    monkeypatch.setattr(
        OutcomeTracker,
        "record_pending_market_outcome",
        fail_first_pending,
    )
    with pytest.raises(RuntimeError, match="injected pending persistence failure"):
        await run_kr_shadow_product(
            composer=composer,
            backend=FakeLLMBackend(
                [LLMResult(text="not-json"), LLMResult(text="not-json")]
            ),
            settings=settings,
            config=config,
            output_path=tmp_path / "first-must-not-exist.md",
        )

    monkeypatch.setattr(
        OutcomeTracker,
        "record_pending_market_outcome",
        original_pending,
    )
    replay_backend = FakeLLMBackend([])
    replay = await run_kr_shadow_product(
        composer=composer,
        backend=replay_backend,
        settings=settings,
        config=config,
        output_path=tmp_path / "reconciled.md",
    )

    import sqlite3

    with sqlite3.connect(settings.research_db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM process_quality_outcomes"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM proposal_outcomes"
        ).fetchone()[0] == 6
    with sqlite3.connect(settings.ops_db_path) as connection:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM job_runs WHERE job_key LIKE 'pipeline:%' "
                "ORDER BY created_at"
            )
        ]
        assert statuses == ["ERROR", "SUCCESS"]
        assert connection.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
    assert replay.idempotent_replay is True
    assert replay_backend.calls == []
    assert (tmp_path / "reconciled.md").exists()


@pytest.mark.asyncio
async def test_product_invocation_identity_changes_with_policy_or_evaluator_provenance() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    snapshot = await KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    ).acquire(as_of=AS_OF)
    run_config = ProductRunConfig(
        evaluated_at=AS_OF,
        run_type="daily-close",
        model_id="fixture-model",
        model_version="fixture-v1",
        code_version="same-code-version",
        owner_id="fixture-runner",
    )
    evaluator_config = StrategyEvaluatorConfig(
        model_provider="chatgpt_oauth",
        model_id=run_config.model_id,
        model_version=run_config.model_version,
        sampling_version="sampling.v1",
        sampling={"temperature": Decimal("1")},
        policy_version="policy.v1",
        config_version="composition.v1",
        code_version=run_config.code_version,
        schema_version="feedback.v1",
    )
    policy = ProposalValidationPolicy(
        validator_version="validator.v1",
        max_snapshot_age=timedelta(hours=2),
        max_risk_multiplier=Decimal("0.8"),
        max_llm_quant_score_gap=Decimal("25"),
        max_regime_divergence=Decimal("25"),
    )

    baseline = product_invocation_id(snapshot, run_config, evaluator_config, policy)
    changed_evaluator = product_invocation_id(
        snapshot,
        run_config,
        replace(evaluator_config, policy_version="policy.v2"),
        policy,
    )
    changed_policy = product_invocation_id(
        snapshot,
        run_config,
        evaluator_config,
        replace(policy, max_risk_multiplier=Decimal("0.7")),
    )
    swing_input = snapshot.strategy_inputs[StrategyId.SWING_V1]
    changed_feature_version = product_invocation_id(
        replace(
            snapshot,
            strategy_inputs={
                **snapshot.strategy_inputs,
                StrategyId.SWING_V1: replace(
                    swing_input,
                    feature_snapshot=replace(
                        swing_input.feature_snapshot,
                        feature_version="phase1.features.v2",
                    ),
                ),
            },
        ),
        run_config,
        evaluator_config,
        policy,
    )
    changed_score_version = product_invocation_id(
        replace(
            snapshot,
            strategy_inputs={
                **snapshot.strategy_inputs,
                StrategyId.SWING_V1: replace(
                    swing_input,
                    quant_score=replace(
                        swing_input.quant_score,
                        score_version="swing-score.shadow.v2",
                    ),
                ),
            },
        ),
        run_config,
        evaluator_config,
        policy,
    )

    assert len(
        {
            baseline,
            changed_evaluator,
            changed_policy,
            changed_feature_version,
            changed_score_version,
        }
    ) == 5


@pytest.mark.asyncio
async def test_product_invocation_identity_treats_snapshot_source_and_evaluation_drift_as_new_work() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    snapshot = await KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence=_evidence(),
    ).acquire(as_of=AS_OF)
    run_config = ProductRunConfig(
        evaluated_at=AS_OF,
        run_type="daily-close",
        model_id="fixture-model",
        model_version="fixture-v1",
        code_version="same-code-version",
        owner_id="fixture-runner",
    )
    evaluator_config = StrategyEvaluatorConfig(
        model_provider="chatgpt_oauth",
        model_id=run_config.model_id,
        model_version=run_config.model_version,
        sampling_version="sampling.v1",
        sampling={"temperature": Decimal("1")},
        policy_version="policy.v1",
        config_version="composition.v1",
        code_version=run_config.code_version,
        schema_version="feedback.v1",
    )
    policy = ProposalValidationPolicy(
        validator_version="validator.v1",
        max_snapshot_age=timedelta(hours=2),
        max_risk_multiplier=Decimal("0.8"),
        max_llm_quant_score_gap=Decimal("25"),
        max_regime_divergence=Decimal("25"),
    )

    baseline = product_invocation_id(snapshot, run_config, evaluator_config, policy)
    snapshot_drift = product_invocation_id(
        replace(
            snapshot,
            data_snapshot_id=UUID("00000000-0000-0000-0000-000000000099"),
            strategy_inputs={},
        ),
        run_config,
        evaluator_config,
        policy,
    )
    source_drift = product_invocation_id(
        replace(
            snapshot,
            source_payload={**snapshot.source_payload, "evidence_hash": "changed"},
        ),
        run_config,
        evaluator_config,
        policy,
    )
    evaluation_drift = product_invocation_id(
        snapshot,
        replace(run_config, evaluated_at=AS_OF + timedelta(seconds=1)),
        evaluator_config,
        policy,
    )

    assert len({baseline, snapshot_drift, source_drift, evaluation_drift}) == 4
