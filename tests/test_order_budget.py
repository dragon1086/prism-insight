import pytest

from prism_core.order_budget import order_budget_evidence, resolve_order_budget, whole_share_quantity


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), False, 'bad'])
def test_explicit_invalid_budget_never_uses_default(value):
    assert resolve_order_budget(value, 1_000_000) == 0
    assert whole_share_quantity(value, 300_000) == 0


def test_half_budget_is_cap_not_half_filled():
    assert resolve_order_budget(None, 1_000_000) == 1_000_000
    assert whole_share_quantity(500_000, 300_000) == 1
    evidence = order_budget_evidence(500_000, 300_000)
    assert evidence['proposed_order_notional'] == 300_000
    assert evidence['unallocated_order_budget'] == 200_000
    assert evidence['budget_evidence_basis'] == 'proposed_order_not_fill'
    assert whole_share_quantity(500_000, 600_000) == 0


def test_decimal_price_boundary_does_not_round_quantity_up():
    assert whole_share_quantity(0.3, 0.1) == 3
    assert whole_share_quantity(0.299999, 0.1) == 2
