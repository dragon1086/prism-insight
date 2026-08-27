"""레짐 적응 하한선(REGIME_MIN_SCORE_FLOOR) 순수 헬퍼 단위테스트.

 network 없음. 표 매핑 / max() 동작 / 기본 ON·긴급 off / 라벨 관용성 검증.
Run: .venv/bin/python -m pytest tests/test_regime_min_score_floor.py -q
"""

from __future__ import annotations

import pytest

from cores.regime_policy import (
    configured_entry_amount,
    effective_min_score,
    is_rebound_pilot_entry,
    min_score_floor,
    regime_min_score_floor_enabled,
)


# --------------------------------------------------------------------------- #
# min_score_floor — regime -> floor mapping                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("regime,expected", [
    ("strong_bear", 9),
    ("moderate_bear", 8),
    ("sideways", 8),
    ("moderate_bull", 0),
    ("strong_bull", 0),
    ("unknown", 0),
    (None, 0),
    ("", 0),
    ("bogus_regime", 0),          # unmapped -> 0
    ("STRONG_BEAR", 9),           # case-insensitive
    ("strong_bear (하락 추세)", 9),  # decorated label -> leading token
    ("  sideways  ", 8),          # whitespace trimmed
])
def test_min_score_floor_mapping(regime, expected):
    assert min_score_floor(regime) == expected


# --------------------------------------------------------------------------- #
# effective_min_score — flag gating + max() behavior                          #
# --------------------------------------------------------------------------- #
def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "false")
    assert not regime_min_score_floor_enabled()
    # Flag off: LLM value returned unchanged even in the harshest regime.
    assert effective_min_score(3, "strong_bear") == 3
    assert effective_min_score(0, "sideways") == 0
    assert effective_min_score(10, "strong_bear") == 10


def test_flag_on_applies_max(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    assert regime_min_score_floor_enabled()
    # Floor raises when LLM value is below the regime floor.
    assert effective_min_score(3, "strong_bear") == 9   # floor 9 wins
    assert effective_min_score(3, "sideways") == 8      # floor 8 wins
    # LLM value already >= floor -> unchanged (never lowers).
    assert effective_min_score(9, "strong_bear") == 9
    assert effective_min_score(10, "strong_bear") == 10
    # Bullish / unknown regimes have floor 0 -> LLM value passes through.
    assert effective_min_score(5, "strong_bull") == 5
    assert effective_min_score(2, "unknown") == 2
    assert effective_min_score(4, None) == 4


def test_sideways_uptrend_relaxes_floor_to_seven(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")

    assert min_score_floor("sideways", "UPTREND") == 7
    assert effective_min_score(5, "sideways", "UPTREND") == 7
    assert effective_min_score(8, "sideways", "UPTREND") == 8


@pytest.mark.parametrize("pulse_state", [None, "", "UNDER_PRESSURE", "CORRECTION"])
def test_sideways_without_uptrend_keeps_floor_eight(monkeypatch, pulse_state):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")

    assert min_score_floor("sideways", pulse_state) == 8
    assert effective_min_score(5, "sideways", pulse_state) == 8


@pytest.mark.parametrize("decision", ["Enter", "entry", " ENTER "])
def test_score_six_enter_is_half_size_rebound_pilot(monkeypatch, decision):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")

    assert is_rebound_pilot_entry(6, 5, "sideways", "UPTREND", decision)


@pytest.mark.parametrize(
    "buy_score,llm_min_score,regime,pulse_state,decision",
    [
        (7, 5, "sideways", "UPTREND", "Enter"),
        (6, 7, "sideways", "UPTREND", "Enter"),
        (6, 5, "moderate_bear", "UPTREND", "Enter"),
        (6, 5, "sideways", "UNDER_PRESSURE", "Enter"),
        (6, 5, "sideways", "UPTREND", "Skip"),
    ],
)
def test_rebound_pilot_remains_narrow(
    monkeypatch, buy_score, llm_min_score, regime, pulse_state, decision
):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")

    assert not is_rebound_pilot_entry(
        buy_score, llm_min_score, regime, pulse_state, decision
    )


def test_rebound_pilot_is_disabled_with_floor_flag(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "false")

    assert not is_rebound_pilot_entry(6, 5, "sideways", "UPTREND", "Enter")


def test_configured_entry_amount_scales_by_market():
    account = {"buy_amount_krw": 1_000_000, "buy_amount_usd": "2000"}

    assert configured_entry_amount(account, "kr", 0.5) == 500_000
    assert configured_entry_amount(account, "us", 0.5) == 1000.0
    assert configured_entry_amount(account, "kr", 1.0) is None
    assert configured_entry_amount({}, "us", 0.5) is None


@pytest.mark.parametrize("raw,enabled", [
    (None, True), ("", False), ("false", False), ("0", False), ("no", False),
    ("off", False), ("bogus", False),
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("  On  ", True),
])
def test_flag_parsing(monkeypatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("REGIME_MIN_SCORE_FLOOR", raising=False)
    else:
        monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", raw)
    assert regime_min_score_floor_enabled() is enabled


def test_effective_handles_non_int_llm(monkeypatch):
    monkeypatch.setenv("REGIME_MIN_SCORE_FLOOR", "true")
    assert effective_min_score(None, "strong_bear") == 9
    assert effective_min_score("bad", "sideways") == 8
    monkeypatch.delenv("REGIME_MIN_SCORE_FLOOR", raising=False)
    assert effective_min_score(None, "strong_bear") == 9
