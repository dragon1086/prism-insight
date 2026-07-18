"""
Regression tests for the bottom-up screening triggers' change-rate reporting.

Bug: trigger_macro_sector_leader and trigger_contrarian_value computed the
daily change into a "DailyChange" column, but the batch dispatch consumer only
reads "prev_day_change_rate". As a result these two bottom-up triggers reported
a 0% change rate (e.g. the SK hynix / 000660 case), unlike the top-down triggers
which populate "prev_day_change_rate" directly.

Fix: both triggers now mirror DailyChange into prev_day_change_rate.
"""
import pandas as pd
import pytest

import trigger_batch
from trigger_batch import trigger_macro_sector_leader, trigger_contrarian_value


def _expected_change(close, prev_close):
    return (close - prev_close) / prev_close * 100


def test_macro_sector_leader_sets_prev_day_change_rate():
    snapshot = pd.DataFrame(
        {
            "Open": [195000, 79000],
            "Close": [200000, 80000],
            "Amount": [5e11, 4e11],
            "Volume": [1_000_000, 1_000_000],
        },
        index=["000660", "005930"],
    )
    prev_snapshot = pd.DataFrame(
        {"Close": [194000, 78000]},
        index=["000660", "005930"],
    )
    macro_context = {
        "leading_sectors": [{"sector": "반도체", "confidence": 0.8}],
        "sector_map": {"000660": "반도체", "005930": "반도체"},
    }

    result = trigger_macro_sector_leader(
        "20260717", snapshot, prev_snapshot, macro_context=macro_context
    )

    assert not result.empty
    assert "prev_day_change_rate" in result.columns
    # Column must equal the computed DailyChange and be non-zero (the bug reported 0).
    for ticker in result.index:
        expected = _expected_change(
            snapshot.loc[ticker, "Close"], prev_snapshot.loc[ticker, "Close"]
        )
        assert result.loc[ticker, "prev_day_change_rate"] == pytest.approx(expected)
        assert result.loc[ticker, "prev_day_change_rate"] != 0


def test_contrarian_value_sets_prev_day_change_rate(monkeypatch):
    # Rising stock today (Close > Open), deep drawdown vs 52w high, profitable.
    snapshot = pd.DataFrame(
        {
            "Open": [190000],
            "Close": [200000],
            "Amount": [5e11],
            "Volume": [1_000_000],
        },
        index=["000660"],
    )
    prev_snapshot = pd.DataFrame({"Close": [194000]}, index=["000660"])

    # 52-week high => -25% drawdown (inside the -40%..-15% band).
    high_52w = 200000 / 0.75
    fake_hist = pd.DataFrame({"High": [high_52w, high_52w * 0.9]})
    fake_fund = pd.DataFrame({"PER": [10.0], "PBR": [1.0]})

    # trigger_contrarian_value does `from krx_data_client import ...` at call time,
    # so patching the module attributes is enough.
    import krx_data_client
    monkeypatch.setattr(krx_data_client, "get_market_ohlcv_by_date", lambda *a, **k: fake_hist)
    monkeypatch.setattr(krx_data_client, "get_market_fundamental_by_date", lambda *a, **k: fake_fund)

    result = trigger_contrarian_value("20260717", snapshot, prev_snapshot)

    assert not result.empty
    assert "prev_day_change_rate" in result.columns
    expected = _expected_change(200000, 194000)
    assert result.loc["000660", "prev_day_change_rate"] == pytest.approx(expected)
    assert result.loc["000660", "prev_day_change_rate"] != 0
