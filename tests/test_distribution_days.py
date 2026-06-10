"""O'Neil 분산일(Distribution Day) 결정론 카운트 단위테스트 (KR cores).

분산일 정의(-0.2% 종가/거래량↑), 회복 만료(+5%), 윈도우 경과 만료, 거래량 결측 graceful,
그리고 index_summary 정보 주입(_inject_distribution_days; regime 강등 없음)을 합성데이터로 검증.
US cores(prism-us) 헬퍼는 동일 구현이므로 prism-us/tests에 스모크 테스트만 둔다.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import (  # noqa: E402
    _count_distribution_days,
    _inject_distribution_days,
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


# --- information injection (no regime demotion) -----------------------------

def test_inject_sets_distribution_days_and_window():
    summary = {}
    _inject_distribution_days(summary, _mk([100, 99, 98, 97, 96], [10, 11, 12, 13, 14]), "Close")
    assert summary["distribution_days"] == 4
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW


def test_inject_missing_volume_sets_none():
    summary = {}
    df = pd.DataFrame({"Close": [100, 99, 98]}, index=pd.date_range("2024-01-01", periods=3))
    _inject_distribution_days(summary, df, "Close")
    assert summary["distribution_days"] is None
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW


def test_inject_does_not_add_demotion_fields():
    # 결정론 강등은 제거됨 → demotion/threshold 관련 필드를 절대 만들지 않는다
    summary = {}
    _inject_distribution_days(summary, _mk([100, 99, 98, 97, 96], [10, 11, 12, 13, 14]), "Close")
    assert "distribution_demoted_from" not in summary
    assert "distribution_threshold" not in summary


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
