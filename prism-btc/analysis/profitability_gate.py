"""Offline, fail-closed promotion evidence gate. Never activates a strategy.

PASS means the supplied evidence meets a predeclared review contract, not a
guarantee of future profit. Independent review must validate the evidence.
"""
from __future__ import annotations

import math

CHECKS = (
    "complete_market_data", "baseline_trade_parity", "event_time_funding",
    "mark_to_market_nav", "causal_execution", "shared_capital_limits",
    "fault_injection_passed", "exact_fill_linkage", "holdout_untouched",
    "all_trials_disclosed", "multiple_testing_adjusted_ci",
)
METRICS = (
    "oos_closed_trades", "forward_confirmed_trades", "oos_net_return_delta",
    "paired_return_delta_ci95_lower", "baseline_mtm_mdd", "candidate_mtm_mdd",
    "double_cost_expectancy", "positive_oos_folds", "total_oos_folds",
)


def evaluate(evidence):
    missing = [name for name in CHECKS if evidence.get(name) is not True]
    for name in METRICS:
        value = evidence.get(name)
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
            missing.append(name)
    if missing:
        return {"status": "INSUFFICIENT", "reasons": missing, "auto_activate": False}
    if evidence["oos_closed_trades"] < 60 or evidence["forward_confirmed_trades"] < 30:
        return {"status": "INSUFFICIENT", "reasons": ["sample_size"], "auto_activate": False}
    failures = []
    if evidence["oos_net_return_delta"] <= 0 or evidence["paired_return_delta_ci95_lower"] <= 0:
        failures.append("net_improvement_not_demonstrated")
    # MDD values are positive fractions (drawdown magnitude), not signed returns.
    base, candidate = evidence["baseline_mtm_mdd"], evidence["candidate_mtm_mdd"]
    if not 0 <= candidate <= base <= 1:
        failures.append("drawdown_worse_or_invalid")
    if evidence["double_cost_expectancy"] <= 0:
        failures.append("cost_stress_failed")
    total, positive = evidence["total_oos_folds"], evidence["positive_oos_folds"]
    if total < 3 or positive > total or positive/total < 2/3:
        failures.append("subperiod_instability")
    return {"status": "REJECT" if failures else "REVIEW_READY",
            "reasons": failures, "auto_activate": False}
