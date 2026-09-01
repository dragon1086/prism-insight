from __future__ import annotations

import pytest

from prism_core.micro_split import (
    DEFAULT_POLICY,
    MicroSplitPolicy,
    advance_target,
    project_execution_on_advance,
)


def test_terminology_and_regime_caps() -> None:
    assert DEFAULT_POLICY.base_steps_pct == (10, 30, 60, 100)
    assert DEFAULT_POLICY.max_target_pct("moderate_bull") == 100
    assert DEFAULT_POLICY.max_target_pct("sideways") == 100
    assert DEFAULT_POLICY.max_target_pct("strong_bull") == 300
    assert DEFAULT_POLICY.max_target_pct("parabolic") == 300


def test_standard_regime_rejects_pyramiding_target() -> None:
    with pytest.raises(ValueError, match="exceeds regime cap"):
        advance_target(DEFAULT_POLICY, 100, 130, regime="moderate_bull")


def test_target_must_advance_and_match_versioned_steps() -> None:
    transition = advance_target(DEFAULT_POLICY, 30, 60, regime="sideways")
    assert transition.previous_target_pct == 30
    assert transition.target_pct == 60
    assert transition.target_slot_units == 0.6
    assert transition.is_pyramid is False

    with pytest.raises(ValueError, match="must increase"):
        advance_target(DEFAULT_POLICY, 60, 60, regime="sideways")
    with pytest.raises(ValueError, match="not a policy step"):
        advance_target(DEFAULT_POLICY, 30, 55, regime="sideways")


def test_strong_bull_allows_micro_split_pyramiding_to_300() -> None:
    transition = advance_target(DEFAULT_POLICY, 260, 300, regime="strong_bull")
    assert transition.is_pyramid is True
    assert transition.target_slot_units == 3.0


def test_one_share_executes_when_target_notional_crosses_price() -> None:
    projection = project_execution_on_advance(
        unit_amount=1_000_000,
        previous_target_pct=30,
        target_pct=60,
        execution_price=600_000,
        confirmed_strategy_quantity=0,
    )

    assert projection.target_notional == 600_000
    assert projection.desired_quantity == 1
    assert projection.buy_delta_quantity == 1
    assert projection.projected_notional == 600_000


def test_target_can_advance_without_an_additional_real_order() -> None:
    projection = project_execution_on_advance(
        unit_amount=1_000_000,
        previous_target_pct=60,
        target_pct=100,
        execution_price=600_000,
        confirmed_strategy_quantity=1,
    )

    assert projection.desired_quantity == 1
    assert projection.buy_delta_quantity == 0


def test_high_price_stock_waits_for_a_later_target_threshold() -> None:
    early = project_execution_on_advance(
        unit_amount=1_000_000,
        previous_target_pct=10,
        target_pct=30,
        execution_price=1_500_000,
        confirmed_strategy_quantity=0,
    )
    pyramid = project_execution_on_advance(
        unit_amount=1_000_000,
        previous_target_pct=100,
        target_pct=160,
        execution_price=1_500_000,
        confirmed_strategy_quantity=0,
    )

    assert early.buy_delta_quantity == 0
    assert pyramid.buy_delta_quantity == 1


def test_price_change_without_stage_advance_cannot_create_a_buy() -> None:
    with pytest.raises(ValueError, match="must increase"):
        project_execution_on_advance(
            unit_amount=1_000_000,
            previous_target_pct=60,
            target_pct=60,
            execution_price=400_000,
            confirmed_strategy_quantity=1,
        )


def test_projection_never_rounds_above_target_notional() -> None:
    projection = project_execution_on_advance(
        unit_amount=1_000_000,
        previous_target_pct=100,
        target_pct=130,
        execution_price=600_000,
        confirmed_strategy_quantity=1,
    )

    assert projection.desired_quantity == 2
    assert projection.projected_notional <= projection.target_notional


def test_policy_rejects_invalid_step_order_and_caps() -> None:
    with pytest.raises(ValueError):
        MicroSplitPolicy(
            policy_version="bad",
            base_steps_pct=(10, 60, 30, 100),
            pyramid_steps_pct=(130, 200, 300),
        )
    with pytest.raises(ValueError):
        MicroSplitPolicy(
            policy_version="bad",
            base_steps_pct=(10, 30, 60, 100),
            pyramid_steps_pct=(130, 200, 310),
        )
