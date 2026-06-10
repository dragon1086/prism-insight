"""US cores 분산일 헬퍼 스모크 테스트.

알고리즘 자체는 KR(tests/test_distribution_days.py)에서 전수 검증한다.
여기서는 prism-us/cores.data_prefetch 의 동일 구현이 import 되고, 카운트/정보 주입이
동작하는지(regime 강등 없음)만 확인한다.
"""
import os
import sys

import pandas as pd
import pytest

# prism-us 디렉터리를 우선 경로에 둬 cores=prism-us/cores 로 해석되게 함
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import (  # noqa: E402
    _count_distribution_days,
    _inject_distribution_days,
    DISTRIBUTION_WINDOW,
)


def _mk(closes, vols):
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "Volume": vols}, index=idx)


def test_us_counts_distribution_days():
    r = _count_distribution_days(_mk([100, 99, 98, 97, 96], [10, 11, 12, 13, 14]), "Close")
    assert r["count"] == 4


def test_us_inject_sets_count_no_demotion():
    summary = {}
    _inject_distribution_days(summary, _mk([100, 99, 98, 97, 96], [10, 11, 12, 13, 14]), "Close")
    assert summary["distribution_days"] == 4
    assert summary["distribution_window"] == DISTRIBUTION_WINDOW
    assert "distribution_demoted_from" not in summary


def test_us_missing_volume_none():
    df = pd.DataFrame({"Close": [100, 99, 98]}, index=pd.date_range("2024-01-01", periods=3))
    assert _count_distribution_days(df, "Close") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
