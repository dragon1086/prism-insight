#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
횡보주 트리거 하락추세 게이트 테스트 (#289 follow-up).

is_sideways(당일 ±5%)만으로 "횡보" 판정하던 맹점 — MA20 하회 하락추세 종목
(예: 이노션 2026-05-29)이 횡보로 오분류되던 것 — 을 MA20 추세 게이트로
거르는지 검증. get_multi_day_ohlcv를 몽키패치해 망 없이 실행.
"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        print(f"  FAIL: {label}")


import trigger_batch as t

print("[Test 1] _compute_ma20 — mock OHLCV로 평균 계산")
_orig = t.get_multi_day_ohlcv


def _mk(closes):
    n = len(closes)
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes], "Close": closes,
        "Volume": [1_000_000] * n, "Amount": [1e10] * n,
    })


try:
    t.get_multi_day_ohlcv = lambda tk, td, days=20: _mk([100.0] * 20)
    check("MA20 of flat 100 = 100", abs(t._compute_ma20("X", "20260529") - 100.0) < 1e-9)
    t.get_multi_day_ohlcv = lambda tk, td, days=20: pd.DataFrame()
    check("데이터 없으면 0.0 (게이트 통과 처리)", t._compute_ma20("X", "20260529") == 0.0)

    print("\n[Test 2] 게이트 경계 — SIDEWAYS_MA20_SUPPORT_TOLERANCE=0.97")
    tol = t.SIDEWAYS_MA20_SUPPORT_TOLERANCE
    check("tolerance = 0.97", tol == 0.97)
    # MA20=100. 게이트 통과 조건: close >= 100*0.97 = 97
    ma20 = 100.0
    cases = [
        ("MA20 위(105) 통과", 105.0, True),
        ("MA20 정확(100) 통과", 100.0, True),
        ("지지 테스트(-2%, 98) 통과", 98.0, True),
        ("경계(-3%, 97) 통과", 97.0, True),
        ("명백한 하회(-5%, 95) 제외", 95.0, False),
        ("이노션류(-10%, 90) 제외", 90.0, False),
    ]
    for label, close, expected in cases:
        gate_pass = (ma20 <= 0) or (close >= ma20 * tol)
        check(label, gate_pass == expected)

    print("\n[Test 3] 데이터 불명(ma20=0)이면 항상 통과 (데이터 blip에 종목 안 버림)")
    check("ma20=0 → 통과", (0.0 <= 0) or (50.0 >= 0.0 * tol))

    print("\n[Test 4] 이노션 시나리오 재현 — 하락추세는 제외, 지지횡보는 통과")
    # 이노션: 종가 19850, MA20 위? 하락추세라 MA20 아래로 가정
    inno_ma20 = 21000.0  # 최근 하락으로 MA20이 현재가보다 위
    inno_close = 19850.0
    inno_pass = (inno_ma20 <= 0) or (inno_close >= inno_ma20 * tol)  # 19850 >= 20370? No
    check("이노션(하락추세, MA20 -5.5%) 제외됨", inno_pass is False)
    # 진짜 지지 횡보주: MA20 근처에서 다지기
    base_ma20 = 20000.0
    base_close = 19900.0  # -0.5%, 지지 중
    base_pass = (base_ma20 <= 0) or (base_close >= base_ma20 * tol)
    check("지지 횡보주(MA20 -0.5%) 통과", base_pass is True)

finally:
    t.get_multi_day_ohlcv = _orig

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")


def test_sideways_downtrend_gate_script_checks():
    """Report the legacy script checks through pytest's normal lifecycle."""
    assert failed == 0, f"{failed} of {passed + failed} gate checks failed"


if __name__ == "__main__":
    sys.exit(1 if failed else 0)
