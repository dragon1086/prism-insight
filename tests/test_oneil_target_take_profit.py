"""Target reached is never a deterministic sell (2026-10-07).

History: 2026-07-29 INCY was sold at target in a `sideways` regime for +16.76% while
it was still at its post-entry high; a hold-when-strong exception followed. The
2026-10-07 exit study then showed target-reached sells cut big runners in general
(17 of 79 real trades that later reached +50%), so TIER3 no longer sells in any
regime: TIER2 trailing (bull -8%, weak -5%) and the TIER1 stops own every exit.

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



class TestTargetIsNeverASell:
    @pytest.mark.parametrize("regime", ["parabolic", "strong_bull", "moderate_bull",
                                        "sideways", "moderate_bear", "strong_bear"])
    def test_target_hit_holds_in_every_regime(self, regime):
        should_sell, reason = evaluate_oneil_sell(_incy(regime=regime))
        assert should_sell is False, reason
        assert reason.startswith("HOLD: target hit") and "trailing manages the exit" in reason

    def test_profit_at_sale_matches_the_incident(self):
        profit = (INCY_SELL_PRICE - INCY_BUY) / INCY_BUY * 100
        assert profit == pytest.approx(16.76, abs=0.01)

    @pytest.mark.parametrize("ma_50", [0.0, INCY_SELL_PRICE])
    def test_strength_no_longer_matters_above_the_trail(self, ma_50):
        """Rolled 4% off the peak (inside the -5% weak trail) or no 50MA: still a hold."""
        should_sell, reason = evaluate_oneil_sell(_incy(current_price=INCY_PEAK * 0.96, ma_50=ma_50))
        assert should_sell is False, reason


class TestSafetyInvariants:
    """Removing the target sell must not touch any loss-cutting tier."""

    def test_hard_stop_still_fires(self):
        should_sell, reason = evaluate_oneil_sell(_incy(current_price=INCY_STOP * 0.98))
        assert should_sell is True and reason.startswith("TIER1")

    def test_abs_7pct_stop_still_fires(self):
        should_sell, reason = evaluate_oneil_sell(_incy(current_price=INCY_BUY * 0.92))
        assert should_sell is True and reason.startswith("TIER1")

    @pytest.mark.parametrize("regime,drop", [("sideways", 0.94), ("moderate_bull", 0.91)])
    def test_trailing_stop_still_fires(self, regime, drop):
        """TIER2 (weak -5%, bull -8%) still owns the profit-protecting exit."""
        price = INCY_PEAK * drop
        should_sell, reason = evaluate_oneil_sell(_incy(current_price=price, regime=regime))
        assert should_sell is True and reason.startswith("TIER2_TRAIL"), reason


class TestKrUsParity:
    def test_kr_and_us_copies_are_identical(self):
        """The two oneil_fallback.py copies are hand-synced; drift silently makes
        KR and US trade differently."""
        kr = (_ROOT / "cores" / "oneil_fallback.py").read_bytes()
        us = (_ROOT / "prism-us" / "cores" / "oneil_fallback.py").read_bytes()
        assert kr == us, "cores/ and prism-us/cores/ oneil_fallback.py have diverged"
