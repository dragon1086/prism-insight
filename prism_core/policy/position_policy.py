"""Deterministic position-level safety policy for long-only strategy books."""

from __future__ import annotations

from decimal import Decimal

from prism_core.policy.dispositions import DispositionAction
from prism_core.policy.proposal_validator import (
    ProposalValidationResult,
    ProposalValidationStatus,
)
from prism_core.portfolio.models import StrategyPosition


class PositionPolicyViolation(ValueError):
    """A requested position transition violates a deterministic safety rule."""


class PositionPolicy:
    """Validate stop changes and additions without calculating quantity or orders."""

    def validate_stop_update(
        self, *, position: StrategyPosition, proposed_stop: Decimal
    ) -> Decimal:
        if not isinstance(position, StrategyPosition):
            raise TypeError("position must be a StrategyPosition")
        if (
            not isinstance(proposed_stop, Decimal)
            or not proposed_stop.is_finite()
            or proposed_stop <= 0
        ):
            raise ValueError("proposed_stop must be a positive finite Decimal")
        if proposed_stop < position.stop_price:
            raise PositionPolicyViolation("stop_widening_prohibited")
        if proposed_stop >= position.current_price:
            raise PositionPolicyViolation("stop_must_remain_below_current_price")
        return proposed_stop

    def validate_addition(
        self,
        *,
        position: StrategyPosition,
        validation_result: ProposalValidationResult,
        candidate_field_path: str,
    ) -> None:
        if not isinstance(position, StrategyPosition):
            raise TypeError("position must be a StrategyPosition")
        if position.current_price <= position.average_entry_price:
            raise PositionPolicyViolation("loss_position_averaging_down_prohibited")
        if not isinstance(validation_result, ProposalValidationResult):
            raise TypeError("validation_result must be a ProposalValidationResult")
        if validation_result.status is not ProposalValidationStatus.ACCEPTED:
            raise PositionPolicyViolation("validated_pyramiding_candidate_required")
        proposal = validation_result.proposal
        if (
            proposal is None
            or proposal.strategy_id is not position.strategy_id
            or proposal.security_id != position.security_id
            or proposal.market is not position.market
        ):
            raise PositionPolicyViolation("candidate_position_binding_mismatch")
        if (
            not isinstance(candidate_field_path, str)
            or not candidate_field_path.startswith("pyramiding_candidates[")
            or not candidate_field_path.endswith("]")
        ):
            raise ValueError("candidate_field_path must identify a pyramiding candidate")
        matches = tuple(
            item
            for item in validation_result.dispositions
            if item.field_path == candidate_field_path
            and item.action is DispositionAction.ACCEPT
            and item.resolved_value == "validated"
        )
        if len(matches) != 1:
            raise PositionPolicyViolation("validated_pyramiding_candidate_required")
