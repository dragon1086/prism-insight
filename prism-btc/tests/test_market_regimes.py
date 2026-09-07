"""Regime labels must describe only the information available before execution."""
import pandas as pd
import pytest

from core.market_regimes import BAR_MS, SHOCK_HOLD_MS, classify_regime, volatility_sequence


def label(**overrides):
    values = dict(ts_ms=10_000_000, trend4h="up", trend1d="up", strength4h=2.,
                  shock_event_ms=None)
    values.update(overrides)
    return classify_regime(**values)


@pytest.mark.parametrize("trend", ["up", "down", "range"])
@pytest.mark.parametrize("shock", [False, True])
def test_six_descriptive_cells(trend, shock):
    actual = label(trend4h=trend if trend != "range" else "flat", trend1d=trend if trend != "range" else "up",
                   shock_event_ms=10_000_000 if shock else None)
    assert actual["label"] == f'{trend}_{"shock" if shock else "normal"}'


@pytest.mark.parametrize("overrides", [{"trend4h": None}, {"trend1d": None},
    {"strength4h": None}, {"strength4h": float("nan")}, {"strength4h": True},
    {"strength4h": -1}, {"volatility_ready": False}])
def test_missing_is_unknown_not_range(overrides):
    assert label(**overrides)["label"] == "unknown"


def test_range_includes_mixed_and_weak_trends():
    assert label(trend1d="down")["trend"] == "range"
    assert label(strength4h=1.99)["trend"] == "range"


def test_shock_exact_expiry_and_future_rejected():
    assert label(shock_event_ms=10_000_000 - SHOCK_HOLD_MS + 1)["volatility"] == "shock"
    assert label(shock_event_ms=10_000_000 - SHOCK_HOLD_MS)["volatility"] == "normal"
    with pytest.raises(ValueError, match="future"):
        label(shock_event_ms=10_000_001)
    with pytest.raises(ValueError):
        label(ts_ms=True)


def bars():
    frame = pd.DataFrame({"open": [100.] * 50, "high": [101.] * 50,
                          "low": [99.] * 50, "close": [100.] * 50})
    frame.index = pd.to_datetime([i * BAR_MS for i in range(50)], unit="ms", utc=True)
    return frame


def test_shock_uses_prior_atr_not_current_and_available_at_close():
    frame = bars()
    frame.loc[frame.index[20], "high"] = 105.1  # TR6.1 vs PRIOR ATR2: shock.
    sequence = volatility_sequence(frame)
    assert sequence[19]["shock_event_ms"] is None
    assert sequence[20]["prior_atr14"] == 2.
    assert sequence[20]["shock_event_ms"] == 21 * BAR_MS
    assert sequence[13]["ready"] is False
    assert sequence[14]["ready"] is True


def test_future_perturbation_preserves_volatility_prefix():
    frame = bars()
    baseline = volatility_sequence(frame)
    frame.loc[frame.index[30]:, "high"] = 900.
    changed = volatility_sequence(frame)
    assert changed[:30] == baseline[:30]
    assert changed[30]["shock_event_ms"] == 31 * BAR_MS
