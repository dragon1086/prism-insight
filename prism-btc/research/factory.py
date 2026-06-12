# research/factory.py — 자동 연구공장 (가설 → 검증 → 자동 반영 → 자동 은퇴)
#
#   python -m research.factory --run      # 주간 자동 실행 진입점 (LaunchAgent)
#   python -m research.factory --status   # 챔피언/판정 이력 출력
#
# 닫힌 루프에서 이 모듈의 권한:
#   - btc_lessons(hypothesis) 중 화이트리스트 {param, value} 가설만 집어
#     train(2020~2024) + OOS(2025~) 백테스트로 챔피언과 비교 판정한다.
#   - 합격 → btc_overrides 활성 (다음 데몬 tick 부터 실매매 행동 변경 — 사람 개입 0)
#   - 매 실행마다 기존 활성 오버라이드 재검증 — 최신 데이터로 게이트 재통과 실패 시 자동 은퇴.
#   - 모든 판정은 btc_research_runs 에 evidence 전문과 함께 영구 기록 (기각 메모리 = 재검증 방지).
#
# 판정은 100% 결정적 (LLM 무관여). 동률·애매 = 기각.
from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
from typing import Any, Optional

import pandas as pd

from live import tracking
from research import overrides

log = logging.getLogger("research.factory")

TRAIN_PERIOD = ("2020-01-01", "2024-12-31")
OOS_START = "2025-01-01"
INITIAL_EQUITY = 10_000.0

# 결정적 합격 게이트 (tasks/btc_autoloop_design.md §합격 게이트)
MIN_TRAIN_TRADES = 40
MIN_OOS_TRADES = 8
PF_IMPROVE_MULT = 1.05      # train PF 는 챔피언 대비 +5% 이상
MDD_WORSEN_CAP = 1.10       # train MDD 악화 상한 +10%
RETURN_TOL = 0.05           # train 수익률 허용 하락폭 (|챔피언|의 5%)
OOS_PF_FLOOR = 1.3
OOS_PF_KEEP_FRAC = 0.9      # OOS PF 는 챔피언 OOS 의 90% 이상 (과적합 차단)

MAX_CANDIDATES_PER_RUN = 4


# ---------------------------------------------------------------------------
# 판정 기록 — 기각 메모리 (동일 가설 재검증/진동 방지)
# ---------------------------------------------------------------------------

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS btc_research_runs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT    NOT NULL,
        mode       TEXT    NOT NULL,
        param      TEXT    NOT NULL,
        value      TEXT    NOT NULL,         -- JSON 인코딩
        verdict    TEXT    NOT NULL,         -- validated | rejected | error
        lesson_id  INTEGER,
        evidence   TEXT    NOT NULL          -- 양쪽 메트릭 + 게이트 상세 JSON
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_btc_research_pv ON btc_research_runs(mode, param, value)",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA:
        conn.execute(stmt)
    overrides.ensure_schema(conn)
    conn.commit()


def _already_judged(conn: sqlite3.Connection, mode: str, param: str, value: Any) -> Optional[str]:
    row = conn.execute(
        "SELECT verdict FROM btc_research_runs WHERE mode=? AND param=? AND value=? "
        "AND verdict IN ('validated','rejected') ORDER BY id DESC LIMIT 1",
        (mode, param, json.dumps(value))).fetchone()
    return row[0] if row else None


def _record_run(conn: sqlite3.Connection, mode: str, param: str, value: Any,
                verdict: str, lesson_id: Optional[int], evidence: dict) -> None:
    conn.execute(
        "INSERT INTO btc_research_runs (ts, mode, param, value, verdict, lesson_id, evidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pd.Timestamp.now("UTC").isoformat(), mode, param, json.dumps(value),
         verdict, lesson_id, json.dumps(evidence, ensure_ascii=False)))
    conn.commit()


# ---------------------------------------------------------------------------
# 백테스트 실행 (결정적) — backtest.engine 그대로 재사용
# ---------------------------------------------------------------------------

def _clean(metrics: dict) -> dict:
    """JSON 안전화: numpy 스칼라 → 파이썬 기본형, inf/NaN → 유한값 치환."""
    out = {}
    for k, v in metrics.items():
        if hasattr(v, "item"):  # numpy 스칼라 (bool_/int64/float64)
            v = v.item()
        if isinstance(v, float) and not math.isfinite(v):
            v = 9999.0 if v > 0 else -9999.0
        out[k] = v
    return out


def _run_period(market_db_path: Optional[str], start: str, end: str) -> dict:
    from collector.store import get_connection
    from backtest.engine import run_backtest, compute_metrics
    conn = get_connection(market_db_path)
    try:
        state = run_backtest(conn, pd.Timestamp(start, tz="UTC"),
                             pd.Timestamp(end, tz="UTC"),
                             initial_equity=INITIAL_EQUITY)
    finally:
        conn.close()
    return _clean(compute_metrics(state, INITIAL_EQUITY))


def _run_train_oos(market_db_path: Optional[str], cfg: dict[str, Any]) -> tuple[dict, dict]:
    """주어진 오버라이드 세트로 train/OOS 두 구간 실행 (적용 후 원상복원)."""
    oos_end = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
    with overrides.apply(cfg):
        m_train = _run_period(market_db_path, *TRAIN_PERIOD)
        m_oos = _run_period(market_db_path, OOS_START, oos_end)
    return m_train, m_oos


# ---------------------------------------------------------------------------
# 결정적 합격 게이트
# ---------------------------------------------------------------------------

def evaluate_gate(base_train: dict, var_train: dict,
                  base_oos: dict, var_oos: dict) -> tuple[bool, dict[str, bool]]:
    bt_ret = float(base_train["total_return_pct"])
    checks = {
        "train_no_liq": var_train["liq_approach_count"] == 0,
        "oos_no_liq": var_oos["liq_approach_count"] == 0,
        "train_min_trades": var_train["trade_count"] >= MIN_TRAIN_TRADES,
        "oos_min_trades": var_oos["trade_count"] >= MIN_OOS_TRADES,
        "train_pf_improve": (var_train["profit_factor"]
                             >= base_train["profit_factor"] * PF_IMPROVE_MULT),
        "train_mdd_cap": var_train["mdd_pct"] <= base_train["mdd_pct"] * MDD_WORSEN_CAP,
        "train_return_keep": (var_train["total_return_pct"]
                              >= bt_ret - abs(bt_ret) * RETURN_TOL),
        "oos_pf_floor": (var_oos["profit_factor"]
                         >= max(OOS_PF_FLOOR,
                                base_oos["profit_factor"] * OOS_PF_KEEP_FRAC)),
    }
    checks = {k: bool(v) for k, v in checks.items()}  # numpy.bool_ → bool (JSON 안전)
    return all(checks.values()), checks


# ---------------------------------------------------------------------------
# 후보 수집 — 구조화 가설만 (화이트리스트 통과분)
# ---------------------------------------------------------------------------

def _structured_candidates(conn: sqlite3.Connection, mode: str) -> list[dict]:
    """btc_lessons(hypothesis) 에서 {param, value} 가설 추출. 비구조/범위밖은 보류
    (사람 리뷰 대기열 — 자동 루프의 의도적 경계)."""
    out = []
    cur = conn.execute(
        "SELECT id, suggested_backtest FROM btc_lessons "
        "WHERE mode=? AND status='hypothesis' AND suggested_backtest IS NOT NULL "
        "ORDER BY id", (mode,))
    for lesson_id, sb_json in cur.fetchall():
        try:
            sb = json.loads(sb_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(sb, dict) or "param" not in sb or "value" not in sb:
            continue
        try:
            value = overrides.validate(str(sb["param"]), sb["value"])
        except overrides.OverrideError as exc:
            log.info("lesson %s 화이트리스트 밖 — 보류: %s", lesson_id, exc)
            continue
        out.append({"lesson_id": lesson_id, "param": str(sb["param"]), "value": value})
    return out


def _set_lesson_status(conn: sqlite3.Connection, lesson_id: int,
                       status: str, evidence: dict) -> None:
    conn.execute(
        "UPDATE btc_lessons SET status=?, evidence=?, updated_at=? WHERE id=?",
        (status, json.dumps(evidence, ensure_ascii=False),
         pd.Timestamp.now("UTC").isoformat(), lesson_id))
    conn.commit()


# ---------------------------------------------------------------------------
# 메인 루프 — 후보 판정 + 활성 재검증
# ---------------------------------------------------------------------------

def run_factory(conn: sqlite3.Connection, mode: str = "shadow",
                market_db_path: Optional[str] = None,
                limit: int = MAX_CANDIDATES_PER_RUN) -> dict:
    """주간 자동 실행 본체. 반환: 판정 요약 dict."""
    ensure_schema(conn)
    summary = {"validated": 0, "rejected": 0, "skipped": 0,
               "retired": 0, "kept": 0, "errors": 0}

    champion = overrides.load_active(conn, mode)
    log.info("champion overrides: %s", champion or "(동결 그대로)")
    base_train = base_oos = None  # 후보가 있을 때만 lazy 실행

    # --- 1. 신규 가설 판정 ---
    candidates = _structured_candidates(conn, mode)[:limit]
    for cand in candidates:
        param, value, lesson_id = cand["param"], cand["value"], cand["lesson_id"]
        prior = _already_judged(conn, mode, param, value)
        if prior is not None:
            # 기각 메모리: 동일 (param,value) 재검증 금지 — 교훈 상태만 정리
            _set_lesson_status(conn, lesson_id, prior,
                               {"note": f"기판정 재사용 ({prior}) — 중복 가설"})
            summary["skipped"] += 1
            continue
        if champion.get(param) == value:
            _set_lesson_status(conn, lesson_id, "validated",
                               {"note": "이미 챔피언에 활성된 값"})
            summary["skipped"] += 1
            continue

        try:
            if base_train is None:
                base_train, base_oos = _run_train_oos(market_db_path, champion)
            var_train, var_oos = _run_train_oos(
                market_db_path, {**champion, param: value})
            passed, checks = evaluate_gate(base_train, var_train, base_oos, var_oos)
            evidence = {
                "champion": champion, "candidate": {param: value},
                "gate": checks,
                "train": {"champion": base_train, "variant": var_train},
                "oos": {"champion": base_oos, "variant": var_oos},
            }
            verdict = "validated" if passed else "rejected"
            _record_run(conn, mode, param, value, verdict, lesson_id, evidence)
            _set_lesson_status(conn, lesson_id, verdict, evidence)
            if passed:
                try:
                    overrides.activate(conn, param, value, lesson_id, evidence, mode)
                    champion = overrides.load_active(conn, mode)  # 다음 후보의 baseline 갱신
                    base_train = base_oos = None
                    tracking.log_event(
                        conn, "research",
                        f"오버라이드 자동 활성: {param}={value} "
                        f"(train PF {var_train['profit_factor']:.2f}, "
                        f"OOS PF {var_oos['profit_factor']:.2f}) — 다음 틱부터 적용",
                        mode=mode)
                except overrides.OverrideError as exc:
                    tracking.log_event(conn, "research",
                                       f"합격했으나 활성 보류 (슬롯): {param}={value}: {exc}",
                                       mode=mode)
                summary["validated"] += 1
            else:
                failed = [k for k, ok in checks.items() if not ok]
                tracking.log_event(conn, "research",
                                   f"가설 기각: {param}={value} — 실패 게이트 {failed}",
                                   mode=mode)
                summary["rejected"] += 1
        except Exception as exc:  # noqa: BLE001 — 한 후보 실패가 공장을 못 멈춤
            log.exception("candidate %s=%s 검증 오류", param, value)
            _record_run(conn, mode, param, value, "error", lesson_id,
                        {"error": str(exc)})
            summary["errors"] += 1

    # --- 2. 활성 오버라이드 재검증 (최신 데이터 — 자동 롤백) ---
    cur = conn.execute(
        "SELECT id, param, value FROM btc_overrides WHERE mode=? AND status='active' "
        "ORDER BY id", (mode,))
    actives = [(r[0], r[1], json.loads(r[2])) for r in cur.fetchall()]
    for oid, param, value in actives:
        try:
            current = overrides.load_active(conn, mode)
            without = {k: v for k, v in current.items() if k != param}
            base_t, base_o = _run_train_oos(market_db_path, without)
            var_t, var_o = _run_train_oos(market_db_path, current)
            passed, checks = evaluate_gate(base_t, var_t, base_o, var_o)
            if passed:
                summary["kept"] += 1
            else:
                overrides.retire(conn, oid, reason="weekly revalidation failed: "
                                 + ",".join(k for k, ok in checks.items() if not ok))
                tracking.log_event(
                    conn, "research",
                    f"오버라이드 자동 은퇴: {param}={value} — 최신 데이터 게이트 재통과 실패",
                    level="warn", mode=mode)
                summary["retired"] += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("revalidation %s 오류 — 활성 유지(보수적)", param)
            summary["errors"] += 1

    tracking.log_event(conn, "research", f"연구공장 완료: {summary}", mode=mode)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_status(conn: sqlite3.Connection, mode: str) -> None:
    ensure_schema(conn)
    print("=== 챔피언 오버라이드 (active) ===")
    print(json.dumps(overrides.load_active(conn, mode), ensure_ascii=False) or "{}")
    print("=== 최근 판정 10건 ===")
    cur = conn.execute(
        "SELECT ts, param, value, verdict FROM btc_research_runs WHERE mode=? "
        "ORDER BY id DESC LIMIT 10", (mode,))
    for ts, param, value, verdict in cur.fetchall():
        print(f"{ts}  {param}={value}  -> {verdict}")


def main() -> int:
    parser = argparse.ArgumentParser(description="prism-btc 자동 연구공장")
    parser.add_argument("--run", action="store_true", help="가설 판정 + 활성 재검증")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--limit", type=int, default=MAX_CANDIDATES_PER_RUN)
    parser.add_argument("--mode", default="shadow")
    parser.add_argument("--root-db", default=None)
    parser.add_argument("--market-db", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = tracking.get_connection(args.root_db)
    tracking.ensure_schema(conn)
    try:
        if args.status:
            _print_status(conn, args.mode)
            return 0
        if args.run:
            res = run_factory(conn, args.mode, args.market_db, args.limit)
            print(json.dumps(res, ensure_ascii=False))
            return 0
        print("--run 또는 --status 필요")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
