"""약세·횡보장 top-down 억제 옵션(REGIME_WEAK_NO_TOPDOWN) 단위테스트 — US.

KR tests/test_regime_weak_no_topdown.py 의 US 미러(bug-fix parity). 기본 off = 현행
슬롯 유지. ON 시 sideways/moderate_bear 의 top-down 슬롯 0(매수 절제), 강세장(bull)은 무영향.
prism-us/tests/conftest.py 가 prism-us 를 sys.path 우선 등록하므로 cores=prism-us/cores 로 해석된다.
"""
import os

import pytest

# 서버 전용 의존성 미설치 시 import 실패 → 스킵(KR 미러와 동일 정책).
us_trigger_batch = pytest.importorskip("us_trigger_batch")


def _slots(regime, flag):
    if flag is None:
        os.environ.pop("REGIME_WEAK_NO_TOPDOWN", None)
    else:
        os.environ["REGIME_WEAK_NO_TOPDOWN"] = flag
    try:
        return us_trigger_batch._get_regime_slots(regime)
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
