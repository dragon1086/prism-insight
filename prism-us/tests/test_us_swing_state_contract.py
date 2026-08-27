"""US structural regime stays authoritative while swing context is additive."""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import _compute_us_regime  # noqa: E402


def _frame(values):
    index = pd.date_range("2025-01-01", periods=len(values), freq="B")
    close = np.asarray(values, dtype=float)
    return pd.DataFrame(
        {
            "Open": close * 0.999,
            "High": close * 1.005,
            "Low": close * 0.995,
            "Close": close,
            "Volume": np.arange(len(close), dtype=float) + 1_000_000,
        },
        index=index,
    )


def test_strong_structural_bull_can_report_swing_consolidation():
    base = list(np.linspace(3000, 6000, 210))
    # Strong first half of the 20-day window keeps 4-week return above 3%,
    # while the last ten sessions cool off for both S&P and Nasdaq.
    tail = [6050, 6120, 6200, 6280, 6360, 6440, 6520, 6600, 6680, 6760,
            6740, 6720, 6700, 6680, 6660, 6640, 6620, 6600, 6580, 6560]
    sp500 = _frame(base + tail)
    nasdaq = _frame([value * 3.0 for value in base + tail])
    vix = _frame([15.0] * len(sp500))

    result = _compute_us_regime(sp500, nasdaq, vix)

    assert result["market_regime"] == "strong_bull"
    assert result["primary_trend_regime"] == "strong_bull"
    assert result["effective_entry_regime"] == "strong_bull"
    assert result["swing_state"] == "consolidation"
    assert result["index_summary"]["swing_state"] == "consolidation"


def test_calm_bull_with_short_term_confirmation_reports_trend_up():
    closes = list(np.linspace(3000, 6500, 230))
    sp500 = _frame(closes)
    nasdaq = _frame([value * 3.0 for value in closes])
    vix = _frame([14.0] * len(sp500))

    result = _compute_us_regime(sp500, nasdaq, vix)

    assert result["market_regime"] == "strong_bull"
    assert result["swing_state"] == "trend_up"
