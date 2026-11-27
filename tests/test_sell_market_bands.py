"""2026-11-28 sell-band round: market-specific trail widths and the KR intraday-high peak (switches default off)."""
import pytest

from cores import oneil_fallback as sell
from cores.oneil_fallback import SellInputs, evaluate_oneil_sell, trail_params

SWITCHES = ("SELL_WIDE_BULL_BAND_MARKETS", "SELL_WIDE_WEAK_BAND_MARKETS", "SELL_PEAK_DAY_HIGH_MARKETS",
            "SELL_TIGHT_NONBULL_ENABLED")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


def test_default_unchanged():
    assert trail_params("strong_bull", True, "", "KR") == (5.0, 0.92)
    assert trail_params("sideways", True, "", "US") == (5.0, 0.95)


def test_switches_are_per_market(monkeypatch):
    monkeypatch.setenv("SELL_WIDE_BULL_BAND_MARKETS", "KR")
    monkeypatch.setenv("SELL_WIDE_WEAK_BAND_MARKETS", "US")
    assert trail_params("strong_bull", True, "", "KR") == (5.0, 0.90)
    assert trail_params("sideways", True, "", "KR") == (5.0, 0.95)      # KR non-bull unchanged
    assert trail_params("sideways", True, "", "US") == (5.0, 0.93)
    assert trail_params("strong_bull", True, "", "US") == (5.0, 0.92)   # US bull unchanged
    assert trail_params("sideways", True, "", "") == (5.0, 0.95)        # unknown market: default
    assert trail_params("sideways", False, "", "US") == (5.0, 0.90)     # stale regime still -10%


def test_tight_positions_keep_their_band(monkeypatch):
    monkeypatch.setenv("SELL_TIGHT_NONBULL_ENABLED", "KR")
    monkeypatch.setenv("SELL_WIDE_BULL_BAND_MARKETS", "KR")
    assert trail_params("strong_bull", True, "sideways", "KR") == (3.0, 0.94)


def test_exit_rule_uses_market_band(monkeypatch):
    # US, sideways, peak 110 -> default line 103.98 sells at 103.5; widened line 101.79 holds
    inp = SellInputs(buy_price=100, current_price=103.5, highest_price=110, market_condition="sideways",
                     regime_is_live=True, market="US")
    assert evaluate_oneil_sell(inp)[0] is True
    monkeypatch.setenv("SELL_WIDE_WEAK_BAND_MARKETS", "US")
    assert evaluate_oneil_sell(inp)[0] is False


def test_from_stock_data_carries_market():
    inp = sell.from_stock_data({"buy_price": 100, "current_price": 101, "scenario": "{}"}, "sideways", market="us")
    assert inp.market == "US"


def test_peak_day_high_switch(monkeypatch):
    assert sell.peak_day_high_markets() == set()
    monkeypatch.setenv("SELL_PEAK_DAY_HIGH_MARKETS", "KR")
    assert sell.peak_day_high_markets() == {"KR"}
