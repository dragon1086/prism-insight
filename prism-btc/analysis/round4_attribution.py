# analysis/round4_attribution.py — 라운드4 변경 귀인 분석 (최적화 스윕 아님)
# 목적: 게이트 상향(85/2.0) vs 청산 변경(1.5R/12h) 중 무엇이 성과 변화를 유발했는지
# 2x2 매트릭스로 분리 측정. 각 셀은 진단 문서에 기록된 값만 사용.
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.WARNING)

import pandas as pd

import engine.config as cfg
import backtest.engine as bte
from backtest.engine import run_backtest, compute_metrics
from collector.store import get_connection

PERIODS = [
    ("2022-01-01", "2022-12-31", "2022_bear"),
    ("2023-01-01", "2023-12-31", "2023_side"),
    ("2024-01-01", "2025-12-31", "2024_25_bull"),
]

# (label, ENTRY_SCORE_MIN, TS_MIN, TS_GATE_TFS, BE_TRAIL_ACTIVATE_R, TRAILING_TF)
# 셀 구성 원칙: 진단 문서(v3_edge_diagnosis §1)에 기록된 가설만. 그리드 스윕 금지.
#  - score 70: 역엣지가 입증된 55–70 버킷만 제거 (70–85는 14d +1.78%로 보존)
#  - 4h-only ts2: H1 연구가 측정한 것은 4h ts 단독 버킷 — 1d 동시조건은 연구 외 가정
CELLS = [
    ("R3 baseline (55/1.0both + 1R/1h)", 55.0, 1.0, ("4h", "1d"), 1.0, "1h"),
    ("gate-only   (85/2.0both + 1R/1h)", 85.0, 2.0, ("4h", "1d"), 1.0, "1h"),
    ("exit-only   (55/1.0both + 1.5R/12h)", 55.0, 1.0, ("4h", "1d"), 1.5, "12h"),
    ("round4 full (85/2.0both + 1.5R/12h)", 85.0, 2.0, ("4h", "1d"), 1.5, "12h"),
    ("score70     (70/1.0both + 1.5R/12h)", 70.0, 1.0, ("4h", "1d"), 1.5, "12h"),
    ("ts2-4honly  (55/2.0-4h + 1.5R/12h)", 55.0, 2.0, ("4h",), 1.5, "12h"),
    ("combo       (70/2.0-4h + 1.5R/12h)", 70.0, 2.0, ("4h",), 1.5, "12h"),
]


def main() -> None:
    conn = get_connection(None)
    print(f"{'cell':<34} {'period':<13} {'ret%':>7} {'mdd%':>6} {'PF':>6} "
          f"{'win%':>6} {'n':>4} {'avgR':>7} {'grossR':>7} {'liq':>4}")
    for label, score_min, ts_min, gate_tfs, act_r, trail_tf in CELLS:
        cfg.ENTRY_SCORE_MIN = score_min
        cfg.TS_MIN = ts_min
        cfg.TS_GATE_TFS = gate_tfs
        bte.BE_TRAIL_ACTIVATE_R = act_r
        bte.TRAILING_TF = trail_tf
        for start, end, pname in PERIODS:
            st = run_backtest(
                conn, pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                initial_equity=10_000.0,
            )
            m = compute_metrics(st, 10_000.0)
            print(f"{label:<34} {pname:<13} {m['total_return_pct']:>7.2f} "
                  f"{m['mdd_pct']:>6.2f} {m['profit_factor']:>6.3f} "
                  f"{m['win_rate_pct']:>6.1f} {m['trade_count']:>4d} "
                  f"{m.get('avg_r', float('nan')):>7.3f} "
                  f"{m.get('gross_avg_r', float('nan')):>7.3f} "
                  f"{m.get('liq_approach_count', 0):>4d}")
    conn.close()


if __name__ == "__main__":
    main()
