"""Slot weight of one strategy-ledger trade (KR/US shared).

Cumulative simulator returns sum per-trade ``profit_rate`` over a 10-slot book.
A rebound pilot buys only ``position_fraction`` (0.5) of a slot and an owned
adaptive campaign deploys its confirmed strategy allocation (0.30-1.0), so
their returns must count by that fraction instead of as a full slot.
Unknown or malformed metadata keeps the legacy full-slot weight.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any


def _fraction(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or not 0 < number <= 1:
        return None
    return number


def slot_fraction(scenario: Any) -> float:
    """Return the fraction of one slot a trade actually occupied (default 1.0)."""
    if isinstance(scenario, (str, bytes)):
        try:
            scenario = json.loads(scenario or "{}")
        except (ValueError, TypeError):
            return 1.0
    if not isinstance(scenario, dict):
        return 1.0
    owned = scenario.get("_oneil_execution")
    if isinstance(owned, dict):
        allocation = _fraction(owned.get("strategy_allocation"))
        if allocation is not None:
            return allocation
    policy = scenario.get("regime_entry_policy")
    if isinstance(policy, dict):
        fraction = _fraction(policy.get("position_fraction"))
        if fraction is not None:
            return fraction
    return 1.0


def weighted_profit_rate(profit_rate: Any, scenario: Any) -> float:
    """Per-trade return expressed in full-slot units."""
    try:
        rate = float(profit_rate or 0)
    except (TypeError, ValueError):
        return 0.0
    return rate * slot_fraction(scenario)


def weighted_profit_sum(rows: Iterable[tuple[Any, Any]]) -> float:
    """Sum ``(profit_rate, scenario)`` pairs in full-slot units."""
    return sum(weighted_profit_rate(rate, scenario) for rate, scenario in rows)
