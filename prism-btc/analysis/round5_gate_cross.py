# analysis/round5_gate_cross.py — 라운드5: score × ts_4h 교차셀 forward-edge 진단
#
# 배경 (2026-07, 라이브 20일 무매매 관찰): score 게이트(|70|)와 ts_4h 게이트(2.0)가
# 서로 다른 시점에 켜져(ts는 추세 초기, score는 전 TF 정렬 후반) 동시 통과가 드물다는
# 가설이 제기됨. 기존 H1 연구(h1_signal_power)는 score 버킷과 ts 버킷을 각각
# marginal 로만 측정했고 교차셀은 본 적 없음.
#
# 목적: 고정된 교차셀(score band × ts_4h band)의 방향조정 forward return 을
# 2020–2026 전체 표본에서 측정해 (1) 현행 게이트의 타당성 재검증,
# (2) "조기 강추세 레인"(55–70 × ts>=3.5) 가설의 표본 확장 검증.
# 최적화 스윕 아님 — 셀 경계는 기존 연구(v3_edge_diagnosis §1)의 버킷 그대로.
#
# 실행 (prism-btc 패키지 루트에서):
#   python -m analysis.round5_gate_cross [--db state/btc_market.db]
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from engine.indicators import add_indicators
from engine.regime import RegimeSnapshot, build_tf_state, compute_alignment_score
from engine.signal import (
    ENTRY_TRIGGER_TF,
    LONG_ENTRY_POSITIONS,
    SHORT_ENTRY_POSITIONS,
    _entry_tf_aligned,
    _long_tf_direction_negative,
    _long_tf_direction_positive,
    trend_strength,
)

ALL_TFS = ("30m", "1h", "4h", "12h", "1d", "1w")
EVAL_TF = "4h"
FWD_TF = "1h"
HORIZONS = {"3d": 72, "7d": 168, "14d": 336}  # 1h bars

# 셀 경계 — 기존 연구 버킷 고정 (스윕 금지)
SCORE_BANDS = [(40, 55, "40-55"), (55, 70, "55-70"), (70, 85, "70-85"), (85, 101, "85+")]
TS_BANDS = [(0.0, 2.0, "ts<2"), (2.0, 3.5, "ts2-3.5"), (3.5, 99.0, "ts3.5+")]
PERIODS = [("2020", "2021"), ("2022", "2023"), ("2024", "2026")]
MIN_ABS_SCORE = 40.0  # 표본 하한 — 이보다 약한 정렬은 어떤 가설 셀에도 안 쓰임


def load_tf(conn: sqlite3.Connection, tf: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover "
        "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time ASC",
        conn, params=(tf,))
    df = add_indicators(df)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df


def extract_signals(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    data = {tf: load_tf(conn, tf) for tf in ALL_TFS}
    conn.close()
    for tf, df in data.items():
        print(f"[load] {tf}: {len(df)} bars {df['dt'].min()} .. {df['dt'].max()}")
        # 환경 가드: 일부 로컬 환경(Python 3.14 + pandas 2.3.3 + numpy 1.26)에서
        # ~1.6만 행 이상 시리즈의 rolling 이 전부 NaN 을 반환하는 버그 확인됨
        # (2026-07). 조용히 0 신호가 되는 대신 즉시 실패시킨다.
        if len(df) >= 35 and df["ma10"].isna().all():
            raise RuntimeError(
                f"indicator computation broken in this environment "
                f"({tf}: all-NaN ma10, {len(df)} rows) — pandas rolling bug. "
                f"Run on a known-good env (e.g. db-server pyenv 3.11).")

    fwd = data[FWD_TF].dropna(subset=["ma10", "ma35", "atr14"]).reset_index(drop=True)
    fwd_times = fwd["open_time"].values
    fwd_close = fwd["close"].values

    eval_df = data[EVAL_TF].dropna(subset=["ma10", "ma35", "atr14"]).reset_index(drop=True)
    tf_arr = {tf: data[tf]["open_time"].values for tf in ALL_TFS}

    rows = []
    for i in range(len(eval_df)):
        t = eval_df["open_time"].iloc[i]
        tf_states = {}
        ok = True
        for tf in ALL_TFS:
            arr = tf_arr[tf]
            idx = np.searchsorted(arr, t, side="right") - 1
            if idx < 34:
                ok = False
                break
            try:
                tf_states[tf] = build_tf_state(data[tf].iloc[:idx + 1])
            except ValueError:
                ok = False
                break
        if not ok:
            continue

        score = compute_alignment_score(tf_states)
        if abs(score) < MIN_ABS_SCORE:
            continue

        # 진입 경로의 비(非)게이트 조건을 라이브와 동일하게 요구:
        # 장기TF 가중방향 + 4h 트리거 캔들 위치. 게이트(score/ts)만 셀에서 조건화.
        if score > 0:
            if not _long_tf_direction_positive(tf_states):
                continue
            if not _entry_tf_aligned(tf_states, ENTRY_TRIGGER_TF, LONG_ENTRY_POSITIONS):
                continue
            side, sgn = "long", 1.0
        else:
            if not _long_tf_direction_negative(tf_states):
                continue
            if not _entry_tf_aligned(tf_states, ENTRY_TRIGGER_TF, SHORT_ENTRY_POSITIONS):
                continue
            side, sgn = "short", -1.0

        # 진입가 근사: 이 4h 확정봉 직후의 1h 종가 (H1 연구와 동일)
        j = np.searchsorted(fwd_times, t, side="right") - 1
        if j < 0:
            continue
        p0 = fwd_close[j]
        rec = {
            "t": t, "side": side, "score": score,
            "ts_4h": trend_strength(tf_states["4h"]),
            "ts_1d": trend_strength(tf_states["1d"]),
        }
        valid = True
        for name, nbars in HORIZONS.items():
            k = j + nbars
            if k >= len(fwd_close):
                valid = False
                break
            rec[f"fwd_{name}"] = sgn * (fwd_close[k] - p0) / p0
        if valid:
            rows.append(rec)

    df = pd.DataFrame(rows)
    df["dt"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    return df


def summarize(d: pd.DataFrame, label: str) -> None:
    if len(d) == 0:
        print(f"{label:34s} n=   0")
        return
    parts = [f"{label:34s} n={len(d):4d}"]
    for h in HORIZONS:
        f = d[f"fwd_{h}"]
        parts.append(f"{h}: {f.mean() * 100:+.2f}%/{(f > 0).mean() * 100:.0f}%")
    print("  ".join(parts))


def report(df: pd.DataFrame) -> None:
    df = df.copy()
    df["abs_score"] = df["score"].abs()

    print(f"\n=== 표본: {df.dt.min():%Y-%m-%d} ~ {df.dt.max():%Y-%m-%d}, n={len(df)} "
          f"(방향조정 forward, mean%/hit%) ===")

    print("\n--- score × ts_4h 교차셀 ---")
    for slo, shi, sn in SCORE_BANDS:
        for tlo, thi, tn in TS_BANDS:
            d = df[(df.abs_score >= slo) & (df.abs_score < shi)
                   & (df.ts_4h >= tlo) & (df.ts_4h < thi)]
            summarize(d, f"score{sn} x {tn}")
        print()

    print("--- 룰 단위 요약 ---")
    cur = df[(df.abs_score >= 70) & (df.ts_4h >= 2.0)]
    summarize(cur, "현행 (|score|>=70 & ts>=2.0)")
    early = df[(df.abs_score >= 55) & (df.abs_score < 70) & (df.ts_4h >= 3.5)]
    summarize(early, "조기레인 추가분 (55-70 & ts>=3.5)")
    two = df[((df.abs_score >= 70) & (df.ts_4h >= 2.0))
             | ((df.abs_score >= 55) & (df.ts_4h >= 3.5))]
    summarize(two, "2레인 합계")

    print("\n--- 기간별 안정성 ---")
    for y0, y1 in PERIODS:
        p = df[(df.dt >= f"{y0}-01-01") & (df.dt <= f"{y1}-12-31")]
        summarize(p[(p.abs_score >= 70) & (p.ts_4h >= 2.0)], f"{y0}-{y1} 현행")
        summarize(p[(p.abs_score >= 55) & (p.abs_score < 70) & (p.ts_4h >= 3.5)],
                  f"{y0}-{y1} 조기레인 추가분")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path(__file__).resolve().parents[1]
                                            / "state" / "btc_market.db"))
    parser.add_argument("--out", default=None, help="신호 CSV 저장 경로 (선택)")
    args = parser.parse_args()

    df = extract_signals(args.db)
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"[save] {args.out} ({len(df)} rows)")
    report(df)


if __name__ == "__main__":
    main()
