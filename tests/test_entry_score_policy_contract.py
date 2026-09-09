"""Front/final score-policy regression cases; no network or trading calls."""
import pytest

from cores.buy_gate import evaluate_production_buy_gate
from cores.regime_policy import configured_entry_amount, is_rebound_pilot_entry


def gate(score=7, **kwargs):
    scenario = dict(buy_score=score, min_score=5, decision="Enter",
                    target_price=115, stop_loss=95, risk_reward_ratio=3)
    scenario.update(kwargs.pop("scenario", {}))
    args = dict(current_price=100, market_regime="sideways", market_pulse="UPTREND")
    args.update(kwargs)
    return evaluate_production_buy_gate(scenario, **args)


def test_seven_uptrend_passes_without_pilot(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    assert gate()["allowed"]


@pytest.mark.parametrize("pulse", [None, "", "CORRECTION", "UNDER_PRESSURE"])
def test_seven_without_current_uptrend_blocked(pulse):
    assert not gate(market_pulse=pulse)["allowed"]


def test_six_requires_explicit_runtime_budget():
    assert not gate(6)["allowed"]
    assert gate(6, pilot_budget_available=True)["allowed"]
    assert not gate(6, pilot_budget_available=True, is_add=True)["allowed"]


@pytest.mark.parametrize("minimum", [6.1, 7, 8])
def test_pilot_never_lowers_original_llm_minimum(minimum):
    assert not gate(6, pilot_budget_available=True, scenario={"min_score": minimum})["allowed"]


@pytest.mark.parametrize("score", [6.9, "6.9", True, float("nan"), float("inf")])
def test_pilot_score_is_exact_not_integer_truncated(score):
    assert not is_rebound_pilot_entry(score, 5, "sideways", "UPTREND", "Enter")


@pytest.mark.parametrize("regime,score", [("moderate_bull", 7), ("sideways", 7), ("moderate_bear", 8)])
def test_distribution_downgrade_keeps_defensive_floor(regime, score):
    assert not gate(score, market_regime=regime, distribution_days=6,
                    pilot_budget_available=True)["allowed"]


def test_distribution_caution_disables_pilot():
    assert not gate(6, distribution_days=6, pilot_budget_available=True)["allowed"]


@pytest.mark.parametrize("scenario,facts,code", [
    ({"target_price": 101, "risk_reward_ratio": .2}, "", "rr_below_floor"),
    ({"fundamental_check": {"all_passed": False}}, "", "fundamental_gate_failed"),
    ({}, "T1_hit: True", "individual_trend_t1"),
    ({"stop_loss": 90, "risk_reward_ratio": 1.5}, "", "stop_exceeds_regime_limit"),
])
def test_pilot_preserves_independent_gates(scenario, facts, code):
    result = gate(6, pilot_budget_available=True, scenario=scenario, trend_facts=facts)
    assert code in {f["code"] for f in result["hard_findings"]}


def test_scenario_cannot_authorize_pulse_or_budget():
    scenario = {"market_pulse": "UPTREND", "pilot_budget_available": True,
                "regime_entry_policy": {"position_fraction": .5, "pilot_budget_available": True}}
    assert not gate(7, market_pulse=None, scenario=scenario)["allowed"]
    assert not gate(6, scenario=scenario)["allowed"]


def test_strict_flag_off_keeps_legacy_normal_floor(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "false")
    assert gate(5, market_pulse=None)["allowed"]
    assert not gate(4, market_pulse=None)["allowed"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, True, "bad"])
def test_bad_configured_amount_cannot_authorize_budget(value):
    assert configured_entry_amount({"buy_amount_usd": value}, "us", .5) is None


@pytest.mark.parametrize("fraction", [float("nan"), float("inf"), -1, 0, True, "bad"])
def test_bad_fraction_cannot_authorize_budget(fraction):
    assert configured_entry_amount({"buy_amount_usd": 100}, "us", fraction) is None


def test_budget_rounds_down_never_above_fraction():
    assert configured_entry_amount({"buy_amount_usd": "100.03"}, "us", .5) == 50.01
    assert configured_entry_amount({"buy_amount_krw": 101}, "kr", .5) == 50
    assert configured_entry_amount({"buy_amount_krw": 1}, "kr", .5) is None
