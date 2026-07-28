from __future__ import annotations

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.llm.trade_plan import (
    MissingDataStatus,
    MissingOrStaleData,
    ProposedDecision,
    TradePlanProposal,
)
from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.reporting.scenario_completeness import (
    ProductScenarioState,
    assess_missing_analysis,
    assess_scenario,
)
from tests.llm.test_trade_plan_schema import valid_proposal_payload


def _proposal(decision: ProposedDecision = ProposedDecision.ENTRY_CANDIDATE) -> TradePlanProposal:
    proposal = TradePlanProposal.model_validate(valid_proposal_payload())
    return proposal.model_copy(update={"decision": decision})


def _predicate_disposition(result: str) -> FieldDisposition:
    return FieldDisposition(
        field_path="entry_predicates[0].evaluation",
        action=DispositionAction.RECALCULATE,
        reason="predicate evaluated from feature snapshot",
        resolved_value=result,
        evidence_ids=("ev-price-1",),
    )


def _accepted_dispositions(result: str) -> tuple[FieldDisposition, ...]:
    return (
        _predicate_disposition(result),
        FieldDisposition(
            field_path="entry_predicates[0].observed_value",
            action=DispositionAction.RECALCULATE,
            reason="feature value read from bound snapshot",
            resolved_value="0.10",
            evidence_ids=("ev-price-1",),
        ),
        FieldDisposition(
            field_path="evidence.ev-price-1",
            action=DispositionAction.ACCEPT,
            reason="evidence exists",
            resolved_value="ev-price-1",
            evidence_ids=("ev-price-1",),
        ),
        FieldDisposition(
            field_path="evidence.ev-risk-1",
            action=DispositionAction.ACCEPT,
            reason="evidence exists",
            resolved_value="ev-risk-1",
            evidence_ids=("ev-risk-1",),
        ),
    )


def test_quality_gated_states_are_distinct_and_never_claim_a_decision() -> None:
    unavailable = assess_missing_analysis(
        data_quality="UNAVAILABLE", quality_disposition="REJECT", reasons=("no price",)
    )
    incomplete = assess_missing_analysis(
        data_quality="PARTIAL", quality_disposition="REJECT", reasons=("partial price",)
    )
    report_only = assess_missing_analysis(
        data_quality="STALE", quality_disposition="REPORT_ONLY", reasons=("stale news",)
    )

    assert unavailable.state is ProductScenarioState.DATA_UNAVAILABLE
    assert incomplete.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert report_only.state is ProductScenarioState.REPORT_ONLY
    assert all(
        item.complete is False and item.proposed_decision is None and item.scenario == {}
        for item in (unavailable, incomplete, report_only)
    )


def test_invalid_and_policy_rejected_proposals_remain_distinct() -> None:
    invalid = assess_scenario(
        parse_status="REJECTED",
        validation_status="REJECTED",
        reasons=("schema rejected",),
    )
    policy_rejected = assess_scenario(
        parse_status="PARSED",
        validation_status="REJECTED",
        proposal=_proposal(),
        dispositions=(
            FieldDisposition(
                field_path="risk_multiplier_candidate.value",
                action=DispositionAction.REJECT,
                reason="policy cap exceeded",
            ),
        ),
    )

    assert invalid.state is ProductScenarioState.INVALID_PROPOSAL
    assert policy_rejected.state is ProductScenarioState.POLICY_REJECTED
    assert invalid.proposed_decision is None
    assert policy_rejected.proposed_decision is None


def test_complete_decision_requires_evaluated_thresholds_and_next_review() -> None:
    accepted = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(),
        dispositions=_accepted_dispositions("true"),
    )

    assert accepted.state is ProductScenarioState.ENTRY_CANDIDATE
    assert accepted.complete is True
    assert accepted.proposed_decision is ProposedDecision.ENTRY_CANDIDATE
    assert accepted.scenario["next_review_at"] == "2026-07-27T20:00:00+00:00"
    assert accepted.scenario["triggers"][0]["observed_result"] == "true"  # type: ignore[index]
    assert accepted.scenario["triggers"][0]["observed_value"] == "0.10"  # type: ignore[index]
    assert "reference_price" not in accepted.scenario["triggers"][0]  # type: ignore[index]
    assert accepted.scenario["market_judgment"] == accepted.scenario["regime"]
    assert accepted.scenario["bull_path"]["summary"].startswith("Leadership broadens")  # type: ignore[index]
    assert accepted.scenario["base_path"]["confirmations"]  # type: ignore[index]
    assert accepted.scenario["bear_path"]["evidence_ids"] == ["ev-risk-1"]  # type: ignore[index]
    assert accepted.scenario["security_judgment"]["llm_score"] == "72.5"  # type: ignore[index]
    assert accepted.scenario["security_judgment"]["score_breakdown"][0] == {  # type: ignore[index]
        "name": "trend_structure",
        "score": "75",
        "rationale": "Price structure is constructive.",
        "evidence_ids": ["ev-price-1"],
    }
    assert accepted.scenario["entry_triggers"] == accepted.scenario["triggers"]
    assert accepted.scenario["avoid_triggers"] == []
    assert accepted.scenario["stop_candidates"][0]["price"] == "95"  # type: ignore[index]
    assert accepted.scenario["target_candidates"][0]["price"] == "115"  # type: ignore[index]
    assert accepted.scenario["bull_evidence_ids"] == ["ev-price-1"]
    assert accepted.scenario["bear_evidence_ids"] == ["ev-risk-1"]
    assert accepted.scenario["counter_evidence"] == ["ev-risk-1"]
    assert accepted.scenario["field_dispositions"][0]["field_path"] == (  # type: ignore[index]
        "entry_predicates[0].evaluation"
    )
    assert accepted.scenario["field_dispositions"][0]["resolved_value"] == "true"  # type: ignore[index]


def test_no_entry_requires_observed_threshold_failure() -> None:
    complete = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(ProposedDecision.NO_ENTRY),
        dispositions=_accepted_dispositions("false"),
    )
    dishonest = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(ProposedDecision.NO_ENTRY),
        dispositions=_accepted_dispositions("true"),
    )

    assert complete.state is ProductScenarioState.NO_ENTRY
    assert complete.complete is True
    assert dishonest.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert dishonest.proposed_decision is None
    assert "scenario field unavailable: no_entry_threshold_failure" in dishonest.reasons


def test_watch_requires_at_least_one_pending_predicate() -> None:
    pending = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(ProposedDecision.WATCH),
        dispositions=_accepted_dispositions("false"),
    )
    already_satisfied = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(ProposedDecision.WATCH),
        dispositions=_accepted_dispositions("true"),
    )

    assert pending.state is ProductScenarioState.WATCH
    assert pending.complete is True
    assert pending.scenario["failure_transition"]
    assert already_satisfied.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert already_satisfied.proposed_decision is None
    assert "scenario field unavailable: watch_pending_predicate" in already_satisfied.reasons


def test_critical_missing_data_cannot_be_completed_as_no_entry() -> None:
    proposal = _proposal(ProposedDecision.NO_ENTRY).model_copy(
        update={
            "missing_or_stale_data": (
                MissingOrStaleData(
                    field="core_price",
                    status=MissingDataStatus.MISSING,
                    critical=True,
                    detail="required close is unavailable",
                ),
            )
        }
    )
    assessment = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=proposal,
        dispositions=_accepted_dispositions("false"),
    )

    assert assessment.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert assessment.complete is False
    assert assessment.proposed_decision is None
    assert any("critical_missing_or_stale_data:core_price:MISSING" in item for item in assessment.reasons)


def test_report_only_remains_distinct_and_incomplete() -> None:
    assessment = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(ProposedDecision.REPORT_ONLY),
        dispositions=_accepted_dispositions("false"),
    )

    assert assessment.state is ProductScenarioState.REPORT_ONLY
    assert assessment.complete is False
    assert assessment.proposed_decision is None
    assert "scenario field unavailable: report_only_not_investment_decision" in assessment.reasons


def test_non_eligible_feature_quality_cannot_be_completed_as_no_entry() -> None:
    proposal = _proposal(ProposedDecision.NO_ENTRY)
    proposal = proposal.model_copy(
        update={
            "feature_provenance": proposal.feature_provenance.model_copy(
                update={
                    "data_quality_status": DataQualityStatus.STALE,
                    "quality_disposition": QualityDisposition.REPORT_ONLY,
                }
            )
        }
    )

    assessment = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=proposal,
        dispositions=_accepted_dispositions("false"),
    )

    assert assessment.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert assessment.complete is False
    assert assessment.proposed_decision is None
    assert any(
        "feature_quality_not_eligible:STALE:REPORT_ONLY" in item
        for item in assessment.reasons
    )


def test_complete_state_rejects_persisted_identity_mismatch() -> None:
    assessment = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(),
        dispositions=_accepted_dispositions("true"),
        expected_identity={"market": "KR", "strategy_id": "SWING_V1"},
    )

    assert assessment.state is ProductScenarioState.POLICY_REJECTED
    assert assessment.complete is False
    assert assessment.proposed_decision is None
    assert assessment.reasons == ("proposal identity mismatch: market",)


def test_trigger_without_actual_observed_value_is_incomplete() -> None:
    assessment = assess_scenario(
        parse_status="PARSED",
        validation_status="ACCEPTED",
        proposal=_proposal(),
        dispositions=(
            _predicate_disposition("true"),
            *_accepted_dispositions("true")[2:],
        ),
    )

    assert assessment.state is ProductScenarioState.ANALYSIS_INCOMPLETE
    assert assessment.complete is False
    assert "scenario field unavailable: trigger_observed_values" in assessment.reasons
