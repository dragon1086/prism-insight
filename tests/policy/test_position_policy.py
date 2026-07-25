from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from prism_core.data.contracts import SecurityId
from prism_core.policy.position_policy import PositionPolicy, PositionPolicyViolation
from prism_core.policy.proposal_validator import ProposalValidationResult
from prism_core.portfolio.models import BookKind, StrategyPosition
from prism_core.strategies.contracts import Market, StrategyId
from tests.policy.test_proposal_validator import (
    EVALUATED_AT,
    EVIDENCE_IDS,
    feature_snapshot,
    parsed_result,
    quant_score,
    validator,
)


def _position(*, current_price: str = "110", stop_price: str = "95") -> StrategyPosition:
    return StrategyPosition(
        book_id="swing-shadow",
        book_kind=BookKind.VIRTUAL,
        strategy_id=StrategyId.SWING_V1,
        security_id=feature_snapshot().security_id,
        symbol="AAPL",
        sector="TECHNOLOGY",
        market=Market.US,
        currency="USD",
        base_currency="USD",
        fx_rate_to_base=Decimal("1"),
        quantity=10,
        average_entry_price=Decimal("100"),
        current_price=Decimal(current_price),
        stop_price=Decimal(stop_price),
    )


def _validated_pyramiding_candidate() -> ProposalValidationResult:
    snapshot = feature_snapshot()
    return validator().validate(
        parse_result=parsed_result(snapshot=snapshot),
        feature_snapshot=snapshot,
        quant_score=quant_score(snapshot),
        available_evidence_ids=EVIDENCE_IDS,
        evaluated_at=EVALUATED_AT,
    )


def test_stop_update_cannot_widen_long_position_risk() -> None:
    with pytest.raises(PositionPolicyViolation, match="stop_widening_prohibited"):
        PositionPolicy().validate_stop_update(
            position=_position(), proposed_stop=Decimal("94")
        )


def test_stop_update_may_tighten_without_crossing_current_price() -> None:
    assert (
        PositionPolicy().validate_stop_update(
            position=_position(), proposed_stop=Decimal("102")
        )
        == Decimal("102")
    )


def test_loss_position_cannot_be_averaged_down() -> None:
    with pytest.raises(PositionPolicyViolation, match="loss_position_averaging_down_prohibited"):
        PositionPolicy().validate_addition(
            position=_position(current_price="99"),
            validation_result=_validated_pyramiding_candidate(),
            candidate_field_path="pyramiding_candidates[0]",
        )


def test_pyramiding_requires_profitable_strategy_position_candidate() -> None:
    with pytest.raises(PositionPolicyViolation, match="validated_pyramiding_candidate_required"):
        PositionPolicy().validate_addition(
            position=_position(current_price="101"),
            validation_result=replace(
                _validated_pyramiding_candidate(), dispositions=()
            ),
            candidate_field_path="pyramiding_candidates[0]",
        )

    PositionPolicy().validate_addition(
        position=_position(current_price="101"),
        validation_result=_validated_pyramiding_candidate(),
        candidate_field_path="pyramiding_candidates[0]",
    )


def test_pyramiding_candidate_must_match_position_identity() -> None:
    with pytest.raises(PositionPolicyViolation, match="candidate_position_binding_mismatch"):
        PositionPolicy().validate_addition(
            position=replace(
                _position(current_price="101"),
                security_id=SecurityId(value=uuid4()),
            ),
            validation_result=_validated_pyramiding_candidate(),
            candidate_field_path="pyramiding_candidates[0]",
        )


def test_gapped_below_stop_position_remains_representable_and_cannot_add() -> None:
    position = _position(current_price="90", stop_price="95")

    with pytest.raises(PositionPolicyViolation, match="loss_position_averaging_down_prohibited"):
        PositionPolicy().validate_addition(
            position=position,
            validation_result=_validated_pyramiding_candidate(),
            candidate_field_path="pyramiding_candidates[0]",
        )
