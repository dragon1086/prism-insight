#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
US 스크리너 양봉(Close > Open) 필터 회귀 테스트 (no network).

배경: 2026-08-01 오후 배치에서 MU 가 `Macro Sector Leader` 로 선정됐는데
당일(07-31) 캔들이 시가 919.65 → 종가 823.03 (−10.51%) 인 음봉이었다.

원인: 8개 스크리너 중 일부만 양봉 필터를 적용하고 있었다.
  적용됨 — morning_volume_surge / morning_gap_up_momentum /
           morning_value_to_cap_ratio / afternoon_closing_strength
  누락됨 — afternoon_daily_rise_top, macro_sector_leader   ← 이 PR 에서 추가
  의도적 제외 — contrarian_value(역발상은 약세를 사는 트리거),
               afternoon_volume_surge_flat(횡보 축적 트리거)

이 테스트는 '누락됨' 두 트리거가 음봉을 배제하는지 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

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


import us_trigger_batch as u


def _frames():
    """실제 2026-07-31 값 기반. BULL=양봉, BEAR=음봉(둘 다 전일대비 상승)."""
    idx = ["BULL", "BEAR"]
    snap = pd.DataFrame(
        {
            # BEAR: 갭상승 후 밀려 음봉이지만 전일대비로는 +5%
            "Open": [154.01, 919.65],
            "High": [162.57, 930.88],
            "Low": [153.61, 818.00],
            "Close": [161.95, 823.03],
            "Volume": [5_000_000, 9_000_000],
            "Amount": [8.0e8, 7.5e9],
        },
        index=idx,
    )
    prev = pd.DataFrame(
        {
            "Open": [148.27, 793.14],
            "High": [154.99, 882.50],
            "Low": [143.77, 789.00],
            # BEAR 전일종가를 낮게 둬서 DailyChange 를 양수로 만든다
            "Close": [154.25, 780.00],
            "Volume": [4_000_000, 8_000_000],
            "Amount": [7.0e8, 7.0e9],
        },
        index=idx,
    )
    cap = pd.DataFrame({"MarketCap": [8.0e10, 9.0e11]}, index=idx)
    return snap, prev, cap


snap, prev, cap = _frames()
print("입력: BULL 종가>시가(양봉), BEAR 종가<시가(음봉) — 둘 다 전일대비 상승")
print(f"  BULL  시 {snap.loc['BULL','Open']} → 종 {snap.loc['BULL','Close']}  "
      f"(전일대비 {(snap.loc['BULL','Close']/prev.loc['BULL','Close']-1)*100:+.2f}%)")
print(f"  BEAR  시 {snap.loc['BEAR','Open']} → 종 {snap.loc['BEAR','Close']}  "
      f"(전일대비 {(snap.loc['BEAR','Close']/prev.loc['BEAR','Close']-1)*100:+.2f}%)")

print("\n[Test 1] afternoon_daily_rise_top — 음봉 배제")
try:
    res = u.trigger_afternoon_daily_rise_top("20260731", snap, prev, cap)
    names = list(res.index) if res is not None and not res.empty else []
    check(f"BEAR(음봉) 제외됨 (결과: {names})", "BEAR" not in names)
    check(f"BULL(양봉) 유지됨 (결과: {names})", "BULL" in names)
except Exception as e:
    check(f"실행 예외 없음 ({type(e).__name__}: {e})", False)

print("\n[Test 2] macro_sector_leader — 음봉 배제")
# leading_sectors 가 비면 필터를 타기 전에 조기 return 하므로(공허한 통과),
# 섹터 맵을 주입해 실제로 필터 구간까지 진입시킨다.
macro = {"leading_sectors": [{"sector": "Technology", "confidence": 0.8}]}
_orig_map = u.get_us_sector_map
try:
    u.get_us_sector_map = lambda tickers: {t: "Technology" for t in tickers}
    res2 = u.trigger_macro_sector_leader("20260731", snap, prev, cap, macro_context=macro)
    names2 = list(res2.index) if res2 is not None and not res2.empty else []
    # 음성 대조군: BULL 이 살아남아야 "필터가 BEAR 를 걸렀다"가 증명된다.
    check(f"BULL(양봉) 유지됨 — 조기 return 이 아님 (결과: {names2})", "BULL" in names2)
    check(f"BEAR(음봉) 제외됨 (결과: {names2})", "BEAR" not in names2)
except Exception as e:
    check(f"실행 예외 없음 ({type(e).__name__}: {e})", False)
finally:
    u.get_us_sector_map = _orig_map

print("\n[Test 3] 의도적 제외 트리거는 건드리지 않았다")
import inspect
check("contrarian_value 에 양봉 필터 없음(역발상 트리거)",
      "IsRising" not in inspect.getsource(u.trigger_contrarian_value))

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
sys.exit(1 if failed else 0)
