"""Fail-closed parsing boundary for strict TradePlanProposal responses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import ValidationError

from prism_core.strategies.contracts import FeatureSnapshot
from prism_core.llm.trade_plan import TradePlanProposal


class ProposalParseStatus(str, Enum):
    PARSED = "PARSED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ProposalParseResult:
    """Audit envelope that always preserves the exact model response."""

    status: ProposalParseStatus
    raw_response: str
    proposal: TradePlanProposal | None
    errors: tuple[str, ...]


class ProposalService:
    """Parse and bind an LLM response to one immutable feature snapshot.

    This service has no model transport, policy, sizing, execution, or persistence
    capability. A parsed proposal remains a candidate for later deterministic
    validation.
    """

    def parse(
        self,
        *,
        raw_response: str,
        feature_snapshot: FeatureSnapshot,
        available_evidence_ids: frozenset[str] = frozenset(),
    ) -> ProposalParseResult:
        if not isinstance(raw_response, str):
            raise TypeError("raw_response must be a string")
        if not isinstance(feature_snapshot, FeatureSnapshot):
            raise TypeError("feature_snapshot must be a FeatureSnapshot")
        if not isinstance(available_evidence_ids, frozenset) or any(
            not isinstance(item, str) or not item.strip()
            for item in available_evidence_ids
        ):
            raise TypeError("available_evidence_ids must be a frozenset of non-empty strings")

        try:
            proposal = TradePlanProposal.model_validate_json(raw_response)
        except ValidationError as exc:
            return self._rejected(
                raw_response,
                tuple(
                    f"{'.'.join(str(part) for part in error['loc'])}: "
                    f"{error['type']}: {error['msg']}"
                    for error in exc.errors(include_input=False, include_url=False)
                ),
            )

        errors = self._binding_errors(
            proposal=proposal,
            feature_snapshot=feature_snapshot,
            available_evidence_ids=available_evidence_ids,
        )
        if errors:
            return self._rejected(raw_response, errors)
        return ProposalParseResult(
            status=ProposalParseStatus.PARSED,
            raw_response=raw_response,
            proposal=proposal,
            errors=(),
        )

    @staticmethod
    def _binding_errors(
        *,
        proposal: TradePlanProposal,
        feature_snapshot: FeatureSnapshot,
        available_evidence_ids: frozenset[str],
    ) -> tuple[str, ...]:
        provenance = proposal.feature_provenance
        expected = {
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
        errors = [
            f"feature_binding_mismatch:{field}"
            for field, (actual, wanted) in expected.items()
            if actual != wanted
        ]

        feature_names = {item.name for item in feature_snapshot.values}
        unsupported_features = sorted(
            {
                predicate.feature_name
                for predicate in proposal.entry_predicates
                if predicate.feature_name not in feature_names
            }
        )
        errors.extend(
            f"unsupported_predicate_feature:{name}" for name in unsupported_features
        )

        referenced_evidence = set(proposal.bull_evidence_ids) | set(
            proposal.bear_evidence_ids
        )
        for component in proposal.score_breakdown:
            referenced_evidence.update(component.evidence_ids)
        referenced_evidence.update(proposal.risk_multiplier_candidate.evidence_ids)
        for candidate in (
            *proposal.entry_predicates,
            *proposal.stop_candidates,
            *proposal.target_candidates,
            *proposal.reentry_candidates,
            *proposal.pyramiding_candidates,
        ):
            referenced_evidence.update(candidate.evidence_ids)
        missing_evidence = sorted(referenced_evidence - available_evidence_ids)
        errors.extend(f"unknown_evidence_id:{item}" for item in missing_evidence)
        return tuple(errors)

    @staticmethod
    def _rejected(raw_response: str, errors: tuple[str, ...]) -> ProposalParseResult:
        return ProposalParseResult(
            status=ProposalParseStatus.REJECTED,
            raw_response=raw_response,
            proposal=None,
            errors=errors or ("proposal_rejected",),
        )
