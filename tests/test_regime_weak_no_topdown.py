"""약세·횡보장 top-down 억제 옵션(REGIME_WEAK_NO_TOPDOWN) 단위테스트.

기본 off = 현행 슬롯 유지. ON 시 sideways/moderate_bear의 top-down 슬롯 0(매수 절제),
강세장(bull)은 무영향.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 로컬엔 krx_data_client 미설치 → import 실패 시 스킵. CI/서버에서 실행.
trigger_batch = pytest.importorskip("trigger_batch")


def _slots(regime, flag):
    if flag is None:
        os.environ.pop("REGIME_WEAK_NO_TOPDOWN", None)
    else:
        os.environ["REGIME_WEAK_NO_TOPDOWN"] = flag
    try:
        return trigger_batch._get_regime_slots(regime)
    finally:
        os.environ.pop("REGIME_WEAK_NO_TOPDOWN", None)


def test_default_off_preserves_current():
    assert _slots("sideways", None) == (1, 2)
    assert _slots("moderate_bear", None) == (1, 2)
    assert _slots("moderate_bull", None) == (1, 2)


def test_on_suppresses_topdown_in_weak_regimes():
    assert _slots("sideways", "true") == (0, 2)
    assert _slots("moderate_bear", "true") == (0, 2)


def test_on_does_not_touch_bull_or_strong_bear():
    assert _slots("moderate_bull", "true") == (1, 2)
    assert _slots("strong_bull", "true") == (2, 1)
    assert _slots("strong_bear", "true") == (0, 3)  # 이미 top-down 0
