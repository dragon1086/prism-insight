# analysis/round7_h4_swing_reentry.py — 라운드7 H4: 스윙 MA35 재탈환 재진입 A/B
#
# 가설 (라운드6 잔여 후보, tasks/btc_round6_swing_lane.md §후속):
#   Lane B 는 4h MA10/35 크로스 순간에만 진입 → 크로스 레짐이 살아있는데
#   MA35 이탈로 청산된 뒤 재상승(7월형)을 놓친다. 레짐 유지 중 4h 종가가
#   MA35 를 재탈환(전봉 이하 → 당봉 초과)하면 재진입한다.
#
# 룰 (단일 고정 — 스윕 금지):
#   재진입 조건 (플랫 상태에서, 롱 기준):
#     - 현재 4h MA10 > MA35 (골든 레짐 유지)
#     - 이 레짐에서 이미 크로스 진입 이력이 있음 (재진입은 재탈환 전용)
#     - 완결 1d MA10 > MA35 (기존 필터 동일)
#     - 종가 재탈환 이벤트: c[i-1] <= ma35[i-1] AND c[i] > ma35[i]
#   체결/스탑/청산은 기존 Lane B 와 동일 (다음봉 시가, 2ATR 봉내 스탑,
#   MA35 역이탈 다음봉 시가 청산, 왕복비용 0.0015). 숏은 대칭.
#   레짐 내 재진입 횟수 무제한 (각각 새 재탈환 이벤트 필요).
#
# 반증 조건 (사전 등록, tasks/claude_btc_review.md §5 H4):
#   전체기간 누적수익 개선 없음, 또는 maxDD −40% 초과 악화.
#   3분리 기간(2020-21/2022-23/2024-26) 중 재진입이 명백히 유해한 구간 존재 시 기각.
#
# 산출: backtest/results/round7_h4_swing_reentry.json
# 실행: ../.venv-bt/bin/python -m analysis.round7_h4_swing_reentry
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from collector.store import get_connection
from analysis.round7_portfolio import _load, COST
from engine.config import SWING_STOP_ATR_MULT

SPLITS = [("2020-01-01", "2022-01-01"), ("2022-01-01", "2024-01-01"),
          ("2024-01-01", "2027-01-01")]


def simulate(reentry: bool) -> pd.DataFrame:
    conn = get_connection(None)
    d4 = _load(conn, "4h")
    d1 = _load(conn, "1d")
    conn.close()

    d1_close_time = d1["open_time"].values + 86_400_000
    t_close = (d4["open_time"] + 4 * 3_600_000).values
    i1 = np.searchsorted(d1_close_time, t_close, side="right") - 1
    d1_up = (d1["ma10"] > d1["ma35"]).values

    o = d4["open"].values; h = d4["high"].values; l = d4["low"].values
    c = d4["close"].values; atr = d4["atr14"].values
    ma10 = d4["ma10"].values; ma35 = d4["ma35"].values
    xup = (ma10 > ma35) & (np.roll(ma10, 1) <= np.roll(ma35, 1))
    xdn = (ma10 < ma35) & (np.roll(ma10, 1) >= np.roll(ma35, 1))
    xup[0] = xdn[0] = False
    dts = d4["dt"].values
    n = len(d4)

    rows = []
    pos = 0; ep = st = 0.0; ei = -1
    # 레짐 추적: +1 골든(ma10>ma35), -1 데드. traded_in_regime 는 크로스 진입 이력.
    regime = 0
    traded_in_regime = False

    for i in range(40, n - 1):
        # 레짐 갱신 (크로스 이벤트에서 리셋)
        if xup[i]:
            regime, traded_in_regime = 1, False
        elif xdn[i]:
            regime, traded_in_regime = -1, False

        if pos == 0:
            k = i1[i]
            sig = 0; kind = None
            if k >= 35:
                # 기존 Lane B 크로스 진입
                if xup[i] and d1_up[k] and c[i] > ma35[i]:
                    sig, kind = 1, "cross"
                elif xdn[i] and (not d1_up[k]) and c[i] < ma35[i]:
                    sig, kind = -1, "cross"
                # H4 재탈환 재진입 (레짐 유지 + 이 레짐에서 진입 이력 필수)
                elif reentry and traded_in_regime and i > 0:
                    if (regime == 1 and d1_up[k]
                            and c[i - 1] <= ma35[i - 1] and c[i] > ma35[i]):
                        sig, kind = 1, "recapture"
                    elif (regime == -1 and (not d1_up[k])
                            and c[i - 1] >= ma35[i - 1] and c[i] < ma35[i]):
                        sig, kind = -1, "recapture"
            if sig != 0:
                pos = sig
                ep = o[i + 1]
                st = ep - sig * SWING_STOP_ATR_MULT * atr[i]
                ei = i + 1
                traded_in_regime = True
                entry_kind = kind
        else:
            j = i
            if j >= ei:
                exited = False
                if pos == 1 and l[j] <= st:
                    x = st; b = j; exited = True
                elif pos == -1 and h[j] >= st:
                    x = st; b = j; exited = True
                elif ((pos == 1 and c[j] < ma35[j])
                      or (pos == -1 and c[j] > ma35[j])) and j + 1 < n:
                    x = o[j + 1]; b = j + 1; exited = True
                if exited:
                    rows.append({
                        # .values 가 tz 를 벗기므로 UTC 재부여 (원본이 UTC)
                        "entry_dt": pd.Timestamp(dts[ei]).tz_localize("UTC"),
                        "exit_dt": pd.Timestamp(dts[b]).tz_localize("UTC"),
                        "side": "L" if pos == 1 else "S", "kind": entry_kind,
                        "entry": ep, "exit": x,
                        "ret": pos * (x - ep) / ep - COST, "bars": b - ei,
                    })
                    pos = 0
    return pd.DataFrame(rows)


def stats(t: pd.DataFrame) -> dict:
    if len(t) == 0:
        return {"n": 0}
    r = t["ret"]
    eq = (1 + r).cumprod()
    peak = eq.cummax()
    mdd = float((eq / peak - 1).min())
    gross_win = r[r > 0].sum(); gross_loss = -r[r < 0].sum()
    return {
        "n": int(len(t)),
        "win_pct": round(100 * float((r > 0).mean()), 1),
        "cum_ret_pct": round((float(eq.iloc[-1]) - 1) * 100, 1),
        "mdd_pct": round(mdd * 100, 1),
        "pf": round(float(gross_win / gross_loss), 2) if gross_loss > 0 else None,
        "long_cum_pct": round((float((1 + r[t['side'] == 'L']).prod()) - 1) * 100, 1),
        "short_cum_pct": round((float((1 + r[t['side'] == 'S']).prod()) - 1) * 100, 1),
    }


def main() -> None:
    out: dict = {}
    for name, re in [("laneB_base", False), ("laneB_recapture", True)]:
        t = simulate(re)
        entry = {"full": stats(t)}
        for lo, hi in SPLITS:
            m = t[(t["entry_dt"] >= pd.Timestamp(lo, tz="UTC"))
                  & (t["entry_dt"] < pd.Timestamp(hi, tz="UTC"))]
            entry[f"{lo[:4]}~"] = stats(m)
        if re:
            rec = t[t["kind"] == "recapture"]
            entry["recapture_only"] = stats(rec)
        out[name] = entry
        print(f"[{name}] full={entry['full']}")
        for lo, _ in SPLITS:
            print(f"    {lo[:4]}~: {entry[f'{lo[:4]}~']}")
        if re:
            print(f"    recapture-only trades: {entry['recapture_only']}")

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round7_h4_swing_reentry.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nsaved → {res}")


if __name__ == "__main__":
    main()
