"""Half-slot pilots and owned campaigns count by the slot fraction they occupied."""
import json
from decimal import Decimal

import pytest

from prism_core.oneil_routing import strategy_entry_from_orders
from prism_core.slot_weight import (
    slot_fraction,
    weighted_profit_rate,
    weighted_profit_sum,
)

PILOT = {"regime_entry_policy": {"mode": "rebound_pilot", "position_fraction": 0.5}}


@pytest.mark.parametrize("scenario, expected", [
    (None, 1.0), ("", 1.0), ("not json", 1.0), ("[]", 1.0), ({}, 1.0),
    (PILOT, 0.5), (json.dumps(PILOT), 0.5),
    ({"regime_entry_policy": {"mode": "normal", "position_fraction": 1}}, 1.0),
    # Malformed fractions keep the legacy full-slot weight.
    ({"regime_entry_policy": {"position_fraction": True}}, 1.0),
    ({"regime_entry_policy": {"position_fraction": 0}}, 1.0),
    ({"regime_entry_policy": {"position_fraction": 2}}, 1.0),
    ({"regime_entry_policy": {"position_fraction": "nan"}}, 1.0),
    ({"_oneil_execution": {"campaign_id": "c", "strategy_allocation": "0.8"}}, 0.8),
    ({"_oneil_execution": {"campaign_id": "c"}}, 1.0),
])
def test_slot_fraction(scenario, expected):
    assert slot_fraction(scenario) == expected


def test_half_slot_pilot_counts_half_in_cumulative_sum():
    rows = [(10.0, json.dumps(PILOT)), (-4.0, None), (6.0, "{}")]
    assert weighted_profit_sum(rows) == pytest.approx(5.0 - 4.0 + 6.0)
    assert weighted_profit_rate(None, PILOT) == 0.0


def order(target, price, at, *, side="BUY", basis=None):
    data = {"intent": {"side": side, "limit_price": str(price)}, "target_allocation": str(target), "reserved_at": at}
    if basis:
        data["cancellation_basis"] = basis
    return data


def test_owned_campaign_entry_weights_each_add_by_its_allocation_step():
    orders = [
        order("0.8", 110, "2026-10-01T15:00:00+00:00"),
        order("0.5", 100, "2026-10-01T14:00:00+00:00"),
        order("0", 120, "2026-10-01T16:00:00+00:00", side="SELL"),
    ]
    entry, allocation = strategy_entry_from_orders(orders)
    assert allocation == Decimal("0.8")
    # 0.5 slot at 100 + 0.3 slot at 110 -> cost-weighted entry.
    assert entry == Decimal("0.8") / (Decimal("0.5") / 100 + Decimal("0.3") / 110)
    assert 103 < entry < 104


def test_owned_campaign_ignores_steps_never_submitted():
    orders = [
        order("0.5", 100, "2026-10-01T14:00:00+00:00"),
        order("1.0", 104, "2026-10-01T15:00:00+00:00", basis="PREFLIGHT_NOT_SUBMITTED"),
        order("1.0", 104, "2026-10-01T15:05:00+00:00", basis="NEVER_SUBMITTED_LOCAL_CAS"),
    ]
    assert strategy_entry_from_orders(orders) == (Decimal(100), Decimal("0.5"))
    assert strategy_entry_from_orders([]) is None


def test_dashboards_weight_cumulative_curve(monkeypatch):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    history = [
        {"sell_date": "2026-10-01 10:00:00", "profit_rate": 10.0, "scenario": PILOT},
        {"sell_date": "2026-10-02 10:00:00", "profit_rate": 4.0, "scenario": {}},
    ]
    market = [{"date": "2026-10-01"}, {"date": "2026-10-02"}]
    for name in ("generate_dashboard_json.py", "generate_us_dashboard_json.py"):
        spec = importlib.util.spec_from_file_location(name[:-3], root / "examples" / name)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError as error:  # optional market-data dependencies
            pytest.skip(f"{name}: {error}")
        cls = next(v for k, v in vars(module).items() if isinstance(v, type) and hasattr(v, "calculate_cumulative_realized_profit"))
        generator = cls.__new__(cls)
        generator.US_SEASON1_START_DATE = "2026-01-01"
        curve = generator.calculate_cumulative_realized_profit(history, market)
        assert [round(p["cumulative_realized_profit"], 6) for p in curve] == [5.0, 9.0]
        assert curve[-1]["prism_simulator_return"] == pytest.approx(0.9)
