"""US Market Pulse reads both the S&P 500 and the NASDAQ Composite (#822 follow-up)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = _load("_dual_regime_policy", "cores/regime_policy.py")
mp = _load("_dual_market_pulse", "cores/market_pulse.py")
U, P, C = rp.UPTREND, rp.UNDER_PRESSURE, rp.CORRECTION


def _bars(closes, start=0):
    return [mp.DailyBar(date=f"2026-{1 + (i + start) // 28:02d}-{1 + (i + start) % 28:02d}", close=c, volume=1e6)
            for i, c in enumerate(closes)]


RISING = [100 + i * .1 for i in range(120)]
CRASHED = RISING[:-5] + [RISING[-6] * .88] * 5          # >=10% below the peak -> CORRECTION


@pytest.mark.parametrize("spx,ndx,want", [
    (C, C, C), (C, U, P), (U, C, P), (C, P, P), (P, C, P),
    (U, U, U), (U, P, P), (P, U, P), (P, P, P), (C, None, C), (U, None, U),
])
def test_combination_table(spx, ndx, want):
    assert rp.combine_us_pulse(spx, ndx) == want


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("US_MARKET_PULSE_INDEX_MODE", raising=False)
    rp._reset_state_cache()
    yield
    rp._reset_state_cache()


def _patched(spx, ndx=None, ndx_error=None):
    ndx_kwargs = {"side_effect": ndx_error} if ndx_error else {"return_value": ndx}
    return (patch.object(rp, "_fetch_us_bars", return_value=spx),
            patch.object(rp, "_fetch_us_nasdaq_bars", **ndx_kwargs),
            patch.object(rp, "_load_root_cores", return_value=mp))


def test_state_needs_both_indexes_for_correction():
    a, b, c = _patched(_bars(CRASHED), _bars(RISING))
    with a, b, c:
        assert rp.get_market_pulse_state("us", use_cache=False) == P
    a, b, c = _patched(_bars(CRASHED), _bars(CRASHED))
    with a, b, c:
        assert rp.get_market_pulse_state("us", use_cache=False) == C


def test_nasdaq_failure_and_rollback_keep_sp500_only(monkeypatch):
    a, b, c = _patched(_bars(CRASHED), ndx_error=RuntimeError("down"))
    with a, b, c:
        assert rp.get_market_pulse_state("us", use_cache=False) == C
    monkeypatch.setenv("US_MARKET_PULSE_INDEX_MODE", "spx")
    a, b, c = _patched(_bars(CRASHED), _bars(RISING))
    with a, b, c:
        assert rp.get_market_pulse_state("us", use_cache=False) == C


def test_detail_reports_combined_state_and_sp500_dd():
    a, b, c = _patched(_bars(CRASHED), _bars(RISING))
    with a, b, c:
        detail = rp.get_market_pulse_detail("us", use_cache=False)
    spx = mp.MarketPulse()
    for bar in _bars(CRASHED):
        spx.feed(bar)
    assert detail.state == P and detail.distribution_days == spx.distribution_days


def test_nasdaq_state_is_carried_to_sp500_sessions_without_a_nasdaq_bar():
    spx = _bars(CRASHED)
    ndx = [b for i, b in enumerate(_bars(CRASHED)) if i != len(CRASHED) - 1]   # last NASDAQ day missing
    states, _ = rp.combined_us_states(spx, ndx, mp.MarketPulse)
    assert states[-1] == C


def test_pilot_window_uses_combined_series(monkeypatch):
    monkeypatch.setenv("PULSE_PILOT_REEXPOSURE", "true")
    a, b, c = _patched(_bars(CRASHED), _bars(RISING))
    with a, b, c:
        # Combined series never reaches CORRECTION -> no correction exit -> no pilot window.
        assert rp.pilot_reexposure_active("us", use_cache=False) is False


def test_kr_is_unchanged():
    with patch.object(rp, "_fetch_kr_bars", return_value=_bars(CRASHED)), \
            patch.object(rp, "_fetch_us_nasdaq_bars", side_effect=AssertionError("not for KR")), \
            patch.object(rp, "_load_root_cores", return_value=mp):
        assert rp.get_market_pulse_state("kr", use_cache=False) == C


def _distribution_bars(n_dd):
    """Rising index with `n_dd` recent distribution days (drop >=0.2% on higher volume, no +5% recovery)."""
    closes, vols = [100 + i * .1 for i in range(80)], [1e6] * 80
    for k in range(n_dd):
        i = 60 + 3 * k
        closes[i] = closes[i - 1] * 0.995
        vols[i] = vols[i - 1] * 1.5
        for j in range(i + 1, 80):
            closes[j] = min(closes[j], closes[i] * 1.02)
    return [mp.DailyBar(date=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", close=c, volume=v)
            for i, (c, v) in enumerate(zip(closes, vols))]


@pytest.mark.parametrize("mode,want", [("dual", 0), ("spx", 5)])
def test_us_distribution_days_need_both_indexes_in_dual_mode(monkeypatch, mode, want):
    """2026-09-29: S&P 500 had 6 distribution days, NASDAQ 0; the S&P count alone
    stepped every US new buy down to sideways. Dual mode now reports the smaller count."""
    spx, ndx = _distribution_bars(5), _distribution_bars(0)  # 5: below the CORRECTION reset
    assert mp.MarketPulse().distribution_days == 0  # sanity: fresh machine
    probe = mp.MarketPulse()
    for bar in spx:
        probe.feed(bar)
    assert probe.distribution_days == 5
    monkeypatch.setenv("US_MARKET_PULSE_INDEX_MODE", mode)
    monkeypatch.setattr(rp, "_fetch_us_bars", lambda DailyBar: spx)
    monkeypatch.setattr(rp, "_fetch_us_nasdaq_bars", lambda DailyBar: ndx)
    rp._DETAIL_CACHE.clear()
    detail = rp.get_market_pulse_detail("us", use_cache=False)
    assert detail is not None and detail.distribution_days == want
