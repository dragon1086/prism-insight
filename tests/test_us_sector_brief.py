"""US signal alert: today's strong industries from industry ETFs (2026-10-07, quick version before U1~U4)."""
import subprocess
import sys
from pathlib import Path

import pandas as pd

from prism_core import us_sector_brief as brief

ROOT = Path(__file__).resolve().parents[1]


def fake(changes, day="2026-10-07"):
    """yfinance-like data[etf]["Close"]: previous close 100, latest bar on `day` = 100 + change."""
    def download(_tickers):
        index = pd.to_datetime(["2026-10-06", day])
        return {etf: pd.DataFrame({"Close": [100.0, 100.0 + pct]}, index=index) for etf, pct in changes.items()}
    return download


def test_top_three_rising_industries_in_korean():
    text = brief.sector_brief("20261007", "ko", fake({"SMH": 2.1, "URA": 1.8, "ITA": 1.2, "XBI": 0.9, "XLE": -1.0}))
    assert "🧭 오늘 강한 업종" in text
    assert "1) 반도체(SMH) +2.1% · 2) 우라늄·원전(URA) +1.8% · 3) 항공·방산(ITA) +1.2%" in text
    assert "XBI" not in text and text.endswith("\n\n")


def test_flat_or_falling_day_and_stale_bars_show_nothing():
    assert brief.sector_brief("20261007", "ko", fake({"XBI": 0.04, "XLE": -1.0})) == ""
    # The latest bar is the previous session (alert before the open data arrived): not today's move.
    assert brief.sector_brief("20261007", "ko", fake({"SMH": 2.0}, day="2026-10-06")) == ""


def test_english_and_download_failure():
    assert "Semiconductors(SMH) +2.1%" in brief.sector_brief("20261007", "en", fake({"SMH": 2.1}))

    def broken(_tickers):
        raise TimeoutError("yfinance")
    assert brief.sector_brief("20261007", "ko", broken) == ""


def test_us_alert_places_the_block_after_the_regime_line():
    script = r'''
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator
o = USStockAnalysisOrchestrator.__new__(USStockAnalysisOrchestrator)
meta = {"metadata": {"market_regime": "moderate_bull", "primary_trend_regime": "moderate_bull",
                     "selection_strategy": "hybrid_topdown_bottomup", "topdown_count": 1, "bottomup_count": 2}}
block = "🧭 오늘 강한 업종 (업종 ETF 등락, 알림 시점·전일 종가 대비)\n1) 반도체(SMH) +2.1%\n\n"
m = o._create_trigger_alert_message("morning", meta, "20261007", "ko", sector_brief=block)
assert m.index("장기추세") < m.index("오늘 강한 업종"), m
assert "오늘 강한 업종" not in o._create_trigger_alert_message("morning", meta, "20261007", "ko")
'''
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
