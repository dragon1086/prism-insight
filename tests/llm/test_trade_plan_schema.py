from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.features.service import PriceBasis
from prism_core.llm.trade_plan import (
    CandidateBasis,
    MarketSession,
    MissingDataStatus,
    PredicateOperator,
    ProposedDecision,
    ScoreComponentName,
    TradePlanProposal,
)
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


def valid_proposal_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "proposal_id": UUID("00000000-0000-0000-0000-000000000101"),
        "proposal_version": "trade-plan.v1",
        "strategy_id": StrategyId.SWING_V1,
        "strategy_version": {"value": "swing-v1.0.0"},
        "market": Market.US,
        "security_id": {"value": UUID("00000000-0000-0000-0000-000000000102")},
        "feature_provenance": {
            "feature_snapshot_id": UUID("00000000-0000-0000-0000-000000000103"),
            "data_snapshot_id": UUID("00000000-0000-0000-0000-000000000104"),
            "as_of": datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc),
            "feature_version": "features.v1",
            "data_quality_status": DataQualityStatus.FRESH,
            "quality_disposition": QualityDisposition.ACCEPT,
        },
        "decision": ProposedDecision.ENTRY_CANDIDATE,
        "llm_score": Decimal("72.5"),
        "score_breakdown": [
            {
                "name": "trend_structure",
                "score": Decimal("75"),
                "rationale": "Price structure is constructive.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "regime": {
            "probabilities": {
                "strong_bull": Decimal("0.10"),
                "moderate_bull": Decimal("0.45"),
                "sideways": Decimal("0.25"),
                "moderate_bear": Decimal("0.15"),
                "strong_bear": Decimal("0.05"),
            },
            "confidence": Decimal("0.70"),
            "drivers": ["Breadth and trend are constructive."],
            "falsifiers": ["Breadth breaks below the supplied threshold."],
        },
        "entry_predicates": [
            {
                "feature_name": "swing_v1.price_momentum_5d",
                "operator": "GREATER_THAN_OR_EQUAL",
                "comparison_value": Decimal("0"),
                "upper_value": None,
                "reference_price": Decimal("100"),
                "reference_price_basis": "ADJUSTED",
                "market_session": "REGULAR",
                "valid_until": datetime(2026, 7, 27, 20, 0, tzinfo=timezone.utc),
                "rationale": "Require non-negative short momentum.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "stop_candidates": [
            {
                "price": Decimal("95"),
                "basis": "STRUCTURE",
                "price_basis": "ADJUSTED",
                "rationale": "Below supplied support.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "target_candidates": [
            {
                "price": Decimal("115"),
                "basis": "STRUCTURE",
                "price_basis": "ADJUSTED",
                "rationale": "Prior resistance candidate.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "risk_multiplier_candidate": {
            "value": Decimal("0.75"),
            "rationale": "Uncertainty warrants reduced risk.",
            "evidence_ids": ["ev-risk-1"],
        },
        "reentry_candidates": [
            {
                "conditions": ["New evidence confirms trend recovery."],
                "rationale": "Candidate only after a distinct recovery signal.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "pyramiding_candidates": [
            {
                "conditions": ["Position is profitable and new evidence confirms continuation."],
                "requires_profitable_position": True,
                "rationale": "Candidate only; deterministic policy owns eligibility.",
                "evidence_ids": ["ev-price-1"],
            }
        ],
        "bull_evidence_ids": ["ev-price-1"],
        "bear_evidence_ids": ["ev-risk-1"],
        "missing_or_stale_data": [],
        "uncertainty": {
            "level": Decimal("0.30"),
            "known_unknowns": ["Next session gap risk is unknown."],
            "assumptions": ["Supplied evidence remains valid through evaluation."],
        },
        "model": {
            "provider": "fixture",
            "model_id": "fixture-model",
            "model_version": "2026-07-01",
        },
        "prompt_version": "swing-prompt.v1",
        "sampling": {
            "version": "sampling.v1",
            "temperature": Decimal("0.2"),
            "top_p": Decimal("0.9"),
            "seed": 7,
        },
    }
    payload["strategy_version"] = StrategyVersion("swing-v1.0.0")
    score_breakdown = payload["score_breakdown"]
    assert isinstance(score_breakdown, list)
    score_breakdown[0]["name"] = ScoreComponentName.TREND_STRUCTURE
    entry_predicates = payload["entry_predicates"]
    assert isinstance(entry_predicates, list)
    entry_predicates[0]["operator"] = PredicateOperator.GREATER_THAN_OR_EQUAL
    entry_predicates[0]["reference_price_basis"] = PriceBasis.ADJUSTED
    entry_predicates[0]["market_session"] = MarketSession.REGULAR
    for field in ("stop_candidates", "target_candidates"):
        candidates = payload[field]
        assert isinstance(candidates, list)
        candidates[0]["basis"] = CandidateBasis.STRUCTURE
        candidates[0]["price_basis"] = PriceBasis.ADJUSTED
    return _strict_collections(payload)


def _strict_collections(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_strict_collections(item) for item in value)
    if isinstance(value, dict):
        return {key: _strict_collections(item) for key, item in value.items()}
    return value


def test_valid_proposal_reuses_strategy_identity_and_feature_provenance() -> None:
    proposal = TradePlanProposal.model_validate(valid_proposal_payload())

    assert proposal.strategy_id is StrategyId.SWING_V1
    assert proposal.strategy_version == StrategyVersion("swing-v1.0.0")
    assert proposal.market is Market.US
    assert proposal.security_id == SecurityId(
        value=UUID("00000000-0000-0000-0000-000000000102")
    )
    assert proposal.feature_provenance.feature_version == "features.v1"
    assert proposal.decision is ProposedDecision.ENTRY_CANDIDATE


def test_regime_distribution_has_fixed_structured_output_fields() -> None:
    schema = TradePlanProposal.model_json_schema()

    probability_schema = schema["$defs"]["RegimeProbabilities"]
    assert set(probability_schema["properties"]) == {
        "strong_bull",
        "moderate_bull",
        "sideways",
        "moderate_bear",
        "strong_bear",
    }
    assert probability_schema["additionalProperties"] is False


def test_every_structured_output_object_forbids_unknown_fields() -> None:
    schema = TradePlanProposal.model_json_schema()

    object_schemas = [schema] + [
        definition
        for definition in schema["$defs"].values()
        if definition.get("type") == "object"
    ]
    assert object_schemas
    assert all(item.get("additionalProperties") is False for item in object_schemas)
    assert all(
        set(item.get("properties", {})) == set(item.get("required", ()))
        for item in object_schemas
    )


def test_entry_predicate_requires_explicit_price_basis_and_market_session() -> None:
    proposal = TradePlanProposal.model_validate(valid_proposal_payload())

    predicate = proposal.entry_predicates[0]
    assert predicate.reference_price == Decimal("100")
    assert predicate.reference_price_basis.value == "ADJUSTED"
    assert predicate.market_session.value == "REGULAR"


@pytest.mark.parametrize(
    "required_candidates",
    ["entry_predicates", "stop_candidates", "target_candidates"],
)
def test_entry_decision_requires_entry_stop_and_target_candidates(
    required_candidates: str,
) -> None:
    payload = valid_proposal_payload()
    payload[required_candidates] = ()

    with pytest.raises(ValidationError, match="ENTRY_CANDIDATE requires"):
        TradePlanProposal.model_validate(payload)


def test_entry_stop_and_target_price_basis_cannot_be_mixed() -> None:
    payload = valid_proposal_payload()
    payload["target_candidates"][0]["price_basis"] = PriceBasis.RAW

    with pytest.raises(ValidationError, match="price basis"):
        TradePlanProposal.model_validate(payload)


@pytest.mark.parametrize(
    "prohibited_field",
    [
        "final_quantity",
        "execution_approved",
        "order_intent",
        "policy_override",
        "stop_widening_authorized",
        "exposure_increase_authorized",
        "averaging_down_authorized",
    ],
)
def test_unknown_and_execution_authority_fields_are_forbidden(
    prohibited_field: str,
) -> None:
    payload = valid_proposal_payload()
    payload[prohibited_field] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TradePlanProposal.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_score", Decimal("100.01")),
        ("risk_multiplier", Decimal("1.01")),
        ("regime_confidence", Decimal("-0.01")),
        ("sampling_temperature", Decimal("2.01")),
    ],
)
def test_scores_probabilities_and_multiplier_are_bounded(
    field: str, value: Decimal
) -> None:
    payload = deepcopy(valid_proposal_payload())
    if field == "llm_score":
        payload["llm_score"] = value
    elif field == "risk_multiplier":
        payload["risk_multiplier_candidate"]["value"] = value
    elif field == "regime_confidence":
        payload["regime"]["confidence"] = value
    else:
        payload["sampling"]["temperature"] = value

    with pytest.raises(ValidationError):
        TradePlanProposal.model_validate(payload)


def test_regime_probabilities_must_sum_exactly_to_one() -> None:
    payload = deepcopy(valid_proposal_payload())
    payload["regime"]["probabilities"]["sideways"] = Decimal("0.26")

    with pytest.raises(ValidationError, match="sum exactly to 1"):
        TradePlanProposal.model_validate(payload)


def test_predicate_operator_is_allowlisted() -> None:
    payload = deepcopy(valid_proposal_payload())
    payload["entry_predicates"][0]["operator"] = "EXECUTE_PYTHON"

    with pytest.raises(ValidationError):
        TradePlanProposal.model_validate(payload)


def test_score_breakdown_names_are_allowlisted() -> None:
    payload = valid_proposal_payload()
    payload["score_breakdown"][0]["name"] = "portfolio_override"

    with pytest.raises(ValidationError):
        TradePlanProposal.model_validate(payload)


def test_declared_missing_or_stale_data_forces_fail_closed_decision() -> None:
    payload = deepcopy(valid_proposal_payload())
    payload["missing_or_stale_data"] = (
        {
            "field": "fundamental.earnings",
            "status": MissingDataStatus.STALE,
            "critical": False,
            "detail": "The supplied earnings evidence is stale.",
        },
    )

    with pytest.raises(ValidationError, match="must fail closed"):
        TradePlanProposal.model_validate(payload)


def test_declared_noncritical_missing_data_can_be_report_only() -> None:
    payload = valid_proposal_payload()
    payload["decision"] = ProposedDecision.REPORT_ONLY
    payload["feature_provenance"]["data_quality_status"] = DataQualityStatus.PARTIAL
    payload["feature_provenance"]["quality_disposition"] = (
        QualityDisposition.REPORT_ONLY
    )
    payload["missing_or_stale_data"] = (
        {
            "field": "fundamental.earnings",
            "status": MissingDataStatus.MISSING,
            "critical": False,
            "detail": "Non-core earnings evidence is unavailable.",
        },
    )

    proposal = TradePlanProposal.model_validate(payload)

    assert proposal.decision is ProposedDecision.REPORT_ONLY


def test_pyramiding_cannot_authorize_averaging_down() -> None:
    payload = deepcopy(valid_proposal_payload())
    payload["pyramiding_candidates"][0]["requires_profitable_position"] = False

    with pytest.raises(ValidationError):
        TradePlanProposal.model_validate(payload)


@pytest.mark.parametrize("required_field", ["model", "prompt_version", "sampling"])
def test_model_prompt_and_sampling_versions_are_required(required_field: str) -> None:
    payload = valid_proposal_payload()
    del payload[required_field]

    with pytest.raises(ValidationError):
        TradePlanProposal.model_validate(payload)


def test_strict_json_round_trip_preserves_structured_contract() -> None:
    proposal = TradePlanProposal.model_validate(valid_proposal_payload())

    reparsed = TradePlanProposal.model_validate_json(proposal.model_dump_json())

    assert reparsed == proposal
