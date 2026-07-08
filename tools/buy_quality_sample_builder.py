#!/usr/bin/env python3
"""
buy_quality 검증용 라벨 표본 빌더 (SPEC §3).
=================================================================
`stock_tracking_db.sqlite` 의 실제 매매기록(trading_history / us_trading_history)에서
규칙 기반으로 양성/음성 대조 표본을 뽑아 JSON 으로 떨군다. 하네스(
tools/buy_quality_backtest.py)가 이 JSON 을 입력으로 as-of 차트를 렌더하고
게이트 판정을 재생한다.

편향 차단(SPEC §2):
- 선택 편향 금지 → "돌이켜보니 통과한 것"이 아니라 **매매결과 규칙**으로만 선정.
- regime 은 여기서 부여하지 않는다(하네스가 D 시점 값으로 계산 → 룩어헤드 차단).

exit_kind 주의:
- 스펙은 exit_kind ∈ {target, trend_exit, ai}(승자) / {stop}(손절)을 가정하지만
  **현행 DB 스키마에는 exit_kind 컬럼이 없다**(id, ticker, company_name, buy/sell
  price·date, profit_rate, holding_days, scenario, trigger_type, trigger_mode,
  sector). 따라서 label 은 profit_rate 부호로 도출한다:
    profit_rate > 0 → label='win'  (S1 양성대조, exit_kind='win_exit')
    profit_rate < 0 → label='loss' (S3 음성대조, exit_kind='stop')
    profit_rate == 0 → 모호 → 스킵.
  (실제 운영 DB 에 exit_kind 류 컬럼이 추가되면 여기만 바꾸면 된다.)

market 추론: 티커가 6자리 숫자면 KR, 아니면 US.

실행:
  cd /root/prism-insight && python tools/buy_quality_sample_builder.py \
      [--db stock_tracking_db.sqlite] [--days-back 60] [--out PATH]
"""
import os
import json
import argparse
import logging
import sqlite3
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("buy_quality_sample_builder")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "stock_tracking_db.sqlite")
DEFAULT_OUT = os.path.join(
    ROOT, "tasks", "buy_quality_validation", "sample_from_history.json"
)

# 현행 DB 에는 exit_kind 컬럼이 없어 profit 부호로 판정 → 매핑 상수.
_WIN_EXIT_KIND = "win_exit"   # profit>0 (target/trend_exit/ai 를 구분 불가하므로 통합)
_LOSS_EXIT_KIND = "stop"      # profit<0 (손절 프록시)

_KR_TABLE = "trading_history"
_US_TABLE = "us_trading_history"


def _infer_market(ticker: str) -> str:
    """티커 형태로 시장 추론: 6자리 숫자 → KR, 그 외 → US."""
    t = (ticker or "").strip()
    return "KR" if (len(t) == 6 and t.isdigit()) else "US"


def _norm_date(raw: str) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD' → 'YYYY-MM-DD' (매수일 D)."""
    if not raw:
        return None
    s = str(raw).strip().split(" ")[0].split("T")[0]
    # 유효성 최소 검증
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _latest_buy_date(conn: sqlite3.Connection, tables: list[str]) -> str | None:
    """선정 대상 테이블들에서 가장 최근 date(buy_date). days-back 기준점."""
    latest = None
    for tbl in tables:
        if not _table_exists(conn, tbl):
            continue
        row = conn.execute(f"SELECT MAX(date(buy_date)) FROM {tbl}").fetchone()
        if row and row[0]:
            if latest is None or row[0] > latest:
                latest = row[0]
    return latest


def _extract_rows(
    conn: sqlite3.Connection, table: str, cutoff_date: str
) -> list[dict]:
    """한 테이블에서 cutoff_date(포함) 이후 매수건을 라벨링해 뽑는다."""
    if not _table_exists(conn, table):
        logger.warning("table not found, skipping: %s", table)
        return []
    q = (
        f"SELECT ticker, company_name, buy_date, profit_rate "
        f"FROM {table} WHERE date(buy_date) >= ? ORDER BY buy_date"
    )
    out: list[dict] = []
    for ticker, company_name, buy_date, profit_rate in conn.execute(q, (cutoff_date,)):
        d = _norm_date(buy_date)
        if d is None:
            logger.warning("bad buy_date, skip: %s %s", ticker, buy_date)
            continue
        try:
            pr = float(profit_rate)
        except (TypeError, ValueError):
            continue
        if pr > 0:
            label, exit_kind = "win", _WIN_EXIT_KIND
        elif pr < 0:
            label, exit_kind = "loss", _LOSS_EXIT_KIND
        else:
            continue  # profit==0 모호 → 스킵
        out.append(
            {
                "ticker": str(ticker),
                "company_name": company_name,
                "market": _infer_market(str(ticker)),
                "buy_date": d,
                "label": label,
                "profit_rate": round(pr, 4),
                "exit_kind": exit_kind,
            }
        )
    return out


def build_sample(db_path: str, days_back: int) -> list[dict]:
    """trading_history + us_trading_history 에서 표본을 구성한다."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [_KR_TABLE, _US_TABLE]
        latest = _latest_buy_date(conn, tables)
        if latest is None:
            logger.error("no rows in %s / %s", _KR_TABLE, _US_TABLE)
            return []
        # days-back 은 DB 내 최신 매수일 기준으로 anchor(운영 stale 데이터에도 안전).
        anchor = datetime.strptime(latest, "%Y-%m-%d")
        cutoff = (anchor.toordinal() - int(days_back))
        cutoff_date = datetime.fromordinal(cutoff).strftime("%Y-%m-%d")
        logger.info(
            "latest buy_date=%s, days_back=%d → cutoff=%s",
            latest, days_back, cutoff_date,
        )
        rows: list[dict] = []
        for tbl in tables:
            got = _extract_rows(conn, tbl, cutoff_date)
            logger.info("%s: %d rows", tbl, len(got))
            rows.extend(got)
        return rows
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="buy_quality 검증용 라벨 표본 빌더 (S1 승자 / S3 손절)."
    )
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite DB 경로")
    ap.add_argument(
        "--days-back", type=int, default=60,
        help="DB 최신 매수일 기준 최근 N일(기본 60)",
    )
    ap.add_argument("--out", default=DEFAULT_OUT, help="출력 JSON 경로")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        logger.error("DB not found: %s", args.db)
        return 1

    rows = build_sample(args.db, args.days_back)
    if not rows:
        logger.error("empty sample — nothing to write")
        return 2

    n_win = sum(1 for r in rows if r["label"] == "win")
    n_loss = sum(1 for r in rows if r["label"] == "loss")
    n_kr = sum(1 for r in rows if r["market"] == "KR")
    n_us = sum(1 for r in rows if r["market"] == "US")

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db": os.path.abspath(args.db),
        "days_back": args.days_back,
        "note": (
            "label derived from profit_rate sign (DB has no exit_kind column): "
            "win=profit>0, loss=profit<0. regime NOT assigned here (harness does)."
        ),
        "counts": {
            "total": len(rows), "win": n_win, "loss": n_loss,
            "kr": n_kr, "us": n_us,
        },
        "rows": rows,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(
        "wrote %d rows (win=%d loss=%d | KR=%d US=%d) → %s",
        len(rows), n_win, n_loss, n_kr, n_us, args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
