"""Pure rule tests: each rule keeps one accepted example and one rejected counterexample."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from prism_inverse.domain import indicators as ind
from prism_inverse.domain.broken_leaders import leader_state, leader_states
from prism_inverse.domain.entry import entry_signal
from prism_inverse.domain.etf_selector import best_per_underlying, choose_index, prefer_multiplier, reject_reason
from prism_inverse.domain.exit import exit_reason
from prism_inverse.domain.market_gate import broken_weight, gate
from prism_inverse.domain.models import Bar, EtfSnapshot, LeaderState, Position
from prism_inverse.domain.policy import InversePolicy
from prism_inverse.domain.sizing import size, worst_up_gap
from prism_inverse.domain.synthetic import inverse_bars
from prism_inverse.domain.ticks import etf_tick
from prism_inverse.tests.helpers import bars, run_up_then_break, spec

P = InversePolicy()
D = date(2026, 11, 6)
GOOD = EtfSnapshot(price=1341.0, tick=1.0, value_20d=1.2e12, premium_pct=0.1, tracking_error=1.6)


def test_indicators_need_enough_history():
    assert ind.sma([1, 2, 3], 3) == 2 and ind.sma([1, 2], 3) is None
    b = bars([10, 11, 12, 13])
    assert ind.atr(b, 3) is not None and ind.atr(b, 4) is None


def test_volume_ratio_unknown_volume_stays_unknown():
    b = bars([10] * 6, volumes=[1.0, 1.0, None, 1.0, 1.0, 2.0])
    assert ind.volume_ratio(b, 5) is None
    assert ind.volume_ratio(bars([10] * 6, volumes=[1.0] * 5 + [2.0]), 5) == 2.0


def test_leader_breaks_after_run_up_on_volume():
    closes, vols = run_up_then_break()
    st = leader_state("X", bars(closes, vols), P)
    assert st.status == "broken" and st.prior_gain >= 0.5 and st.drop_pct >= 0.2


def test_leader_break_needs_volume_confirmation_and_missing_volume_cannot_confirm():
    closes, _ = run_up_then_break()
    assert leader_state("X", bars(closes, [1_000_000.0] * len(closes)), replace(P, leader_volume_ratio=1.5)).status is None
    assert leader_state("X", bars(closes, [None] * len(closes)), P).status is None
    assert leader_state("X", bars(closes, [None] * len(closes)), replace(P, leader_volume_ratio=None)).status == "broken"


def test_flat_stock_is_never_a_leader():
    assert leader_state("X", bars([100.0] * 300), P).status is None


def test_recovery_then_second_break_is_rebroken():
    closes, vols = run_up_then_break(n_down=60)
    back = [closes[-1] + (200 - closes[-1]) * (i + 1) / 40 for i in range(40)]
    again = [back[-1] * (1 - 0.35 * (i + 1) / 60) for i in range(60)]
    seq = closes + back + again
    states = leader_states("X", bars(seq, vols + [1e6] * 40 + [2e6] * 60), P)
    seen = [s.status for s in states]
    assert "broken" in seen and "recovered" in seen and seen[-1] == "rebroken"


def test_gate_codes():
    assert gate(None, None, P) == (False, "MISSING_PULSE")
    assert gate("UPTREND", None, P) == (False, "PULSE_UPTREND")
    assert gate("CORRECTION", None, P) == (True, "GATE_ON")
    strict = replace(P, gate_min_leader_weight=0.1)
    assert gate("CORRECTION", None, strict) == (False, "MISSING_LEADER_WEIGHT")
    assert gate("CORRECTION", 0.05, strict) == (False, "LEADER_WEIGHT_LOW")


def test_broken_weight_counts_only_broken_constituents():
    leaders = {"A": LeaderState("A", "broken", D), "B": LeaderState("B", "recovered", D)}
    assert broken_weight(leaders, {"A": 0.2, "B": 0.3, "C": 0.1}) == pytest.approx(0.2)
    assert broken_weight(leaders, {}) is None


def test_eligibility_rejects_unknown_and_costly():
    s = spec()
    assert reject_reason(s, GOOD, D, P) is None
    assert reject_reason(s, EtfSnapshot(), D, P) == "MISSING_VALUE_20D"
    # KR 2x at 100 won: one tick is 1% of price.
    assert reject_reason(spec("252670", mult=-2), replace(GOOD, price=100.0), D, P) == "TICK_COST_HIGH"
    assert reject_reason(s, replace(GOOD, premium_pct=None), D, P) == "MISSING_PREMIUM"
    assert reject_reason(spec(market="US", code="SH"), EtfSnapshot(price=33.7, tick=0.01, value_20d=3e8), D, P) is None
    assert reject_reason(spec(mult=-3), GOOD, D, P) == "MULTIPLIER_EXCLUDED"
    assert reject_reason(spec(listed=date(2027, 1, 1)), GOOD, D, P) == "NOT_LISTED"


def test_same_underlying_keeps_cheapest_then_most_traded():
    a, b = spec("114800"), spec("123310")
    kept = best_per_underlying([(b, replace(GOOD, value_20d=6.8e9)), (a, GOOD)])
    assert [s.code for s, _ in kept] == ["114800"]


def test_choose_index_main_and_overlap():
    kospi, kosdaq = spec("114800", underlying="069500"), spec("251340", underlying="229200")
    assert choose_index([kospi, kosdaq], {}, "069500", P)[0] is kospi
    over = replace(P, index_choice="overlap")
    assert choose_index([kospi, kosdaq], {}, "069500", over) == (None, "MISSING_OVERLAP")
    assert choose_index([kospi, kosdaq], {"069500": 0.1, "229200": 0.3}, "069500", over)[0] is kosdaq
    assert choose_index([kosdaq], {}, "069500", P) == (None, "MAIN_NOT_GATED")


def test_single_stock_prefers_1x():
    one, two = spec("NVDD", "US", -1, "stock", "NVDA", "stock"), spec("NVD", "US", -2, "stock", "NVDA", "stock")
    assert prefer_multiplier([two, one], (-1, -2))["NVDA"] is one
    assert prefer_multiplier([two], (-1, -2))["NVDA"] is two


def _rally_to_ma50():
    seq = [100 - i * 0.3 for i in range(60)] + [82 + i * 0.5 for i in range(10)]
    return bars(seq, spread=0.002)


def test_entry_rally_fires_only_when_the_rally_touches_the_ma_and_closes_below():
    b = _rally_to_ma50()
    ma = ind.sma(ind.closes(b), 50)
    touching = b[:-1] + [Bar(b[-1].day, b[-1].open, ma * 1.001, b[-1].low, ma * 0.995, b[-1].volume)]
    sig = entry_signal(touching, False, P)
    assert sig.fired and sig.reason == "ENTRY_RALLY_MA50" and sig.stop >= ma
    above = touching[:-1] + [replace(touching[-1], close=ma * 1.01, high=ma * 1.02)]
    assert entry_signal(above, False, P).reason == "NO_RALLY_TO_MA"
    assert entry_signal(b[:10], False, P).reason in ("MISSING_ATR", "MISSING_MA")


def test_entry_signal_mode_needs_a_new_signal():
    b = _rally_to_ma50()
    sig_policy = replace(P, entry_mode="signal")
    assert entry_signal(b, True, sig_policy).reason == "ENTRY_SIGNAL_DAY"
    assert entry_signal(b, False, sig_policy).reason == "NO_NEW_SIGNAL"


def _pos(**kw):
    base = dict(position_id="p", etf="114800", lane="index", multiplier=-1, qty=10, entry_price=1000.0,
                entry_underlying=100.0, stop_underlying=105.0, target_underlying=90.0, opened=date(2020, 1, 1))
    base.update(kw)
    return Position(**base)


def test_exit_checks_stop_first_then_pulse_target_time():
    b = bars([100.0] * 5)
    assert exit_reason(_pos(), bars([100.0] * 4 + [106.0]), "UPTREND", P) == "STOP"
    assert exit_reason(_pos(), b, "UPTREND", P) == "PULSE_UPTREND"
    assert exit_reason(_pos(), bars([100.0] * 4 + [89.0]), "CORRECTION", P) == "TARGET"
    assert exit_reason(_pos(), b, "CORRECTION", P) is None
    assert exit_reason(_pos(), bars([100.0] * 12), "CORRECTION", P) == "TIME_STOP"
    assert exit_reason(_pos(), bars([100.0] * 4), None, P) is None  # unknown pulse is not an exit


def test_size_equal_loss_across_multipliers_and_gap_cap():
    q1, _ = size(1e8, 1000.0, -1, 100.0, 105.0, P)
    q2, _ = size(1e8, 1000.0, -2, 100.0, 105.0, P)
    assert q1 == 10_000 and q2 == 5_000
    capped, _ = size(1e8, 1000.0, -2, 100.0, 105.0, P, gap=0.15, need_gap=True)
    assert capped == 3_333
    assert size(1e8, 1000.0, -2, 100.0, 105.0, P, gap=None, need_gap=True) == (0, "MISSING_GAP_HISTORY")
    assert size(None, 1000.0, -1, 100.0, 105.0, P) == (0, "MISSING_SIZING_INPUT")
    assert size(1e8, 1000.0, -1, 100.0, 99.0, P) == (0, "BAD_STOP")


def test_worst_up_gap():
    b = bars([100.0, 100.0, 100.0])
    b = b[:2] + [replace(b[2], open=110.0)]
    assert worst_up_gap(b, 2) == pytest.approx(0.10)
    assert worst_up_gap(b, 5) is None


def test_synthetic_inverse_daily_reset():
    out = inverse_bars(bars([100.0, 110.0, 99.0]), -1, fee_annual=0.0)
    assert out[1].close == pytest.approx(90.0) and out[2].close == pytest.approx(90.0 * 1.1)
    two = inverse_bars(bars([100.0, 160.0]), -2, fee_annual=0.0)
    assert two[1].close == 0.0  # a >50% up day wipes a 2x inverse; never negative
    assert all(b.low <= b.close <= b.high for b in out)


def test_kr_etf_tick():
    assert etf_tick("KR", 125) == 1 and etf_tick("KR", 3110) == 5 and etf_tick("US", 33.7) == 0.01
