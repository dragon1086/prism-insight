"""O'Neil 분산일(Distribution Day) 결정론 카운트 단위테스트 (KR cores).

분산일 정의(-0.2% 종가/거래량↑), 회복 만료(+5%), 윈도우 경과 만료,
거래량 결측 graceful, regime 강등 사다리·클램프·임계 경계를 합성데이터로 검증.
US cores(prism-us)의 헬퍼는 동일 구현이므로 prism-us/tests에 스모크 테스트만 둔다.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import (  # noqa: E402
    _count_distribution_days,
    _apply_distribution_demotion,
    KR_DISTRIBUTION_THRESHOLD,
    DISTRIBUTION_WINDOW,
)


def _mk(closes, vols):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


def test_clear_distribution_days_counted():
    # 4 consecutive ~-1% down days on rising volume → 4 distribution days
    r = _count_distribution_days(_mk([100, 99, 98, 97, 96], [10, 11, 12, 13, 14]), "Close")
    assert r["count"] == 4
    assert r["raw_count"] == 4


def test_down_but_volume_not_rising_is_not_distribution():
    # price down but volume falling → not institutional distribution
    r = _count_distribution_days(_mk([100, 99, 98, 97, 96], [14, 13, 12, 11, 10]), "Close")
    assert r["count"] == 0


def test_drop_below_threshold_not_counted():
    # -0.05% drops are below the -0.2% threshold → ignored
    r = _count_distribution_days(_mk([100, 99.95, 99.9, 99.85], [10, 11, 12, 13]), "Close")
    assert r["count"] == 0


def test_recovery_5pct_expires_distribution_day():
    # day1 -2% on rising vol = distribution; day2 closes +12% (> 98*1.05) → expires it
    r = _count_distribution_days(_mk([100, 98, 110], [10, 11, 12]), "Close")
    assert r["raw_count"] == 1
    assert r["count"] == 0


def test_recovery_just_below_5pct_keeps_distribution_day():
    # day1 -2% (close 98) on rising vol; later max close 102 < 98*1.05(=102.9) → kept
    r = _count_distribution_days(_mk([100, 98, 102], [10, 11, 12]), "Close")
    assert r["count"] == 1


def test_missing_volume_returns_none():
    df = pd.DataFrame({"Close": [100, 99, 98]}, index=pd.date_range("2024-01-01", periods=3))
    assert _count_distribution_days(df, "Close") is None


def test_all_zero_volume_returns_none():
    r = _count_distribution_days(_mk([100, 99, 98], [0, 0, 0]), "Close")
    assert r is None


def test_window_caps_old_distribution_days():
    # 40 down-on-rising-volume days; recovery disabled → only last `window` counted
    closes, vols = [100], [10]
    for _ in range(40):
        closes.append(round(closes[-1] * 0.99, 4))
        vols.append(vols[-1] + 1)
    r = _count_distribution_days(_mk(closes, vols), "Close", recovery_pct=1e9)
    assert r["count"] == DISTRIBUTION_WINDOW


def test_korean_volume_column_detected():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({"종가": [100, 99, 98, 97, 96], "거래량": [10, 11, 12, 13, 14]}, index=idx)
    r = _count_distribution_days(df, "종가")
    assert r["count"] == 4


# --- demotion ladder --------------------------------------------------------

def _n_distribution_days(n):
    """정확히 n개의 분산일을 만드는 (단조 하락+거래량 증가) 시계열."""
    closes = [100.0] + [round(100 * (0.99 ** k), 4) for k in range(1, n + 1)]
    vols = [10 + k for k in range(len(closes))]
    return _mk(closes, vols)


def test_demotion_one_step_when_threshold_met():
    summary = {}
    regime, conf = _apply_distribution_demotion(
        "strong_bull", 0.9, summary, _n_distribution_days(KR_DISTRIBUTION_THRESHOLD),
        "Close", KR_DISTRIBUTION_THRESHOLD,
    )
    assert regime == "moderate_bull"
    assert conf == 0.70
    assert summary["distribution_demoted_from"] == "strong_bull"
    assert summary["distribution_days"] >= KR_DISTRIBUTION_THRESHOLD


def test_strong_bear_clamps_no_further_demotion():
    summary = {}
    regime, _ = _apply_distribution_demotion(
        "strong_bear", 0.9, summary, _n_distribution_days(KR_DISTRIBUTION_THRESHOLD),
        "Close", KR_DISTRIBUTION_THRESHOLD,
    )
    assert regime == "strong_bear"
    assert "distribution_demoted_from" not in summary


def test_no_demotion_in_up_market():
    summary = {}
    regime, conf = _apply_distribution_demotion(
        "strong_bull", 0.9, summary, _mk([100, 100.5, 101, 101.5], [10, 11, 12, 13]),
        "Close", KR_DISTRIBUTION_THRESHOLD,
    )
    assert regime == "strong_bull"
    assert conf == 0.9
    assert summary["distribution_days"] == 0


def test_threshold_boundary_one_below_no_demotion():
    # exactly threshold-1 distribution days → no demotion
    n = KR_DISTRIBUTION_THRESHOLD - 1
    closes = [100] + [round(100 * (0.99 ** k), 4) for k in range(1, n + 1)]
    vols = [10 + k for k in range(len(closes))]
    summary = {}
    regime, _ = _apply_distribution_demotion(
        "strong_bull", 0.9, summary, _mk(closes, vols), "Close", KR_DISTRIBUTION_THRESHOLD,
    )
    assert summary["distribution_days"] == n
    assert regime == "strong_bull"


def test_missing_volume_skips_demotion():
    summary = {}
    df = pd.DataFrame({"Close": [100, 99, 98, 97, 96]}, index=pd.date_range("2024-01-01", periods=5))
    regime, conf = _apply_distribution_demotion(
        "strong_bull", 0.9, summary, df, "Close", KR_DISTRIBUTION_THRESHOLD,
    )
    assert regime == "strong_bull"
    assert summary["distribution_days"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
