# analysis/round8_market_pulse.py — Phase B B-1: PULSE_V1 계산 + 국면 조건부 성과 관측
#
# 정의는 tasks/btc_phase_b_design.md §2 에 사전 등록된 PULSE_V1 을 문자 그대로
# 구현한다 (이 파일에서 파라미터 변경 금지 — 변경은 V2 재설계로만).
#
# 관측(§3): 라운드7 메인 연속 실행 + 스윙 시뮬의 트레이드 각각에 "진입 시각
# 이전 마지막 완결 1d 봉 기준" pulse 상태를 라벨링 (point-in-time 보장),
# 상태×방향×레인별 성과를 집계하고 사전 등록된 B-2 진행 조건을 판정한다.
#
# 산출: backtest/results/round8_pulse_observation.json
# 실행: ../.venv-bt/bin/python -m analysis.round8_market_pulse
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from collector.store import get_connection
from analysis.round7_portfolio import run_main_lane, run_swing_lane

SPOT_DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"

# --- PULSE_V1 파라미터 (사전 등록 고정값 — 수정 금지) ---
DIST_CHG = -0.005        # 분산일: 종가 -0.5% 이하
DIST_WINDOW = 25         # 분산일 카운트 창
DD_WINDOW = 60           # 고점 낙폭 창
BEAR_PRESSURE_DD = -0.15
BEAR_CONFIRM_DD = -0.25
BEAR_CONFIRM_DIST = 5
BULL_PRESSURE_DIST = 4
BULL_RECOVER_DIST = 2
FTD_MIN_DAY = 4
FTD_MAX_DAY = 13
FTD_CHG = 0.03
MA_LEN = 50
BOOT_BARS = 10           # NEUTRAL→BULL 부트스트랩: 10봉 연속 close>ma50 & dd60>-8%
BOOT_DD = -0.08

STATES = ("BULL_CONFIRMED", "BULL_UNDER_PRESSURE", "NEUTRAL",
          "BEAR_UNDER_PRESSURE", "BEAR_CONFIRMED")


def load_spot(symbol: str = "BTCUSDT") -> pd.DataFrame:
    conn = sqlite3.connect(SPOT_DB)
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume FROM spot_klines "
        "WHERE symbol=? AND timeframe='1d' AND confirmed=1 ORDER BY open_time",
        conn, params=(symbol,))
    conn.close()
    df["close_time"] = df["open_time"] + 86_400_000  # 완결 시각 (PIT 기준)
    return df


def compute_pulse(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"].values; v = df["volume"].values; low = df["low"].values
    n = len(df)
    chg = np.zeros(n); chg[1:] = c[1:] / c[:-1] - 1.0
    vol_up = np.zeros(n, dtype=bool); vol_up[1:] = v[1:] > v[:-1]
    dist = (chg <= DIST_CHG) & vol_up
    dist_count = pd.Series(dist.astype(int)).rolling(DIST_WINDOW, min_periods=1).sum().values
    roll_max = pd.Series(c).rolling(DD_WINDOW, min_periods=1).max().values
    dd60 = c / roll_max - 1.0
    new_60d_high = c >= roll_max  # V2b: 60일 종가 신고가 (BEAR 구조적 탈출구)
    ma50 = pd.Series(c).rolling(MA_LEN).mean().values
    above_ma = c > np.where(np.isnan(ma50), np.inf, ma50)
    above_streak = np.zeros(n, dtype=int)
    for i in range(n):
        above_streak[i] = above_streak[i - 1] + 1 if (i > 0 and above_ma[i]) else (1 if above_ma[i] else 0)

    # PULSE_V2 (설계서 §2 V2): BULL 측 낙폭 기준점은 BULL 진입 후 신고점(anchor).
    # V1 은 옛 60일 고점 기준이라 FTD 직후 즉시 BEAR 회귀하는 퇴화 루프였음.
    states = []
    state = "NEUTRAL"
    in_correction = False
    rally_day = 0
    corr_low = np.inf
    bull_anchor = None  # BULL 측 체류 중 신고점

    for i in range(n):
        # 조정 추적 (FTD 카운터) — 오닐 원형: 새 저점에서 리셋, 저점 후 첫
        # 상승 마감일이 rally day 1, 이후 매 봉 +1 (상승 여부 무관).
        if dd60[i] <= BEAR_PRESSURE_DD:
            in_correction = True
        if in_correction:
            if low[i] < corr_low:
                corr_low = low[i]
                rally_day = 0
            elif rally_day == 0:
                if chg[i] > 0:
                    rally_day = 1
            else:
                rally_day += 1
            ftd = (FTD_MIN_DAY <= rally_day <= FTD_MAX_DAY
                   and chg[i] >= FTD_CHG and vol_up[i])
        else:
            ftd = False

        # 상태 전이 (설계서 §2, V2 anchor 규칙)
        if state in ("BULL_CONFIRMED", "BULL_UNDER_PRESSURE"):
            bull_anchor = c[i] if bull_anchor is None else max(bull_anchor, c[i])
            if c[i] / bull_anchor - 1.0 <= BEAR_PRESSURE_DD:
                state = "BEAR_UNDER_PRESSURE"
                bull_anchor = None
        elif state == "NEUTRAL" and dd60[i] <= BEAR_PRESSURE_DD:
            state = "BEAR_UNDER_PRESSURE"
        if state == "BEAR_UNDER_PRESSURE" and (
                dd60[i] <= BEAR_CONFIRM_DD
                or (dist_count[i] >= BEAR_CONFIRM_DIST and not above_ma[i])):
            state = "BEAR_CONFIRMED"
        # V2b: FTD 또는 60일 종가 신고가(시장의 자기 증명) 로 BEAR 탈출
        if state in ("BEAR_UNDER_PRESSURE", "BEAR_CONFIRMED") and (ftd or new_60d_high[i]):
            state = "BULL_CONFIRMED"
            bull_anchor = c[i]
            in_correction = False
            corr_low = np.inf
            rally_day = 0
        if state == "NEUTRAL" and above_streak[i] >= BOOT_BARS and dd60[i] > BOOT_DD:
            state = "BULL_CONFIRMED"
            bull_anchor = c[i]
        if state == "BULL_CONFIRMED" and dist_count[i] >= BULL_PRESSURE_DIST:
            state = "BULL_UNDER_PRESSURE"
        if state == "BULL_UNDER_PRESSURE" and dist_count[i] <= BULL_RECOVER_DIST:
            state = "BULL_CONFIRMED"
        states.append(state)

    out = df.copy()
    out["state"] = states
    out["dist_count"] = dist_count
    out["dd60"] = dd60
    return out


def label_trades(trades: list[dict], pulse: pd.DataFrame, lane: str) -> list[dict]:
    close_times = pulse["close_time"].values  # ms, 완결 시각
    states = pulse["state"].values
    rows = []
    for t in trades:
        e = t["entry_dt"]
        e = e.tz_localize("UTC") if e.tzinfo is None else e
        ms = int(e.value // 1_000_000)
        k = int(np.searchsorted(close_times, ms, side="right")) - 1
        if k < 0:
            continue
        rows.append({"lane": lane, "side": t["side"],
                     "entry": str(e.date()), "r": t["r"], "state": states[k],
                     "year": e.year})
    return rows


def agg(rows: list[dict]) -> dict:
    out = {}
    for st in STATES:
        for side in ("long", "short"):
            sel = [r for r in rows if r["state"] == st and r["side"] == side]
            if not sel:
                continue
            rs = [r["r"] for r in sel]
            out[f"{st}|{side}"] = {
                "n": len(sel),
                "avg_r": round(float(np.mean(rs)), 3),
                "win_pct": round(100 * float(np.mean([r > 0 for r in rs])), 1),
                "sum_r": round(float(np.sum(rs)), 1),
            }
    return out


def main() -> None:
    pulse = compute_pulse(load_spot())
    days = pulse["state"].value_counts().to_dict()
    print("== 상태 체류 분포 (일) ==")
    for st in STATES:
        print(f"  {st:22s} {days.get(st, 0)}")

    # --- sanity 게이트 (설계서 V2b): 알려진 강세/약세 연도의 측면 우세 확인 ---
    pulse["year"] = pd.to_datetime(pulse["close_time"], unit="ms").dt.year
    bull_side = pulse["state"].str.startswith("BULL")
    yearly = pulse.groupby("year")["state"].apply(
        lambda s: round(100 * s.str.startswith("BULL").mean(), 1)).to_dict()
    print("== 연도별 BULL 측 체류 비중 (%) ==")
    print("  ", {int(k): v for k, v in yearly.items()})
    sanity = (yearly.get(2021, 0) > 50 and yearly.get(2024, 0) > 50
              and yearly.get(2025, 0) > 50 and yearly.get(2022, 100) < 50)
    print(f"SANITY GATE: {'PASS' if sanity else 'FAIL'}")

    print("\n[관측] 메인 연속 실행 + 스윙 시뮬 (라운드7 재사용) ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()

    rows = label_trades(main_tr, pulse, "main") + label_trades(swing_tr, pulse, "swing")

    result = {"sanity_gate_pass": bool(sanity),
              "yearly_bull_share_pct": {int(k): v for k, v in yearly.items()},
              "state_days": {s: int(days.get(s, 0)) for s in STATES},
              "all": agg(rows),
              "half_2022_2023": agg([r for r in rows if r["year"] <= 2023]),
              "half_2024_2026": agg([r for r in rows if r["year"] >= 2024])}

    # --- 사전 등록 판정 (설계서 §3) ---
    def mean_r(rows_, states_, side):
        sel = [r["r"] for r in rows_ if r["state"] in states_ and r["side"] == side]
        return (float(np.mean(sel)), len(sel)) if sel else (None, 0)

    verdicts = {}
    for name, sub in (("all", rows),
                      ("2022_2023", [r for r in rows if r["year"] <= 2023]),
                      ("2024_2026", [r for r in rows if r["year"] >= 2024])):
        bull, nb = mean_r(sub, ("BULL_CONFIRMED",), "long")
        bear, nr = mean_r(sub, ("BEAR_UNDER_PRESSURE", "BEAR_CONFIRMED"), "long")
        verdicts[name] = {
            "bull_conf_long_avg_r": None if bull is None else round(bull, 3),
            "bull_conf_long_n": nb,
            "bear_long_avg_r": None if bear is None else round(bear, 3),
            "bear_long_n": nr,
            "spread": None if (bull is None or bear is None) else round(bull - bear, 3),
        }
    result["verdict_inputs"] = verdicts

    spread_all = verdicts["all"]["spread"]
    h1, h2 = verdicts["2022_2023"]["spread"], verdicts["2024_2026"]["spread"]
    n_ok = verdicts["all"]["bull_conf_long_n"] >= 15
    passed = (spread_all is not None and spread_all >= 0.3
              and h1 is not None and h2 is not None and (h1 > 0) == (h2 > 0) == True
              and n_ok)
    result["B2_PROCEED"] = bool(passed)

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round8_pulse_observation.json"
    with open(res, "w") as f:
        json.dump(result, f, indent=2)

    print("\n== 상태×방향 성과 (전체) ==")
    for k, val in result["all"].items():
        print(f"  {k:30s} {val}")
    print(f"\n== 판정 입력 ==\n{json.dumps(verdicts, indent=2)}")
    print(f"\nB-2 진행 판정: {'PASS' if passed else 'FAIL'}")
    print(f"saved → {res}")


if __name__ == "__main__":
    main()
