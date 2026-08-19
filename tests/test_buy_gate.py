from cores.buy_gate import effective_buy_regime, evaluate_production_buy_gate


def _scenario(**overrides):
    data = {
        "buy_score": 8,
        "min_score": 5,
        "target_price": 115.0,
        "stop_loss": 95.0,
        "risk_reward_ratio": 3.0,
        "market_condition": "moderate_bull",
    }
    data.update(overrides)
    return data


def test_production_gate_accepts_valid_legacy_scenario():
    result = evaluate_production_buy_gate(
        _scenario(), current_price=100.0, market_regime="moderate_bull"
    )
    assert result["allowed"]
    assert result["effective_regime"] == "moderate_bull"


def test_missing_computed_regime_is_fail_closed():
    result = evaluate_production_buy_gate(
        _scenario(), current_price=100.0, market_regime=None
    )
    assert not result["allowed"]
    assert "missing_computed_regime" in {item["code"] for item in result["hard_findings"]}


def test_distribution_days_step_down_the_entry_rule():
    regime, caution = effective_buy_regime("strong_bull", 6)
    assert (regime, caution) == ("moderate_bull", True)

    result = evaluate_production_buy_gate(
        _scenario(buy_score=3, min_score=3),
        current_price=100.0,
        market_regime="strong_bull",
        distribution_days=6,
    )
    assert not result["allowed"]
    assert result["effective_regime"] == "moderate_bull"
    assert "score_below_floor" in {item["code"] for item in result["hard_findings"]}


def test_rr_is_recomputed_and_reported_value_cannot_override_it():
    result = evaluate_production_buy_gate(
        _scenario(target_price=101.0, stop_loss=99.0, risk_reward_ratio=5.0),
        current_price=100.0,
        market_regime="moderate_bear",
    )
    codes = {item["code"] for item in result["hard_findings"]}
    assert {"rr_below_floor", "rr_arithmetic_mismatch"} <= codes


def test_trend_facts_are_final_blockers():
    result = evaluate_production_buy_gate(
        _scenario(),
        current_price=100.0,
        market_regime="moderate_bull",
        trend_facts="- T1_hit(종가<MA50): True / T2_hit(MA20 하락): False",
    )
    assert not result["allowed"]
    assert "individual_trend_t1" in {item["code"] for item in result["hard_findings"]}


def test_optional_new_fields_are_checked_when_present():
    result = evaluate_production_buy_gate(
        _scenario(
            fundamental_check={"all_passed": False},
            momentum_signal_count=1,
            additional_confirmation_count=0,
        ),
        current_price=100.0,
        market_regime="moderate_bear",
    )
    codes = {item["code"] for item in result["hard_findings"]}
    assert {"fundamental_gate_failed", "momentum_count_below_floor", "confirmation_count_below_floor"} <= codes
