"""US morning Value-to-Cap shortage-only capacity-fill regression tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_us(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_market_cap_loader_uses_fast_info_and_skips_failures():
    _run_us(
        r'''
import os
import sys

sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
from cores import us_surge_detector as detector

class FakeTicker:
    def __init__(self, ticker):
        self.ticker = ticker

    @property
    def fast_info(self):
        if self.ticker == "BAD":
            raise RuntimeError("quote unavailable")
        return {"marketCap": {"AAA": 10_000_000_000, "BBB": 20_000_000_000}[self.ticker]}

detector.yf.Ticker = FakeTicker
result = detector.get_market_cap_df(["AAA", "BAD", "BBB"], max_workers=2)
assert list(result.index) == ["AAA", "BBB"]
assert result.loc["AAA", "MarketCap"] == 10_000_000_000
assert result.loc["BBB", "MarketCap"] == 20_000_000_000
'''
    )


def test_morning_batch_loads_value_to_cap_only_when_primary_pool_is_short():
    _run_us(
        r'''
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
import us_trigger_batch as u

snapshot = pd.DataFrame.from_dict({
    "ORCL": {"Open": 100.0, "High": 104.0, "Low": 99.0, "Close": 103.0, "Volume": 2_000_000, "Amount": 206_000_000.0},
    "INTU": {"Open": 100.0, "High": 106.0, "Low": 99.0, "Close": 105.0, "Volume": 2_000_000, "Amount": 210_000_000.0},
    "SNDK": {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 104.0, "Volume": 2_000_000, "Amount": 208_000_000.0},
    "TOO_SMALL": {"Open": 100.0, "High": 110.0, "Low": 99.0, "Close": 109.0, "Volume": 100_000, "Amount": 10_900_000.0},
}, orient="index")
previous = snapshot.copy()
previous["Close"] = 100.0

u.get_major_tickers = lambda: list(snapshot.index)
u.get_snapshot = lambda *_args, **_kwargs: snapshot
u.get_previous_snapshot = lambda *_args, **_kwargs: (previous, "20260825")
u.get_nearest_business_day = lambda *_args, **_kwargs: "20260826"
u.enhance_dataframe = lambda frame: frame

empty = pd.DataFrame()
gap = snapshot.loc[["ORCL"]].copy()
gap["CompanyName"] = "Oracle"
requested = []

u.trigger_morning_volume_surge = lambda *_args, **_kwargs: empty
u.trigger_morning_gap_up_momentum = lambda *_args, **_kwargs: gap

def market_caps(tickers, **_kwargs):
    requested.extend(tickers)
    return pd.DataFrame({"MarketCap": 50_000_000_000.0}, index=tickers)

def value_trigger(_date, _snapshot, _previous, cap_df, **_kwargs):
    assert cap_df is not None and not cap_df.empty
    result = snapshot.loc[["INTU", "SNDK"]].copy()
    result["CompanyName"] = ["Intuit", "Sandisk"]
    return result

u.get_market_cap_df = market_caps
u.trigger_morning_value_to_cap_ratio = value_trigger
u.select_final_tickers = lambda triggers, **_kwargs: triggers

result = u.run_batch("morning", override_date="20260826")
value = result["Value-to-Cap Ratio Top"]
assert set(requested) == {"ORCL", "INTU", "SNDK"}
assert "TOO_SMALL" not in requested
assert set(value.index) == {"INTU", "SNDK"}
assert value["CapacityFill"].eq(True).all()
assert value["CapacityFillReason"].eq("primary_candidates_below_target").all()
'''
    )


def test_morning_batch_skips_cap_lookup_when_three_primary_candidates_exist():
    _run_us(
        r'''
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
import us_trigger_batch as u

snapshot = pd.DataFrame.from_dict({
    ticker: {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 104.0,
             "Volume": 2_000_000, "Amount": 208_000_000.0}
    for ticker in ("AAA", "BBB", "CCC")
}, orient="index")
previous = snapshot.copy()
previous["Close"] = 100.0
primary = snapshot.copy()
primary["CompanyName"] = primary.index

u.get_major_tickers = lambda: list(snapshot.index)
u.get_snapshot = lambda *_args, **_kwargs: snapshot
u.get_previous_snapshot = lambda *_args, **_kwargs: (previous, "20260825")
u.get_nearest_business_day = lambda *_args, **_kwargs: "20260826"
u.trigger_morning_volume_surge = lambda *_args, **_kwargs: primary
u.trigger_morning_gap_up_momentum = lambda *_args, **_kwargs: pd.DataFrame()
u.get_market_cap_df = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cap lookup must be skipped"))
u.trigger_morning_value_to_cap_ratio = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("value trigger must be skipped"))
u.select_final_tickers = lambda triggers, **_kwargs: triggers

result = u.run_batch("morning", override_date="20260826")
assert result["Value-to-Cap Ratio Top"].empty
'''
    )
