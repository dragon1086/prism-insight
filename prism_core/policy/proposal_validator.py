"""Deterministic validation boundary for parsed TradePlanProposal candidates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.llm.proposal_service import ProposalParseResult, ProposalParseStatus
from prism_core.llm.trade_plan import PredicateOperator, TradePlanProposal
from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.strategies.contracts import FeatureSnapshot, QuantScoreBreakdown
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY, StrategyRegistry


class ProposalValidationStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ProposalValidationPolicy:
    """Explicit, versioned bounds applied without model or runtime authority."""

    validator_version: str
    max_snapshot_age: timedelta
    max_risk_multiplier: Decimal
    max_llm_quant_score_gap: Decimal
    max_regime_divergence: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.validator_version, str) or not self.validator_version.strip():
            raise ValueError("validator_version must be a non-empty string")
        if not isinstance(self.max_snapshot_age, timedelta) or self.max_snapshot_age <= timedelta(0):
            raise ValueError("max_snapshot_age must be a positive timedelta")
        for label, value, lower, upper in (
            ("max_risk_multiplier", self.max_risk_multiplier, Decimal("0"), Decimal("1")),
            ("max_llm_quant_score_gap", self.max_llm_quant_score_gap, Decimal("0"), Decimal("100")),
            ("max_regime_divergence", self.max_regime_divergence, Decimal("0"), Decimal("100")),
        ):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < lower
                or value > upper
                or (label == "max_risk_multiplier" and value == lower)
            ):
                raise ValueError(f"{label} is outside its allowed range")


@dataclass(frozen=True)
class ProposalValidationResult:
    """Audit envelope retaining the exact raw model output and parsed identity.

    ``proposal`` remains the immutable raw candidate. Downstream policy consumers
    must use each disposition's ``resolved_value`` for clamped or recalculated
    fields; the raw proposal is never the resolved execution or sizing contract.
    """

    status: ProposalValidationStatus
    validator_version: str
    raw_response: str
    proposal: TradePlanProposal | None
    proposal_id: UUID | None
    dispositions: tuple[FieldDisposition, ...]
    reasons: tuple[str, ...]


class ProposalValidator:
    """Validate a parsed proposal without sizing, execution, or OrderIntent creation."""

    def __init__(
        self,
        policy: ProposalValidationPolicy,
        *,
        registry: StrategyRegistry = DEFAULT_STRATEGY_REGISTRY,
    ) -> None:
        if not isinstance(policy, ProposalValidationPolicy):
            raise TypeError("policy must be a ProposalValidationPolicy")
        if not isinstance(registry, StrategyRegistry):
            raise TypeError("registry must be a StrategyRegistry")
        self._policy = policy
        self._registry = registry

    def validate(
        self,
        *,
        parse_result: ProposalParseResult,
        feature_snapshot: FeatureSnapshot,
        quant_score: QuantScoreBreakdown,
        available_evidence_ids: frozenset[str],
        evaluated_at: datetime,
        hard_vetoes: tuple[str, ...] = (),
    ) -> ProposalValidationResult:
        if not isinstance(parse_result, ProposalParseResult):
            raise TypeError("parse_result must be a ProposalParseResult")
        if not isinstance(feature_snapshot, FeatureSnapshot):
            raise TypeError("feature_snapshot must be a FeatureSnapshot")
        if not isinstance(quant_score, QuantScoreBreakdown):
            raise TypeError("quant_score must be a QuantScoreBreakdown")
        if not isinstance(available_evidence_ids, frozenset) or any(
            not isinstance(item, str) or not item.strip() for item in available_evidence_ids
        ):
            raise TypeError("available_evidence_ids must be a frozenset of non-empty strings")
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if not isinstance(hard_vetoes, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in hard_vetoes
        ):
            raise TypeError("hard_vetoes must be a tuple of non-empty strings")

        if parse_result.status is not ProposalParseStatus.PARSED or parse_result.proposal is None:
            reasons = parse_result.errors or ("proposal_not_parsed",)
            dispositions = tuple(
                FieldDisposition(
                    field_path=_parse_error_field_path(reason),
                    action=DispositionAction.REJECT,
                    reason=reason,
                )
                for reason in reasons
            )
            return self._rejected(parse_result, reasons, dispositions)

        if hard_vetoes:
            reasons = tuple(f"policy_hard_veto:{item}" for item in hard_vetoes)
            dispositions = tuple(
                FieldDisposition(
                    field_path="policy.hard_veto",
                    action=DispositionAction.REJECT,
                    reason=reason,
                    proposed_value=item,
                )
                for item, reason in zip(hard_vetoes, reasons, strict=True)
            )
            return self._rejected(parse_result, reasons, dispositions)

        proposal = parse_result.proposal
        duplicate_evidence = _find_duplicate_evidence_reference(proposal)
        if duplicate_evidence is not None:
            field_path, evidence_id = duplicate_evidence
            return self._reject_field(
                parse_result,
                field_path=f"{field_path}.evidence_ids",
                reason=f"duplicate_evidence_id:{field_path}:{evidence_id}",
                proposed_value=evidence_id,
                evidence_ids=(evidence_id,),
            )
        provenance = proposal.feature_provenance
        proposal_bindings = {
            "strategy_id": (proposal.strategy_id, feature_snapshot.strategy_id),
            "strategy_version": (
                proposal.strategy_version,
                feature_snapshot.strategy_version,
            ),
            "market": (proposal.market, feature_snapshot.market),
            "security_id": (proposal.security_id, feature_snapshot.security_id),
            "feature_snapshot_id": (
                provenance.feature_snapshot_id,
                feature_snapshot.feature_snapshot_id,
            ),
            "data_snapshot_id": (
                provenance.data_snapshot_id,
                feature_snapshot.data_snapshot_id,
            ),
            "as_of": (provenance.as_of, feature_snapshot.as_of),
            "feature_version": (
                provenance.feature_version,
                feature_snapshot.feature_version,
            ),
            "data_quality_status": (
                provenance.data_quality_status,
                feature_snapshot.data_quality_status,
            ),
            "quality_disposition": (
                provenance.quality_disposition,
                feature_snapshot.quality_disposition,
            ),
        }
        provenance_fields = {
            "feature_snapshot_id",
            "data_snapshot_id",
            "as_of",
            "feature_version",
            "data_quality_status",
            "quality_disposition",
        }
        for field, (actual, expected) in proposal_bindings.items():
            if actual != expected:
                prefix = "feature_provenance." if field in provenance_fields else ""
                return self._reject_field(
                    parse_result,
                    field_path=f"{prefix}{field}",
                    reason=f"proposal_binding_mismatch:{field}",
                    proposed_value=str(actual),
                )
        try:
            strategy = self._registry.get(proposal.strategy_id)
        except KeyError:
            return self._reject_field(
                parse_result,
                field_path="strategy_id",
                reason=f"strategy_not_registered:{proposal.strategy_id.value}",
                proposed_value=proposal.strategy_id.value,
            )
        if proposal.strategy_version != strategy.version:
            return self._reject_field(
                parse_result,
                field_path="strategy_version",
                reason="strategy_version_incompatible",
                proposed_value=proposal.strategy_version.value,
            )
        if proposal.market not in strategy.supported_markets:
            return self._reject_field(
                parse_result,
                field_path="market",
                reason="strategy_market_incompatible",
                proposed_value=proposal.market.value,
            )
        if (
            feature_snapshot.data_quality_status is not DataQualityStatus.FRESH
            or feature_snapshot.quality_disposition is not QualityDisposition.ACCEPT
        ):
            return self._reject_field(
                parse_result,
                field_path="feature_provenance.data_quality_status",
                reason="core_data_not_proposal_eligible",
                proposed_value=feature_snapshot.data_quality_status.value,
            )
        for index, issue in enumerate(proposal.missing_or_stale_data):
            if issue.critical:
                return self._reject_field(
                    parse_result,
                    field_path=f"missing_or_stale_data[{index}]",
                    reason=f"declared_critical_data_issue:{issue.field}",
                    proposed_value=issue.status.value,
                )
        snapshot_age = evaluated_at - feature_snapshot.as_of
        if snapshot_age < timedelta(0):
            return self._reject_field(
                parse_result,
                field_path="feature_provenance.as_of",
                reason="future_data_snapshot",
                proposed_value=feature_snapshot.as_of.isoformat(),
            )
        if snapshot_age > self._policy.max_snapshot_age:
            return self._reject_field(
                parse_result,
                field_path="feature_provenance.as_of",
                reason="stale_data_snapshot",
                proposed_value=feature_snapshot.as_of.isoformat(),
            )
        quant_bindings = {
            "feature_snapshot_id": (
                quant_score.feature_snapshot_id,
                feature_snapshot.feature_snapshot_id,
            ),
            "strategy_id": (quant_score.strategy_id, feature_snapshot.strategy_id),
            "strategy_version": (
                quant_score.strategy_version,
                feature_snapshot.strategy_version,
            ),
            "market": (quant_score.market, feature_snapshot.market),
            "security_id": (quant_score.security_id, feature_snapshot.security_id),
        }
        for field, (actual, expected) in quant_bindings.items():
            if actual != expected:
                return self._reject_field(
                    parse_result,
                    field_path=f"quant_score.{field}",
                    reason=f"quant_score_binding_mismatch:{field}",
                    proposed_value=str(actual),
                )
        score_gap = abs(proposal.llm_score - quant_score.total_score)
        if score_gap > self._policy.max_llm_quant_score_gap:
            return self._reject_field(
                parse_result,
                field_path="llm_score",
                reason="llm_quant_score_gap_exceeded",
                proposed_value=format(proposal.llm_score, "f"),
            )
        feature_values = {item.name: item.value for item in feature_snapshot.values}
        regime_feature_name = f"{proposal.strategy_id.value.lower()}.regime_compatibility"
        try:
            regime_feature = feature_values[regime_feature_name]
        except KeyError:
            return self._reject_field(
                parse_result,
                field_path="regime.probabilities",
                reason=f"missing_regime_feature:{regime_feature_name}",
            )
        proposed_regime_score = _regime_score(proposal)
        if abs(proposed_regime_score - regime_feature) > self._policy.max_regime_divergence:
            return self._reject_field(
                parse_result,
                field_path="regime.probabilities",
                reason="regime_feature_divergence_exceeded",
                proposed_value=format(proposed_regime_score, "f"),
                evidence_ids=tuple(
                    sorted(set(proposal.bull_evidence_ids) | set(proposal.bear_evidence_ids))
                ),
            )
        evidence_ids = tuple(sorted(_referenced_evidence(proposal)))
        missing_evidence = tuple(
            item for item in evidence_ids if item not in available_evidence_ids
        )
        if missing_evidence:
            reasons = tuple(f"unknown_evidence_id:{item}" for item in missing_evidence)
            dispositions = tuple(
                FieldDisposition(
                    field_path=f"evidence.{item}",
                    action=DispositionAction.REJECT,
                    reason=reason,
                    proposed_value=item,
                    evidence_ids=(item,),
                )
                for item, reason in zip(missing_evidence, reasons, strict=True)
            )
            return self._rejected(parse_result, reasons, dispositions)
        entry_reference_prices = tuple(
            item.reference_price for item in proposal.entry_predicates
        )
        if entry_reference_prices:
            # Phase 1 strategy contracts are long-only; short-side stop semantics
            # require a separate explicit contract rather than silently inverting.
            lowest_entry = min(entry_reference_prices)
            highest_entry = max(entry_reference_prices)
            for index, candidate in enumerate(proposal.stop_candidates):
                if candidate.price >= lowest_entry:
                    return self._reject_field(
                        parse_result,
                        field_path=f"stop_candidates[{index}].price",
                        reason="stop_not_below_entry_reference",
                        proposed_value=format(candidate.price, "f"),
                        evidence_ids=candidate.evidence_ids,
                    )
            for index, candidate in enumerate(proposal.target_candidates):
                if candidate.price <= highest_entry:
                    return self._reject_field(
                        parse_result,
                        field_path=f"target_candidates[{index}].price",
                        reason="target_not_above_entry_reference",
                        proposed_value=format(candidate.price, "f"),
                        evidence_ids=candidate.evidence_ids,
                    )
        dispositions = [
            FieldDisposition(
                field_path=f"evidence.{evidence_id}",
                action=DispositionAction.ACCEPT,
                reason="evidence_exists",
                proposed_value=evidence_id,
                resolved_value=evidence_id,
                evidence_ids=(evidence_id,),
            )
            for evidence_id in evidence_ids
        ]
        dispositions.extend(
            FieldDisposition(
                field_path=f"stop_candidates[{index}].price",
                action=DispositionAction.ACCEPT,
                reason=(
                    "stop_below_entry_reference"
                    if entry_reference_prices
                    else "stop_price_recorded_without_entry_reference"
                ),
                proposed_value=format(candidate.price, "f"),
                resolved_value=format(candidate.price, "f"),
                evidence_ids=candidate.evidence_ids,
            )
            for index, candidate in enumerate(proposal.stop_candidates)
        )
        dispositions.extend(
            FieldDisposition(
                field_path=f"target_candidates[{index}].price",
                action=DispositionAction.ACCEPT,
                reason=(
                    "target_above_entry_reference"
                    if entry_reference_prices
                    else "target_price_recorded_without_entry_reference"
                ),
                proposed_value=format(candidate.price, "f"),
                resolved_value=format(candidate.price, "f"),
                evidence_ids=candidate.evidence_ids,
            )
            for index, candidate in enumerate(proposal.target_candidates)
        )
        for index, predicate in enumerate(proposal.entry_predicates):
            if predicate.valid_until < evaluated_at:
                return self._reject_field(
                    parse_result,
                    field_path=f"entry_predicates[{index}].valid_until",
                    reason="entry_predicate_expired",
                    proposed_value=predicate.valid_until.isoformat(),
                    evidence_ids=predicate.evidence_ids,
                )
            try:
                feature_value = feature_values[predicate.feature_name]
            except KeyError:
                return self._reject_field(
                    parse_result,
                    field_path=f"entry_predicates[{index}].feature_name",
                    reason=f"unsupported_predicate_feature:{predicate.feature_name}",
                    proposed_value=predicate.feature_name,
                    evidence_ids=predicate.evidence_ids,
                )
            evaluated = _evaluate_predicate(
                feature_value,
                predicate.operator,
                predicate.comparison_value,
                predicate.upper_value,
            )
            dispositions.append(
                FieldDisposition(
                    field_path=f"entry_predicates[{index}].evaluation",
                    action=DispositionAction.RECALCULATE,
                    reason="predicate_evaluated_from_feature_snapshot",
                    proposed_value=None,
                    resolved_value="true" if evaluated else "false",
                    evidence_ids=predicate.evidence_ids,
                )
            )
        proposed_multiplier = proposal.risk_multiplier_candidate.value
        resolved_multiplier = min(
            proposed_multiplier, self._policy.max_risk_multiplier
        )
        multiplier_action = (
            DispositionAction.ACCEPT
            if proposed_multiplier == resolved_multiplier
            else DispositionAction.CLAMP
        )
        dispositions.append(
            FieldDisposition(
                field_path="risk_multiplier_candidate.value",
                action=multiplier_action,
                reason=(
                    "risk_multiplier_within_policy_bound"
                    if multiplier_action is DispositionAction.ACCEPT
                    else "risk_multiplier_clamped_to_policy_maximum"
                ),
                proposed_value=format(proposed_multiplier, "f"),
                resolved_value=format(resolved_multiplier, "f"),
                evidence_ids=proposal.risk_multiplier_candidate.evidence_ids,
            )
        )
        dispositions.append(
            FieldDisposition(
                field_path="regime.probabilities",
                action=DispositionAction.ACCEPT,
                reason="regime_feature_divergence_within_policy_bound",
                proposed_value=format(proposed_regime_score, "f"),
                resolved_value=format(proposed_regime_score, "f"),
                evidence_ids=tuple(
                    sorted(set(proposal.bull_evidence_ids) | set(proposal.bear_evidence_ids))
                ),
            )
        )
        dispositions.append(
            FieldDisposition(
                field_path="llm_score",
                action=DispositionAction.ACCEPT,
                reason="llm_quant_score_gap_within_policy_bound",
                proposed_value=format(proposal.llm_score, "f"),
                resolved_value=format(proposal.llm_score, "f"),
            )
        )
        dispositions.append(
            FieldDisposition(
                field_path="quant_score.total_score",
                action=DispositionAction.RECALCULATE,
                reason="deterministic_quant_score_is_authoritative",
                resolved_value=format(quant_score.total_score, "f"),
            )
        )
        return ProposalValidationResult(
            status=ProposalValidationStatus.ACCEPTED,
            validator_version=self._policy.validator_version,
            raw_response=parse_result.raw_response,
            proposal=proposal,
            proposal_id=proposal.proposal_id,
            dispositions=tuple(dispositions),
            reasons=(),
        )

    def _rejected(
        self,
        parse_result: ProposalParseResult,
        reasons: tuple[str, ...],
        dispositions: tuple[FieldDisposition, ...],
    ) -> ProposalValidationResult:
        proposal = parse_result.proposal
        return ProposalValidationResult(
            status=ProposalValidationStatus.REJECTED,
            validator_version=self._policy.validator_version,
            raw_response=parse_result.raw_response,
            proposal=proposal,
            proposal_id=None if proposal is None else proposal.proposal_id,
            dispositions=dispositions,
            reasons=reasons,
        )

    def _reject_field(
        self,
        parse_result: ProposalParseResult,
        *,
        field_path: str,
        reason: str,
        proposed_value: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> ProposalValidationResult:
        disposition = FieldDisposition(
            field_path=field_path,
            action=DispositionAction.REJECT,
            reason=reason,
            proposed_value=proposed_value,
            evidence_ids=evidence_ids,
        )
        return self._rejected(parse_result, (reason,), (disposition,))


def _referenced_evidence(proposal: TradePlanProposal) -> set[str]:
    evidence = set(proposal.bull_evidence_ids) | set(proposal.bear_evidence_ids)
    for component in proposal.score_breakdown:
        evidence.update(component.evidence_ids)
    evidence.update(proposal.risk_multiplier_candidate.evidence_ids)
    for candidate in (
        *proposal.entry_predicates,
        *proposal.stop_candidates,
        *proposal.target_candidates,
        *proposal.reentry_candidates,
        *proposal.pyramiding_candidates,
    ):
        evidence.update(candidate.evidence_ids)
    return evidence


def _find_duplicate_evidence_reference(
    proposal: TradePlanProposal,
) -> tuple[str, str] | None:
    groups = (
        ("risk_multiplier_candidate", (proposal.risk_multiplier_candidate,)),
        ("score_breakdown", proposal.score_breakdown),
        ("entry_predicates", proposal.entry_predicates),
        ("stop_candidates", proposal.stop_candidates),
        ("target_candidates", proposal.target_candidates),
        ("reentry_candidates", proposal.reentry_candidates),
        ("pyramiding_candidates", proposal.pyramiding_candidates),
    )
    for group_name, items in groups:
        for index, item in enumerate(items):
            seen: set[str] = set()
            for evidence_id in item.evidence_ids:
                if evidence_id in seen:
                    field_path = (
                        group_name
                        if group_name == "risk_multiplier_candidate"
                        else f"{group_name}[{index}]"
                    )
                    return field_path, evidence_id
                seen.add(evidence_id)
    return None


def _parse_error_field_path(reason: str) -> str:
    field_path, separator, _ = reason.partition(":")
    return field_path.strip() if separator and field_path.strip() else "proposal"


def _evaluate_predicate(
    feature_value: Decimal,
    operator: PredicateOperator,
    comparison_value: Decimal,
    upper_value: Decimal | None,
) -> bool:
    if operator is PredicateOperator.GREATER_THAN:
        return feature_value > comparison_value
    if operator is PredicateOperator.GREATER_THAN_OR_EQUAL:
        return feature_value >= comparison_value
    if operator is PredicateOperator.LESS_THAN:
        return feature_value < comparison_value
    if operator is PredicateOperator.LESS_THAN_OR_EQUAL:
        return feature_value <= comparison_value
    if operator is PredicateOperator.EQUAL:
        return feature_value == comparison_value
    if operator is PredicateOperator.BETWEEN_INCLUSIVE and upper_value is not None:
        return comparison_value <= feature_value <= upper_value
    raise ValueError("predicate operator shape is not evaluable")


def _regime_score(proposal: TradePlanProposal) -> Decimal:
    probabilities = proposal.regime.probabilities
    directional_expectation = (
        probabilities.strong_bull
        + probabilities.moderate_bull * Decimal("0.5")
        - probabilities.moderate_bear * Decimal("0.5")
        - probabilities.strong_bear
    )
    return (directional_expectation + Decimal("1")) * Decimal("50")
