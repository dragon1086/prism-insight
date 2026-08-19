import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRISM_US_DIR = PROJECT_ROOT / "prism-us"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from us_stock_analysis_orchestrator import resolve_us_trade_date  # noqa: E402


def test_override_date_is_the_canonical_pipeline_date():
    assert resolve_us_trade_date("20260818") == "20260818"


def test_override_date_rejects_non_yyyymmdd():
    with pytest.raises(ValueError):
        resolve_us_trade_date("2026-08-18")
