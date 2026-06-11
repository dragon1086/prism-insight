# tests/test_core_entries.py — Unit tests for the pure entry-decision core.
#
# Exercise core.entries.evaluate_entry directly (no pandas, no DB): a Signal plus
# precomputed inputs in, an OpenIntent or None out. Covers the engine-relied
# paths: cooldown block, fresh-entry accept, sizing rejection, pyramid gate.
from __future__ import annotations

from engine.signal import Signal
from engine.sizing import TRANCHE_FRACS, RISK_PER_TRADE
from core.entries import EntryInputs, CooldownState, evaluate_entry
from core.actions import OpenIntent

# A known non-rejected long sizing input (verified against compute_sizing):
LONG_INPUTS = EntryInputs(entry_price=100.0, atr_1h=1.0, swing_ref=96.0, ma35_1h=98.0)
SHORT_INPUTS = EntryInputs(entry_price=100.0, atr_1h=1.0, swing_ref=104.0, ma35_1h=102.0)

LONG_SIG = Signal(side="long", strength=60.0, reason="test")
SHORT_SIG = Signal(side="short", strength=60.0, reason="test")
NONE_SIG = Signal(side="none", strength=0.0, reason="none")


def test_none_signal_returns_none():
    out = evaluate_entry(NONE_SIG, 10_000.0, 0, inputs=LONG_INPUTS,
                         cooldown=CooldownState(bars_since_close=999, cooldown_bars=8))
    assert out is None


def test_cooldown_blocks_fresh_entry():
    # bars_since_close < cooldown_bars → blocked even with a valid signal.
    out = evaluate_entry(LONG_SIG, 10_000.0, 0, inputs=LONG_INPUTS,
                         cooldown=CooldownState(bars_since_close=3, cooldown_bars=8))
    assert out is None


def test_cooldown_elapsed_allows_fresh_entry():
    out = evaluate_entry(LONG_SIG, 10_000.0, 0, inputs=LONG_INPUTS,
                         cooldown=CooldownState(bars_since_close=8, cooldown_bars=8))
    assert isinstance(out, OpenIntent)
    assert out.side == "long"
    assert out.tranche_index == 0
    assert out.limit_price == 100.0
    assert out.sizing.qty > 0
    # initial_risk = equity * RISK_PER_TRADE * TRANCHE_FRACS[0]
    assert abs(out.initial_risk - 10_000.0 * RISK_PER_TRADE * TRANCHE_FRACS[0]) < 1e-9


def test_short_fresh_entry_allowed():
    out = evaluate_entry(SHORT_SIG, 10_000.0, 0, inputs=SHORT_INPUTS,
                         cooldown=CooldownState(bars_since_close=20, cooldown_bars=8))
    assert isinstance(out, OpenIntent)
    assert out.side == "short"


def test_sizing_rejection_returns_none():
    # abs_score below the leverage floor (40) → compute_sizing rejects → None.
    weak = Signal(side="long", strength=10.0, reason="weak")
    out = evaluate_entry(weak, 10_000.0, 0, inputs=LONG_INPUTS,
                         cooldown=CooldownState(bars_since_close=99, cooldown_bars=8))
    assert out is None


def test_pyramid_blocked_when_not_in_profit_long():
    # Long pyramid requires current_price > avg_entry; here it is not.
    out = evaluate_entry(LONG_SIG, 10_000.0, 1, inputs=LONG_INPUTS,
                         avg_entry=101.0, current_price=100.0)
    assert out is None


def test_pyramid_allowed_when_in_profit_long():
    out = evaluate_entry(LONG_SIG, 10_000.0, 1, inputs=LONG_INPUTS,
                         avg_entry=99.0, current_price=100.0)
    assert isinstance(out, OpenIntent)
    assert out.tranche_index == 1
    assert abs(out.initial_risk - 10_000.0 * RISK_PER_TRADE * TRANCHE_FRACS[1]) < 1e-9


def test_no_add_at_or_above_max_tranches():
    out = evaluate_entry(LONG_SIG, 10_000.0, 3, inputs=LONG_INPUTS,
                         avg_entry=99.0, current_price=100.0)
    assert out is None
