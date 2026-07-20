"""Regression tests for issue #448: distribution_days count in KR buy-decision prompt.

TDD protocol: written BEFORE the fix. Pre-fix: all tests fail.
Post-fix: all tests pass.

Run with:
    python3 -m pytest tests/test_issue_448_distribution_days_prompt.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Stub unavailable modules so stock_tracking_agent can be imported in CI/local
# (mcp_agent, Crypto/pycryptodome, etc. are not installed in dev/test environments).
# ---------------------------------------------------------------------------
for _n in [
    "mcp_agent",
    "mcp_agent.app",
    "mcp_agent.workflows",
    "mcp_agent.workflows.llm",
    "mcp_agent.workflows.llm.augmented_llm",
    "cores.llm",
    "cores.llm.openai_responses_llm",
    "cores.agents.trading_agents",
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "Crypto.Util",
    "Crypto.Util.Padding",
    "trading.kis_auth",  # reads YAML config at import time
    "seaborn",           # required by cores.stock_chart
    "cores.stock_chart", # requires seaborn; imported lazily inside _get_trend_facts
]:
    sys.modules.setdefault(_n, MagicMock())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n_flat: int, n_dd: int, DailyBar):
    """Synthetic bars: n_flat flat sessions then n_dd distribution days.

    Distribution day = close -1% on rising volume (meets -0.2% threshold).
    """
    bars = []
    close = 100.0
    vol = 1_000.0
    for i in range(n_flat):
        bars.append(DailyBar(date=f"2023-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                             close=close, volume=vol))
    for j in range(n_dd):
        close = round(close * 0.99, 6)  # -1% -> distribution day
        vol += 100.0
        bars.append(DailyBar(date=f"2024-{(j // 28) + 1:02d}-{(j % 28) + 1:02d}",
                             close=close, volume=vol))
    return bars


def _fake_ohlcv(n: int = 70) -> pd.DataFrame:
    """Minimal DataFrame mimicking get_market_ohlcv_by_date output."""
    dates = pd.date_range("2026-01-01", periods=n)
    return pd.DataFrame({
        "Close": [100.0 + i * 0.5 for i in range(n)],
        "Open":  [99.0  + i * 0.5 for i in range(n)],
        "High":  [101.0 + i * 0.5 for i in range(n)],
        "Low":   [98.0  + i * 0.5 for i in range(n)],
        "Volume": [1_000_000] * n,
    }, index=dates)


# ---------------------------------------------------------------------------
# Part A: get_market_pulse_detail — new function contract
# ---------------------------------------------------------------------------

def test_get_market_pulse_detail_exists():
    """FAILS pre-fix: get_market_pulse_detail does not exist yet."""
    import cores.regime_policy as rp
    assert hasattr(rp, "get_market_pulse_detail"), (
        "get_market_pulse_detail not found in cores.regime_policy"
    )


def test_market_pulse_detail_dataclass_exists():
    """FAILS pre-fix: MarketPulseDetail dataclass does not exist yet."""
    import cores.regime_policy as rp
    assert hasattr(rp, "MarketPulseDetail"), (
        "MarketPulseDetail not found in cores.regime_policy"
    )


def test_get_market_pulse_detail_returns_state_and_dd_and_window():
    """FAILS pre-fix: function doesn't exist.
    Post-fix: returns MarketPulseDetail with state/distribution_days/window.
    """
    from cores.market_pulse import DailyBar
    import cores.regime_policy as rp

    rp._reset_state_cache()
    bars = _make_bars(n_flat=60, n_dd=3, DailyBar=DailyBar)

    with patch.object(rp, "_fetch_kr_bars", return_value=bars):
        detail = rp.get_market_pulse_detail("kr", use_cache=False)

    assert detail is not None, "get_market_pulse_detail returned None for valid bars"
    assert isinstance(detail.state, str), f"state should be str, got {type(detail.state)}"
    assert isinstance(detail.distribution_days, int), (
        f"distribution_days should be int, got {type(detail.distribution_days)}"
    )
    assert detail.distribution_days >= 0
    assert isinstance(detail.window, int), (
        f"window should be int, got {type(detail.window)}"
    )
    assert detail.window > 0


def test_get_market_pulse_detail_fail_open_returns_none():
    """FAILS pre-fix (fn doesn't exist). Post-fix: exception -> None, never raises."""
    import cores.regime_policy as rp

    rp._reset_state_cache()

    with patch.object(rp, "_fetch_kr_bars", side_effect=RuntimeError("simulated network failure")):
        detail = rp.get_market_pulse_detail("kr", use_cache=False)

    assert detail is None, "fail-open: should return None on exception, not raise"


def test_get_market_pulse_state_contract_unchanged():
    """Pre-fix: passes (fn exists). Post-fix: still passes (contract unchanged)."""
    from cores.market_pulse import DailyBar
    import cores.regime_policy as rp

    rp._reset_state_cache()
    bars = _make_bars(n_flat=60, n_dd=0, DailyBar=DailyBar)

    with patch.object(rp, "_fetch_kr_bars", return_value=bars):
        state = rp.get_market_pulse_state("kr", use_cache=False)

    # State is str or None — contract unchanged
    assert state is None or isinstance(state, str), (
        f"get_market_pulse_state contract broken: got {type(state)}"
    )


def test_reset_state_cache_clears_detail_cache():
    """FAILS pre-fix if _DETAIL_CACHE doesn't exist or isn't cleared."""
    import cores.regime_policy as rp
    assert hasattr(rp, "_DETAIL_CACHE"), "_DETAIL_CACHE module-level dict missing"
    # Verify reset clears it (should not raise)
    rp._reset_state_cache()
    assert rp._DETAIL_CACHE == {}, "_DETAIL_CACHE not cleared by _reset_state_cache()"


# ---------------------------------------------------------------------------
# Part B: _get_trend_facts — distribution_days number in prompt output
# ---------------------------------------------------------------------------

def _configure_stock_chart_mock(fake_ohlcv):
    """Configure the cores.stock_chart MagicMock stub with realistic return values."""
    sc = sys.modules["cores.stock_chart"]
    sc.get_market_ohlcv_by_date.return_value = fake_ohlcv
    sc.get_index_ohlcv_by_date.return_value = None
    sc._detect_index_ticker.return_value = "^KS11"
    return sc


def test_trend_facts_contains_distribution_days_number(tmp_path):
    """FAILS pre-fix: _get_trend_facts output lacks distribution_days count.
    Post-fix: output includes the numeric dd count.
    """
    import cores.regime_policy as rp
    if not hasattr(rp, "MarketPulseDetail"):
        pytest.fail("MarketPulseDetail missing from cores.regime_policy (pre-fix)")

    fake_detail = rp.MarketPulseDetail(state="UPTREND", distribution_days=3, window=25)
    fake_ohlcv = _fake_ohlcv(70)
    _configure_stock_chart_mock(fake_ohlcv)

    from stock_tracking_agent import StockTrackingAgent
    db_path = str(tmp_path / "test_trend_kr.sqlite")
    agent = StockTrackingAgent(db_path=db_path)

    with patch("cores.regime_policy.get_market_pulse_detail", return_value=fake_detail):
        result = agent._get_trend_facts("005930")

    assert result, "trend_facts should not be empty"
    assert "3" in result, (
        f"distribution_days count (3) not found in trend_facts output.\n"
        f"Output:\n{result}"
    )
    assert "분산일" in result or "distribution days" in result, (
        f"distribution_days label not found in trend_facts.\nOutput:\n{result}"
    )


def test_trend_facts_omits_market_pulse_line_when_detail_none(tmp_path):
    """FAILS pre-fix (get_market_pulse_detail doesn't exist, so AttributeError on patch).
    Post-fix: when detail=None, Market Pulse line is omitted (fail-open).
    """
    fake_ohlcv = _fake_ohlcv(70)
    _configure_stock_chart_mock(fake_ohlcv)

    from stock_tracking_agent import StockTrackingAgent
    db_path = str(tmp_path / "test_trend_kr_none.sqlite")
    agent = StockTrackingAgent(db_path=db_path)

    with patch("cores.regime_policy.get_market_pulse_detail", return_value=None):
        result = agent._get_trend_facts("005930")

    assert result, "trend_facts should not be empty (fail-open on None detail)"
    assert "Market Pulse:" not in result, (
        f"Market Pulse line should be omitted when detail=None.\nOutput:\n{result}"
    )
