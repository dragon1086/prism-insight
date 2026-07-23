# analysis/round7_portfolio.py — 라운드7 E2: 메인+스윙 통합 포트폴리오 baseline (1차 근사)
#
# 목적: 핸드오프 13절 Q6 — "메인과 스윙을 합친 계좌 수준 위험·CAGR·MDD" 최초 측정.
#
# 방법 (근사임을 명시):
#   메인: backtest.engine.run_backtest 연속 실행 (2022-01-01 ~ 2026-07-22).
#     포지션별 계좌영향 f = r_multiple × (RISK_PER_TRADE × TRANCHE_FRACS[k]).
#     (r_multiple = net_pnl / initial_risk, initial_risk = equity@entry × 2% × frac
#      이므로 이 변환은 정의상 정확. 트랜치 중첩의 equity 시점차만 1차 근사.)
#   스윙: round6 Lane B 룰 문자 그대로 미러 (4h MA10/35 크로스 + 완결 1d 일치 +
#     4h 종가 MA35 순방향, 다음 4h봉 시가 진입, 2ATR 봉내 스탑, MA35 역이탈
#     다음봉 시가 청산, 왕복비용 0.0015). 사이징은 core/swing.py 미러:
#     f = price_ret_net × min(0.01 / stop_dist_pct, 5.0).
#   결합: 청산 시각 순으로 공유 equity 에 복리 적용 (closed-trade equity).
#     스윙 신규 진입은 메인이 반대방향 보유 중이면 금지 (conflicts_with_main 미러).
#
# 한계 (해석 시 필수):
#   - closed-trade equity 기준 MDD 는 intra-trade drawdown 을 과소평가한다.
#   - 두 레인이 같은 계좌를 쓰되 각자 자기 사이징 규칙으로 독립 계산 — margin
#     상호작용/부분체결/동시노출 캡은 미반영 (완전 공동 시뮬은 후속 과제).
#   - 스윙 레버리지 캡(5x) 바인딩 시 실효 리스크 <1% (core/swing.py 와 동일).
#
# 실행 (prism-btc 패키지 루트, .venv-bt 필수):
#   ../.venv-bt/bin/python -m analysis.round7_portfolio
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import backtest.engine as be
from collector.store import get_connection
from engine.indicators import add_indicators
from engine.sizing import RISK_PER_TRADE, TRANCHE_FRACS
from engine.config import SWING_RISK_PER_TRADE, SWING_MAX_LEVERAGE, SWING_STOP_ATR_MULT

COST = 0.0015  # round6 과 동일 (왕복 수수료+슬리피지)

START = "2022-01-01"
END = "2026-07-22"
EXT_START = "2022-03-28"   # 외부 비교 전략 구간
EXT_END = "2026-07-23"
INITIAL = 10_000.0


def _load(conn: sqlite3.Connection, tf: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover FROM klines "
        "WHERE timeframe=? AND confirmed=1 ORDER BY open_time ASC",
        conn, params=(tf,))
    df = add_indicators(df)
    if len(df) >= 35 and df["ma10"].isna().all():
        raise RuntimeError(f"indicator broken ({tf}) — pandas rolling bug; use .venv-bt")
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.dropna(subset=["ma10", "ma35", "atr14"]).reset_index(drop=True)


def run_main_lane() -> list[dict]:
    """메인 연속 백테스트 → [{exit_dt, entry_dt, side, f}] (f = 계좌영향 비율)."""
    conn = get_connection(None)
    state = be.run_backtest(
        conn, pd.Timestamp(START, tz="UTC"), pd.Timestamp(END, tz="UTC"),
        initial_equity=INITIAL,
    )
    conn.close()
    trades = []
    for t in state.trade_logs:
        frac = RISK_PER_TRADE * TRANCHE_FRACS[min(t.tranche_index, len(TRANCHE_FRACS) - 1)]
        trades.append({
            "lane": "main",
            "entry_dt": pd.Timestamp(t.entry_time),
            "exit_dt": pd.Timestamp(t.exit_time),
            "side": t.side,
            "f": t.r_multiple * frac,
            "r": t.r_multiple,
        })
    return trades


def run_swing_lane(conn: sqlite3.Connection) -> list[dict]:
    """round6 Lane B 미러 → 계좌영향 비율 트레이드 리스트 (전 기간, 필터는 호출부)."""
    d4 = _load(conn, "4h")
    d1 = _load(conn, "1d")

    d1_close_time = d1["open_time"].values + 86_400_000
    d4 = d4.assign(t_close=d4["open_time"] + 4 * 3_600_000)
    i1 = np.searchsorted(d1_close_time, d4["t_close"].values, side="right") - 1
    d1_up = (d1["ma10"] > d1["ma35"]).values

    o = d4["open"].values; h = d4["high"].values; l = d4["low"].values
    c = d4["close"].values; atr = d4["atr14"].values; ma35 = d4["ma35"].values
    ma10 = d4["ma10"].values
    xup = (ma10 > ma35) & (np.roll(ma10, 1) <= np.roll(ma35, 1))
    xdn = (ma10 < ma35) & (np.roll(ma10, 1) >= np.roll(ma35, 1))
    xup[0] = xdn[0] = False
    dts = d4["dt"].values
    n = len(d4)

    trades = []
    pos = 0; ep = st = 0.0; ei = -1
    for i in range(40, n - 1):
        if pos == 0:
            k = i1[i]
            sig = 0
            if k >= 35:
                if xup[i] and d1_up[k] and c[i] > ma35[i]:
                    sig = 1
                elif xdn[i] and (not d1_up[k]) and c[i] < ma35[i]:
                    sig = -1
            if sig != 0:
                pos = sig
                ep = o[i + 1]
                st = ep - sig * SWING_STOP_ATR_MULT * atr[i]
                ei = i + 1
        else:
            j = i
            if j >= ei:
                exited = False
                if pos == 1 and l[j] <= st:
                    x = st; b = j; exited = True
                elif pos == -1 and h[j] >= st:
                    x = st; b = j; exited = True
                elif ((pos == 1 and c[j] < ma35[j]) or (pos == -1 and c[j] > ma35[j])) and j + 1 < n:
                    x = o[j + 1]; b = j + 1; exited = True
                if exited:
                    stop_dist_pct = abs(ep - st) / ep
                    lev = min(SWING_RISK_PER_TRADE / stop_dist_pct, SWING_MAX_LEVERAGE)
                    price_ret = pos * (x - ep) / ep - COST
                    trades.append({
                        "lane": "swing",
                        "entry_dt": pd.Timestamp(dts[ei]),
                        "exit_dt": pd.Timestamp(dts[b]),
                        "side": "long" if pos == 1 else "short",
                        "f": price_ret * lev,
                        "r": price_ret / (stop_dist_pct + COST),
                    })
                    pos = 0
    return trades


def _combine(main_tr: list[dict], swing_tr: list[dict], lo: str, hi: str,
             block_conflicts: bool = True) -> dict:
    lo_ts = pd.Timestamp(lo, tz="UTC"); hi_ts = pd.Timestamp(hi, tz="UTC")

    def _tz(ts: pd.Timestamp) -> pd.Timestamp:
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts

    m = [t for t in main_tr if lo_ts <= _tz(t["entry_dt"]) < hi_ts]
    s = [t for t in swing_tr if lo_ts <= _tz(t["entry_dt"]) < hi_ts]

    # 스윙 진입 시 메인이 반대방향 보유 중이면 진입 금지 (conflicts_with_main 미러)
    if block_conflicts:
        kept = []
        for t in s:
            e = _tz(t["entry_dt"])
            conflict = any(
                _tz(mt["entry_dt"]) <= e < _tz(mt["exit_dt"]) and mt["side"] != t["side"]
                for mt in m
            )
            if not conflict:
                kept.append(t)
        blocked = len(s) - len(kept)
        s = kept
    else:
        blocked = 0

    def curve(trs: list[dict]) -> dict:
        if not trs:
            return {"n": 0}
        trs = sorted(trs, key=lambda t: _tz(t["exit_dt"]))
        eq = 1.0; peak = 1.0; mdd = 0.0
        for t in trs:
            eq *= (1.0 + t["f"])
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1.0)
        yrs = ( _tz(trs[-1]["exit_dt"]) - _tz(trs[0]["entry_dt"]) ).days / 365.25
        cagr = eq ** (1 / max(yrs, 0.1)) - 1
        wins = sum(1 for t in trs if t["f"] > 0)
        return {
            "n": len(trs),
            "cum_ret_pct": round((eq - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "mdd_pct": round(mdd * 100, 1),
            "calmar": round(cagr / abs(mdd), 2) if mdd < 0 else None,
            "win_pct": round(100 * wins / len(trs), 1),
        }

    return {
        "window": f"{lo} ~ {hi}",
        "swing_blocked_by_conflict": blocked,
        "main_only": curve(m),
        "swing_only": curve(s),
        "combined": curve(m + s),
    }


def main() -> None:
    print("[E2] 메인 연속 백테스트 실행 중 ...")
    main_tr = run_main_lane()
    print(f"  main trades: {len(main_tr)}")

    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()
    print(f"  swing trades (전 기간): {len(swing_tr)}")

    out = {
        "method": "closed-trade shared-equity 1st-order approximation "
                  "(see file header for caveats)",
        "full_window": _combine(main_tr, swing_tr, START, END),
        "external_comparison_window": _combine(main_tr, swing_tr, EXT_START, EXT_END),
    }

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round7_portfolio_baseline.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)

    for k in ("full_window", "external_comparison_window"):
        w = out[k]
        print(f"\n== {w['window']} (swing conflict-blocked: {w['swing_blocked_by_conflict']}) ==")
        for lane in ("main_only", "swing_only", "combined"):
            print(f"  {lane:10s}: {w[lane]}")
    print(f"\nsaved → {res}")


if __name__ == "__main__":
    main()
