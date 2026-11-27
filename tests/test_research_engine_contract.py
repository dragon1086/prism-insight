"""Production values the full-ops research engine mirrors (user rule 2026-11-18).

The engine (local `tasks/research-20261117/full-ops/acct4.py`, checklist `FIDELITY_CHECKLIST_ko.md`)
is the baseline for strategy research and copies these production numbers. When one of them
changes, this test fails on purpose: update the engine to the new rule, rerun its
`revalidate.sh` until the scorecard passes, then update the value here in the same PR.
Rules that live only in server `.env`/crontab are not covered; check `tools/feature_status.py`.
"""
import re
from decimal import Decimal
from pathlib import Path

from cores import oneil_fallback as sell
from prism_core import add_plan, micro_split_live, reentry_campaign, reentry_v3_live
from prism_core.oneil_adaptive_policy import initial_sizing
from tracking import helpers

ROOT = Path(__file__).resolve().parents[1]
WHY = "production rule changed: update the full-ops research engine, rerun revalidate.sh, then this value"


def test_first_size_formula():
    # (entry, ATR14) -> first allocation: floor 0.30, mid, cap 0.80
    got = [initial_sizing(100, atr)[1] for atr in (10, 4, 1)]
    assert got == [Decimal("0.3500"), Decimal("0.5833"), Decimal("0.8000")], WHY


def test_add_rails():
    assert (add_plan.GRID, add_plan.MAX_STEP, add_plan.MIN_CHASE_PCT, add_plan.MAX_CHASE_PCT) == (
        Decimal("0.05"), Decimal("0.25"), Decimal("1.5"), Decimal("2.0")), WHY
    assert (add_plan.ACCELERATION_GAIN, add_plan.ACCELERATION_PACE, add_plan.ADDS_PER_SESSION,
            add_plan.ACCELERATION_ADDS_PER_SESSION) == (Decimal("0.08"), Decimal("1.5"), 1, 2), WHY


def test_pyramid_rows():
    assert (helpers.PYRAMID_ALLOWED_REGIMES, helpers.PYRAMID_MIN_PROFIT_PCT, helpers.PYRAMID_MAX_ROWS) == (
        ("strong_bull", "parabolic"), 5.0, 3), WHY
    assert (micro_split_live.PYRAMID_RESERVE_DEFAULT, micro_split_live.PYRAMID_RESERVE_BELOW_DEFAULT) == (2, 8), WHY


def test_sell_lines():
    assert (sell.ABS_STOP_LOSS_PCT, sell.STOP_WICK_BUFFER, sell.TRAIL_ACTIVATION_PCT, sell.TRAIL_DROP_BULL,
            sell.TRAIL_DROP_WEAK) == (-7.0, 0.005, 5.0, 0.92, 0.95), WHY
    assert (sell.TIGHT_ACTIVATION_PCT, sell.TIGHT_DROP_BULL, sell.TIGHT_DROP_WEAK) == (3.0, 0.94, 0.96), WHY
    assert sell.BULL_REGIMES == {"parabolic", "strong_bull", "moderate_bull"}, WHY


def test_reentry_and_slots():
    assert (reentry_v3_live.DAILY_CAP, reentry_campaign.MAX_ATTEMPTS) == (2, 3), WHY
    for path in ("stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"):
        assert re.search(r"^\s+MAX_SLOTS = 10\b", (ROOT / path).read_text(encoding="utf-8"), re.M), WHY


def test_rules_live_since_20261121():
    """Non-bull 52-week gate and bull-regime money slots (H-1121); the engine mirrors both in ops_now.py."""
    from cores import buy_gate
    assert buy_gate.NONBULL_52W_MIN_RATIO == 0.75, WHY
    assert micro_split_live.MONEY_SLOT_ROW_CAP == 16, WHY
    assert micro_split_live._MONEY_SLOT_BULL == buy_gate._BULL_REGIMES == {"parabolic", "strong_bull", "moderate_bull"}, WHY


def test_market_band_switches_20261128():
    """Widened trail per market (KR bull, US non-bull) behind switches; engine sets sl_hibc (KR, PEAK=high) / sl_bwo (US)."""
    assert (sell.WIDE_DROP_BULL, sell.WIDE_DROP_WEAK) == (0.90, 0.93), WHY
