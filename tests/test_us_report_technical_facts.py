"""Same-snapshot arithmetic and real report consumer regression tests, no I/O."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from prism_core.report_technical_facts import (
    build_report_technical_facts,
    extract_report_technical_facts,
    render_report_technical_facts,
)

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location("technical_test_" + Path(path).stem, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame(values):
    return pd.DataFrame({"close": values}, index=pd.bdate_range("2025-01-01", periods=len(values)))


def test_exact_full_window_arithmetic_and_sorted_dates():
    data = frame(np.arange(1.0, 251.0))
    facts = build_report_technical_facts(data.iloc[::-1])
    assert facts == build_report_technical_facts(data)
    assert facts["latest_observed_close"] == 250
    assert facts["latest_row_date"] == data.index[-1].isoformat()
    assert facts["indicators"]["SMA20"] == 240.5
    assert facts["indicators"]["SMA50"] == 225.5
    assert facts["indicators"]["SMA200"] == 150.5
    assert facts["indicators"]["RSI14"] == 100
    macd = data.close.ewm(span=12, adjust=False).mean() - data.close.ewm(span=26, adjust=False).mean()
    assert facts["indicators"]["MACD"] == pytest.approx(macd.iloc[-1])
    assert facts["indicators"]["MACD_SIGNAL"] == pytest.approx(macd.ewm(span=9, adjust=False).mean().iloc[-1])
    assert facts["indicators"]["BB20_UPPER"] == pytest.approx(240.5 + 2 * data.close.tail(20).std())


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), 0, -1, True, np.bool_(True), "250"])
def test_invalid_latest_close_never_promotes_historical_price(value):
    data = frame(list(range(1, 251)) + [value])
    facts = build_report_technical_facts(data)
    assert facts["latest_observed_close"] is None
    assert facts["last_valid_close"] == 250
    assert facts["last_valid_close_date"] == data.index[-2].isoformat()
    assert facts["indicator_asof_date"] == data.index[-2].isoformat()
    assert facts["indicators"]["SMA50"] == 225.5
    assert facts["status"] == "partial"
    assert facts["reason"] == "latest_close_missing_or_invalid_historical_indicators_only"


def test_audit_pattern_terminal_nan_preserves_dated_historical_sma():
    # Synthetic monotonic fixture matching the audited 50-close mean, not DGX data.
    data = frame(list(np.arange(200.0) + 58.8818) + [np.nan, np.nan])
    facts = build_report_technical_facts(data)
    assert facts["indicators"]["SMA50"] == pytest.approx(233.3818)
    assert facts["latest_observed_close"] is None
    assert facts["indicator_asof_date"] == data.index[-3].isoformat()
    assert facts["latest_row_date"] == data.index[-1].isoformat()
    rendered = render_report_technical_facts(data)
    assert "| SMA50 | 233.3818 |" in rendered
    assert "과거 지표 / Historical indicators only" in rendered
    assert "Latest observed Close (USD): N/A" in rendered
    data.iloc[-32, 0] = np.nan
    facts = build_report_technical_facts(data)
    assert facts["indicators"]["SMA50"] is None
    assert facts["indicators"]["SMA20"] is not None


def test_all_invalid_closes_have_no_historical_asof():
    facts = build_report_technical_facts(frame([None, 0, np.nan]))
    assert facts["indicator_asof_date"] is None
    assert all(v is None for v in facts["indicators"].values())


def test_missing_historical_value_not_dropped_from_window():
    data = frame(np.arange(1.0, 251.0))
    data.iloc[-30, 0] = np.nan
    values = build_report_technical_facts(data)["indicators"]
    assert values["SMA20"] == 240.5
    assert values["SMA50"] is None and values["SMA200"] is None
    assert values["MACD"] is not None and values["MACD_SIGNAL"] is None


def test_true_zero_indicators_not_missing_and_flat_rsi_undefined():
    flat = build_report_technical_facts(frame([100.0] * 250))["indicators"]
    assert flat["MACD"] == flat["MACD_SIGNAL"] == flat["MACD_HISTOGRAM"] == 0
    assert flat["RSI14"] is None
    declining = build_report_technical_facts(frame(range(250, 0, -1)))["indicators"]
    assert declining["RSI14"] == 0
    assert "| MACD | 0.0000 |" in render_report_technical_facts(frame([100.0] * 250))


@pytest.mark.parametrize("count", [1, 9, 14, 19, 25, 33, 49, 199])
def test_insufficient_history_is_explicit(count):
    values = build_report_technical_facts(frame(range(1, count + 1)))["indicators"]
    for name, required in (("SMA10", 10), ("SMA20", 20), ("SMA50", 50), ("SMA200", 200),
                           ("RSI14", 15), ("MACD", 26), ("MACD_SIGNAL", 34), ("BB20_UPPER", 20)):
        assert (values[name] is None) == (count < required)


def test_ambiguous_dates_and_shapes_fail_closed():
    data = frame(range(1, 60))
    duplicate = pd.concat([data, data.tail(1)])
    assert build_report_technical_facts(duplicate)["reason"] == "ambiguous_dates"
    assert build_report_technical_facts(data.reset_index())["status"] == "unavailable"
    data.columns = pd.MultiIndex.from_tuples([("Close", "TEST")])
    assert build_report_technical_facts(data)["status"] == "unavailable"


@pytest.mark.parametrize("language", ["ko", "en"])
def test_exact_fetch_and_real_price_agent_consumer(monkeypatch, language):
    prefetch = load("prism-us/cores/data_prefetch.py")
    agent_module = load("prism-us/cores/agents/stock_price_agents.py")
    data = frame(np.arange(1.0, 251.0))
    original = data.copy(deep=True)
    calls = []

    def get_ohlcv(*args, **kwargs):
        calls.append((args, kwargs))
        return data

    monkeypatch.setattr(prefetch, "_get_us_data_client", lambda: SimpleNamespace(get_ohlcv=get_ohlcv))
    text = prefetch.prefetch_us_stock_ohlcv("TEST")
    assert len(calls) == 1
    pd.testing.assert_frame_equal(data, original)
    canonical = extract_report_technical_facts(text)
    assert "| SMA50 | 225.5000 |" in canonical and "OHLCV: TEST" not in canonical
    assert extract_report_technical_facts("missing") == ""
    agent = agent_module.create_us_price_volume_analysis_agent(
        "Example", "TEST", "20260923", "20250923", 1, language, text)
    assert canonical in agent.instruction
    assert "반올림만 허용" in agent.instruction if language == "ko" else "display rounding only" in agent.instruction
    assert agent.server_names == []
