"""Unit tests for cores.regime_policy — pure batch-policy + env-mode parsing.

Pure logic, zero network. Table-driven over every market x mode x state combo.
Run with:
    .venv/bin/python -m pytest tests/test_regime_policy.py -q
"""

from __future__ import annotations

import pytest

from cores.regime_policy import (
    CORRECTION,
    UNDER_PRESSURE,
    UPTREND,
    BatchPolicy,
    decide_batch_policy,
    market_pulse_mode,
)


# --------------------------------------------------------------------------- #
# decide_batch_policy — table-driven (market, mode, state) -> expected run_batch #
# --------------------------------------------------------------------------- #
# §7 Rev.3/Rev.4: CORRECTION reduces to one daily window.
#   KR: morning rests, afternoon runs.
#   US: midday runs, morning & afternoon rest.
# §7 Rev.5: UNDER_PRESSURE partial brake — US morning rests (midday/afternoon run),
#   KR unchanged (all run). UPTREND / None(unknown) run everything (fail-open).
_CASES = [
    # --- KR, CORRECTION (Rev.4: 종가확인 오후 유지, 아침 갭페이드 회피) ---
    ("kr", "morning", CORRECTION, False),
    ("kr", "afternoon", CORRECTION, True),
    ("kr", "both", CORRECTION, True),         # unknown mode fails open -> run
    # --- KR, non-CORRECTION ---
    ("kr", "morning", UPTREND, True),
    ("kr", "afternoon", UPTREND, True),
    ("kr", "morning", UNDER_PRESSURE, True),
    ("kr", "afternoon", UNDER_PRESSURE, True),
    ("kr", "morning", None, True),
    ("kr", "afternoon", None, True),
    # --- US, CORRECTION ---
    ("us", "morning", CORRECTION, False),
    ("us", "midday", CORRECTION, True),
    ("us", "afternoon", CORRECTION, False),
    ("us", "both", CORRECTION, True),         # unknown mode fails open -> run
    # --- US, non-CORRECTION ---
    ("us", "morning", UPTREND, True),
    ("us", "midday", UPTREND, True),
    ("us", "afternoon", UPTREND, True),
    # Rev.5: US morning rests under UNDER_PRESSURE; midday/afternoon run.
    ("us", "morning", UNDER_PRESSURE, False),
    ("us", "midday", UNDER_PRESSURE, True),
    ("us", "afternoon", UNDER_PRESSURE, True),
    ("us", "both", UNDER_PRESSURE, True),      # unknown mode fails open -> run
    ("us", "morning", None, True),
    ("us", "midday", None, True),
    ("us", "afternoon", None, True),
]


@pytest.mark.parametrize("market,mode,state,expected", _CASES)
def test_decide_batch_policy_table(market, mode, state, expected):
    pol = decide_batch_policy(market, mode, state)
    assert isinstance(pol, BatchPolicy)
    assert pol.run_batch is expected
    assert pol.pulse_state == state
    assert isinstance(pol.reason, str) and pol.reason


def test_only_documented_batches_rest():
    """Only the documented (state, market, mode) combos rest; everything else runs.

    Rev.5: rest set = {CORRECTION: kr-morning, us-morning, us-afternoon;
                       UNDER_PRESSURE: us-morning}. All other combos run.
    """
    rest = {
        (CORRECTION, "kr", "morning"),
        (CORRECTION, "us", "morning"),
        (CORRECTION, "us", "afternoon"),
        (UNDER_PRESSURE, "us", "morning"),
    }
    for market in ("kr", "us"):
        for mode in ("morning", "midday", "afternoon", "both"):
            for state in (UPTREND, UNDER_PRESSURE, CORRECTION, None):
                pol = decide_batch_policy(market, mode, state)
                expected_run = (state, market, mode) not in rest
                assert pol.run_batch is expected_run, (market, mode, state)


def test_under_pressure_rev5_partial_brake():
    """Rev.5: US morning rests under UNDER_PRESSURE; US midday/afternoon and all KR run."""
    # US: only morning rests.
    assert decide_batch_policy("us", "morning", UNDER_PRESSURE).run_batch is False
    assert decide_batch_policy("us", "midday", UNDER_PRESSURE).run_batch is True
    assert decide_batch_policy("us", "afternoon", UNDER_PRESSURE).run_batch is True
    # KR: unchanged — everything runs under UNDER_PRESSURE.
    assert decide_batch_policy("kr", "morning", UNDER_PRESSURE).run_batch is True
    assert decide_batch_policy("kr", "afternoon", UNDER_PRESSURE).run_batch is True
    # None (fail-open) runs everything on both markets.
    for market in ("kr", "us"):
        for mode in ("morning", "midday", "afternoon"):
            assert decide_batch_policy(market, mode, None).run_batch is True


def test_correction_rest_sets():
    """Exactly the documented batches rest during CORRECTION."""
    assert decide_batch_policy("kr", "morning", CORRECTION).run_batch is False
    assert decide_batch_policy("kr", "afternoon", CORRECTION).run_batch is True
    assert decide_batch_policy("us", "morning", CORRECTION).run_batch is False
    assert decide_batch_policy("us", "afternoon", CORRECTION).run_batch is False
    assert decide_batch_policy("us", "midday", CORRECTION).run_batch is True


def test_decide_is_case_insensitive():
    assert decide_batch_policy("KR", "MORNING", CORRECTION).run_batch is False
    assert decide_batch_policy("US", "Midday", CORRECTION).run_batch is True


def test_decide_fail_open_on_none_state():
    """None (unknown / fail-open) state runs every batch on both markets."""
    for market in ("kr", "us"):
        for mode in ("morning", "midday", "afternoon"):
            assert decide_batch_policy(market, mode, None).run_batch is True


def test_decide_unknown_market_fails_open():
    """An unknown market has no rest-batches -> runs even in CORRECTION."""
    assert decide_batch_policy("jp", "morning", CORRECTION).run_batch is True
    assert decide_batch_policy("", "afternoon", CORRECTION).run_batch is True


def test_batchpolicy_is_frozen():
    pol = decide_batch_policy("kr", "morning", UPTREND)
    with pytest.raises(Exception):
        pol.run_batch = False  # frozen dataclass -> FrozenInstanceError


# --------------------------------------------------------------------------- #
# market_pulse_mode — env parsing                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected", [
    (None, "shadow"),          # unset -> default
    ("", "shadow"),            # empty -> default
    ("shadow", "shadow"),
    ("live", "live"),
    ("off", "off"),
    ("SHADOW", "shadow"),      # case-insensitive
    ("  live  ", "live"),      # whitespace trimmed
    ("bogus", "shadow"),       # unknown -> safe default
    ("Live", "live"),
])
def test_market_pulse_mode_parsing(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MARKET_PULSE_MODE", raising=False)
    else:
        monkeypatch.setenv("MARKET_PULSE_MODE", raw)
    assert market_pulse_mode() == expected


def test_market_pulse_mode_returns_valid_value_only():
    """Whatever the env, the returned value is always one of the 3 valid modes."""
    import os
    assert market_pulse_mode() in ("shadow", "live", "off")
    # (does not mutate os.environ here; just a sanity invariant)
    assert isinstance(os.getenv("PATH"), str)
