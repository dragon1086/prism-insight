"""Post-FTD 파일럿 재진입 축소매수(PULSE_PILOT_REEXPOSURE) 순수 헬퍼 단위테스트.

network 없음. 전이후 세션수 계산 / 윈도우 판정 / 플래그 off=항상 정상사이즈 검증.
Run: .venv/bin/python -m pytest tests/test_pulse_pilot_reexposure.py -q
"""

from __future__ import annotations

import pytest

from cores.regime_policy import (
    CORRECTION,
    UNDER_PRESSURE,
    UPTREND,
    PULSE_PILOT_FACTOR,
    PULSE_PILOT_WINDOW_SESSIONS,
    _sessions_since_correction_exit,
    is_pilot_window,
    pilot_reexposure_enabled,
)


def test_factor_and_window_constants():
    assert PULSE_PILOT_FACTOR == 0.5
    assert PULSE_PILOT_WINDOW_SESSIONS == 5


# --------------------------------------------------------------------------- #
# _sessions_since_correction_exit — pure transition counting                  #
# --------------------------------------------------------------------------- #
def test_exit_day_is_zero_sessions_ago():
    # CORRECTION ... then first non-CORRECTION session = exit day (ago == 0).
    assert _sessions_since_correction_exit([CORRECTION, CORRECTION, UPTREND]) == 0


def test_counts_sessions_after_exit():
    states = [CORRECTION, CORRECTION, UPTREND, UPTREND, UPTREND]  # exit at idx 2
    assert _sessions_since_correction_exit(states) == 2


def test_exit_to_under_pressure_also_counts():
    # A CORRECTION -> UNDER_PRESSURE transition is still an exit.
    assert _sessions_since_correction_exit([CORRECTION, UNDER_PRESSURE]) == 0


def test_currently_in_correction_returns_none():
    assert _sessions_since_correction_exit([UPTREND, CORRECTION, CORRECTION]) is None


def test_no_transition_returns_none():
    assert _sessions_since_correction_exit([UPTREND, UPTREND, UNDER_PRESSURE]) is None
    assert _sessions_since_correction_exit([]) is None


def test_uses_most_recent_transition():
    # Two episodes: exit#1 (idx1), re-enter, exit#2 (idx4). Latest exit is idx4.
    states = [CORRECTION, UPTREND, CORRECTION, CORRECTION, UPTREND, UPTREND]
    # len 6, exit_idx 4 -> ago = 5 - 4 = 1
    assert _sessions_since_correction_exit(states) == 1


# --------------------------------------------------------------------------- #
# is_pilot_window — flag gating + window boundaries                           #
# --------------------------------------------------------------------------- #
def test_within_window_true_after_false():
    # Within 5 sessions (0..4) -> True; 5+ -> False.
    for ago in range(0, PULSE_PILOT_WINDOW_SESSIONS):
        assert is_pilot_window(ago, flag_on=True) is True, ago
    assert is_pilot_window(PULSE_PILOT_WINDOW_SESSIONS, flag_on=True) is False
    assert is_pilot_window(PULSE_PILOT_WINDOW_SESSIONS + 3, flag_on=True) is False


def test_none_sessions_is_full_size():
    assert is_pilot_window(None, flag_on=True) is False


def test_flag_off_always_full_size():
    # Flag OFF -> never pilot, even squarely inside the window.
    for ago in (0, 1, 4, None):
        assert is_pilot_window(ago, flag_on=False) is False


def test_flag_defaults_to_env(monkeypatch):
    monkeypatch.setenv("PULSE_PILOT_REEXPOSURE", "true")
    assert is_pilot_window(0) is True
    monkeypatch.delenv("PULSE_PILOT_REEXPOSURE", raising=False)
    assert is_pilot_window(0) is False


# --------------------------------------------------------------------------- #
# pilot_reexposure_enabled — env parsing                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,enabled", [
    (None, False), ("", False), ("false", False), ("0", False), ("off", False),
    ("1", True), ("true", True), ("YES", True), ("on", True), ("  On  ", True),
])
def test_enabled_parsing(monkeypatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("PULSE_PILOT_REEXPOSURE", raising=False)
    else:
        monkeypatch.setenv("PULSE_PILOT_REEXPOSURE", raw)
    assert pilot_reexposure_enabled() is enabled
