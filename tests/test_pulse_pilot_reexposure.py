"""Post-FTD 파일럿 재진입 신규진입 스로틀(PULSE_PILOT_REEXPOSURE) 순수 단위테스트.

network 없음. 전이후 세션수 계산 / 윈도우 판정 / 플래그 off=현행 유지 검증.
+ trigger_batch._get_regime_slots 파일럿 신규진입 캡(배치당 1종목) 검증.

파일럿은 '금액'을 절대 건드리지 않는다(all-in/all-out per position 계약). 신규 진입 '수'만
조인다: 배치당 신규 진입 슬롯을 총 1개로 캡(top-down 우선) + 중복매수(피라미딩) 동결.
Run: .venv/bin/python -m pytest tests/test_pulse_pilot_reexposure.py -q
"""

from __future__ import annotations

import pytest

import cores.regime_policy as regime_policy
from cores.regime_policy import (
    CORRECTION,
    UNDER_PRESSURE,
    UPTREND,
    PULSE_PILOT_WINDOW_SESSIONS,
    _sessions_since_correction_exit,
    is_pilot_window,
    pilot_reexposure_enabled,
)
from trigger_batch import _get_regime_slots


def test_window_constant():
    assert PULSE_PILOT_WINDOW_SESSIONS == 5


def test_factor_constant_removed():
    # 금액 기반 축소매수(defect)는 제거됨 — PULSE_PILOT_FACTOR 존재하면 안 된다.
    assert not hasattr(regime_policy, "PULSE_PILOT_FACTOR")


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


def test_none_sessions_is_inactive():
    assert is_pilot_window(None, flag_on=True) is False


def test_flag_off_always_inactive():
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


# --------------------------------------------------------------------------- #
# _get_regime_slots — 파일럿 신규진입 캡(배치당 1종목, top-down 우선)               #
# --------------------------------------------------------------------------- #
def test_slots_pilot_off_unchanged(monkeypatch):
    # 파일럿 비활성(윈도우 밖/플래그 off) -> 레짐별 기본 슬롯 그대로.
    monkeypatch.setattr(regime_policy, "pilot_reexposure_active", lambda market, **kw: False)
    assert _get_regime_slots("strong_bull") == (2, 1)
    assert _get_regime_slots("moderate_bull") == (1, 2)
    assert _get_regime_slots("sideways") == (1, 2)
    assert _get_regime_slots("moderate_bear") == (1, 2)
    assert _get_regime_slots("strong_bear") == (0, 3)


def test_slots_pilot_active_caps_to_one(monkeypatch):
    # 파일럿 활성 -> 배치당 신규 진입 총 1종목. top-down >=1 이면 (1,0), 아니면 (0,1).
    monkeypatch.setattr(regime_policy, "pilot_reexposure_active", lambda market, **kw: True)
    assert _get_regime_slots("strong_bull") == (1, 0)      # (2,1) -> (1,0)
    assert _get_regime_slots("moderate_bull") == (1, 0)    # (1,2) -> (1,0)
    assert _get_regime_slots("sideways") == (1, 0)         # (1,2) -> (1,0)
    assert _get_regime_slots("moderate_bear") == (1, 0)    # (1,2) -> (1,0)
    assert _get_regime_slots("strong_bear") == (0, 1)      # (0,3) -> (0,1)


def test_slots_pilot_uses_kr_market(monkeypatch):
    # KR trigger_batch 는 'kr' 시장으로 파일럿을 질의해야 한다.
    seen = {}

    def _fake(market, **kw):
        seen["market"] = market
        return True

    monkeypatch.setattr(regime_policy, "pilot_reexposure_active", _fake)
    assert _get_regime_slots("strong_bull") == (1, 0)
    assert seen["market"] == "kr"


def test_slots_pilot_exception_fail_open(monkeypatch):
    # 파일럿 판정 중 예외 -> fail-open: 원래 슬롯 유지.
    def _boom(market, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(regime_policy, "pilot_reexposure_active", _boom)
    assert _get_regime_slots("strong_bull") == (2, 1)
    assert _get_regime_slots("strong_bear") == (0, 3)
