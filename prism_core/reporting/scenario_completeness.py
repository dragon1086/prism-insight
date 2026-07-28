"""One honest completion contract for product strategy scenarios.

This module classifies persisted/model proposal evidence without inventing an
investment decision.  It intentionally exposes only normalized, allowlisted
scenario content and bounded validator reasons; raw model output stays in the
audit store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping

from pydantic import ValidationError

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.llm.trade_plan import ProposedDecision, TradePlanProposal
from prism_core.policy.dispositions import DispositionAction, FieldDisposition


class ProductScenarioState(str, Enum):
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"
    INVALID_PROPOSAL = "INVALID_PROPOSAL"
    POLICY_REJECTED = "POLICY_REJECTED"
    REPORT_ONLY = "REPORT_ONLY"
    WATCH = "WATCH"
    NO_ENTRY = "NO_ENTRY"
    ENTRY_CANDIDATE = "ENTRY_CANDIDATE"


@dataclass(frozen=True)
class ScenarioAssessment:
    state: ProductScenarioState
    complete: bool
    reasons: tuple[str, ...]
    proposed_decision: ProposedDecision | None
    scenario: Mapping[str, object]


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def sanitize_scenario_reasons(reasons: Iterable[object]) -> tuple[str, ...]:
    """Return bounded single-line validator reasons suitable for public surfaces."""

    result: list[str] = []
    for value in reasons:
        if not isinstance(value, str):
            continue
        reason = _CONTROL.sub(" ", value).strip()
        if not reason:
            continue
        reason = reason[:240]
        if reason not in result:
            result.append(reason)
        if len(result) == 20:
            break
    return tuple(result)


def assess_scenario(
    *,
    parse_status: str,
    validation_status: str,
    normalized_proposal_json: str | None = None,
    proposal: TradePlanProposal | None = None,
    dispositions: Iterable[FieldDisposition] = (),
    reasons: Iterable[object] = (),
    expected_identity: Mapping[str, str] | None = None,
) -> ScenarioAssessment:
    """Classify one proposal using the shared application/report/dashboard contract."""

    disposition_values = tuple(dispositions)
    public_reasons = sanitize_scenario_reasons(
        (
            *reasons,
            *(
                item.reason
                for item in disposition_values
                if item.action is DispositionAction.REJECT
            ),
        )
    )
    if parse_status != "PARSED":
        return _incomplete(
            ProductScenarioState.INVALID_PROPOSAL,
            public_reasons or ("strict proposal parsing failed",),
        )

    if proposal is None and normalized_proposal_json is not None:
        try:
            proposal = TradePlanProposal.model_validate_json(normalized_proposal_json)
        except (ValidationError, ValueError, TypeError):
            return _incomplete(
                ProductScenarioState.INVALID_PROPOSAL,
                public_reasons or ("normalized proposal failed strict readback",),
            )
    if proposal is None:
        return _incomplete(
            ProductScenarioState.INVALID_PROPOSAL,
            public_reasons or ("normalized proposal is unavailable",),
        )
    identity_mismatches = _identity_mismatches(proposal, expected_identity or {})
    if identity_mismatches:
        return _incomplete(
            ProductScenarioState.POLICY_REJECTED,
            sanitize_scenario_reasons(
                (*public_reasons, *(f"proposal identity mismatch: {item}" for item in identity_mismatches))
            ),
        )
    if validation_status != "ACCEPTED" or any(
        item.action is DispositionAction.REJECT for item in disposition_values
    ):
        return _incomplete(
            ProductScenarioState.POLICY_REJECTED,
            public_reasons or ("deterministic proposal validation rejected",),
        )

    accepted_evidence = {
        item.field_path.removeprefix("evidence.")
        for item in disposition_values
        if item.field_path.startswith("evidence.")
        and item.action is DispositionAction.ACCEPT
    }
    missing_evidence = sorted(_referenced_evidence(proposal) - accepted_evidence)
    if missing_evidence:
        return _incomplete(
            ProductScenarioState.POLICY_REJECTED,
            sanitize_scenario_reasons(
                f"evidence disposition unavailable: {item}" for item in missing_evidence
            ),
        )

    scenario, missing = _project_complete_scenario(proposal, disposition_values)
    if missing:
        incomplete_state = (
            ProductScenarioState.REPORT_ONLY
            if proposal.decision is ProposedDecision.REPORT_ONLY
            else ProductScenarioState.ANALYSIS_INCOMPLETE
        )
        return ScenarioAssessment(
            state=incomplete_state,
            complete=False,
            reasons=sanitize_scenario_reasons(
                (*public_reasons, *(f"scenario field unavailable: {item}" for item in missing))
            ),
            proposed_decision=None,
            scenario=scenario,
        )
    return ScenarioAssessment(
        state=ProductScenarioState(proposal.decision.value),
        complete=True,
        reasons=public_reasons,
        proposed_decision=proposal.decision,
        scenario=scenario,
    )


def assess_missing_analysis(
    *,
    data_quality: str,
    quality_disposition: str,
    reasons: Iterable[object] = (),
) -> ScenarioAssessment:
    """Classify a quality-gated run that correctly made no model proposal."""

    public_reasons = sanitize_scenario_reasons(reasons)
    if data_quality == "UNAVAILABLE":
        state = ProductScenarioState.DATA_UNAVAILABLE
    elif quality_disposition == "REPORT_ONLY":
        state = ProductScenarioState.REPORT_ONLY
    else:
        state = ProductScenarioState.ANALYSIS_INCOMPLETE
    return _incomplete(state, public_reasons or ("scenario inputs are unavailable",))


def _incomplete(
    state: ProductScenarioState, reasons: tuple[str, ...]
) -> ScenarioAssessment:
    return ScenarioAssessment(
        state=state,
        complete=False,
        reasons=reasons,
        proposed_decision=None,
        scenario={},
    )


def _project_complete_scenario(
    proposal: TradePlanProposal,
    dispositions: tuple[FieldDisposition, ...],
) -> tuple[dict[str, object], tuple[str, ...]]:
    predicate_results = {
        item.field_path: item.resolved_value
        for item in dispositions
        if item.field_path.startswith("entry_predicates[")
        and item.field_path.endswith("].evaluation")
        and item.action is DispositionAction.RECALCULATE
        and item.resolved_value in {"true", "false"}
    }
    observed_values = {
        item.field_path: item.resolved_value
        for item in dispositions
        if item.field_path.startswith("entry_predicates[")
        and item.field_path.endswith("].observed_value")
        and item.action is DispositionAction.RECALCULATE
        and item.resolved_value is not None
    }
    triggers = []
    for index, predicate in enumerate(proposal.entry_predicates):
        result = predicate_results.get(f"entry_predicates[{index}].evaluation")
        observed_value = observed_values.get(
            f"entry_predicates[{index}].observed_value"
        )
        triggers.append(
            {
                "feature_name": predicate.feature_name,
                "operator": predicate.operator.value,
                "comparison_value": format(predicate.comparison_value, "f"),
                "upper_value": (
                    None
                    if predicate.upper_value is None
                    else format(predicate.upper_value, "f")
                ),
                "observed_value": observed_value,
                "observed_result": result,
                "valid_until": predicate.valid_until.isoformat(),
                "evidence_ids": list(predicate.evidence_ids),
            }
        )
    validity_times = [
        *(item.valid_until for item in proposal.entry_predicates),
        proposal.bull_case.valid_until,
        proposal.base_case.valid_until,
        proposal.bear_case.valid_until,
    ]
    next_review: datetime | None = min(validity_times) if validity_times else None
    regime = proposal.regime
    regime_projection = {
        "probabilities": regime.probabilities.model_dump(mode="json"),
        "confidence": format(regime.confidence, "f"),
        "drivers": list(regime.drivers),
        "falsifiers": list(regime.falsifiers),
    }
    score_breakdown = [
        {
            "name": component.name.value,
            "score": format(component.score, "f"),
            "rationale": component.rationale,
            "evidence_ids": list(component.evidence_ids),
        }
        for component in proposal.score_breakdown
    ]
    sector_components = [
        component
        for component in score_breakdown
        if component["name"] == "sector_leadership"
    ]
    price_candidate = lambda item: {
        "price": format(item.price, "f"),
        "basis": item.basis.value,
        "price_basis": item.price_basis.value,
        "rationale": item.rationale,
        "evidence_ids": list(item.evidence_ids),
    }
    disposition_projection = [
        {
            "field_path": item.field_path,
            "action": item.action.value,
            "reason": item.reason,
            "proposed_value": item.proposed_value,
            "resolved_value": item.resolved_value,
            "evidence_ids": list(item.evidence_ids),
        }
        for item in dispositions
    ]
    branch_projection = lambda item: {
        "summary": item.summary,
        "conditions": list(item.conditions),
        "confirmations": list(item.confirmations),
        "falsifiers": list(item.falsifiers),
        "next_event": item.next_event,
        "valid_until": item.valid_until.isoformat(),
        "evidence_ids": list(item.evidence_ids),
    }
    scenario: dict[str, object] = {
        "regime": regime_projection,
        "market_judgment": regime_projection,
        "sector_judgment": {
            "status": "ASSESSED" if sector_components else "NOT_ASSESSED",
            "score_components": sector_components,
        },
        "security_judgment": {
            "llm_score": format(proposal.llm_score, "f"),
            "score_breakdown": score_breakdown,
            "decision": proposal.decision.value,
        },
        "bull_path": branch_projection(proposal.bull_case),
        "base_path": branch_projection(proposal.base_case),
        "bear_path": branch_projection(proposal.bear_case),
        "bull_evidence_ids": list(proposal.bull_evidence_ids),
        "bear_evidence_ids": list(proposal.bear_evidence_ids),
        "counter_evidence": list(proposal.bear_evidence_ids),
        "current_action": proposal.decision.value,
        "triggers": triggers,
        "entry_triggers": triggers,
        "avoid_triggers": [
            item for item in triggers if item["observed_result"] == "false"
        ],
        "stop_candidates": [price_candidate(item) for item in proposal.stop_candidates],
        "target_candidates": [
            price_candidate(item) for item in proposal.target_candidates
        ],
        "risk_multiplier_candidate": {
            "value": format(proposal.risk_multiplier_candidate.value, "f"),
            "rationale": proposal.risk_multiplier_candidate.rationale,
            "evidence_ids": list(proposal.risk_multiplier_candidate.evidence_ids),
        },
        "reentry_candidates": [
            {
                "conditions": list(item.conditions),
                "rationale": item.rationale,
                "evidence_ids": list(item.evidence_ids),
            }
            for item in proposal.reentry_candidates
        ],
        "pyramiding_candidates": [
            {
                "conditions": list(item.conditions),
                "requires_profitable_position": item.requires_profitable_position,
                "rationale": item.rationale,
                "evidence_ids": list(item.evidence_ids),
            }
            for item in proposal.pyramiding_candidates
        ],
        "failure_transition": list(regime.falsifiers),
        "falsifiers": list(regime.falsifiers),
        "uncertainty": {
            "level": format(proposal.uncertainty.level, "f"),
            "known_unknowns": list(proposal.uncertainty.known_unknowns),
            "assumptions": list(proposal.uncertainty.assumptions),
        },
        "field_dispositions": disposition_projection,
        "next_review_at": None if next_review is None else next_review.isoformat(),
    }
    missing: list[str] = []
    for field in (
        "regime",
        "bull_path",
        "base_path",
        "bear_path",
        "current_action",
        "triggers",
        "failure_transition",
        "falsifiers",
        "uncertainty",
        "next_review_at",
    ):
        if not scenario[field]:
            missing.append(field)
    if not dispositions:
        missing.append("field_dispositions")
    if any(item["observed_result"] not in {"true", "false"} for item in triggers):
        missing.append("machine_evaluable_trigger_results")
    if any(item["observed_value"] is None for item in triggers):
        missing.append("trigger_observed_values")

    observed_results = [item["observed_result"] for item in triggers]
    if (
        proposal.feature_provenance.data_quality_status is not DataQualityStatus.FRESH
        or proposal.feature_provenance.quality_disposition is not QualityDisposition.ACCEPT
    ):
        missing.append(
            "feature_quality_not_eligible:"
            f"{proposal.feature_provenance.data_quality_status.value}:"
            f"{proposal.feature_provenance.quality_disposition.value}"
        )
    missing.extend(
        f"critical_missing_or_stale_data:{item.field}:{item.status.value}"
        for item in proposal.missing_or_stale_data
        if item.critical
    )
    if proposal.decision is ProposedDecision.REPORT_ONLY:
        missing.append("report_only_not_investment_decision")
    if proposal.decision is ProposedDecision.WATCH:
        if not observed_results:
            missing.append("watch_trigger")
        elif all(item == "true" for item in observed_results):
            missing.append("watch_pending_predicate")
    if proposal.decision is ProposedDecision.NO_ENTRY and (
        not observed_results or any(item != "false" for item in observed_results)
    ):
        missing.append("no_entry_threshold_failure")
    if proposal.decision is ProposedDecision.ENTRY_CANDIDATE:
        if not observed_results or any(item != "true" for item in observed_results):
            missing.append("entry_trigger_confirmation")
        if not proposal.stop_candidates:
            missing.append("stop_candidate")
        if not proposal.target_candidates:
            missing.append("target_candidate")
    return scenario, tuple(dict.fromkeys(missing))


def _identity_mismatches(
    proposal: TradePlanProposal,
    expected: Mapping[str, str],
) -> tuple[str, ...]:
    actual = {
        "strategy_id": proposal.strategy_id.value,
        "strategy_version": proposal.strategy_version.value,
        "market": proposal.market.value,
        "security_id": str(proposal.security_id.value),
        "data_snapshot_id": str(proposal.feature_provenance.data_snapshot_id),
        "feature_snapshot_id": str(proposal.feature_provenance.feature_snapshot_id),
    }
    return tuple(
        field
        for field, expected_value in expected.items()
        if field not in actual or actual[field] != expected_value
    )


def _referenced_evidence(proposal: TradePlanProposal) -> set[str]:
    evidence = set(proposal.bull_evidence_ids) | set(proposal.bear_evidence_ids)
    for branch in (proposal.bull_case, proposal.base_case, proposal.bear_case):
        evidence.update(branch.evidence_ids)
    for item in proposal.score_breakdown:
        evidence.update(item.evidence_ids)
    for collection in (
        proposal.entry_predicates,
        proposal.stop_candidates,
        proposal.target_candidates,
        proposal.reentry_candidates,
        proposal.pyramiding_candidates,
    ):
        for item in collection:
            evidence.update(item.evidence_ids)
    evidence.update(proposal.risk_multiplier_candidate.evidence_ids)
    return evidence
