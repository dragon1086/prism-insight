# live/journal.py — 매매일지 파이프라인 (학습 기어, 결정적 레이어)
#
# 톱니바퀴 설계 (tasks/btc_journal_design.md):
#   트레이딩 기어(core/engine/shadow — 동결)가 btc_trading_history 에 종결 트레이드를
#   쓰면, 이 모듈이 tick 끝에서 그것을 읽어 (1) 결정적 사실(facts)을 추출해 먼저
#   저장하고 (2) postmortem(LLM)에 해석을 요청한다.
#
# 불변 조건:
#   - 이 모듈은 btc_journal / btc_lessons 에만 쓴다 (주문 경로 테이블 불간섭).
#   - 모든 수치는 여기(순수 파이썬)서 계산된다 — LLM 은 숫자를 만들지 않는다.
#   - facts 는 LLM 호출 전에 저장된다 (LLM 실패 = 해석 보류, 데이터 무손실).
#   - 교훈 수명주기: observation → hypothesis → (백테스트) validated/rejected.
#     validated + 사람 승인 없이는 어떤 교훈도 동결 룰을 바꿀 수 없다.
#
# CLI:
#   python -m live.journal --backfill     # 미처리 트레이드 전체 부검
#   python -m live.journal --facts-only   # LLM 없이 사실만 추출
#   python -m live.journal --weekly       # 주간 기억압축 → 가설 백로그
#   python -m live.journal --show 5       # 최근 일지 출력
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

from live import tracking

log = logging.getLogger("live.journal")

# 동결 스펙 기대치 (6년 백테스트, risk 4%) — 부검의 기준선.
# 출처: tasks/v3_strategy_report_v3.md. 전략 변경 없이는 갱신 금지.
FROZEN_EXPECTATION = {
    "rr": 2.29,           # 평균 손익비
    "win_rate": 0.54,     # 승률
    "pf": 2.46,           # Profit Factor
    "source": "6yr backtest (2020.3~2026.6), real funding, risk 4%",
}

MAX_LLM_ATTEMPTS = 3
_RESULTS_DIR = Path(__file__).resolve().parent.parent / "backtest" / "results"


# ---------------------------------------------------------------------------
# 스키마 — btc_* 프리픽스만 생성 (주식 테이블 불간섭, tracking.py 와 동일 원칙)
# ---------------------------------------------------------------------------

_JOURNAL_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS btc_journal (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        mode          TEXT    NOT NULL,
        trade_rowid   INTEGER NOT NULL,           -- btc_trading_history.id
        trade_id      INTEGER NOT NULL,
        created_at    TEXT    NOT NULL,
        updated_at    TEXT    NOT NULL,
        facts         TEXT    NOT NULL,           -- 결정적 사실 JSON (숫자 단일 출처)
        analysis      TEXT,                       -- LLM 해석 JSON
        one_line      TEXT,
        pattern_tags  TEXT,                       -- JSON list
        confidence    REAL,
        status        TEXT    NOT NULL DEFAULT 'facts_only',
                       -- facts_only | analyzed | failed
        llm_provider  TEXT,
        llm_ms        INTEGER,
        attempts      INTEGER NOT NULL DEFAULT 0,
        UNIQUE (mode, trade_rowid)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS btc_lessons (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        mode               TEXT    NOT NULL,
        created_at         TEXT    NOT NULL,
        updated_at         TEXT    NOT NULL,
        source_journal_id  INTEGER,               -- NULL 이면 주간압축 산출
        category           TEXT    NOT NULL,      -- entry/exit/sizing/regime/execution/weekly
        lesson             TEXT    NOT NULL,
        status             TEXT    NOT NULL DEFAULT 'observation',
                            -- observation | hypothesis | validated | rejected | retired
        suggested_backtest TEXT,                  -- 검증 가능한 백테스트 스펙 (가설일 때)
        evidence           TEXT                   -- 백테스트 결과 JSON (검증 후)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_btc_journal_status ON btc_journal(mode, status)",
    "CREATE INDEX IF NOT EXISTS idx_btc_lessons_status ON btc_lessons(mode, status)",
]


def ensure_journal_schema(conn: sqlite3.Connection) -> None:
    for stmt in _JOURNAL_SCHEMA:
        conn.execute(stmt)
    conn.commit()


# ---------------------------------------------------------------------------
# 결정적 사실 추출 — 순수 파이썬, LLM 없음. 모든 부검 수치의 유일한 출처.
# ---------------------------------------------------------------------------

def _initial_risk_usd(trade: dict) -> Optional[float]:
    """초기 리스크(USD) 역산.

    주의: btc_trading_history.sl_price 는 종결 시점의 (트레일된) 스탑이라
    |entry - sl| 은 초기 스탑거리가 아니다. 엔진이 r_multiple 을
    net_pnl / initial_risk 로 계산하므로 역산이 정확하다.
    """
    for pnl_key, r_key in (("net_pnl", "r_multiple"), ("gross_pnl", "gross_r_multiple")):
        pnl = trade.get(pnl_key) or 0.0
        r = trade.get(r_key) or 0.0
        if abs(r) > 1e-9 and abs(pnl) > 1e-12:
            return abs(pnl / r)
    return None


def _excursion(trade: dict, bars_30m: pd.DataFrame, risk_usd: Optional[float]) -> dict:
    """보유 구간 MFE/MAE — 30m 고저가 기준, 초기 리스크의 R 단위.

    단위 스탑거리 = risk_usd / qty (qty 는 진입 총수량) 근사.
    리스크 역산 불가 시 % 익스커션만 제공.
    """
    out: dict = {
        "mfe_r": None, "mae_r": None, "mfe_pct": None, "mae_pct": None,
        "time_to_mfe_hours": None, "holding_hours": None, "capture_ratio": None,
        "method": "30m high/low, R = excursion / initial unit stop (risk_usd/qty)",
    }
    try:
        entry_ts = pd.Timestamp(trade["entry_time"])
        exit_ts = pd.Timestamp(trade["exit_time"])
    except Exception:
        return out
    out["holding_hours"] = round((exit_ts - entry_ts).total_seconds() / 3600.0, 2)

    if bars_30m is None or bars_30m.empty:
        return out
    window = bars_30m[(bars_30m.index >= entry_ts) & (bars_30m.index <= exit_ts)]
    if window.empty:
        return out

    entry_price = float(trade["entry_price"])
    side = trade["side"]
    if side == "long":
        fav = window["high"].astype(float) - entry_price
        adv = entry_price - window["low"].astype(float)
    else:
        fav = entry_price - window["low"].astype(float)
        adv = window["high"].astype(float) - entry_price

    mfe = float(fav.max())
    mae = float(adv.max())
    out["mfe_pct"] = round(mfe / entry_price * 100, 3)
    out["mae_pct"] = round(mae / entry_price * 100, 3)
    mfe_time = fav.idxmax()
    out["time_to_mfe_hours"] = round((mfe_time - entry_ts).total_seconds() / 3600.0, 2)

    qty = float(trade.get("qty") or 0.0)
    if risk_usd and qty > 0:
        unit_stop = risk_usd / qty
        if unit_stop > 0:
            out["mfe_r"] = round(mfe / unit_stop, 3)
            out["mae_r"] = round(mae / unit_stop, 3)
            net_r = trade.get("r_multiple")
            if net_r is not None and out["mfe_r"] and out["mfe_r"] > 0:
                out["capture_ratio"] = round(float(net_r) / out["mfe_r"], 3)
    return out


def _snapshot_context(tf_data: Optional[dict], at_time: str) -> Optional[dict]:
    """진입/청산 시점 레짐 스냅샷 재구성 — 결정적 (같은 klines → 같은 스냅샷).

    엔진이 그 봉에서 본 것과 동일한 _build_snapshot_at 호출. TF 데이터 부족 시 None.
    """
    if not tf_data:
        return None
    try:
        from backtest.engine import _build_snapshot_at
        from engine.signal import trend_strength
        snap = _build_snapshot_at(tf_data, pd.Timestamp(at_time))
        if snap is None:
            return None
        ctx = {
            "alignment_score": round(snap.alignment_score, 2),
            "trend_strength_4h": round(trend_strength(snap.tf_states["4h"]), 3)
            if "4h" in snap.tf_states else None,
            "trend_strength_1d": round(trend_strength(snap.tf_states["1d"]), 3)
            if "1d" in snap.tf_states else None,
            "tf_trends": {tf: s.trend for tf, s in snap.tf_states.items()},
        }
        return ctx
    except Exception as exc:  # noqa: BLE001 — 컨텍스트는 부가정보, 실패해도 facts 는 진행
        log.warning("snapshot context failed at %s: %s", at_time, exc)
        return None


def _backtest_baseline(net_r: Optional[float]) -> dict:
    """백테스트 6년 R분포 대비 이번 트레이드의 위치 (결정적 기준선)."""
    base: dict = {"expectation": FROZEN_EXPECTATION, "r_percentile": None,
                  "n_backtest_trades": None}
    if net_r is None:
        return base
    try:
        frames = []
        for csv in sorted(_RESULTS_DIR.glob("*_trades.csv")):
            df = pd.read_csv(csv, usecols=["entry_time", "exit_time", "side",
                                           "r_multiple", "exit_reason"])
            # 옛 라운드 산출물 배제: 동결 스펙은 TP2/3 없음 (라운드6 TP사다리 제거).
            # tp2/tp3 청산이 존재하는 CSV 는 다른 전략의 분포 — 기준선 오염 방지.
            if df["exit_reason"].isin(["tp2", "tp3"]).any():
                continue
            frames.append(df)
        if not frames:
            return base
        allr = (pd.concat(frames)
                .drop_duplicates(subset=["entry_time", "exit_time", "side"])["r_multiple"]
                .astype(float))
        if len(allr) == 0:
            return base
        base["n_backtest_trades"] = int(len(allr))
        base["r_percentile"] = round(float((allr < float(net_r)).mean() * 100), 1)
    except Exception as exc:  # noqa: BLE001
        log.warning("backtest baseline failed: %s", exc)
    return base


def extract_facts(trade: dict, tf_data: Optional[dict] = None) -> dict:
    """종결 트레이드 1건의 결정적 사실 추출. 부검 수치의 유일한 출처."""
    risk_usd = _initial_risk_usd(trade)
    net_r = trade.get("r_multiple")
    gross_r = trade.get("gross_r_multiple")
    fee_r = funding_r = residual = None
    if risk_usd:
        fee_r = round(float(trade.get("fee_paid") or 0.0) / risk_usd, 4)
        funding_r = round(float(trade.get("funding_paid") or 0.0) / risk_usd, 4)
        if gross_r is not None and net_r is not None:
            # 자가검증: gross - fee - funding ≈ net (잔차가 크면 회계 의심 신호)
            residual = round(float(gross_r) - fee_r - funding_r - float(net_r), 4)

    bars_30m = tf_data.get("30m") if tf_data else None
    facts = {
        "identity": {
            "trade_id": trade.get("trade_id"),
            "side": trade.get("side"),
            "tranche_index": trade.get("tranche_index"),
            "entry_time": trade.get("entry_time"),
            "entry_price": trade.get("entry_price"),
            "exit_time": trade.get("exit_time"),
            "exit_price": trade.get("exit_price"),
            "exit_reason": trade.get("exit_reason"),
            "leverage": trade.get("leverage"),
            "num_legs": trade.get("num_legs"),
            "mode": trade.get("mode"),
        },
        "r_decomposition": {
            "net_r": net_r,
            "gross_r": gross_r,
            "fee_r": fee_r,
            "funding_r": funding_r,
            "self_check_residual": residual,
            "initial_risk_usd": round(risk_usd, 2) if risk_usd else None,
            "net_pnl": trade.get("net_pnl"),
            "fee_paid": trade.get("fee_paid"),
            "funding_paid": trade.get("funding_paid"),
        },
        "excursion": _excursion(trade, bars_30m, risk_usd),
        "entry_context": _snapshot_context(tf_data, trade["entry_time"]),
        "exit_context": _snapshot_context(tf_data, trade["exit_time"]),
        "baseline": _backtest_baseline(net_r),
    }
    return facts


# ---------------------------------------------------------------------------
# 파이프라인 — pending 감지 → facts 저장 → LLM 부검 → 교훈 적재
# ---------------------------------------------------------------------------

def _trades_without_journal(conn: sqlite3.Connection, mode: str) -> list[dict]:
    cur = conn.execute(
        """
        SELECT h.* FROM btc_trading_history h
        LEFT JOIN btc_journal j ON j.trade_rowid = h.id AND j.mode = h.mode
        WHERE h.mode = ? AND j.id IS NULL
        ORDER BY h.id
        """,
        (mode,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _journals_pending_analysis(conn: sqlite3.Connection, mode: str, limit: int) -> list[dict]:
    cur = conn.execute(
        """
        SELECT * FROM btc_journal
        WHERE mode = ? AND status IN ('facts_only', 'failed') AND attempts < ?
        ORDER BY id LIMIT ?
        """,
        (mode, MAX_LLM_ATTEMPTS, limit),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _insert_facts(conn: sqlite3.Connection, mode: str, trade: dict, facts: dict) -> int:
    now = pd.Timestamp.now("UTC").isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO btc_journal
            (mode, trade_rowid, trade_id, created_at, updated_at, facts, status)
        VALUES (?, ?, ?, ?, ?, ?, 'facts_only')
        """,
        (mode, trade["id"], trade["trade_id"], now, now,
         json.dumps(facts, ensure_ascii=False)),
    )
    conn.commit()
    return cur.lastrowid or 0


def _active_lessons(conn: sqlite3.Connection, mode: str, limit: int = 12) -> list[dict]:
    cur = conn.execute(
        """
        SELECT category, lesson, status FROM btc_lessons
        WHERE mode = ? AND status IN ('observation', 'hypothesis', 'validated')
        ORDER BY id DESC LIMIT ?
        """,
        (mode, limit),
    )
    return [{"category": c, "lesson": l, "status": s} for c, l, s in cur.fetchall()]


def _save_analysis(conn: sqlite3.Connection, journal_id: int, analysis: dict,
                   provider: str, ms: int) -> None:
    now = pd.Timestamp.now("UTC").isoformat()
    conn.execute(
        """
        UPDATE btc_journal SET analysis = ?, one_line = ?, pattern_tags = ?,
            confidence = ?, status = 'analyzed', llm_provider = ?, llm_ms = ?,
            updated_at = ?, attempts = attempts + 1
        WHERE id = ?
        """,
        (json.dumps(analysis, ensure_ascii=False),
         str(analysis.get("one_line_summary", ""))[:300],
         json.dumps(analysis.get("pattern_tags", []), ensure_ascii=False),
         float(analysis.get("confidence_score", 0.5)),
         provider, ms, now, journal_id),
    )
    conn.commit()


def _mark_failed(conn: sqlite3.Connection, journal_id: int, err: str) -> None:
    now = pd.Timestamp.now("UTC").isoformat()
    conn.execute(
        "UPDATE btc_journal SET status = 'failed', attempts = attempts + 1, "
        "updated_at = ? WHERE id = ?",
        (now, journal_id),
    )
    conn.commit()
    log.warning("journal %s analysis failed: %s", journal_id, err)


def _insert_lessons(conn: sqlite3.Connection, mode: str, journal_id: Optional[int],
                    lessons: list, default_category: str = "execution") -> int:
    """LLM 교훈 적재. testable+suggested_backtest 있으면 hypothesis, 아니면 observation.

    어떤 status 로 들어와도 'validated' 로 직행할 수 없다 — 검증은 백테스트+사람 몫.
    """
    now = pd.Timestamp.now("UTC").isoformat()
    n = 0
    for lesson in lessons or []:
        if not isinstance(lesson, dict):
            lesson = {"text": str(lesson)}
        text = str(lesson.get("text") or lesson.get("lesson") or "").strip()
        if not text:
            continue
        testable = bool(lesson.get("testable"))
        suggested = lesson.get("suggested_backtest")
        status = "hypothesis" if (testable and suggested) else "observation"
        conn.execute(
            """
            INSERT INTO btc_lessons
                (mode, created_at, updated_at, source_journal_id, category,
                 lesson, status, suggested_backtest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (mode, now, now, journal_id,
             str(lesson.get("category") or default_category)[:40],
             text[:1000], status,
             json.dumps(suggested, ensure_ascii=False) if suggested else None),
        )
        n += 1
    conn.commit()
    return n


def process_pending(conn: sqlite3.Connection, tf_data: Optional[dict] = None,
                    mode: str = "shadow", limit: int = 1, do_llm: bool = True) -> dict:
    """tick 끝에서 호출되는 진입점. 예외를 밖으로 던지지 않는 것은 호출측(runner) 책임.

    1) journal 없는 종결 트레이드 → facts 추출·저장 (전건, LLM 무관 — 데이터 무손실)
    2) 해석 대기 journal → LLM 부검 (틱당 limit 건 — 시간 상한)
    """
    ensure_journal_schema(conn)
    result = {"facts_created": 0, "analyzed": 0, "failed": 0, "lessons": 0}

    for trade in _trades_without_journal(conn, mode):
        facts = extract_facts(trade, tf_data)
        _insert_facts(conn, mode, trade, facts)
        result["facts_created"] += 1
        tracking.log_event(
            conn, "journal",
            f"facts saved: trade #{trade['trade_id']} {trade['side']} "
            f"net_r={trade.get('r_multiple')}", mode=mode)

    if not do_llm:
        return result

    for row in _journals_pending_analysis(conn, mode, limit):
        from live import postmortem  # 지연 임포트 — LLM 게이트웨이는 선택적 기어
        try:
            facts = json.loads(row["facts"])
            analysis, provider, ms = postmortem.analyze(
                facts, _active_lessons(conn, mode))
            _save_analysis(conn, row["id"], analysis, provider, ms)
            n_lessons = _insert_lessons(conn, mode, row["id"],
                                        analysis.get("lessons", []))
            result["analyzed"] += 1
            result["lessons"] += n_lessons
            tracking.log_event(
                conn, "journal",
                f"postmortem ok: trade #{row['trade_id']} via {provider} "
                f"({ms}ms, {n_lessons} lessons) — {analysis.get('one_line_summary', '')[:120]}",
                mode=mode)
        except postmortem.PostmortemUnavailable as exc:
            # LLM 게이트웨이 자체가 없음 — 재시도 카운트 올리지 않고 보류 유지
            log.info("postmortem unavailable (kept pending): %s", exc)
            break
        except Exception as exc:  # noqa: BLE001
            _mark_failed(conn, row["id"], str(exc))
            result["failed"] += 1
            tracking.log_event(conn, "error",
                               f"postmortem failed: trade #{row['trade_id']}: {exc}",
                               level="error", mode=mode)
    return result


# ---------------------------------------------------------------------------
# 주간 기억압축 → 가설 백로그
# ---------------------------------------------------------------------------

def weekly_digest(conn: sqlite3.Connection, mode: str = "shadow") -> dict:
    """최근 7일 일지+교훈을 LLM 으로 압축해 검증 가능한 가설 백로그를 만든다.

    산출 가설은 btc_lessons(status='hypothesis', category='weekly') 로만 적재.
    백테스트 실행/룰 반영은 이 함수 밖 (연구공장 + 사람 승인).
    """
    from live import postmortem
    ensure_journal_schema(conn)
    cutoff = (pd.Timestamp.now("UTC") - pd.Timedelta(days=7)).isoformat()
    cur = conn.execute(
        "SELECT trade_id, facts, analysis, one_line FROM btc_journal "
        "WHERE mode = ? AND created_at >= ? ORDER BY id",
        (mode, cutoff),
    )
    entries = [{"trade_id": t, "facts": json.loads(f),
                "analysis": json.loads(a) if a else None, "one_line": o}
               for t, f, a, o in cur.fetchall()]
    lessons = _active_lessons(conn, mode, limit=30)
    if not entries and not lessons:
        return {"hypotheses": 0, "note": "no journal entries in window"}

    analysis, provider, ms = postmortem.weekly_compress(entries, lessons)
    n = _insert_lessons(conn, mode, None, analysis.get("hypotheses", []),
                        default_category="weekly")
    tracking.log_event(conn, "journal",
                       f"weekly digest via {provider} ({ms}ms): {n} hypotheses — "
                       f"{analysis.get('summary', '')[:150]}", mode=mode)
    return {"hypotheses": n, "summary": analysis.get("summary", ""), "provider": provider}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_tf_data_for_cli(market_db_path=None) -> Optional[dict]:
    try:
        from collector.store import get_connection as market_connection
        from backtest.engine import _load_tf_data, ALL_TFS
        from engine.indicators import add_indicators
        mconn = market_connection(market_db_path)
        try:
            return {tf: add_indicators(_load_tf_data(mconn, tf)) for tf in ALL_TFS}
        finally:
            mconn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("tf_data load failed (facts will lack context): %s", exc)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="prism-btc 매매일지/부검 파이프라인")
    parser.add_argument("--backfill", action="store_true", help="미처리 전체 부검")
    parser.add_argument("--facts-only", action="store_true", help="LLM 없이 사실만")
    parser.add_argument("--weekly", action="store_true", help="주간 압축→가설 백로그")
    parser.add_argument("--show", type=int, default=0, help="최근 N건 일지 출력")
    parser.add_argument("--mode", default="shadow")
    parser.add_argument("--root-db", default=None)
    parser.add_argument("--market-db", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    conn = tracking.get_connection(args.root_db)
    tracking.ensure_schema(conn)
    ensure_journal_schema(conn)
    try:
        if args.show:
            cur = conn.execute(
                "SELECT trade_id, status, one_line, confidence, created_at "
                "FROM btc_journal WHERE mode = ? ORDER BY id DESC LIMIT ?",
                (args.mode, args.show))
            for row in cur.fetchall():
                print(json.dumps(dict(zip(
                    ["trade_id", "status", "one_line", "confidence", "created_at"], row)),
                    ensure_ascii=False))
            return 0
        if args.weekly:
            print(json.dumps(weekly_digest(conn, args.mode), ensure_ascii=False, indent=2))
            return 0
        tf_data = _load_tf_data_for_cli(args.market_db)
        res = process_pending(conn, tf_data, mode=args.mode,
                              limit=1000 if args.backfill else 1,
                              do_llm=not args.facts_only)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
