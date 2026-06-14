# analysis/intraday_proto.py — 단방향 인트라데이 변동성 돌파 프로토타입 v0
#
# 가설 (Rocky 데이트레이딩 컨셉): 4h 추세 방향으로, 30m 레인지 돌파 시 진입,
# 짧은 구조 SL로 끊고(가용금 잠김 없음), 당일~24h 내 청산.
#
# 반과적합 원칙:
#  - 2026 데이터는 OOS 봉인 — 룰 확정 전까지 절대 미사용
#  - v0 룰은 모두 사전 고정(이 파일이 스펙). 그리드 스윕 금지.
#  - 비용은 비관적(taker+슬리피지 왕복 0.13%) 적용
#
# v0 룰 (고정):
#  - 방향 필터: 직전 확정 4h봉 기준 trend_strength(|MA10-MA35|/ATR14) >= 2.0
#    AND MA10>MA35(롱만) / MA10<MA35(숏만)  ← 라운드4에서 검증된 필터 재사용
#  - 진입: 직전 8개 30m봉(4시간) 최고가 돌파 시 stop-buy 체결 가정 (숏은 대칭)
#  - SL: 진입가 대비 max(0.6%, min(1.5%, 직전 8봉 최저가까지 거리))
#  - TP: +2R 고정 / 타임스탑: 진입 후 48봉(24h) 경과 시 종가 청산
#  - 동시 1포지션, 피라미딩 없음, 같은 봉 SL·TP 동시터치 시 SL 우선(비관적)
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from collector.store import get_connection

FEE_RT = 0.0013          # round-trip taker 0.055%x2 + slippage 0.02% ≈ 0.13%
TS_MIN = 2.0             # 검증된 4h 추세강도 게이트 (라운드4)
BREAK_N = 8              # 30m x 8 = 4h 레인지
SL_MIN, SL_MAX = 0.006, 0.015
TP_R = 2.0
TIME_STOP_BARS = 48      # 24h
RISK = 0.01              # 1% per trade (복리 평가용)


def load_tf(conn, tf: str) -> pd.DataFrame:
    df = pd.read_sql(
        f"SELECT open_time, open, high, low, close FROM klines "
        f"WHERE timeframe='{tf}' AND confirmed=1 ORDER BY open_time", conn)
    df["t"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    return df.reset_index(drop=True)


# NOTE: 이 venv의 pandas는 ~6.5만행 이상 Series에서 rolling 집계가 전부 NaN을
# 반환하는 빌드 버그가 있다 (10000행 OK, 78000행 전멸 — 합성 데이터로 재현됨).
# 30m 프레임(7.8만행)이 걸리므로 rolling은 numpy sliding_window_view로 직접 계산.
def _roll(arr: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) >= n:
        w = np.lib.stride_tricks.sliding_window_view(arr, n)
        out[n - 1:] = fn(w, axis=1)
    return out


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    return pd.Series(_roll(tr.to_numpy(), n, np.nanmean), index=df.index)


conn_ref = [None]  # prep 내 1d 로드용


def prep(conn):
    conn_ref[0] = conn
    m30 = load_tf(conn, "30m")
    h4 = load_tf(conn, "4h")
    h4["ma10"] = _roll(h4.close.to_numpy(), 10, np.mean)
    h4["ma35"] = _roll(h4.close.to_numpy(), 35, np.mean)
    h4["atr14"] = atr(h4)
    h4["ts"] = (h4.ma10 - h4.ma35).abs() / h4.atr14
    h4["dir"] = np.sign(h4.ma10 - h4.ma35)
    # 4h bar is usable only AFTER it closes: close time = open + 4h
    h4["avail"] = h4.t + pd.Timedelta(hours=4)
    f = pd.merge_asof(m30, h4[["avail", "ts", "dir"]], left_on="t", right_on="avail")
    # v0.1: 1d 방향 일치 필터 (분해 분석 근거 — 2022 롱 -0.34R/2023 숏 -0.31R은
    # 전부 4h dir이 상위추세와 역행한 구간. 부호 일치 조건만 추가, 임계값 없음)
    d1 = load_tf(conn_ref[0], "1d")
    d1["ma10"] = _roll(d1.close.to_numpy(), 10, np.mean)
    d1["ma35"] = _roll(d1.close.to_numpy(), 35, np.mean)
    d1["dir1d"] = np.sign(d1.ma10 - d1.ma35)
    d1["avail"] = d1.t + pd.Timedelta(days=1)
    f = pd.merge_asof(f, d1[["avail", "dir1d"]].rename(columns={"avail": "avail1d"}),
                      left_on="t", right_on="avail1d")
    # breakout levels from PREVIOUS BREAK_N bars (exclude current)
    f["hh"] = pd.Series(_roll(f.high.to_numpy(), BREAK_N, np.max), index=f.index).shift(1)
    f["ll"] = pd.Series(_roll(f.low.to_numpy(), BREAK_N, np.min), index=f.index).shift(1)
    return f


def run(f: pd.DataFrame, start: str, end: str, exit_mode: str = "tp2r"):
    """exit_mode:
    - "tp2r": v0.1 — TP +2R 고정 + 24h 타임스탑 (승자 절단형 — 비교 기준)
    - "trail": v0.2 — TP 없음. 트레일 스탑 = 돌파에 쓴 동일 8봉 레인지의 반대편
      (롱: prev-8bar low가 올라올 때만 상향). 신규 파라미터 0개. 추세 라이딩.
    버그수정(비관화): 갭 진입은 open 체결, 진입봉 내 SL 터치 인정(SL 우선).
    """
    s_ = pd.Timestamp(start, tz="UTC"); e_ = pd.Timestamp(end, tz="UTC")
    g = f[(f.t >= s_) & (f.t <= e_)].reset_index(drop=True)
    hh_a = g.hh.to_numpy(); ll_a = g.ll.to_numpy()
    hi_a = g.high.to_numpy(); lo_a = g.low.to_numpy()
    op_a = g.open.to_numpy(); cl_a = g.close.to_numpy()
    ts_a = g.ts.to_numpy(); dir_a = g.dir.to_numpy(); d1_a = g.dir1d.to_numpy()
    trades = []
    equity = 1.0
    i, n = 0, len(g)
    while i < n:
        if not (np.isfinite(hh_a[i]) and np.isfinite(ts_a[i])) or ts_a[i] < TS_MIN \
           or not np.isfinite(d1_a[i]) or dir_a[i] != d1_a[i]:
            i += 1; continue
        side = 0
        if dir_a[i] > 0 and hi_a[i] > hh_a[i]:
            side, entry = 1, max(hh_a[i], op_a[i])     # 갭 시 open 체결 (비관)
        elif dir_a[i] < 0 and lo_a[i] < ll_a[i]:
            side, entry = -1, min(ll_a[i], op_a[i])
        if side == 0:
            i += 1; continue
        raw = (entry - ll_a[i]) / entry if side == 1 else (hh_a[i] - entry) / entry
        sl_d = min(max(raw, SL_MIN), SL_MAX)
        stop = entry * (1 - side * sl_d)
        tp = entry * (1 + side * sl_d * TP_R)
        exit_px = reason = None
        # 진입봉 내 SL: 종가 기준만 인정 (논리적 확정 — 돌파(진입) 후 종가가 스탑
        # 아래면 경로상 entry→stop 하락이 반드시 발생. low만 아래인 경우는 돌파 전
        # 저가일 가능성이 높아 모호 → 미히트 처리. 과소/과대 비관 모두 회피)
        if (side == 1 and cl_a[i] <= stop) or (side == -1 and cl_a[i] >= stop):
            exit_px, reason, j = stop, "sl", i
        else:
            last_j = min(i + TIME_STOP_BARS, n - 1) if exit_mode == "tp2r" else n - 1
            j = i
            for j in range(i + 1, last_j + 1):
                if exit_mode == "trail":
                    # 트레일 갱신은 직전 봉까지의 정보(ll/hh는 이미 shift됨)
                    if side == 1 and np.isfinite(ll_a[j]):
                        stop = max(stop, ll_a[j])
                    elif side == -1 and np.isfinite(hh_a[j]):
                        stop = min(stop, hh_a[j])
                if side == 1 and lo_a[j] <= stop:
                    px = min(stop, op_a[j])  # 갭다운 개장 시 open 체결 (비관)
                    exit_px, reason = px, ("trail" if exit_mode == "trail" and stop > entry * (1 - sl_d) else "sl"); break
                if side == -1 and hi_a[j] >= stop:
                    px = max(stop, op_a[j])  # 갭업 개장 시 open 체결 (비관)
                    exit_px, reason = px, ("trail" if exit_mode == "trail" and stop < entry * (1 + sl_d) else "sl"); break
                if exit_mode == "tp2r":
                    if side == 1 and hi_a[j] >= tp: exit_px, reason = tp, "tp"; break
                    if side == -1 and lo_a[j] <= tp: exit_px, reason = tp, "tp"; break
            if exit_px is None:
                exit_px, reason = cl_a[j], "time"
        gross_r = side * (exit_px - entry) / entry / sl_d
        net_r = gross_r - FEE_RT / sl_d
        equity *= (1 + RISK * net_r)
        trades.append((g.t.iloc[i], side, sl_d, reason, gross_r, net_r))
        i = j + 1
    return trades, equity


def report(label, trades, equity):
    if not trades:
        print(f"{label:<14} n=0"); return
    df = pd.DataFrame(trades, columns=["t", "side", "sl_d", "reason", "gross_r", "net_r"])
    wins = df[df.net_r > 0].net_r.sum(); losses = -df[df.net_r <= 0].net_r.sum()
    pf = wins / losses if losses > 0 else float("inf")
    days = (df.t.iloc[-1] - df.t.iloc[0]).days or 1
    print(f"{label:<14} n={len(df):4d} ({len(df)/days*30:4.1f}/월) win%={100*(df.net_r>0).mean():5.1f} "
          f"avgR(net)={df.net_r.mean():+.3f} PF={pf:5.2f} ret={100*(equity-1):+7.1f}% "
          f"sl/tp/time={sum(df.reason=='sl')}/{sum(df.reason=='tp')}/{sum(df.reason=='time')} "
          f"long/short={sum(df.side==1)}/{sum(df.side==-1)}")


def main():
    conn = get_connection(None)
    f = prep(conn)
    conn.close()
    periods = [
        ("2022-01-01", "2022-12-31", "2022_bear"),
        ("2023-01-01", "2023-12-31", "2023_side"),
        ("2024-01-01", "2024-12-31", "2024_bull1"),
        ("2025-01-01", "2025-12-31", "2025_bull2"),
    ]
    for mode in ("tp2r", "trail"):
        print(f"--- exit_mode={mode} ---")
        for start, end, label in periods:
            trades, eq = run(f, start, end, exit_mode=mode)
            report(label, trades, eq)


if __name__ == "__main__":
    main()
