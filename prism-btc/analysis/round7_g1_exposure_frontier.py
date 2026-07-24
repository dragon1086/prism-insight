# analysis/round7_g1_exposure_frontier.py — 라운드7 G-1 준비: 노출 배수 결정 테이블
#
# 목적: E2 통합 baseline (round7_portfolio.py) 의 트레이드 리스트에 위험 배수
# k ∈ {1.0, 1.25, 1.5, 1.75, 2.0} 를 적용했을 때의 CAGR/MDD/Calmar 프론티어.
# Rocky 가 "감내할 MDD 행"을 고르면 그것이 통합 governor(G-1)의 위험 예산이 된다.
#
# 주의 (측정 전용, 라이브 무변경):
#   - closed-trade equity 기준 — intra-trade MDD 과소평가. 실제 감내 MDD 는
#     표의 1.5~2배로 상정하고 보수적으로 고를 것.
#   - 배수 적용은 f→k·f (트레이드당 계좌영향 선형 확대). 레버리지 상한·청산가
#     버퍼 등 실집행 제약은 G-1 본실험(완전 공동 시뮬)에서 검증한다.
#
# 산출: backtest/results/round7_g1_frontier.json
# 실행: ../.venv-bt/bin/python -m analysis.round7_g1_exposure_frontier
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from collector.store import get_connection
from analysis.round7_portfolio import (
    run_main_lane, run_swing_lane, EXT_START, EXT_END,
)

MULTS = [1.0, 1.25, 1.5, 1.75, 2.0]


def frontier(trades: list[dict], lo: str, hi: str) -> list[dict]:
    lo_ts = pd.Timestamp(lo, tz="UTC"); hi_ts = pd.Timestamp(hi, tz="UTC")

    def _tz(ts: pd.Timestamp) -> pd.Timestamp:
        return ts.tz_localize("UTC") if ts.tzinfo is None else ts

    trs = sorted(
        (t for t in trades if lo_ts <= _tz(t["entry_dt"]) < hi_ts),
        key=lambda t: _tz(t["exit_dt"]),
    )
    rows = []
    for k in MULTS:
        eq = 1.0; peak = 1.0; mdd = 0.0
        for t in trs:
            eq *= (1.0 + k * t["f"])
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1.0)
        yrs = (_tz(trs[-1]["exit_dt"]) - _tz(trs[0]["entry_dt"])).days / 365.25
        cagr = eq ** (1 / max(yrs, 0.1)) - 1
        rows.append({
            "risk_mult": k,
            "main_risk_pct": round(2.0 * k, 2),
            "swing_risk_pct": round(1.0 * k, 2),
            "cum_ret_pct": round((eq - 1) * 100, 1),
            "cagr_pct": round(cagr * 100, 2),
            "mdd_closed_trade_pct": round(mdd * 100, 1),
            "calmar": round(cagr / abs(mdd), 2) if mdd < 0 else None,
        })
    return rows


def main() -> None:
    print("[G-1] 메인 연속 백테스트 + 스윙 시뮬 실행 중 ...")
    main_tr = run_main_lane()
    conn = get_connection(None)
    swing_tr = run_swing_lane(conn)
    conn.close()
    combined = main_tr + swing_tr

    rows = frontier(combined, EXT_START, EXT_END)
    out = {
        "window": f"{EXT_START} ~ {EXT_END}",
        "note": "closed-trade MDD — intra-trade 는 이보다 깊다. 보수적으로 고를 것. "
                "외부 비교 전략 참고치: CAGR 46.0%, MDD -18.1%, Calmar 2.54.",
        "frontier": rows,
    }
    res = Path(__file__).resolve().parents[1] / "backtest" / "results" / "round7_g1_frontier.json"
    with open(res, "w") as f:
        json.dump(out, f, indent=2)

    print(f"\n== 노출 프론티어 ({out['window']}) ==")
    print(f"{'배수':>5} {'메인위험':>7} {'스윙위험':>7} {'누적':>9} {'CAGR':>7} {'MDD':>7} {'Calmar':>7}")
    for r in rows:
        print(f"{r['risk_mult']:>5} {r['main_risk_pct']:>6.1f}% {r['swing_risk_pct']:>6.1f}% "
              f"{r['cum_ret_pct']:>8.1f}% {r['cagr_pct']:>6.2f}% "
              f"{r['mdd_closed_trade_pct']:>6.1f}% {r['calmar']:>7}")
    print(f"\nsaved → {res}")


if __name__ == "__main__":
    main()
