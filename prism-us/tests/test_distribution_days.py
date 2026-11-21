"""US index_summary 분산일 = 게이트가 보는 Market Pulse 값(두 지수면 작은 값, #833).

규칙 자체는 tests/test_market_pulse.py, KR 주입은 tests/test_distribution_days.py가 검증한다.
"""
import os
import sys

import pandas as pd
import pytest

# prism-us 디렉터리를 우선 경로에 둬 cores=prism-us/cores 로 해석되게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import DISTRIBUTION_WINDOW, _inject_distribution_days  # noqa: E402


def _mk(tail_closes, tail_vols):
    closes = [100.0] * 30 + list(tail_closes)
    vols = [10.0] * 30 + list(tail_vols)
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


SPX = _mk([99, 98, 97, 96], [11, 12, 13, 14])            # 4 distribution days
NDX = _mk([99, 98, 98.5, 98.6], [11, 12, 13, 14])        # 2 distribution days


def _dd(*args):
    summary = {}
    _inject_distribution_days(summary, *args)
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW
    assert "distribution_demoted_from" not in summary
    return summary["distribution_days"]


def test_dual_mode_reports_smaller_count(monkeypatch):
    monkeypatch.delenv("US_MARKET_PULSE_INDEX_MODE", raising=False)
    assert _dd(SPX, "Close", NDX) == 2
    assert _dd(SPX, "Close") == 4                        # NASDAQ missing keeps S&P 500


def test_spx_mode_keeps_sp500_count(monkeypatch):
    monkeypatch.setenv("US_MARKET_PULSE_INDEX_MODE", "spx")
    assert _dd(SPX, "Close", NDX) == 4


def test_missing_volume_none():
    df = pd.DataFrame({"Close": [100.0] * 40}, index=pd.date_range("2024-01-01", periods=40))
    assert _dd(df, "Close", NDX) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
