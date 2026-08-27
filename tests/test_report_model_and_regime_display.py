from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import report_model_config
from regime_display import regime_label, swing_label


ROOT = Path(__file__).resolve().parents[1]


def test_report_model_defaults_to_luna_medium_with_low_auxiliary(monkeypatch):
    monkeypatch.delenv("REPORT_MODEL", raising=False)
    monkeypatch.delenv("REPORT_EFFORT", raising=False)
    monkeypatch.delenv("REPORT_AUX_MODEL", raising=False)
    monkeypatch.delenv("REPORT_AUX_EFFORT", raising=False)
    module = importlib.reload(report_model_config)

    assert module.REPORT_MODEL == "gpt-5.6-luna"
    assert module.REPORT_EFFORT == "medium"
    assert module.REPORT_AUX_MODEL == "gpt-5.6-luna"
    assert module.REPORT_AUX_EFFORT == "low"
    assert module.report_model_slug() == "gpt-5.6-luna"


def test_regime_labels_expose_enum_and_swing_timeframe():
    assert regime_label("strong_bull") == "강한 강세(strong_bull)"
    assert regime_label("moderate_bull") == "온건 강세(moderate_bull)"
    assert swing_label("consolidation") == "횡보·숨고르기(consolidation)"
    assert regime_label("strong_bull", "en") == "Strong Bull(strong_bull)"


def test_batch_report_filenames_are_not_hardcoded_to_mini():
    for relative in (
        "stock_analysis_orchestrator.py",
        "prism-us/us_stock_analysis_orchestrator.py",
    ):
        source = (ROOT / relative).read_text()
        assert "_gpt5.4-mini.md" not in source
        assert "report_model_slug" in source


def test_kr_trigger_alert_displays_authoritative_enum_and_swing_state():
    from stock_analysis_orchestrator import StockAnalysisOrchestrator

    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    message = orchestrator._create_trigger_alert_message(
        "morning",
        {
            "metadata": {
                "market_regime": "strong_bull",
                "primary_trend_regime": "strong_bull",
                "swing_state": "consolidation",
                "selection_strategy": "hybrid_topdown_bottomup",
                "topdown_count": 1,
                "bottomup_count": 2,
            }
        },
        "20260827",
    )

    assert "강한 강세(strong_bull)" in message
    assert "횡보·숨고르기(consolidation)" in message
    assert "실행기준: 강한 강세(strong_bull)" in message


def test_us_trigger_alert_uses_the_same_regime_contract():
    script = r'''
import os
import sys
sys.path.insert(0, os.path.join(os.getcwd(), "prism-us"))
from us_stock_analysis_orchestrator import USStockAnalysisOrchestrator

orchestrator = USStockAnalysisOrchestrator.__new__(USStockAnalysisOrchestrator)
message = orchestrator._create_trigger_alert_message(
    "afternoon",
    {"metadata": {
        "market_regime": "strong_bull",
        "primary_trend_regime": "strong_bull",
        "swing_state": "consolidation",
        "selection_strategy": "hybrid_topdown_bottomup",
        "topdown_count": 2,
        "bottomup_count": 1,
    }},
    "20260826",
    "ko",
)
assert "강한 강세(strong_bull)" in message
assert "횡보·숨고르기(consolidation)" in message
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
