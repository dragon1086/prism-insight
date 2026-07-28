from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.llm.proposal_service import ProposalService
from prism_core.llm.trade_plan import (
    MissingDataStatus,
    MissingOrStaleData,
    ProposedDecision,
    TradePlanProposal,
)
from prism_core.policy import (
    DispositionAction,
    FieldDisposition,
    ProposalValidationPolicy,
    ProposalValidationStatus,
    ProposalValidator,
)
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    QuantScoreBreakdown,
    QuantScoreComponent,
    StrategyId,
    StrategyVersion,
)
from prism_core.strategies.registry import StrategyRegistry
from prism_core.strategies.swing import SWING_V1
from prism_core.strategies.trend import TREND_V1
from tests.llm.test_trade_plan_schema import valid_proposal_payload


AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)
EVALUATED_AT = datetime(2026, 7, 24, 21, 0, tzinfo=timezone.utc)
EVIDENCE_IDS = frozenset({"ev-price-1", "ev-risk-1"})


def feature_snapshot() -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000103"),
        strategy_id=StrategyId.SWING_V1,
        strategy_version=StrategyVersion("1.0.0"),
        market=Market.US,
        security_id=SecurityId(
            value=UUID("00000000-0000-0000-0000-000000000102")
        ),
        data_snapshot_id=UUID("00000000-0000-0000-0000-000000000104"),
        as_of=AS_OF,
        feature_version="features.v1",
        values=(
            FeatureValue(
                name="swing_v1.price_momentum_5d", value=Decimal("0.05")
            ),
            FeatureValue(
                name="swing_v1.regime_compatibility", value=Decimal("60")
            ),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def quant_score(snapshot: FeatureSnapshot | None = None) -> QuantScoreBreakdown:
    snapshot = snapshot or feature_snapshot()
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
            QuantScoreComponent(
                name="swing_v1.quant_total", score=Decimal("70")
            ),
        ),
    )


def parsed_result(
    *,
    proposal: TradePlanProposal | None = None,
    snapshot: FeatureSnapshot | None = None,
):
    snapshot = snapshot or feature_snapshot()
    if proposal is None:
        payload = valid_proposal_payload()
        payload["strategy_id"] = snapshot.strategy_id
        payload["strategy_version"] = snapshot.strategy_version
        payload["market"] = snapshot.market
        payload["security_id"] = snapshot.security_id
        payload["feature_provenance"] = {
            "feature_snapshot_id": snapshot.feature_snapshot_id,
            "data_snapshot_id": snapshot.data_snapshot_id,
            "as_of": snapshot.as_of,
            "feature_version": snapshot.feature_version,
            "data_quality_status": snapshot.data_quality_status,
            "quality_disposition": snapshot.quality_disposition,
        }
        if snapshot.quality_disposition is not QualityDisposition.ACCEPT:
            payload["decision"] = ProposedDecision.REPORT_ONLY
            payload["missing_or_stale_data"] = (
                {
                    "field": "core.price",
                    "status": MissingDataStatus.STALE,
                    "critical": True,
                    "detail": "Core price data is not fresh.",
                },
            )
        proposal = TradePlanProposal.model_validate(payload)
    return ProposalService().parse(
        raw_response=proposal.model_dump_json(),
        feature_snapshot=snapshot,
        available_evidence_ids=EVIDENCE_IDS,
    )


def validator(
    *,
    max_risk_multiplier: Decimal = Decimal("0.80"),
    registry: StrategyRegistry | None = None,
) -> ProposalValidator:
    policy = ProposalValidationPolicy(
        validator_version="proposal-validator.v1",
        max_snapshot_age=timedelta(hours=2),
        max_risk_multiplier=max_risk_multiplier,
        max_llm_quant_score_gap=Decimal("20"),
        max_regime_divergence=Decimal("15"),
    )
    return ProposalValidator(policy) if registry is None else ProposalValidator(policy, registry=registry)


def test_accepts_bound_proposal_and_preserves_raw_identity_and_evidence_links() -> None:
    parse_result = parsed_result()

    result = validator().validate(
        parse_result=parse_result,
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.ACCEPTED
    assert result.proposal is parse_result.proposal
    assert result.raw_response == parse_result.raw_response
    assert result.proposal_id == parse_result.proposal.proposal_id
    assert result.validator_version == "proposal-validator.v1"
    assert result.reasons == ()
    assert any(
        item.field_path == "quant_score.total_score"
        and item.action is DispositionAction.RECALCULATE
        and item.resolved_value == "70"
        for item in result.dispositions
    )
    evidence_dispositions = [
        item for item in result.dispositions if item.field_path.startswith("evidence.")
    ]
    assert {item.field_path for item in evidence_dispositions} == {
        "evidence.ev-price-1",
        "evidence.ev-risk-1",
    }
    assert all(item.action is DispositionAction.ACCEPT for item in evidence_dispositions)
    assert any(
        item.field_path == "entry_predicates[0].evaluation"
        and item.action is DispositionAction.RECALCULATE
        and item.resolved_value == "true"
        for item in result.dispositions
    )
    assert any(
        item.field_path == "entry_predicates[0].observed_value"
        and item.action is DispositionAction.RECALCULATE
        and item.resolved_value == "0.05"
        for item in result.dispositions
    )
    assert any(
        item.field_path == "pyramiding_candidates[0]"
        and item.action is DispositionAction.ACCEPT
        and item.resolved_value == "validated"
        for item in result.dispositions
    )


def test_schema_rejection_preserves_raw_output_and_emits_reject_dispositions() -> None:
    raw_response = "{}"
    parse_result = ProposalService().parse(
        raw_response=raw_response,
        feature_snapshot=feature_snapshot(),
    )

    result = validator().validate(
        parse_result=parse_result,
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.raw_response == raw_response
    assert result.proposal is None
    assert result.proposal_id is None
    assert result.reasons == parse_result.errors
    assert result.dispositions
    assert all(item.action is DispositionAction.REJECT for item in result.dispositions)
    assert tuple(item.reason for item in result.dispositions) == parse_result.errors


def test_rejects_snapshot_that_is_older_than_policy_allows() -> None:
    result = validator().validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=AS_OF + timedelta(hours=2, microseconds=1),
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("stale_data_snapshot",)
    assert any(
        item.field_path == "feature_provenance.as_of"
        and item.action is DispositionAction.REJECT
        for item in result.dispositions
    )


def test_rejects_evidence_reference_that_is_not_in_the_validation_envelope() -> None:
    result = validator().validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=frozenset({"ev-price-1"}),
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("unknown_evidence_id:ev-risk-1",)
    assert result.dispositions == (
        FieldDisposition(
            field_path="evidence.ev-risk-1",
            action=DispositionAction.REJECT,
            reason="unknown_evidence_id:ev-risk-1",
            proposed_value="ev-risk-1",
            evidence_ids=("ev-risk-1",),
        ),
    )


def test_rejects_duplicate_evidence_ids_without_raising() -> None:
    proposal = parsed_result().proposal
    assert proposal is not None
    duplicated_stop = proposal.stop_candidates[0].model_copy(
        update={"evidence_ids": ("ev-price-1", "ev-price-1")}
    )
    changed = proposal.model_copy(update={"stop_candidates": (duplicated_stop,)})

    result = validator().validate(
        parse_result=parsed_result(proposal=changed),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == (
        "duplicate_evidence_id:stop_candidates[0]:ev-price-1",
    )
    assert result.dispositions[0].field_path == "stop_candidates[0].evidence_ids"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_quant_score_that_is_not_bound_to_the_feature_snapshot() -> None:
    mismatched_score = replace(
        quant_score(),
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000999"),
    )

    result = validator().validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=mismatched_score,
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("quant_score_binding_mismatch:feature_snapshot_id",)
    assert result.dispositions[0].field_path == "quant_score.feature_snapshot_id"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_strategy_when_registered_definition_does_not_support_market() -> None:
    registry = StrategyRegistry(
        (replace(SWING_V1, supported_markets=(Market.KR,)), TREND_V1)
    )

    result = validator(registry=registry).validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("strategy_market_incompatible",)
    assert result.dispositions[0].field_path == "market"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_unregistered_strategy_without_raising() -> None:
    result = validator(registry=StrategyRegistry((TREND_V1,))).validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("strategy_not_registered:SWING_V1",)
    assert result.dispositions[0].field_path == "strategy_id"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_non_fresh_or_non_accept_data_quality_for_new_proposal() -> None:
    snapshot = replace(
        feature_snapshot(),
        data_quality_status=DataQualityStatus.PARTIAL,
        quality_disposition=QualityDisposition.REPORT_ONLY,
    )

    result = validator().validate(
        parse_result=parsed_result(snapshot=snapshot),
        feature_snapshot=snapshot,
        quant_score=quant_score(snapshot),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("core_data_not_proposal_eligible",)
    assert result.dispositions[0].field_path == "feature_provenance.data_quality_status"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_long_entry_stop_that_is_not_below_every_reference_price() -> None:
    proposal = parsed_result().proposal
    assert proposal is not None
    invalid_stop = proposal.stop_candidates[0].model_copy(
        update={"price": Decimal("100")}
    )
    changed = proposal.model_copy(update={"stop_candidates": (invalid_stop,)})

    result = validator().validate(
        parse_result=parsed_result(proposal=changed),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("stop_not_below_entry_reference",)
    assert result.dispositions[0].field_path == "stop_candidates[0].price"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_clamps_risk_multiplier_without_mutating_raw_proposal() -> None:
    parse_result = parsed_result()

    result = validator(max_risk_multiplier=Decimal("0.50")).validate(
        parse_result=parse_result,
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.ACCEPTED
    assert result.proposal is parse_result.proposal
    assert result.proposal is not None
    assert result.proposal.risk_multiplier_candidate.value == Decimal("0.75")
    assert result.raw_response == parse_result.raw_response
    assert any(
        item.field_path == "risk_multiplier_candidate.value"
        and item.action is DispositionAction.CLAMP
        and item.proposed_value == "0.75"
        and item.resolved_value == "0.50"
        for item in result.dispositions
    )


def test_records_stop_and_target_without_claiming_unchecked_entry_relationship() -> None:
    proposal = parsed_result().proposal
    assert proposal is not None
    changed = proposal.model_copy(
        update={
            "decision": ProposedDecision.WATCH,
            "entry_predicates": (),
        }
    )

    result = validator().validate(
        parse_result=parsed_result(proposal=changed),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.ACCEPTED
    stop = next(
        item for item in result.dispositions if item.field_path == "stop_candidates[0].price"
    )
    target = next(
        item for item in result.dispositions if item.field_path == "target_candidates[0].price"
    )
    assert stop.reason == "stop_price_recorded_without_entry_reference"
    assert target.reason == "target_price_recorded_without_entry_reference"


def test_rejects_llm_score_that_diverges_beyond_quant_policy_bound() -> None:
    proposal = parsed_result().proposal
    assert proposal is not None
    changed = proposal.model_copy(update={"llm_score": Decimal("95")})

    result = validator().validate(
        parse_result=parsed_result(proposal=changed),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("llm_quant_score_gap_exceeded",)
    assert result.dispositions[0].field_path == "llm_score"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_rejects_regime_distribution_inconsistent_with_deterministic_feature() -> None:
    snapshot = replace(
        feature_snapshot(),
        values=(
            FeatureValue(
                name="swing_v1.price_momentum_5d", value=Decimal("0.05")
            ),
            FeatureValue(
                name="swing_v1.regime_compatibility", value=Decimal("10")
            ),
        ),
    )

    result = validator().validate(
        parse_result=parsed_result(snapshot=snapshot),
        feature_snapshot=snapshot,
        quant_score=quant_score(snapshot),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("regime_feature_divergence_exceeded",)
    assert result.dispositions[0].field_path == "regime.probabilities"
    assert result.dispositions[0].action is DispositionAction.REJECT


def test_policy_hard_veto_rejects_otherwise_valid_proposal() -> None:
    result = validator().validate(
        parse_result=parsed_result(),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
        hard_vetoes=("kill_switch_active",),
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("policy_hard_veto:kill_switch_active",)
    assert result.dispositions == (
        FieldDisposition(
            field_path="policy.hard_veto",
            action=DispositionAction.REJECT,
            reason="policy_hard_veto:kill_switch_active",
            proposed_value="kill_switch_active",
        ),
    )


def test_rejects_when_validation_snapshot_no_longer_matches_parsed_proposal() -> None:
    changed_snapshot = replace(
        feature_snapshot(),
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000999"),
    )

    result = validator().validate(
        parse_result=parsed_result(),
        feature_snapshot=changed_snapshot,
        quant_score=quant_score(changed_snapshot),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("proposal_binding_mismatch:feature_snapshot_id",)
    assert result.dispositions[0].field_path == (
        "feature_provenance.feature_snapshot_id"
    )
    assert result.dispositions[0].action is DispositionAction.REJECT


@pytest.mark.parametrize(
    "status",
    [MissingDataStatus.MISSING, MissingDataStatus.STALE, MissingDataStatus.CONFLICT],
)
def test_rejects_declared_critical_missing_stale_or_conflicting_claim(
    status: MissingDataStatus,
) -> None:
    proposal = parsed_result().proposal
    assert proposal is not None
    issue = MissingOrStaleData(
        field="core.price",
        status=status,
        critical=True,
        detail="Core price evidence cannot support a new proposal.",
    )
    changed = proposal.model_copy(
        update={
            "decision": ProposedDecision.NO_ENTRY,
            "missing_or_stale_data": (issue,),
        }
    )

    result = validator().validate(
        parse_result=parsed_result(proposal=changed),
        feature_snapshot=feature_snapshot(),
        quant_score=quant_score(),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )

    assert result.status is ProposalValidationStatus.REJECTED
    assert result.reasons == ("declared_critical_data_issue:core.price",)
    assert result.dispositions[0].field_path == "missing_or_stale_data[0]"
    assert result.dispositions[0].action is DispositionAction.REJECT
