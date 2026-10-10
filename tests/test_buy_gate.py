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


def test_stop_volatility_noise_floor_is_shadow_only(monkeypatch):
    monkeypatch.setattr("cores.shadow_lifecycle.feature_mode", lambda feature: "shadow")
    result = evaluate_production_buy_gate(
        _scenario(target_price=110.0, stop_loss=98.0, risk_reward_ratio=5.0),
        current_price=100.0,
        market_regime="moderate_bull",
        trend_facts="- Volatility: ATR20=8.0% / ADR20=10.0%",
    )
    assert result["allowed"]
    assert any(
        item["code"] == "stop_below_volatility_noise_floor"
        for item in result["shadow_findings"]
    )


def _codes(result):
    return {item["code"] for item in result["hard_findings"]}


def test_quote_drift_is_not_reported_as_arithmetic_mismatch():
    # Production case 2026-09-09 (지엔씨에너지): scenario written at 49,350, fresh quote 48,850.
    scenario = _scenario(target_price=56270.0, stop_loss=46000.0, risk_reward_ratio=2.1,
                         expected_return_pct=14.0, expected_loss_pct=6.8, _analysis_entry_price=49350.0)
    result = evaluate_production_buy_gate(scenario, current_price=48850.0, market_regime="moderate_bull")
    codes = _codes(result)
    assert not codes & {"rr_arithmetic_mismatch", "risk_arithmetic_mismatch"}
    assert round(result["recomputed_rr"], 2) == 2.6  # floors still judge the fresh price


def test_wrong_arithmetic_at_its_own_price_still_blocks():
    # Production case 2026-09-10 (리노공업): entry basis equals the quote, reported numbers do not.
    scenario = _scenario(target_price=103500.0, stop_loss=67500.0, risk_reward_ratio=11.9,
                         expected_return_pct=47.23, expected_loss_pct=3.98, _analysis_entry_price=71300.0)
    result = evaluate_production_buy_gate(scenario, current_price=71300.0, market_regime="moderate_bull")
    assert {"rr_arithmetic_mismatch", "risk_arithmetic_mismatch"} <= _codes(result)


def test_without_or_with_invalid_basis_the_fresh_price_is_used():
    scenario = _scenario(target_price=7900.0, stop_loss=6000.0, risk_reward_ratio=4.4)
    result = evaluate_production_buy_gate(scenario, current_price=6380.0, market_regime="moderate_bull")
    assert "rr_arithmetic_mismatch" in _codes(result)
    scenario["_analysis_entry_price"] = 5000.0  # below the stop: not a usable basis
    result = evaluate_production_buy_gate(scenario, current_price=6380.0, market_regime="moderate_bull")
    assert "rr_arithmetic_mismatch" in _codes(result)


def test_drift_that_breaks_the_floor_still_blocks():
    scenario = _scenario(target_price=110.0, stop_loss=95.0, risk_reward_ratio=2.0,
                         expected_return_pct=10.0, expected_loss_pct=5.0, _analysis_entry_price=100.0)
    result = evaluate_production_buy_gate(scenario, current_price=104.0, market_regime="moderate_bear")
    codes = _codes(result)
    assert "rr_below_floor" in codes
    assert not codes & {"rr_arithmetic_mismatch", "risk_arithmetic_mismatch"}


def test_reported_entry_price_is_the_basis_before_the_contract_refresh():
    scenario = _scenario(target_price=56270.0, stop_loss=46000.0, risk_reward_ratio=2.1,
                         expected_return_pct=14.0, expected_loss_pct=6.8, entry_price=49350.0)
    result = evaluate_production_buy_gate(scenario, current_price=48850.0, market_regime="moderate_bull")
    assert not _codes(result) & {"rr_arithmetic_mismatch", "risk_arithmetic_mismatch"}


def test_score_exemption_skips_only_the_score_floor_for_a_rule_approved_reentry():
    no_score = _scenario(buy_score=None, min_score=None)
    blocked = evaluate_production_buy_gate(no_score, current_price=100.0, market_regime="moderate_bull")
    assert "missing_score" in {item["code"] for item in blocked["hard_findings"]}
    exempt = evaluate_production_buy_gate(no_score, current_price=100.0, market_regime="moderate_bull",
                                          score_exempt=True)
    assert exempt["allowed"] and exempt["score_policy"]["mode"] == "score_exempt"
    trend = evaluate_production_buy_gate(no_score, current_price=100.0, market_regime="moderate_bull",
                                         score_exempt=True, trend_facts="T1_hit(종가<MA50): true / T2_hit: false")
    assert "individual_trend_t1" in {item["code"] for item in trend["hard_findings"]}
    rr = evaluate_production_buy_gate(_scenario(buy_score=None, target_price=101.0, stop_loss=99.0,
                                                risk_reward_ratio=0.5),
                                      current_price=100.0, market_regime="moderate_bull", score_exempt=True)
    assert "rr_below_floor" in {item["code"] for item in rr["hard_findings"]}
