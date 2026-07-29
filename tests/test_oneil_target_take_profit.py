"""Tests for the TIER3 target take-profit hold-when-strong exception.

Regression cover for the 2026-07-29 INCY liquidation:
  regime (S&P500-derived) was `sideways` -> WEAK_REGIMES -> TIER3_TARGET fired
  the moment the target was touched, and the position was closed for +16.76%
  while the stock itself was +11% ON THE DAY and sitting at its post-entry high.
  The same session's buy-trigger batch had scored INCY oneil_pct=95 / RS=0.80.

The fix: in a weak/sideways regime the target take-profit is HELD while the
*stock* is demonstrably still trending (above its own 50MA and near its peak);
the exit is then delegated to the TIER2 trailing stop.

Safety invariants asserted here (these must never regress):
  - TIER1 hard stops still fire on a "strong" stock.
  - ma_50 unavailable (0) -> conservative, keeps the ORIGINAL take-profit.
  - No dead band between the hold window and the TIER2 trail.

Pure function, no network, no DB. Run in the KR (root) session.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
from cores.oneil_fallback import (  # noqa: E402
    SellInputs,
    evaluate_oneil_sell,
    TARGET_HOLD_MA50_MARGIN,
    TARGET_HOLD_PEAK_PROXIMITY,
)


# ── Real INCY position, 2026-07-29 (from loop_b_trend_exit.log) ────────────────
INCY_BUY = 113.11
INCY_STOP = 113.62          # scenario stop at time of sale (raised from 109.25)
# Target back-solved from the logged entry scenario: initial stop 109.25 -> risk
# 3.86/share, and the trigger batch logged R/R=3.00 -> 113.11 + 3*3.86.
INCY_TARGET = 124.69
INCY_PEAK = 132.43          # post-entry high (intraday, day of sale)
INCY_SELL_PRICE = 132.07    # price the batch actually sold at (+16.76%)
INCY_MA50 = 116.00          # ~50d MA over the Jun-Jul $113-119 base


def _incy(current_price=INCY_SELL_PRICE, regime="sideways", ma_50=INCY_MA50,
          peak=INCY_PEAK):
    return SellInputs(
        buy_price=INCY_BUY,
        current_price=current_price,
        stop_loss=INCY_STOP,
        target_price=INCY_TARGET,
        highest_price=peak,
        market_condition=regime,
        regime_is_live=True,
        ma_50=ma_50,
    )


class TestIncyRegression:
    def test_strong_stock_in_sideways_regime_is_held(self):
        """THE REGRESSION: INCY must NOT be sold at target while still trending."""
        should_sell, reason = evaluate_oneil_sell(_incy())
        assert should_sell is False, f"INCY sold again: {reason}"
        assert "let it run" in reason
        assert "stock strong" in reason

    def test_profit_at_sale_matches_the_incident(self):
        """Sanity-check the fixture against the logged +16.76% so the test
        genuinely reproduces the incident rather than a nearby scenario."""
        profit = (INCY_SELL_PRICE - INCY_BUY) / INCY_BUY * 100
        assert profit == pytest.approx(16.76, abs=0.01)

    @pytest.mark.parametrize("regime", ["sideways", "moderate_bear", "strong_bear"])
    def test_hold_applies_across_all_weak_regimes(self, regime):
        should_sell, _ = evaluate_oneil_sell(_incy(regime=regime))
        assert should_sell is False


class TestOriginalBehaviourPreserved:
    """The exception must be narrow — everything else keeps the old semantics."""

    def test_rolled_over_stock_still_takes_profit(self):
        """Pulled >3% off the peak -> no longer 'strong' -> TIER3 fires as before."""
        rolled = INCY_PEAK * 0.96
        should_sell, reason = evaluate_oneil_sell(_incy(current_price=rolled))
        assert should_sell is True
        assert reason.startswith("TIER3_TARGET(weak)")

    def test_ma50_unavailable_is_conservative(self):
        """ma_50=0 (fetch failed / dormant) -> cannot prove strength -> sell."""
        should_sell, reason = evaluate_oneil_sell(_incy(ma_50=0.0))
        assert should_sell is True
        assert reason.startswith("TIER3_TARGET(weak)")

    def test_extended_but_below_ma50_margin_still_sells(self):
        """Near peak but barely above its own 50MA -> not a real uptrend."""
        weak_ma = INCY_SELL_PRICE / (TARGET_HOLD_MA50_MARGIN * 0.99)
        should_sell, reason = evaluate_oneil_sell(_incy(ma_50=weak_ma))
        assert should_sell is True
        assert reason.startswith("TIER3_TARGET(weak)")

    def test_bull_regime_unchanged(self):
        should_sell, reason = evaluate_oneil_sell(_incy(regime="moderate_bull"))
        assert should_sell is False
        assert "bull regime" in reason


class TestSafetyInvariants:
    """Strength must never suppress a loss-cutting tier."""

    def test_hard_stop_still_fires_on_a_strong_stock(self):
        """TIER1 is evaluated before TIER3 and must be untouched."""
        inp = _incy(current_price=INCY_STOP * 0.98)
        should_sell, reason = evaluate_oneil_sell(inp)
        assert should_sell is True
        assert reason.startswith("TIER1")

    def test_abs_7pct_stop_still_fires(self):
        inp = _incy(current_price=INCY_BUY * 0.92)
        should_sell, reason = evaluate_oneil_sell(inp)
        assert should_sell is True
        assert reason.startswith("TIER1")

    def test_trailing_stop_still_fires_below_the_hold_band(self):
        """TIER2 (-5% weak band) catches the position once it breaks down."""
        inp = _incy(current_price=INCY_PEAK * 0.94)
        should_sell, reason = evaluate_oneil_sell(inp)
        assert should_sell is True
        assert reason.startswith("TIER2_TRAIL")

    def test_no_dead_band_between_hold_window_and_trail(self):
        """While the target is still exceeded, every price from the TIER2 trail up
        to the peak must resolve to a definite decision — hold-because-strong, or
        an exit — never a silent fall-through to the generic 'trend intact' HOLD.

        (Below the target price TIER3 cannot fire at all by design; that region is
        owned by TIER2 and is asserted separately.)"""
        assert TARGET_HOLD_PEAK_PROXIMITY > 0.95, "hold band must sit above the -5% trail"
        for pct in [0.950, 0.955, 0.960, 0.970, 0.980, 0.990, 1.000]:
            assert INCY_PEAK * pct >= INCY_TARGET, "fixture: price must still be above target"
            inp = _incy(current_price=INCY_PEAK * pct)
            should_sell, reason = evaluate_oneil_sell(inp)
            if pct >= TARGET_HOLD_PEAK_PROXIMITY:
                assert should_sell is False, f"{pct}: expected hold, got {reason}"
            else:
                assert should_sell is True, f"{pct}: expected an exit, got {reason}"
                assert reason.startswith(("TIER2_TRAIL", "TIER3_TARGET")), reason


class TestKrUsParity:
    def test_kr_and_us_copies_are_identical(self):
        """The two oneil_fallback.py copies are hand-synced; drift silently makes
        KR and US trade differently."""
        kr = (_ROOT / "cores" / "oneil_fallback.py").read_bytes()
        us = (_ROOT / "prism-us" / "cores" / "oneil_fallback.py").read_bytes()
        assert kr == us, "cores/ and prism-us/cores/ oneil_fallback.py have diverged"
