from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from prism_app import product_uat
from prism_app.product_composition import phase1_quality_gate
from prism_app.product_uat import load_pit_evidence
from prism_core.data import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.data.providers.kis_http import KISMarketDataTransportError
from prism_core.strategies.contracts import Market, StrategyId


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)


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
                }
            ),
            SimpleNamespace(
                output_payload={
                    "backend_error_type": None,
                    "model_output_error_type": None,
                    "scenario_complete": True,
                    "scenario_state": "NO_ENTRY",
                    "decision": "NO_ENTRY",
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

    skipped = SimpleNamespace(
        quality_decision=SimpleNamespace(disposition=QualityDisposition.REJECT),
        strategies=(),
    )
    with pytest.raises(RuntimeError, match="quality gate"):
        product_uat.require_product_runtime_proof(skipped)


def test_runtime_invocation_evidence_distinguishes_fresh_oauth_calls_from_replay() -> None:
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=False, strategy_count=2
    ) == {
        "fresh_invocation_verified": True,
        "idempotent_replay": False,
        "structured_response_count": 2,
        "replayed_response_count": 0,
    }
    assert product_uat.runtime_invocation_evidence(
        idempotent_replay=True, strategy_count=2
    ) == {
        "fresh_invocation_verified": False,
        "idempotent_replay": True,
        "structured_response_count": 0,
        "replayed_response_count": 2,
    }


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


def test_product_uat_defaults_to_gpt_5_6_sol() -> None:
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

    assert args.model == "gpt-5.6-sol"
    assert args.model_version == "gpt-5.6-sol"
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
