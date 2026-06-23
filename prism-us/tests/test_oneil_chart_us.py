"""
US O'Neil chart tests (Phase 6 S6) — mocked, no network.

These tests exercise ``prism-us/cores/stock_chart.py`` under the US ``cores``
shadowing scheme. They must run in a SEPARATE pytest session from the KR tests
because both define a top-level ``cores`` package and ``sys.path`` is mutated so
``cores`` resolves to ``prism-us/cores``.

Run (from repo root) with prism-us as the import root::

    cd prism-us && python -m pytest tests/test_oneil_chart_us.py -v

or::

    python -m pytest prism-us/tests/test_oneil_chart_us.py -v -p no:cacheprovider

The USDataClient is fully mocked (synthetic OHLCV / index frames), so no
yfinance/network access is required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure prism-us is the import root so `cores` shadows to prism-us/cores.
_PRISM_US = Path(__file__).resolve().parent.parent
if str(_PRISM_US) not in sys.path:
    sys.path.insert(0, str(_PRISM_US))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from cores import stock_chart as us_chart  # noqa: E402


def _make_ohlcv(n: int = 260, start: float = 100.0) -> pd.DataFrame:
    """Build a synthetic lowercased-column OHLCV frame like USDataClient returns."""
    dates = pd.date_range(end="2026-06-20", periods=n, freq="B")
    close = start + np.linspace(0, 40, n) + np.sin(np.linspace(0, 12, n)) * 3
    df = pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 1_000_000, dtype="int64"),
        },
        index=dates,
    )
    return df


class _FakeClient:
    """Mock USDataClient: returns synthetic OHLCV for stock + index."""

    def __init__(self, index_calls=None):
        self.index_calls = index_calls if index_calls is not None else []

    def get_company_info(self, ticker):
        return {"name": f"{ticker} Inc."}

    def get_ohlcv(self, ticker, period="1y", interval="1d"):
        return _make_ohlcv()

    def get_index_data(self, index="^GSPC", period="1y"):
        self.index_calls.append(index)
        return _make_ohlcv(start=4000.0)


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


def _price_axis(fig):
    assert fig is not None, "chart function returned None"
    assert getattr(fig, "axes", None), "figure has no axes"
    return fig.axes[0]


def test_daily_chart_builds_fig_with_price_axis(monkeypatch):
    monkeypatch.setattr(us_chart, "_get_client", lambda: _FakeClient())
    fig = us_chart.create_oneil_daily_chart("AAPL", company_name="Apple Inc.")
    ax = _price_axis(fig)
    # axes[0] is the price axis: its y-range should cover our synthetic prices.
    ymin, ymax = ax.get_ylim()
    assert ymax > ymin
    assert ymax > 100  # prices start at ~100 and rise


def test_weekly_chart_builds_fig_with_price_axis(monkeypatch):
    monkeypatch.setattr(us_chart, "_get_client", lambda: _FakeClient())
    fig = us_chart.create_oneil_weekly_chart("AAPL", company_name="Apple Inc.")
    ax = _price_axis(fig)
    ymin, ymax = ax.get_ylim()
    assert ymax > ymin


def test_rs_index_selection_nasdaq_vs_sp500():
    # Clearly-Nasdaq name -> ^IXIC; everything else -> ^GSPC.
    assert us_chart._detect_index_ticker("AAPL") == "^IXIC"
    assert us_chart._detect_index_ticker("NVDA") == "^IXIC"
    assert us_chart._detect_index_ticker("JPM") == "^GSPC"
    assert us_chart._detect_index_ticker("XYZNOTREAL") == "^GSPC"


def test_daily_chart_uses_detected_index(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        us_chart, "_get_client", lambda: _FakeClient(index_calls=calls)
    )
    us_chart.create_oneil_daily_chart("AAPL")
    assert "^IXIC" in calls  # AAPL is a Nasdaq name


def test_empty_rs_series_does_not_crash():
    """An empty/degenerate RS series must not raise in _add_rs_panel (guard)."""
    fig = plt.figure()
    # Empty series
    us_chart._add_rs_panel(fig, pd.Series(dtype="float64"), "S&P500")
    # All-NaN series
    us_chart._add_rs_panel(fig, pd.Series([np.nan, np.nan]), "S&P500")
    # None
    us_chart._add_rs_panel(fig, None, "S&P500")
    # Single-point series (new-high array length 1) — exercises is_new_high[-1].
    us_chart._add_rs_panel(fig, pd.Series([100.0]), "S&P500")


def test_compute_rs_line_basic():
    stock = pd.Series([100.0, 110.0, 121.0])
    index = pd.Series([100.0, 100.0, 100.0])
    rs = us_chart._compute_rs_line(stock, index)
    assert rs is not None
    assert abs(rs.iloc[0] - 100.0) < 1e-9  # normalized to 100 at start
    assert rs.iloc[-1] > 100.0  # stock outperformed flat index


def test_chart_returns_none_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(us_chart, "_get_client", lambda: None)
    assert us_chart.create_oneil_daily_chart("AAPL") is None
    assert us_chart.create_oneil_weekly_chart("AAPL") is None


def test_chart_returns_none_on_empty_data(monkeypatch):
    class _EmptyClient(_FakeClient):
        def get_ohlcv(self, ticker, period="1y", interval="1d"):
            return pd.DataFrame()

    monkeypatch.setattr(us_chart, "_get_client", lambda: _EmptyClient())
    assert us_chart.create_oneil_daily_chart("AAPL") is None


def test_chart_renders_without_rs_when_index_missing(monkeypatch):
    """RS omitted (index fetch returns None) must still yield a price-axis fig."""
    class _NoIndexClient(_FakeClient):
        def get_index_data(self, index="^GSPC", period="1y"):
            return pd.DataFrame()

    monkeypatch.setattr(us_chart, "_get_client", lambda: _NoIndexClient())
    fig = us_chart.create_oneil_daily_chart("AAPL")
    ax = _price_axis(fig)
    assert ax.get_ylim()[1] > ax.get_ylim()[0]
