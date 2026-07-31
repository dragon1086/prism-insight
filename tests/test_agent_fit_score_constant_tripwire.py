#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_fit_score 상수성 트립와이어 (no live data required).

⚠️ 이 테스트는 "올바른 동작"을 지키는 테스트가 아니다. **경보 장치**다.

현재 `calculate_agent_fit_metrics()` 는 목표가에 `current_price * 1.15` 하한을
적용하기 때문에(`trigger_batch.py` 의 "Guarantee minimum +15% target" 블록),
`agent_fit_score` 가 **모든 종목에서 정확히 1.0** 이 된다.

  potential_loss = current_price * sl_max
  potential_gain >= current_price * 0.15          (하한 때문)
  => risk_reward_ratio >= 0.15 / sl_max
  TRIGGER_CRITERIA 최악 조합 (sl_max=0.08, rr_target=1.5) 에서도
     0.15/0.08 = 1.875, 1.875/1.5 = 1.25 >= 1.0  => rr_score = 1.0
  sl_score 는 1.0 하드코딩  => agent_fit_score = 1.0*0.6 + 1.0*0.4 = 1.0

그 결과 `REGIME_SCORE_WEIGHTS` 의 **w_agent(=0.35)가 랭킹에 아무 영향을 주지 않는다**
(모든 후보에 동일한 상수를 더하므로 정렬 순서가 불변. final_score 는 정렬에만 쓰이고
절대 임계값이 없다).

**이 테스트가 깨졌다면** 누군가 목표가 하한이나 sl_score 하드코딩을 건드린 것이다.
그 순간 **랭킹 가중치의 35% 가 새로 켜진다** — 후보 선정 결과가 바뀐다는 뜻이다.
테스트를 그냥 고치지 말고, 먼저 그 변경이 의도된 것인지, 백테스트를 했는지 확인하라.
근거: tasks/target_floor_dataflow_trace.md
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


import trigger_batch as t


def _fake_ohlcv(resistance_ratio):
    """저항선(고가)을 진입가 대비 임의 비율로 고정한 합성 일봉."""
    def _f(ticker, trade_date, lookback_days):
        p = 10000.0
        return pd.DataFrame(
            {
                "High": [p * (1 + resistance_ratio)] * 5,
                "Low": [p * 0.9] * 5,
                "Close": [p] * 5,
            }
        )
    return _f


print("[Test 1] TRIGGER_CRITERIA 전 조합에서 산술적으로 rr_score == 1.0 이 강제되는가")
worst = None
for name, c in t.TRIGGER_CRITERIA.items():
    min_rr = 0.15 / c["sl_max"]          # 하한이 보장하는 최소 손익비
    ratio = min_rr / c["rr_target"]      # min(ratio, 1.0) 이 rr_score
    if worst is None or ratio < worst[1]:
        worst = (name, ratio)
    check(f"{name}: min_rr={min_rr:.3f} / rr_target={c['rr_target']} = {ratio:.3f} >= 1.0",
          ratio >= 1.0)
print(f"  (최악 조합: {worst[0]}, 여유율 {worst[1]:.3f})")

print("\n[Test 2] 실제 함수 호출 — 저항선 위치와 무관하게 agent_fit_score == 1.0")
_orig = t.get_multi_day_ohlcv
scores = set()
try:
    for ttype in t.TRIGGER_CRITERIA:
        for res in (-0.30, -0.10, 0.0, 0.02, 0.05, 0.10, 0.1499, 0.15, 0.25, 0.50, 1.00):
            t.get_multi_day_ohlcv = _fake_ohlcv(res)
            m = t.calculate_agent_fit_metrics("000000", 10000.0, "20260730", 10, ttype)
            scores.add(round(m["agent_fit_score"], 9))
finally:
    t.get_multi_day_ohlcv = _orig

check(f"고유 agent_fit_score 값이 1개뿐 (관측: {sorted(scores)})", len(scores) == 1)
check("그 값이 정확히 1.0", scores == {1.0})

print("\n[Test 3] 상수이므로 w_agent 가 순위에 기여하지 않는다 (문서화용 불변식)")
check("w_agent 가 모든 regime 에서 동일",
      len({w[1] for w in t.REGIME_SCORE_WEIGHTS.values()}) == 1)
_w_agent = list(t.REGIME_SCORE_WEIGHTS.values())[0][1]
print(f"  (w_agent = {_w_agent} — 상수항이라 정렬 순서 불변. 실효 가중치는 0%)")

print("\n[Test 4] 조기 return 경로만이 예외다")
res0 = t.calculate_agent_fit_metrics("000000", 0.0, "20260730", 10, "일중 상승률 상위주")
check("current_price <= 0 이면 agent_fit_score == 0", res0["agent_fit_score"] == 0)

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
if failed:
    print("\n!! agent_fit_score 가 더 이상 상수가 아니다.")
    print("!! 랭킹 가중치 35%(w_agent)가 새로 활성화된 상태다 — 후보 선정 결과가 바뀐다.")
    print("!! 의도한 변경인지, 백테스트를 했는지 확인하라. tasks/target_floor_dataflow_trace.md")
sys.exit(1 if failed else 0)
