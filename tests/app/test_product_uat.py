from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from prism_app import product_uat
from prism_app.product_composition import phase1_quality_gate
from prism_app.product_uat import load_pit_evidence
from prism_core.data import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.data.providers.dart import UnavailableDARTAdapter
from prism_core.data.providers.kind import UnavailableKINDAdapter
from prism_core.data.providers.kis_fundamentals import KISFundamentalProvider
from prism_core.data.providers.kis_http import KISMarketDataTransportError
from prism_core.strategies.contracts import Market, StrategyId


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


def _current_score_audit(strategy_id: StrategyId) -> dict[str, object]:
    prefix = strategy_id.value
    return {
        "score_version": f"SHADOW_SCORE_V1.{prefix}",
        "total_score": "67.500000",
        "feature_snapshot_id": f"feature-{prefix.lower()}",
        "recomposed_total": "67.500000",
        "recomposition_matches": True,
        "component_details": [{"name": f"{prefix.lower()}.component"}],
        "threshold_version": f"SHADOW_ENTRY_THRESHOLDS_V1.{prefix}",
        "thresholds": [{"name": f"{prefix.lower()}.min_quant_score", "passed": True}],
    }


def _payload() -> dict[str, object]:
    return {
        "observed_at": "2026-07-26T09:58:00+00:00",
        "available_at": "2026-07-26T09:59:00+00:00",
        "ingested_at": "2026-07-26T10:00:00+00:00",
        "observations": {
            "catalyst_recency_sessions": "2",
            "regime_swing_compatibility": "70",
            "earnings_current": "12",
            "earnings_previous": "10",
            "industry_leadership": "75",
            "regime_trend_compatibility": "68",
        },
        "evidence_payload": {
            "user:pit:2026-07-26": {
                "source": "user-curated",
                "available_at": "2026-07-26T09:59:00+00:00",
            }
        },
    }


def test_load_pit_evidence_preserves_decimal_and_timing(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    evidence = load_pit_evidence(path, as_of=NOW)

    assert evidence.observations["earnings_current"] == Decimal("12")
    assert evidence.available_at < NOW
    assert set(evidence.evidence_payload) == {"user:pit:2026-07-26"}


def test_load_pit_evidence_rejects_extra_fields_and_future_availability(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["api_key"] = "must-not-be-accepted"
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly"):
        load_pit_evidence(path, as_of=NOW)

    payload = _payload()
    payload["available_at"] = "2026-07-26T10:01:00+00:00"
    payload["ingested_at"] = "2026-07-26T10:01:00+00:00"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="available after evaluation"):
        load_pit_evidence(path, as_of=NOW)


def test_product_runtime_proof_rejects_backend_failure_or_skipped_quality() -> None:
    accepted = SimpleNamespace(
        quality_decision=SimpleNamespace(disposition=QualityDisposition.ACCEPT),
        strategies=(
            SimpleNamespace(
                output_payload={
                    "backend_error_type": None,
                    "model_output_error_type": None,
                    "scenario_complete": True,
                    "scenario_state": "WATCH",
                    "decision": "WATCH",
                    "quant_score": _current_score_audit(StrategyId.SWING_V1),
                }
            ),
            SimpleNamespace(
                output_payload={
                    "backend_error_type": None,
                    "model_output_error_type": None,
                    "scenario_complete": True,
                    "scenario_state": "NO_ENTRY",
                    "decision": "NO_ENTRY",
                    "quant_score": _current_score_audit(StrategyId.TREND_V1),
                }
            ),
        ),
    )
    product_uat.require_product_runtime_proof(accepted)
    with pytest.raises(RuntimeError, match="fresh non-replay"):
        product_uat.require_product_runtime_proof(accepted, idempotent_replay=True)
    product_uat.require_product_runtime_proof(
        accepted,
        idempotent_replay=True,
        require_fresh_invocation=False,
    )

    backend_failed = SimpleNamespace(
        quality_decision=accepted.quality_decision,
        strategies=(
            SimpleNamespace(output_payload={"backend_error_type": None}),
            SimpleNamespace(
                output_payload={"backend_error_type": "LLM_BACKEND_FAILURE"}
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="OAuth LLM backend"):
        product_uat.require_product_runtime_proof(backend_failed)

    invalid_model_output = SimpleNamespace(
        quality_decision=accepted.quality_decision,
        strategies=(
            SimpleNamespace(output_payload={"backend_error_type": None}),
            SimpleNamespace(
                output_payload={
                    "backend_error_type": None,
                    "model_output_error_type": "LLM_OUTPUT_INVALID",
                }
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="valid structured model output"):
        product_uat.require_product_runtime_proof(invalid_model_output)

    incomplete_scenario = SimpleNamespace(
        quality_decision=accepted.quality_decision,
        strategies=(
            accepted.strategies[0],
            SimpleNamespace(
                output_payload={
                    "backend_error_type": None,
                    "model_output_error_type": None,
                    "scenario_complete": False,
                    "scenario_state": "INVALID_PROPOSAL",
                    "decision": None,
                }
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="complete normalized scenarios"):
        product_uat.require_product_runtime_proof(incomplete_scenario)
    product_uat.enforce_product_runtime_proof(
        incomplete_scenario,
        enabled=False,
        idempotent_replay=False,
        require_fresh_invocation=False,
    )
    with pytest.raises(RuntimeError, match="complete normalized scenarios"):
        product_uat.enforce_product_runtime_proof(
            incomplete_scenario,
            enabled=True,
            idempotent_replay=False,
            require_fresh_invocation=False,
        )

    skipped = SimpleNamespace(
        quality_decision=SimpleNamespace(disposition=QualityDisposition.REJECT),
        strategies=(),
    )
    with pytest.raises(RuntimeError, match="quality gate"):
        product_uat.require_product_runtime_proof(skipped)


def test_product_runtime_proof_requires_complete_current_shadow_score_audit() -> None:
    payload = {
        "backend_error_type": None,
        "model_output_error_type": None,
        "scenario_complete": True,
        "scenario_state": "NO_ENTRY",
        "decision": "NO_ENTRY",
        "quant_score": {
            "score_version": "SHADOW_SCORE_V1.SWING_V1",
            "total_score": "67.500000",
        },
    }
    analysis = SimpleNamespace(
        quality_decision=SimpleNamespace(disposition=QualityDisposition.ACCEPT),
        strategies=(
            SimpleNamespace(output_payload=payload),
            SimpleNamespace(
                output_payload={
                    **payload,
                    "quant_score": _current_score_audit(StrategyId.TREND_V1),
                }
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="score recomposition and threshold audit"):
        product_uat.require_product_runtime_proof(cast(Any, analysis))

    payload["quant_score"] = {
        **payload["quant_score"],
        "feature_snapshot_id": "feature-swing",
        "recomposed_total": "67.500000",
        "recomposition_matches": True,
        "component_details": [{"name": "swing_v1.momentum_state_score"}],
        "threshold_version": "SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1",
        "thresholds": [{"name": "swing_v1.min_quant_score", "passed": True}],
    }
    product_uat.require_product_runtime_proof(cast(Any, analysis))


def test_product_runtime_proof_rejects_missing_or_legacy_score_audit() -> None:
    scenario = {
        "backend_error_type": None,
        "model_output_error_type": None,
        "scenario_complete": True,
        "scenario_state": "WATCH",
        "decision": "WATCH",
    }
    analysis = SimpleNamespace(
        quality_decision=SimpleNamespace(disposition=QualityDisposition.ACCEPT),
        strategies=(
            SimpleNamespace(output_payload=dict(scenario)),
            SimpleNamespace(
                output_payload={
                    **scenario,
                    "quant_score": {
                        "score_version": "score.v1",
                        "total_score": "70",
                    },
                }
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="current SHADOW score audit"):
        product_uat.require_product_runtime_proof(cast(Any, analysis))


def test_runtime_invocation_evidence_distinguishes_fresh_oauth_calls_from_replay() -> None:
    payloads = (
        {"backend_error_type": None, "model_output_error_type": None},
        {"backend_error_type": None, "model_output_error_type": None},
    )
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=False, strategy_payloads=payloads
    ) == {
        "fresh_invocation_verified": True,
        "idempotent_replay": False,
        "structured_response_count": 2,
        "replayed_response_count": 0,
        "backend_failure_count": 0,
        "invalid_model_output_count": 0,
        "invalid_proposal_count": 0,
    }
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=True, strategy_payloads=payloads
    ) == {
        "fresh_invocation_verified": False,
        "idempotent_replay": True,
        "structured_response_count": 0,
        "replayed_response_count": 2,
        "backend_failure_count": 0,
        "invalid_model_output_count": 0,
        "invalid_proposal_count": 0,
    }


def test_runtime_invocation_evidence_does_not_count_failures_as_structured_responses() -> None:
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=False,
        strategy_payloads=(
            {
                "backend_error_type": "LLM_BACKEND_FAILURE",
                "model_output_error_type": None,
            },
            {
                "backend_error_type": None,
                "model_output_error_type": "LLM_OUTPUT_INVALID",
            },
        ),
    ) == {
        "fresh_invocation_verified": True,
        "idempotent_replay": False,
        "structured_response_count": 0,
        "replayed_response_count": 0,
        "backend_failure_count": 1,
        "invalid_model_output_count": 1,
        "invalid_proposal_count": 0,
    }
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=False,
        strategy_payloads=(),
    )["fresh_invocation_verified"] is False


def test_runtime_invocation_evidence_does_not_count_invalid_proposal_as_structured() -> None:
    evidence = product_uat.runtime_invocation_evidence(
        idempotent_replay=False,
        strategy_payloads=(
            {
                "backend_error_type": None,
                "model_output_error_type": None,
                "scenario_state": "INVALID_PROPOSAL",
            },
            {
                "backend_error_type": None,
                "model_output_error_type": None,
                "scenario_state": "POLICY_REJECTED",
            },
        ),
    )

    assert evidence["structured_response_count"] == 1
    assert evidence["invalid_proposal_count"] == 1


def test_runtime_proof_failure_is_not_reported_as_provider_capability_outage() -> None:
    payload = product_uat.sanitized_failure_payload(
        product_uat.ProductRuntimeProofIncomplete(
            "product runtime proof requires complete normalized scenarios"
        )
    )

    assert payload["status"] == "PRODUCT_RUNTIME_PROOF_INCOMPLETE"
    assert payload["failure_type"] == "ProductRuntimeProofIncomplete"
    assert payload["failure_stage"] == "runtime_proof"


def test_runtime_strategy_evidence_preserves_exact_persisted_strategy_identities() -> None:
    analysis = SimpleNamespace(
        strategies=(
            SimpleNamespace(strategy_id=StrategyId.TREND_V1),
            SimpleNamespace(strategy_id=StrategyId.SWING_V1),
        )
    )

    assert product_uat.runtime_strategy_evidence(analysis) == (
        "TREND_V1",
        "SWING_V1",
    )


def test_runtime_strategy_results_exposes_sanitized_model_failure_classes() -> None:
    analysis = SimpleNamespace(
        data_snapshot_id="snapshot-1",
        strategies=(
            SimpleNamespace(
                strategy_id=StrategyId.SWING_V1,
                output_payload={
                    "scenario_state": "ANALYSIS_INCOMPLETE",
                    "scenario_complete": False,
                    "decision": None,
                    "backend_error_type": "LLM_BACKEND_FAILURE",
                    "model_output_error_type": None,
                    "scenario_reasons": ["oauth_llm_backend_failure"],
                },
            ),
        ),
    )

    result = product_uat.runtime_strategy_results(cast(Any, analysis))["SWING_V1"]

    assert result["backend_error_type"] == "LLM_BACKEND_FAILURE"
    assert result["model_output_error_type"] is None


def test_product_uat_defaults_to_live_verified_chatgpt_oauth_model() -> None:
    args = product_uat._parser().parse_args(
        [
            "--research-db",
            "research.sqlite",
            "--ops-db",
            "ops.sqlite",
            "--output",
            "report.md",
        ]
    )

    assert args.model == "gpt-5.4-mini"
    assert args.model_version == "gpt-5.4-mini"
    assert args.evidence_json is None


def test_kr_symbol_derives_optional_fmp_normalization_symbol_and_missing_key_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = product_uat._parser().parse_args(
        [
            "--symbol",
            "214450",
            "--research-db",
            "research.sqlite",
            "--ops-db",
            "ops.sqlite",
            "--output",
            "report.md",
        ]
    )
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINANCIAL_MODELING_PREP_API_KEY", raising=False)

    assert product_uat._resolved_fmp_symbol(args) == "214450.KS"
    assert product_uat._optional_fmp_client() is None
    assert product_uat._product_status(QualityDisposition.REPORT_ONLY) == (
        "REPORT_ONLY_READBACK_VERIFIED"
    )
    assert product_uat._product_status(QualityDisposition.REJECT) == (
        "ANALYSIS_INCOMPLETE_READBACK_VERIFIED"
    )
    decision = phase1_quality_gate().evaluate(
        {
            "calendar": DataQualityStatus.FRESH,
            "evidence": DataQualityStatus.FRESH,
            "price": DataQualityStatus.FRESH,
            "regime": DataQualityStatus.FRESH,
            "fundamental": DataQualityStatus.PARTIAL,
        }
    )
    assert decision.disposition is QualityDisposition.REPORT_ONLY

    us_decision = phase1_quality_gate(market=Market.US).evaluate(
        {
            "calendar": DataQualityStatus.FRESH,
            "evidence": DataQualityStatus.FRESH,
            "price": DataQualityStatus.FRESH,
            "regime": DataQualityStatus.FRESH,
            "fundamental": DataQualityStatus.PARTIAL,
        }
    )
    assert us_decision.disposition is QualityDisposition.REJECT


def test_product_uat_composes_kis_primary_fundamentals_without_fmp() -> None:
    transport = cast(Any, SimpleNamespace())
    provider = KISFundamentalProvider(transport=transport)

    cascade = product_uat._kis_primary_evidence_composer(
        stock_code="214450",
        security_id=product_uat._security_id("214450"),
        provider=provider,
        dart=UnavailableDARTAdapter(),
        kind=UnavailableKINDAdapter(),
    )

    assert cascade._kis is provider
    assert cascade._fmp is None


@pytest.mark.asyncio
async def test_live_product_fixes_decision_as_of_after_kis_fundamental_prefetch() -> None:
    events: list[str] = []

    class Provider:
        async def prefetch(self, *, stock_code: str) -> None:
            events.append(f"prefetch:{stock_code}")

    decision_as_of = await product_uat._prefetch_before_live_decision(
        provider=cast(Any, Provider()),
        stock_code="214450",
        requested_as_of=None,
        clock=lambda: events.append("clock") or NOW,
    )

    assert events == ["prefetch:214450", "clock"]
    assert decision_as_of == NOW

    events.clear()
    decision_as_of = await product_uat._prefetch_before_live_decision(
        provider=cast(Any, Provider()),
        stock_code="214450",
        requested_as_of=NOW,
        clock=lambda: events.append("clock") or NOW,
        force_live_prefetch=True,
    )

    assert events == ["prefetch:214450", "clock"]
    assert decision_as_of == NOW


def test_product_failure_payload_reports_sanitized_kis_stage_and_status_only() -> None:
    error = KISMarketDataTransportError(
        "KIS token request returned an error status",
        operation="token_request",
        status_code=403,
    )

    payload = product_uat.sanitized_failure_payload(error)

    assert payload["failure_type"] == "KISMarketDataTransportError"
    assert payload["failure_stage"] == "token_request"
    assert payload["provider_status_code"] == 403
    assert "token request returned" not in json.dumps(payload)
