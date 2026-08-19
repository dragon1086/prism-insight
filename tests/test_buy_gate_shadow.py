from tools.buy_gate_shadow import validate_scenario
from tools.buy_gate_shadow import build_asof_features
import pandas as pd


def _scenario(**overrides):
    data = {
        "buy_score": 7,
        "min_score": 5,
        "effective_score": 7,
        "fundamental_check": {"all_passed": True},
        "momentum_signal_count": 2,
        "additional_confirmation_count": 1,
        "target_price": 110.0,
        "stop_loss": 95.0,
        "risk_reward_ratio": 2.0,
        "market_condition": "moderate_bear: index below 50MA",
    }
    data.update(overrides)
    return data


def test_valid_scenario_passes_deterministic_checks():
    result = validate_scenario(_scenario(), current_price=100.0, regime="moderate_bear")
    assert not result["would_block"]
    assert result["regime_source"] == "computed"


def test_bear_fundamental_failure_blocks():
    result = validate_scenario(
        _scenario(fundamental_check={"all_passed": False}),
        current_price=100.0,
        regime="moderate_bear",
    )
    assert result["would_block"]
    assert any(f["code"] == "fundamental_gate_failed" for f in result["hard_findings"])


def test_bad_rr_and_stop_are_recomputed_not_trusted():
    result = validate_scenario(
        _scenario(target_price=103.0, stop_loss=95.0, risk_reward_ratio=3.0),
        current_price=100.0,
        regime="moderate_bear",
    )
    codes = {f["code"] for f in result["hard_findings"]}
    assert "rr_below_regime_floor" in codes
    assert "rr_arithmetic_mismatch" in codes


def test_t1_and_t2_prompt_facts_are_reported_as_blockers():
    result = validate_scenario(
        _scenario(),
        current_price=100.0,
        regime="moderate_bull",
        trend_facts=(
            "- T1_hit(종가<MA50): True / T2_hit(MA20 하락 and 종가 MA20 대비 -5%↓): True"
        ),
    )
    codes = {f["code"] for f in result["hard_findings"]}
    assert {"individual_trend_t1", "individual_trend_t2"} <= codes


def test_distribution_is_caution_not_automatic_veto():
    result = validate_scenario(
        _scenario(), current_price=100.0, regime="moderate_bull", distribution_days=6
    )
    assert not result["would_block"]
    assert any(f["code"] == "distribution_day_caution" for f in result["findings"])


def test_asof_features_are_independent_of_scenario_fields():
    closes = [100.0 + i for i in range(80)]
    frame = pd.DataFrame(
        {
            "Close": closes,
            "High": [v * 1.02 for v in closes],
            "Low": [v * 0.98 for v in closes],
            "Volume": [1000] * len(closes),
        }
    )
    features = build_asof_features(frame, entry_price=179.0, regime="strong_bull")
    assert features["price"] == 179.0
    assert features["regime"] == "strong_bull"
    assert features["ma20"] is not None
    assert features["adr20_pct"] is not None
    assert "T1_hit" in features["trend_facts"]
