from decimal import Decimal
from uuid import uuid4

import pytest

from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.policy.proposal_validator import (
    ProposalValidationResult,
    ProposalValidationStatus,
)
from prism_core.policy import (
    DeterministicSizingPolicy,
    SizingConstraints,
    SizingPolicyViolation,
)


def _validation_result(
    *, multiplier: str = "0.25", resolved_stop: str = "90"
) -> ProposalValidationResult:
    return ProposalValidationResult(
        status=ProposalValidationStatus.ACCEPTED,
        validator_version="validator-v1",
        raw_response='{"untrusted": "raw proposal is not sizing authority"}',
        proposal=None,
        proposal_id=uuid4(),
        dispositions=(
            FieldDisposition(
                field_path="stop_candidates[0].price",
                action=DispositionAction.ACCEPT,
                reason="validated_stop",
                proposed_value="80",
                resolved_value=resolved_stop,
            ),
            FieldDisposition(
                field_path="risk_multiplier_candidate.value",
                action=DispositionAction.CLAMP,
                reason="validated_multiplier",
                proposed_value="1",
                resolved_value=multiplier,
            ),
        ),
        reasons=(),
    )


def _constraints(**overrides: object) -> SizingConstraints:
    values = {
        "account_risk_budget": Decimal("1000"),
        "max_risk_multiplier": Decimal("1"),
        "fx_rate_to_base": Decimal("1"),
        "available_cash": Decimal("10000"),
        "symbol_exposure_headroom": Decimal("10000"),
        "sector_exposure_headroom": Decimal("10000"),
        "market_exposure_headroom": Decimal("10000"),
        "currency_exposure_headroom": Decimal("10000"),
        "gross_exposure_headroom": Decimal("10000"),
        "open_order_exposure_headroom": Decimal("10000"),
        "max_liquidity_notional": Decimal("10000"),
        "lot_size": 1,
    }
    values.update(overrides)
    return SizingConstraints(**values)


def test_quantity_uses_only_resolved_stop_and_multiplier() -> None:
    result = DeterministicSizingPolicy().calculate(
        validation_result=_validation_result(multiplier="0.25"),
        entry_price=Decimal("100"),
        stop_field_path="stop_candidates[0].price",
        constraints=_constraints(),
    )

    # 1000 / (100 - resolved stop 90) * resolved multiplier 0.25
    assert result.risk_limited_quantity == 25
    assert result.final_quantity == 25
    assert result.resolved_stop == Decimal("90")
    assert result.resolved_risk_multiplier == Decimal("0.25")


def test_quantity_applies_cash_position_liquidity_and_lot_caps() -> None:
    result = DeterministicSizingPolicy().calculate(
        validation_result=_validation_result(multiplier="1"),
        entry_price=Decimal("100"),
        stop_field_path="stop_candidates[0].price",
        constraints=_constraints(
            available_cash=Decimal("4599"),
            symbol_exposure_headroom=Decimal("8000"),
            sector_exposure_headroom=Decimal("9000"),
            max_liquidity_notional=Decimal("9000"),
            lot_size=10,
        ),
    )

    assert result.risk_limited_quantity == 100
    assert result.final_quantity == 40


def test_rejected_validation_result_cannot_reach_sizing() -> None:
    accepted = _validation_result()
    rejected = ProposalValidationResult(
        status=ProposalValidationStatus.REJECTED,
        validator_version=accepted.validator_version,
        raw_response=accepted.raw_response,
        proposal=accepted.proposal,
        proposal_id=accepted.proposal_id,
        dispositions=accepted.dispositions,
        reasons=("rejected",),
    )

    with pytest.raises(SizingPolicyViolation, match="accepted_validation_required"):
        DeterministicSizingPolicy().calculate(
            validation_result=rejected,
            entry_price=Decimal("100"),
            stop_field_path="stop_candidates[0].price",
            constraints=_constraints(),
        )


def test_resolved_multiplier_cannot_raise_risk() -> None:
    with pytest.raises(SizingPolicyViolation, match="resolved_risk_multiplier_out_of_range"):
        DeterministicSizingPolicy().calculate(
            validation_result=_validation_result(multiplier="1.01"),
            entry_price=Decimal("100"),
            stop_field_path="stop_candidates[0].price",
            constraints=_constraints(),
        )


def test_resolved_multiplier_cannot_exceed_injected_policy_maximum() -> None:
    with pytest.raises(SizingPolicyViolation, match="configured_risk_multiplier_exceeded"):
        DeterministicSizingPolicy().calculate(
            validation_result=_validation_result(multiplier="0.4"),
            entry_price=Decimal("100"),
            stop_field_path="stop_candidates[0].price",
            constraints=_constraints(max_risk_multiplier=Decimal("0.3")),
        )


def test_stop_disposition_must_use_stop_candidate_namespace() -> None:
    with pytest.raises(ValueError, match="stop_field_path must identify a stop candidate"):
        DeterministicSizingPolicy().calculate(
            validation_result=_validation_result(),
            entry_price=Decimal("100"),
            stop_field_path="risk_multiplier_candidate.value",
            constraints=_constraints(),
        )


@pytest.mark.parametrize(
    ("constraint_name", "expected_label"),
    (
        ("symbol_exposure_headroom", "symbol_exposure"),
        ("sector_exposure_headroom", "sector_exposure"),
        ("market_exposure_headroom", "market_exposure"),
        ("currency_exposure_headroom", "currency_exposure"),
        ("gross_exposure_headroom", "gross_exposure"),
        ("open_order_exposure_headroom", "open_order_exposure"),
        ("max_liquidity_notional", "liquidity_notional"),
    ),
)
def test_each_consolidated_headroom_can_bind_quantity(
    constraint_name: str, expected_label: str
) -> None:
    result = DeterministicSizingPolicy().calculate(
        validation_result=_validation_result(multiplier="1"),
        entry_price=Decimal("100"),
        stop_field_path="stop_candidates[0].price",
        constraints=_constraints(**{constraint_name: Decimal("5500")}),
    )

    assert result.final_quantity == 55
    assert result.limiting_constraint == expected_label


def test_non_base_currency_prices_are_converted_before_risk_and_notional_caps() -> None:
    result = DeterministicSizingPolicy().calculate(
        validation_result=_validation_result(multiplier="1", resolved_stop="90000"),
        entry_price=Decimal("100000"),
        stop_field_path="stop_candidates[0].price",
        constraints=_constraints(fx_rate_to_base=Decimal("0.001")),
    )

    # Base-currency risk/unit=(100000-90000)*0.001=10 and notional/unit=100.
    assert result.risk_per_unit == Decimal("10")
    assert result.final_quantity == 100
