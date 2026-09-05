from analysis.profitability_gate import CHECKS, evaluate


def evidence():
    return {**dict.fromkeys(CHECKS, True), "oos_closed_trades": 100,
            "forward_confirmed_trades": 30, "oos_net_return_delta": .05,
            "paired_return_delta_ci95_lower": .01, "baseline_mtm_mdd": .2,
            "candidate_mtm_mdd": .18, "double_cost_expectancy": .001,
            "positive_oos_folds": 3, "total_oos_folds": 3}


def test_missing_evidence_never_promotes():
    assert evaluate({})["status"] == "INSUFFICIENT"


def test_more_profit_from_more_drawdown_does_not_pass():
    assert evaluate(evidence() | {"candidate_mtm_mdd": .3})["status"] == "REJECT"


def test_no_significant_improvement_does_not_pass():
    assert evaluate(evidence() | {"paired_return_delta_ci95_lower": -.001})["status"] == "REJECT"


def test_complete_valid_evidence_only_invites_review_not_activation():
    result = evaluate(evidence())
    assert result == {"status": "REVIEW_READY", "reasons": [], "auto_activate": False}
