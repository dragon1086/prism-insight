"""H-1109-tight-nonbull (2026-11-09): a position bought in a sideways/bear regime trails from +3% at
peak x 0.94 (live bull) / x 0.96 (live sideways/bear); bull or unknown buy-time regimes keep +5%, x 0.92 / x 0.95.
The exit rule, the AI prompt band line and the sell facts must show the same numbers."""
import json

import pytest

from cores.oneil_fallback import SellInputs, evaluate_oneil_sell, from_stock_data, trail_params
from prism_core import sell_facts as F
from prism_core.sell_regime_context import kr_live_regime_block, trailing_band_line


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("SELL_TIGHT_NONBULL_ENABLED", "true")


def test_flag_off_keeps_current_rule(monkeypatch):
    monkeypatch.delenv("SELL_TIGHT_NONBULL_ENABLED", raising=False)
    assert trail_params("strong_bull", True, "sideways") == (5.0, 0.92)
    assert trail_params("sideways", True, "strong_bear") == (5.0, 0.95)


def test_nonbull_entry_trails_early_and_tight(on):
    assert trail_params("strong_bull", True, "sideways") == (3.0, 0.94)
    assert trail_params("moderate_bear", True, "strong_bear") == (3.0, 0.96)
    assert trail_params("", False, "sideways") == (3.0, 0.90)  # live regime unknown stays wide


def test_bull_or_unknown_entry_unchanged(on):
    for entry in ("moderate_bull", "parabolic", "unknown", "", None):
        assert trail_params("sideways", True, entry) == (5.0, 0.95)


def _pos(entry):
    # bought 100, peak 104 (+4%), price 99.3: under 104 x 0.96 x 0.995 = 99.34
    return SellInputs(buy_price=100, current_price=99.3, highest_price=104, market_condition="sideways",
                      regime_is_live=True, entry_regime=entry)


def test_exit_rule_sells_nonbull_entry_only(on):
    sell, reason = evaluate_oneil_sell(_pos("sideways"))
    assert sell and reason.startswith("TIER2_TRAIL") and "-4%" in reason
    assert evaluate_oneil_sell(_pos("moderate_bull"))[0] is False  # +4% peak: not armed under the +5% rule


def test_from_stock_data_reads_buy_time_regime(on):
    inp = from_stock_data({"buy_price": 100, "current_price": 99.3,
                           "scenario": json.dumps({"market_condition": "strong_bear", "highest_price": 104})},
                          live_regime="sideways")
    assert inp.entry_regime == "strong_bear" and evaluate_oneil_sell(inp)[0]


def test_prompt_line_and_facts_match_exit_rule(on):
    line = trailing_band_line("sideways", "ko", "sideways")
    assert "× 0.96" in line and "+3%" in line
    assert "× 0.94" in trailing_band_line("strong_bull", "en", "moderate_bear")
    assert "× 0.95" in kr_live_regime_block("sideways", None, "ko", "moderate_bull")
    t = F.trailing_state(buy_price=100, highest_price=104, stop_loss=93, current_price=101, regime="sideways",
                         threshold_pct=1.5, last_close=101, entry_regime="sideways")
    assert t["activated"] and t["drop"] == 0.96 and t["activation_pct"] == 3.0
