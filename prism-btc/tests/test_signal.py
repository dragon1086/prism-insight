# tests/test_signal.py — Offline tests for signal generation
from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

from engine.regime import RegimeSnapshot, TFState, build_snapshot
from engine.signal import (
    Signal,
    generate_signal,
    check_exit_signal,
    trend_strength,
    chop_filter_passed,
    LONG_ENTRY_POSITIONS,
    SHORT_ENTRY_POSITIONS,
)
from engine.config import TF_WEIGHTS, TS_MIN, ENTRY_SCORE_MIN


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_tf_state(
    trend: str = "up",
    candle_position: str = "above_all",
    ma10: float = 105.0,
    ma35: float = 100.0,
    close: float = 107.0,
    atr14: float = 2.0,
) -> TFState:
    return TFState(
        trend=trend,
        candle_position=candle_position,
        ma10=ma10,
        ma35=ma35,
        close=close,
        atr14=atr14,
    )


def make_snapshot(
    tf_states: dict[str, TFState],
    score: float,
    evaluated_at: str = "2023-01-01T00:00:00Z",
) -> RegimeSnapshot:
    return RegimeSnapshot(
        tf_states=tf_states,
        alignment_score=score,
        evaluated_at=evaluated_at,
    )


def all_bullish_snapshot(score: float = 75.0) -> RegimeSnapshot:
    """All TFs bullish, support_ma10 for short TFs (long entry pattern)."""
    states = {
        "30m": make_tf_state("up", "support_ma10"),
        "1h": make_tf_state("up", "support_ma10"),
        "4h": make_tf_state("up", "above_all"),
        "12h": make_tf_state("up", "above_all"),
        "1d": make_tf_state("up", "above_all"),
        "1w": make_tf_state("up", "above_all"),
    }
    return make_snapshot(states, score)


def all_bearish_snapshot(score: float = -75.0) -> RegimeSnapshot:
    """All TFs bearish, resist_ma10 for short TFs (short entry pattern)."""
    states = {
        "30m": make_tf_state("down", "resist_ma10"),
        "1h": make_tf_state("down", "resist_ma10"),
        "4h": make_tf_state("down", "below_all"),
        "12h": make_tf_state("down", "below_all"),
        "1d": make_tf_state("down", "below_all"),
        "1w": make_tf_state("down", "below_all"),
    }
    return make_snapshot(states, score)


def sideways_snapshot(score: float = 20.0) -> RegimeSnapshot:
    states = {tf: make_tf_state("flat", "between") for tf in TF_WEIGHTS}
    return make_snapshot(states, score)


# ---------------------------------------------------------------------------
# Tests: generate_signal
# ---------------------------------------------------------------------------

class TestGenerateSignal:
    def test_sideways_score_returns_none(self):
        snap = sideways_snapshot(score=20.0)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_score_below_entry_min_returns_none(self):
        # P1-1: entry threshold raised to 55; 54.9 must not trigger entry.
        snap = all_bullish_snapshot(score=54.9)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_score_between_40_and_55_returns_none(self):
        # Previously 40 entered; now 40 is below the 55 entry gate → none.
        snap = all_bullish_snapshot(score=40.0)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_score_at_entry_min_long_all_aligned(self):
        snap = all_bullish_snapshot(score=ENTRY_SCORE_MIN)
        sig = generate_signal(snap)
        assert sig.side == "long"

    def test_score_negative_entry_min_short_all_aligned(self):
        snap = all_bearish_snapshot(score=-ENTRY_SCORE_MIN)
        sig = generate_signal(snap)
        assert sig.side == "short"

    def test_score_just_below_entry_min_returns_none(self):
        # 라운드4: gate raised 55 → 85 (v3_edge_diagnosis §1). Just-below must not enter.
        snap = all_bullish_snapshot(score=ENTRY_SCORE_MIN - 0.1)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_strong_long_signal(self):
        snap = all_bullish_snapshot(score=85.0)
        sig = generate_signal(snap)
        assert sig.side == "long"
        assert sig.strength == pytest.approx(85.0)

    def test_strong_short_signal(self):
        snap = all_bearish_snapshot(score=-85.0)
        sig = generate_signal(snap)
        assert sig.side == "short"

    def test_long_blocked_when_long_tf_bearish(self):
        """Score >= 40 but long TFs are bearish → no signal."""
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            "4h": make_tf_state("up", "above_all"),
            "12h": make_tf_state("down", "below_all"),   # bearish
            "1d": make_tf_state("down", "below_all"),    # bearish
            "1w": make_tf_state("down", "below_all"),    # bearish
        }
        snap = make_snapshot(states, score=42.0)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_long_blocked_when_entry_tf_not_aligned(self):
        """라운드2 #3: trigger TF is 4h now. 4h 'between' → no long (30m/1h irrelevant)."""
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            "4h": make_tf_state("up", "between"),        # not in LONG_ENTRY_POSITIONS
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=55.0)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_short_blocked_when_entry_tf_not_aligned(self):
        """라운드2 #3: 4h candle position not in SHORT_ENTRY_POSITIONS → no short."""
        states = {
            "30m": make_tf_state("down", "resist_ma10"),
            "1h": make_tf_state("down", "resist_ma10"),
            "4h": make_tf_state("down", "between"),      # not in SHORT_ENTRY_POSITIONS
            "12h": make_tf_state("down", "below_all"),
            "1d": make_tf_state("down", "below_all"),
            "1w": make_tf_state("down", "below_all"),
        }
        snap = make_snapshot(states, score=-55.0)
        sig = generate_signal(snap)
        assert sig.side == "none"

    def test_break_up_triggers_long(self):
        """break_ma10_up on short TFs triggers long entry."""
        states = {
            "30m": make_tf_state("up", "break_ma10_up"),
            "1h": make_tf_state("up", "break_ma10_up"),
            "4h": make_tf_state("up", "above_all"),
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=ENTRY_SCORE_MIN + 5.0)
        sig = generate_signal(snap)
        assert sig.side == "long"

    def test_break_down_triggers_short(self):
        """break_ma35_down on short TFs triggers short entry."""
        states = {
            "30m": make_tf_state("down", "break_ma35_down"),
            "1h": make_tf_state("down", "break_ma35_down"),
            "4h": make_tf_state("down", "below_all"),
            "12h": make_tf_state("down", "below_all"),
            "1d": make_tf_state("down", "below_all"),
            "1w": make_tf_state("down", "below_all"),
        }
        snap = make_snapshot(states, score=-(ENTRY_SCORE_MIN + 5.0))
        sig = generate_signal(snap)
        assert sig.side == "short"


# ---------------------------------------------------------------------------
# Tests: check_exit_signal
# ---------------------------------------------------------------------------

class TestCheckExitSignal:
    def test_no_position_returns_hold(self):
        snap = all_bullish_snapshot()
        sig = check_exit_signal(snap, "none")
        assert sig.exit_action == "hold"

    def test_long_position_long_trend_holds(self):
        snap = all_bullish_snapshot(score=75.0)
        sig = check_exit_signal(snap, "long")
        assert sig.exit_action == "hold"

    def test_long_reversed_to_exit(self):
        """Long TFs reverse to bearish with strong negative score → exit."""
        snap = all_bearish_snapshot(score=-50.0)
        sig = check_exit_signal(snap, "long")
        assert sig.exit_action == "exit"

    def test_short_reversed_to_exit(self):
        snap = all_bullish_snapshot(score=50.0)
        sig = check_exit_signal(snap, "short")
        assert sig.exit_action == "exit"

    def test_reduce_on_short_term_reversal_plus_4h_warning(self):
        """30m+1h reverse AND 4h shows warning → reduce."""
        states = {
            "30m": make_tf_state("down", "break_ma10_down"),
            "1h": make_tf_state("down", "below_all"),
            "4h": make_tf_state("down", "break_ma10_down"),  # 4h warning for long
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=30.0)  # score < 40 but holding
        sig = check_exit_signal(snap, "long")
        assert sig.exit_action == "reduce"

    def test_short_reduce_on_reversal(self):
        """Short position: 30m+1h break up + 4h support triggers reduce."""
        states = {
            "30m": make_tf_state("up", "break_ma35_up"),
            "1h": make_tf_state("up", "above_all"),
            "4h": make_tf_state("up", "support_ma10"),  # 4h warning for short
            "12h": make_tf_state("down", "below_all"),
            "1d": make_tf_state("down", "below_all"),
            "1w": make_tf_state("down", "below_all"),
        }
        snap = make_snapshot(states, score=-20.0)
        sig = check_exit_signal(snap, "short")
        assert sig.exit_action == "reduce"


# ---------------------------------------------------------------------------
# Tests: 라운드2 #1 — 횡보 필터 (추세강도 게이트)
# ---------------------------------------------------------------------------

class TestTrendStrengthChopFilter:
    def test_trend_strength_formula(self):
        # |105 - 100| / 2.0 = 2.5
        st = make_tf_state("up", "above_all", ma10=105.0, ma35=100.0, atr14=2.0)
        assert trend_strength(st) == pytest.approx(2.5)

    def test_trend_strength_zero_atr_returns_zero(self):
        st = make_tf_state("up", "above_all", ma10=105.0, ma35=100.0, atr14=0.0)
        assert trend_strength(st) == 0.0

    def test_chop_filter_passes_when_4h_and_1d_strong(self):
        # default fixtures give TS=2.5 (>= TS_MIN) on every TF
        snap = all_bullish_snapshot(score=75.0)
        assert chop_filter_passed(snap.tf_states) is True

    def test_chop_filter_blocks_when_4h_weak(self):
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            # 4h trend_strength = |100.1-100|/2 = 0.05 < TS_MIN → chop
            "4h": make_tf_state("up", "above_all", ma10=100.1, ma35=100.0, atr14=2.0),
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=75.0)
        assert chop_filter_passed(snap.tf_states) is False

    def test_chop_filter_ignores_weak_1d(self):
        # 라운드4: gate is 4h-only (TS_GATE_TFS=("4h",)) — the H1 study measured 4h ts;
        # a weak 1d must NOT block entry anymore.
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            "4h": make_tf_state("up", "above_all"),
            "12h": make_tf_state("up", "above_all"),
            # 1d weak
            "1d": make_tf_state("up", "above_all", ma10=100.1, ma35=100.0, atr14=2.0),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=75.0)
        assert chop_filter_passed(snap.tf_states) is True

    def test_chop_filter_blocks_when_gate_tf_missing(self):
        states = {tf: make_tf_state("up", "above_all") for tf in ("30m", "1h", "12h", "1w")}
        # 4h and 1d absent
        snap = make_snapshot(states, score=75.0)
        assert chop_filter_passed(snap.tf_states) is False

    def test_generate_signal_blocked_by_chop_filter(self):
        """Strong score + aligned but 4h/1d flat (low TS) → no entry."""
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            "4h": make_tf_state("up", "above_all", ma10=100.1, ma35=100.0, atr14=2.0),
            "1d": make_tf_state("up", "above_all", ma10=100.1, ma35=100.0, atr14=2.0),
            "12h": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=80.0)
        sig = generate_signal(snap)
        assert sig.side == "none"
        assert "횡보" in sig.reason or "추세강도" in sig.reason


# ---------------------------------------------------------------------------
# Tests: 라운드2 #3 — 진입 트리거 TF 상향 (4h 캔들 위치 기준)
# ---------------------------------------------------------------------------

class TestEntryTriggerTF4h:
    def test_long_uses_4h_position_not_short_tfs(self):
        """30m/1h in 'between' (would block under old rule) but 4h is above_all → long allowed."""
        states = {
            "30m": make_tf_state("up", "between"),
            "1h": make_tf_state("up", "between"),
            "4h": make_tf_state("up", "above_all"),     # entry trigger TF
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=ENTRY_SCORE_MIN + 5.0)
        sig = generate_signal(snap)
        assert sig.side == "long"

    def test_long_blocked_when_4h_position_not_aligned(self):
        """All long bias but 4h candle position is 'between' → no entry."""
        states = {
            "30m": make_tf_state("up", "support_ma10"),
            "1h": make_tf_state("up", "support_ma10"),
            "4h": make_tf_state("up", "between"),       # not a LONG_ENTRY_POSITION
            "12h": make_tf_state("up", "above_all"),
            "1d": make_tf_state("up", "above_all"),
            "1w": make_tf_state("up", "above_all"),
        }
        snap = make_snapshot(states, score=ENTRY_SCORE_MIN + 5.0)
        sig = generate_signal(snap)
        assert sig.side == "none"
        assert "4h" in sig.reason

    def test_short_uses_4h_position(self):
        states = {
            "30m": make_tf_state("down", "between"),
            "1h": make_tf_state("down", "between"),
            "4h": make_tf_state("down", "below_all"),   # entry trigger TF
            "12h": make_tf_state("down", "below_all"),
            "1d": make_tf_state("down", "below_all"),
            "1w": make_tf_state("down", "below_all"),
        }
        snap = make_snapshot(states, score=-(ENTRY_SCORE_MIN + 5.0))
        sig = generate_signal(snap)
        assert sig.side == "short"
