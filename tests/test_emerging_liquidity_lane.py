"""Emerging-liquidity lane regression tests for KR and US afternoon rise triggers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

import trigger_batch


REPO_ROOT = Path(__file__).resolve().parents[1]


def _kr_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = {
        "STANDARD": {
            "Open": 101.0,
            "High": 109.0,
            "Low": 100.0,
            "Close": 108.0,
            "Volume": 1_000_000,
            "Amount": 20_000_000_000,
        },
        "EMERGING_TOP": {
            "Open": 101.0,
            "High": 110.0,
            "Low": 100.0,
            "Close": 109.0,
            "Volume": 1_000_000,
            "Amount": 9_000_000_000,
        },
        "EMERGING_LOW": {
            "Open": 101.0,
            "High": 107.0,
            "Low": 100.0,
            "Close": 106.0,
            "Volume": 1_000_000,
            "Amount": 6_000_000_000,
        },
        "BELOW_FLOOR": {
            "Open": 101.0,
            "High": 111.0,
            "Low": 100.0,
            "Close": 110.0,
            "Volume": 1_000_000,
            "Amount": 4_900_000_000,
        },
    }
    snapshot = pd.DataFrame.from_dict(rows, orient="index")
    previous = snapshot.copy()
    previous["Open"] = 100.0
    previous["High"] = 101.0
    previous["Low"] = 99.0
    previous["Close"] = 100.0
    cap = pd.DataFrame(
        {"시가총액": 1_000_000_000_000.0},
        index=snapshot.index,
    )
    return snapshot, previous, cap


def test_kr_daily_rise_adds_only_top_emerging_candidate(monkeypatch):
    snapshot, previous, cap = _kr_frames()
    monkeypatch.setattr(trigger_batch, "enhance_dataframe", lambda frame: frame)

    result = trigger_batch.trigger_afternoon_daily_rise_top(
        "20260812", snapshot, previous, cap
    )

    assert "STANDARD" in result.index
    assert "EMERGING_TOP" in result.index
    assert "EMERGING_LOW" not in result.index
    assert "BELOW_FLOOR" not in result.index
    assert result.loc["STANDARD", "liquidity_lane"] == "standard"
    assert result.loc["EMERGING_TOP", "liquidity_lane"] == "emerging"
    assert result.loc["EMERGING_TOP", "liquidity_lane_rank"] == 1
    assert result.loc["EMERGING_TOP", "composite_score"] == 1.0


def test_us_daily_rise_adds_only_top_emerging_candidate():
    script = r'''
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
import us_trigger_batch as u

rows = {
    "STANDARD": {"Open": 101.0, "High": 109.0, "Low": 100.0, "Close": 108.0,
                 "Volume": 1_000_000, "Amount": 200_000_000.0},
    "EMERGING_TOP": {"Open": 101.0, "High": 110.0, "Low": 100.0, "Close": 109.0,
                     "Volume": 1_000_000, "Amount": 90_000_000.0},
    "EMERGING_LOW": {"Open": 101.0, "High": 107.0, "Low": 100.0, "Close": 106.0,
                     "Volume": 1_000_000, "Amount": 60_000_000.0},
    "BELOW_FLOOR": {"Open": 101.0, "High": 111.0, "Low": 100.0, "Close": 110.0,
                    "Volume": 1_000_000, "Amount": 49_000_000.0},
}
snapshot = pd.DataFrame.from_dict(rows, orient="index")
previous = snapshot.copy()
previous["Open"] = 100.0
previous["High"] = 101.0
previous["Low"] = 99.0
previous["Close"] = 100.0
u.enhance_dataframe = lambda frame: frame

result = u.trigger_afternoon_daily_rise_top("20260812", snapshot, previous, None)
assert "STANDARD" in result.index
assert "EMERGING_TOP" in result.index
assert "EMERGING_LOW" not in result.index
assert "BELOW_FLOOR" not in result.index
assert result.loc["STANDARD", "LiquidityLane"] == "standard"
assert result.loc["EMERGING_TOP", "LiquidityLane"] == "emerging"
assert result.loc["EMERGING_TOP", "LiquidityLaneRank"] == 1
assert result.loc["EMERGING_TOP", "CompositeScore"] == 1.0
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
