"""Real buy-gate contract for signed KR/US deterministic volatility facts."""

import pytest

from cores.buy_gate import _volatility_from_text, evaluate_production_buy_gate


def evaluate(facts):
    return evaluate_production_buy_gate(
        {"buy_score": 8, "min_score": 5, "target_price": 110.0,
         "stop_loss": 98.0, "risk_reward_ratio": 5.0,
         "market_condition": "moderate_bull"},
        current_price=100.0, market_regime="moderate_bull", trend_facts=facts,
    )


@pytest.mark.parametrize("mode", ["off", "shadow", "live"])
@pytest.mark.parametrize("suffix", ["stop-width shadow check", "손절폭 shadow 검증"])
def test_signed_producer_facts_match_unsigned_in_every_existing_mode(monkeypatch, mode, suffix):
    monkeypatch.setattr("cores.shadow_lifecycle.feature_mode", lambda feature: mode)
    # Both real tracking producers use f"{value:+.1f}{suffix}" for volatility.
    signed = f"- Volatility: ATR20={3.2:+.1f}% / ADR20={4.1:+.1f}% ({suffix})"
    unsigned = f"- Volatility: ATR20=3.2% / ADR20=4.1% ({suffix})"
    result = evaluate(signed)
    assert result == evaluate(unsigned)
    assert (result["atr20_pct"], result["adr20_pct"]) == (3.2, 4.1)
    assert result["volatility_noise_floor_pct"] == pytest.approx(2.05)
    assert result["allowed"] is (mode != "live")
    findings = [f for f in result["findings"] if f["code"] == "stop_below_volatility_noise_floor"]
    assert len(findings) == (0 if mode == "off" else 1)
    if findings:
        assert findings[0]["hard"] is (mode == "live")


def test_signed_producer_matches_unsigned_under_current_policy():
    # Uses the unmodified current lifecycle implementation, without promoting it.
    assert evaluate("ATR20=+3.2% / ADR20=+4.1%") == evaluate("ATR20=3.2% / ADR20=4.1%")


@pytest.mark.parametrize("token,value", [
    ("3.2%", 3.2), ("+3.2%", 3.2), ("3.2", 3.2), ("+3.2", 3.2),
    ("3", 3), ("0%", 0), ("+0.0%", 0), (".5%", .5), ("3.%", 3),
    ("3.2 %", 3.2),
])
def test_valid_legacy_and_signed_decimal_formats(token, value):
    assert _volatility_from_text(f"ATR20: {token} / adr20 = {token}") == (value, value)


@pytest.mark.parametrize("token", [
    "-3.2%", "-0%", "nan%", "+nan%", "inf%", "+inf%", "Infinity%",
    "3.2oops", "3.2.4", "3.2.4%", "3.2%oops", "3.2%%", "3.2%p",
    "3.2USD", "3.2 USD", "3.2bps", "3.2 bps", "3,200%", "3e2%",
    "++3.2%", "+ 3.2%", ".%", "n/a", "9" * 400 + "%",
])
def test_negative_nonfinite_malformed_or_wrong_unit_is_not_prefix_parsed(token):
    assert _volatility_from_text(f"ATR20={token} / ADR20={token}") == (None, None)


def test_invalid_field_does_not_hide_other_valid_field(monkeypatch):
    monkeypatch.setattr("cores.shadow_lifecycle.feature_mode", lambda feature: "live")
    result = evaluate("ATR20=3.2oops / ADR20=+4.1%")
    assert result["atr20_pct"] is None
    assert result["adr20_pct"] == 4.1
    assert not result["allowed"]
