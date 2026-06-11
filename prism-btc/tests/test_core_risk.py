# tests/test_core_risk.py — Unit tests for the E4 operating-risk overlay.
# Pure helper, live-only (not wired into the backtest engine).
from __future__ import annotations

from core.risk import compute_operating_risk


def test_full_risk_at_peak():
    assert compute_operating_risk(equity=10_000.0, peak=10_000.0) == 0.05


def test_full_risk_small_drawdown():
    # 4% DD < 5% threshold → base risk.
    assert compute_operating_risk(equity=9_600.0, peak=10_000.0) == 0.05


def test_reduced_risk_at_threshold():
    # exactly 5% DD → reduced.
    assert compute_operating_risk(equity=9_500.0, peak=10_000.0) == 0.025


def test_reduced_risk_deep_drawdown():
    assert compute_operating_risk(equity=8_000.0, peak=10_000.0) == 0.025


def test_custom_params():
    out = compute_operating_risk(
        equity=900.0, peak=1_000.0,
        base_risk=0.1, dd_threshold=0.05, reduced_risk=0.04,
    )
    assert out == 0.04


def test_nonpositive_peak_returns_base():
    assert compute_operating_risk(equity=0.0, peak=0.0) == 0.05
