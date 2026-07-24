# analysis/round7_h1h2_ab.py — 라운드7: H1/H2 A/B 실험 러너
#
# 가설·파라미터·반증 조건은 tasks/claude_btc_review.md §5 에 사전 고정.
#   H1 = EXIT_EVAL_ALWAYS   : 신호청산 평가 상시화 (F1 공백 제거)
#       반증: 3분리구간 중 1곳이라도 MDD 악화 >2%p 또는 CAGR(총수익) 1%p 초과 악화
#   H2 = SIGNAL_REDUCE_ONCE : reduce 1회 latch + 새 유리 극값 갱신 시 재무장
#       반증: PF·tail R 개선 없음
# 근거 (E1 계측, {label}_instrumentation.json):
#   - 감축 포지션 평균 20~50회 연속 반토막 (최대 245회), 평균 MFE +0.7~4.3R 이
#     최종 음수 R 로 귀결 — 승자 절단 + taker 수수료 폭증.
#   - 포지션 보유 바의 41~72% 에서 신호청산 평가가 게이트에 막힘 (F1).
#
# 원칙: 파라미터 스윕 금지 — 변형은 사전 등록된 4개(base/H1/H2/H1+H2)뿐.
# 표준 결과 파일(backtest/results/{label}_*.json/csv)은 절대 덮어쓰지 않는다.
# 산출: backtest/results/round7_h1h2_ab.json
#
# 실행 (prism-btc 패키지 루트, .venv-bt 필수):
#   ../.venv-bt/bin/python -m analysis.round7_h1h2_ab
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import backtest.engine as be
from collector.store import get_connection

PERIODS = [
    ("2022-01-01", "2022-12-31", "2022_bear"),
    ("2023-01-01", "2023-12-31", "2023_sideways"),
    ("2024-01-01", "2025-12-31", "2024_2025_bull"),
    ("2026-01-01", "2026-06-09", "2026_ytd"),
]

# (이름, EXIT_EVAL_ALWAYS, SIGNAL_REDUCE_ONCE, rv2_sides)
# rv2_sides: None = R-v2 OFF, 그 외 REDUCE_AS_PYRAMID_BLOCK 적용 방향 튜플.
# R-v2: reduce 를 축소가 아니라 "피라미딩 중단" 신호로 격하 (H1/H2 기각 후
# 후속 단일 변형 — Rocky 방향성 위임 하에 사전 등록, 반증 조건은 태스크 #5).
# Rv2_long_only: 전방향 A/B 에서 개선=전량롱 구간, 악화=전량숏 구간으로 갈려
# 오닐 비대칭 독트린(핸드오프 7.6)에 따라 롱만 격하하는 후속 단일 변형.
# 반증: 2023 개선 +2%p 미만, 또는 타 구간 CAGR 1%p 초과 / MDD 2%p 초과 악화.
VARIANTS = [
    ("base", False, False, None),
    ("H1_exit_always", True, False, None),
    ("H2_reduce_latch", False, True, None),
    ("H1+H2", True, True, None),
    ("Rv2_pyramid_block", False, False, ("long", "short")),
    ("Rv2+H1", True, False, ("long", "short")),
    ("Rv2_long_only", False, False, ("long",)),
]

INITIAL = 10_000.0

KEEP_KEYS = (
    "total_return_pct", "mdd_pct", "profit_factor", "win_rate_pct",
    "avg_r", "trade_count", "total_fees", "total_funding",
    "liq_approach_count", "long_trades", "short_trades",
)


def main() -> None:
    out: dict = {}
    for name, h1, h2, rv2_sides in VARIANTS:
        be.EXIT_EVAL_ALWAYS = h1
        be.SIGNAL_REDUCE_ONCE = h2
        be.REDUCE_AS_PYRAMID_BLOCK = rv2_sides is not None
        be.REDUCE_BLOCK_SIDES = rv2_sides or ("long", "short")
        out[name] = {}
        for start, end, label in PERIODS:
            conn = get_connection(None)
            state = be.run_backtest(
                conn,
                pd.Timestamp(start, tz="UTC"),
                pd.Timestamp(end, tz="UTC"),
                initial_equity=INITIAL,
            )
            conn.close()
            m = be.compute_metrics(state, INITIAL)
            keep = {k: m[k] for k in KEEP_KEYS if k in m}
            keep["reduce_events"] = len(state.instr_reduce_events)
            keep["signal_eval_skipped_bars"] = state.instr_signal_eval_skipped_bars
            out[name][label] = keep
            print(
                f"[{name:16s}] {label:16s} ret={keep['total_return_pct']:>7.2f}% "
                f"mdd={keep['mdd_pct']:>5.2f}% pf={keep['profit_factor']:>6.3f} "
                f"n={keep['trade_count']:>3d} fees=${keep['total_fees']:>8.1f} "
                f"reduces={keep['reduce_events']:>4d}"
            )
    # 동결 상태 복원 (다른 코드가 이 모듈을 재사용할 경우 대비)
    be.EXIT_EVAL_ALWAYS = False
    be.SIGNAL_REDUCE_ONCE = False
    be.REDUCE_AS_PYRAMID_BLOCK = False
    be.REDUCE_BLOCK_SIDES = ("long", "short")

    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round7_h1h2_ab.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nsaved → {res}")


if __name__ == "__main__":
    main()
