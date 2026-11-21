"""index_summary 분산일 = 게이트가 보는 Market Pulse 기계값 (KR cores).

분산일 판정 규칙 자체(-0.2% 종가·거래량↑, +5% 회복 만료, 25세션 창)는 tests/test_market_pulse.py가
검증한다. 여기서는 리포트 경로(_inject_distribution_days)가 같은 기계를 재생해 같은 값을 넣는지,
FTD 뒤 창 리셋이 반영되는지, 모르는 값은 None으로 남는지, high/low 채움을 확인한다.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores import regime_policy as rp  # noqa: E402
from cores.data_prefetch import DISTRIBUTION_WINDOW, _inject_distribution_days  # noqa: E402
from cores.market_pulse import CORRECTION, UPTREND, DailyBar, MarketPulse  # noqa: E402


def _mk(closes, vols, close="Close", volume="Volume"):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({close: closes, volume: vols}, index=idx)


def _flat(n=30):
    return [100.0] * n, [10.0] * n


def _six_dd_then_ftd():
    closes, vols = _flat()
    for i in range(6):                                   # 6 distribution days -> CORRECTION
        closes.append(round(closes[-1] * 0.99, 6))
        vols.append(11.0 + i)
    for _ in range(3):                                   # rally days 1-3 on light volume
        closes.append(round(closes[-1] * 1.005, 6))
        vols.append(10.0)
    closes.append(round(closes[-1] * 1.02, 6))           # day 4 +2% on higher volume = FTD
    vols.append(20.0)
    return closes, vols


def _replay(closes, vols):
    mp = MarketPulse()
    states = [mp.feed(DailyBar(f"d{i}", c, v)) for i, (c, v) in enumerate(zip(closes, vols))]
    return states, mp.distribution_days


def test_inject_matches_pulse_machine():
    closes, vols = _flat()
    closes += [99.0, 98.0, 97.0, 96.0]
    vols += [11.0, 12.0, 13.0, 14.0]
    summary = {}
    _inject_distribution_days(summary, _mk(closes, vols), "Close")
    assert summary["distribution_days"] == _replay(closes, vols)[1] == 4
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW


def test_follow_through_resets_count_like_the_gate():
    # The removed legacy counter had no FTD reset and reported 6 here.
    closes, vols = _six_dd_then_ftd()
    states, machine_dd = _replay(closes, vols)
    assert CORRECTION in states and states[-1] == UPTREND
    summary = {}
    _inject_distribution_days(summary, _mk(closes, vols), "Close")
    assert summary["distribution_days"] == machine_dd == 0


def test_korean_columns_detected():
    closes, vols = _flat()
    closes += [99.0, 98.0]
    vols += [11.0, 12.0]
    summary = {}
    _inject_distribution_days(summary, _mk(closes, vols, "종가", "거래량"), "종가")
    assert summary["distribution_days"] == 2


@pytest.mark.parametrize("frame", [
    pd.DataFrame({"Close": [100.0] * 40}, index=pd.date_range("2024-01-01", periods=40)),
    _mk([100.0] * 40, [0.0] * 40),
    _mk([100.0, 99.0, 98.0], [10.0, 11.0, 12.0]),       # under 30 sessions, like the gate
])
def test_unknown_stays_none(frame):
    summary = {}
    _inject_distribution_days(summary, frame, "Close")
    assert summary["distribution_days"] is None
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW


def test_inject_does_not_add_demotion_fields():
    summary = {}
    _inject_distribution_days(summary, _mk(*_flat()), "Close")
    assert "distribution_demoted_from" not in summary
    assert "distribution_threshold" not in summary


def test_df_to_bars_fills_high_low_when_present():
    idx = pd.date_range("2026-11-19", periods=2, freq="D")
    kis = pd.DataFrame({"Open": [5090.04, 4928.51], "High": [5137.89, 4971.31], "Low": [4963.61, 0.0],
                        "Close": [5137.89, 4869.96], "Volume": [195695, 206729]}, index=idx)
    bars = rp._df_to_bars(kis, "Close", "Volume", DailyBar)
    assert (bars[0].high, bars[0].low) == (5137.89, 4963.61)
    assert bars[1].low is None                           # non-positive => unknown
    plain = rp._df_to_bars(kis[["Close", "Volume"]], "Close", "Volume", DailyBar)
    assert (plain[0].high, plain[0].low) == (None, None)
    assert [(b.date, b.close, b.volume) for b in bars] == [(b.date, b.close, b.volume) for b in plain]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
