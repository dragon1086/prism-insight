"""레짐 적응 하한선(REGIME_MIN_SCORE_FLOOR) 순수 헬퍼 단위테스트.

network 없음. 표 매핑 / max() 동작 / 플래그 off=무보정 / 라벨 관용성 검증.
Run: .venv/bin/python -m pytest tests/test_regime_min_score_floor.py -q
"""

from __future__ import annotations

import pytest

from cores.regime_policy import (
    effective_min_score,
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
    monkeypatch.delenv("REGIME_MIN_SCORE_FLOOR", raising=False)
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


@pytest.mark.parametrize("raw,enabled", [
    (None, False), ("", False), ("false", False), ("0", False), ("no", False),
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
    assert effective_min_score(None, "strong_bear") == 0
