#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
#289 KR screening redesign — unit tests (no live data required).

Validates pure logic of the O'Neil-style RS + extension soft-score redesign:
  - extension_score mapping (ADR units above MA20 → 0~1)
  - regime-aware blend weights (sum to 1.0, climax protection retained in strong_bull)
  - RS cross-candidate normalization
  - final_score blend range + kill-switch reduction
  - Stage-1 threshold unification (no 20% caps remain in KR triggers)
  - calculate_screening_signals safe defaults (no network for invalid price)
  - file parses + module imports
"""
import os
import sys
import ast

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

print("[Test 1] _compute_extension_score mapping")
check("at T_low → 1.0", t._compute_extension_score(t.EXTENSION_ADR_T_LOW) == 1.0)
check("below T_low → 1.0", t._compute_extension_score(0.0) == 1.0)
check("at T_high → 0.0", t._compute_extension_score(t.EXTENSION_ADR_T_HIGH) == 0.0)
check("above T_high → 0.0", t._compute_extension_score(99.0) == 0.0)
_mid = (t.EXTENSION_ADR_T_LOW + t.EXTENSION_ADR_T_HIGH) / 2
check("midpoint ≈ 0.5", abs(t._compute_extension_score(_mid) - 0.5) < 1e-9)
# Monotonic non-increasing
_xs = [0, 1, 2, 3, 4, 5, 6, 7, 8]
_ys = [t._compute_extension_score(x) for x in _xs]
check("monotonic non-increasing", all(_ys[i] >= _ys[i + 1] for i in range(len(_ys) - 1)))
check("all in [0,1]", all(0.0 <= y <= 1.0 for y in _ys))

print("\n[Test 2] regime blend weights")
_expected_regimes = {"strong_bull", "moderate_bull", "sideways", "moderate_bear", "strong_bear"}
check("all 5 regimes present", set(t.REGIME_SCORE_WEIGHTS) == _expected_regimes)
for _r, _w in t.REGIME_SCORE_WEIGHTS.items():
    check(f"{_r} weights sum to 1.0", abs(sum(_w) - 1.0) < 1e-9)
    check(f"{_r} has 4 components", len(_w) == 4)
# strong_bull: RS emphasized but extension NOT zero (climax protection retained — correction 3)
_sb = t.REGIME_SCORE_WEIGHTS["strong_bull"]
_sw = t.REGIME_SCORE_WEIGHTS["sideways"]
check("strong_bull extension weight > 0 (climax guard retained)", _sb[3] > 0)
check("strong_bull RS weight > sideways RS weight", _sb[2] > _sw[2])
check("sideways extension weight > strong_bull (heavier penalty when calm)", _sw[3] > _sb[3])
check("agent R/R weight kept meaningful (>=0.3) in every regime",
      all(_w[1] >= 0.3 for _w in t.REGIME_SCORE_WEIGHTS.values()))

print("\n[Test 3] RS cross-candidate normalization")


def _rs_norm(returns):
    r_min, r_max = min(returns), max(returns)
    r_range = r_max - r_min if r_max > r_min else 0.0
    return [((x - r_min) / r_range) if r_range > 0 else 0.5 for x in returns]


_n = _rs_norm([10.0, 20.0, 30.0])
check("min → 0.0", abs(_n[0] - 0.0) < 1e-9)
check("max → 1.0", abs(_n[2] - 1.0) < 1e-9)
check("mid → 0.5", abs(_n[1] - 0.5) < 1e-9)
check("all-equal returns → 0.5 each", _rs_norm([5.0, 5.0, 5.0]) == [0.5, 0.5, 0.5])

print("\n[Test 4] final_score blend range + kill switch")


def _blend(comp, agent, rs, ext, w):
    return comp * w[0] + agent * w[1] + rs * w[2] + ext * w[3]


# All components in [0,1] + weights sum 1 → final in [0,1]
for _r, _w in t.REGIME_SCORE_WEIGHTS.items():
    _hi = _blend(1, 1, 1, 1, _w)
    _lo = _blend(0, 0, 0, 0, _w)
    check(f"{_r} blend(1,1,1,1)=1.0", abs(_hi - 1.0) < 1e-9)
    check(f"{_r} blend(0,0,0,0)=0.0", abs(_lo - 0.0) < 1e-9)
# Kill switch: w_rs=w_ext=0 → final depends only on composite+agent
_killed = (0.3, 0.7, 0.0, 0.0)
check("kill switch ignores RS/ext (high vs low RS/ext identical)",
      _blend(0.5, 0.5, 1.0, 1.0, _killed) == _blend(0.5, 0.5, 0.0, 0.0, _killed))
# A non-extended leader should outscore a climax leader (same comp/agent/RS, different ext)
_sw_w = t.REGIME_SCORE_WEIGHTS["sideways"]
_leader = _blend(0.5, 0.8, 0.9, 1.0, _sw_w)   # ext_score 1.0 (healthy)
_climax = _blend(0.5, 0.8, 0.9, 0.0, _sw_w)   # ext_score 0.0 (extended)
check("non-extended leader > climax (same else)", _leader > _climax)

print("\n[Test 5] Stage-1 threshold unification (KR, no 20% caps remain)")
_src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trigger_batch.py")).read()
check("no 'prev_day_change_rate'] <= 20.0' remains", 'prev_day_change_rate"] <= 20.0' not in _src)
check("at least 3 '<= 15.0' change-rate caps present", _src.count('prev_day_change_rate"] <= 15.0') >= 3)

print("\n[Test 6] calculate_screening_signals safe defaults (no network)")
_sig = t.calculate_screening_signals("000000", 0.0, "20260529")  # current_price<=0 short-circuits
check("invalid price → extension_score default 1.0", _sig["extension_score"] == 1.0)
check("invalid price → extension_in_adr default 0.0", _sig["extension_in_adr"] == 0.0)
check("invalid price → return_nd default 0.0", _sig["return_nd"] == 0.0)

print("\n[Test 7] file parses + symbols exported")
check("ast.parse OK", ast.parse(_src) is not None)
for _sym in ("REGIME_SCORE_WEIGHTS", "EXTENSION_ADR_T_LOW", "EXTENSION_ADR_T_HIGH",
             "SCREENING_SIGNAL_LOOKBACK_DAYS", "_compute_extension_score",
             "calculate_screening_signals"):
    check(f"exported: {_sym}", hasattr(t, _sym))

print("\n[Test 8] select_final_tickers integration (mocked OHLCV, no network)")
import pandas as pd
import numpy as np


def _fake_ohlcv(ticker, end_date, days=60):
    """Synthetic OHLCV: LEADER = steady trend near base (low extension),
    CLIMAX = late blow-off far above MA20 (high extension), higher raw return."""
    idx = pd.date_range(end="2026-05-29", periods=days)
    if ticker == "LEADER":
        close = np.linspace(100.0, 120.0, days)          # steady uptrend
        high = close * 1.01
        low = close * 0.99                                # ADR ~2%
    else:  # CLIMAX
        close = np.array([100.0] * (days - 3) + [110.0, 130.0, 150.0])
        high = close * 1.025
        low = close * 0.975                               # ADR ~5%, huge late extension
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close,
         "Volume": [1_000_000] * days, "Amount": [1e10] * days}, index=idx)


_orig_ohlcv = t.get_multi_day_ohlcv
try:
    t.get_multi_day_ohlcv = _fake_ohlcv
    df = pd.DataFrame(
        {"Close": [120.0, 150.0], "composite_score": [0.5, 0.5],
         "Amount": [1e10, 1e10], "Volume": [1_000_000, 1_000_000],
         "stock_name": ["LEADER", "CLIMAX"]},
        index=["LEADER", "CLIMAX"])
    triggers = {"일중 상승률 상위주": df}
    result = t.select_final_tickers(
        triggers, trade_date="20260529", use_hybrid=True,
        macro_context={"market_regime": "sideways"})

    # Flatten selected rows
    rows = {}
    for _name, rdf in result.items():
        for tk in rdf.index:
            rows[tk] = rdf.loc[tk]
    check("returns at least one selection", len(rows) >= 1)
    _any = next(iter(rows.values()))
    for col in ("rs_score", "rs_relative", "extension_score", "extension_in_adr", "final_score"):
        check(f"selected row has '{col}'", col in _any.index)
    # Both get selected (3 slots, 2 candidates), but the redesign must SCORE the
    # non-extended LEADER above the higher-RS CLIMAX in sideways (extension weight 0.30).
    check("both candidates present (spare slots)", "LEADER" in rows and "CLIMAX" in rows)
    if "LEADER" in rows and "CLIMAX" in rows:
        check("LEADER extension_score > CLIMAX (climax penalized)",
              float(rows["LEADER"]["extension_score"]) > float(rows["CLIMAX"]["extension_score"]))
        check("LEADER final_score > CLIMAX final_score (re-rank works despite lower RS)",
              float(rows["LEADER"]["final_score"]) > float(rows["CLIMAX"]["final_score"]))
        print(f"    [info] LEADER final={float(rows['LEADER']['final_score']):.3f} "
              f"(ext={float(rows['LEADER']['extension_score']):.2f}, adr={float(rows['LEADER']['extension_in_adr']):.1f}, rs={float(rows['LEADER']['rs_score']):.2f}) | "
              f"CLIMAX final={float(rows['CLIMAX']['final_score']):.3f} "
              f"(ext={float(rows['CLIMAX']['extension_score']):.2f}, adr={float(rows['CLIMAX']['extension_in_adr']):.1f}, rs={float(rows['CLIMAX']['rs_score']):.2f})")
finally:
    t.get_multi_day_ohlcv = _orig_ohlcv

print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
sys.exit(1 if failed else 0)
