#!/usr/bin/env python3
"""Log-only relative-weakness exit check for runner holdings (KR/US, user decision 2026-10-14).

Candidate (e) from the runner-hold bear study: a stored runner (prism_core.runner_hold, status
RUNNER) would be sold when, measured from its highest completed close since the entry session,
the stock fell at least 10%p more than the index over the same days AND its completed close is
below the 20-day MA. Index: KR KOSPI (KOSDAQ recorded as a reference), US SPY.

This tool only records what (e) would do. It reads the holdings read-only, appends one row per
runner per run to logs/runner_rs_shadow_{kr,us}.jsonl and emits a ``runner.rs_shadow`` event.
No orders, no DB writes, no messages; the live runner rule is unchanged.
Study and numbers: db-server /root/prism-research-out/runner-bear/REPORT_ko.md.

Usage:
    python tools/runner_rs_shadow.py --market kr
    python tools/runner_rs_shadow.py --market us --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.getenv("PRISM_REPO_ROOT") or Path(__file__).resolve().parents[1])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core import runner_hold as R  # noqa: E402

logger = logging.getLogger(__name__)

VERSION = "runner-rs-shadow-e-v1"
GAP = -0.10                 # stock change from the peak minus index change over the same days
MA_SHORT = 20
INDEX_DAYS = 200            # calendar days of index history (covers a 40-session hold plus margin)
INDEXES = {"KR": (("KOSPI", "1001"), ("KOSDAQ", "2001")), "US": (("SPY", "SPY"),)}
HOLDINGS_SQL = {
    "KR": "SELECT id, ticker, company_name, buy_price, buy_date, current_price, scenario FROM stock_holdings ORDER BY id",
    "US": "SELECT id, ticker, company_name, buy_price, buy_date, current_price, scenario FROM us_stock_holdings ORDER BY id",
}


def evaluate_e(bars, index_bars, entry_session, gap=GAP):
    """(e) facts on the last completed bar, or None when the stock history is too short.

    ``bars`` and ``index_bars`` are completed [{date, close}] oldest first. The peak is the first
    highest close on or after ``entry_session``. ``fired`` is None when the index lacks either day.
    """
    if len(bars) < MA_SHORT:
        return None
    held = [b for b in bars if b["date"] >= str(entry_session)]
    if not held:
        return None
    last = bars[-1]
    peak = max(held, key=lambda b: b["close"])
    ma20 = sum(b["close"] for b in bars[-MA_SHORT:]) / MA_SHORT
    facts = {"date": last["date"], "close": last["close"], "ma20": round(ma20, 4), "below_ma20": last["close"] < ma20,
             "peak_date": peak["date"], "peak_close": peak["close"],
             "stock_from_peak_pct": round((last["close"] / peak["close"] - 1) * 100, 2)}
    closes = {b["date"]: b["close"] for b in index_bars}
    if peak["date"] not in closes or last["date"] not in closes:
        return dict(facts, index_from_peak_pct=None, rel_pp=None, fired=None)
    index_change = closes[last["date"]] / closes[peak["date"]] - 1
    rel = (last["close"] / peak["close"] - 1) - index_change
    return dict(facts, index_from_peak_pct=round(index_change * 100, 2), rel_pp=round(rel * 100, 2),
                fired=bool(rel <= gap and facts["below_ma20"]))


def runner_rows(db_path, market):
    """One stored RUNNER holding per ticker (the first account row), read-only."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(HOLDINGS_SQL[market])]
    finally:
        conn.close()
    seen, out = set(), []
    for row in rows:
        block = R.record(row.get("scenario"))
        if block is None or block.get("status") != R.RUNNER or row["ticker"] in seen:
            continue
        seen.add(row["ticker"])
        out.append(dict(row, runner=block))
    return out


def fetch_index_bars(market, code, now):
    end = now.date()
    start = end - timedelta(days=INDEX_DAYS)
    if market == "US":
        import yfinance as yf
        frame = yf.Ticker(code).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                        auto_adjust=False)
    else:
        from cores import market_data
        frame = market_data.get_index_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
    if frame is None or frame.empty:
        return []
    columns = {str(c).lower(): c for c in frame.columns}
    close = columns.get("close") or columns.get("종가")
    if close is None:
        return []
    return [{"date": index.date().isoformat(), "close": float(row[close])} for index, row in frame.iterrows()]


def run(market, db_path, *, now=None, fetch_bars=None, fetch_index=None, dry_run=False):
    from prism_core import runner_hold_live as L
    now = now or datetime.now(timezone.utc)
    fetch_bars = fetch_bars or L.fetch_bars_safe
    fetch_index = fetch_index or (lambda code: fetch_index_bars(market, code, now))
    holdings = runner_rows(db_path, market)
    if not holdings:
        logger.info("[RUNNER_RS_SHADOW][%s] no stored runners", market)
        return []
    indexes = {}
    for name, code in INDEXES[market]:
        try:
            indexes[name] = R.completed_bars(fetch_index(code), market=market, now=now)
        except Exception as error:  # noqa: BLE001 - a missing index only leaves fired=None
            logger.warning("[RUNNER_RS_SHADOW][%s] index %s unavailable: %s", market, name, type(error).__name__)
            indexes[name] = []
    primary = INDEXES[market][0][0]
    out = []
    for row in holdings:
        block = row["runner"]
        bars = R.completed_bars(fetch_bars(market, row["ticker"]), market=market, now=now)
        start = block.get("entry_session")
        by_index = {name: evaluate_e(bars, idx, start) for name, idx in indexes.items()} if bars and start else {}
        main = by_index.get(primary)
        record = {"version": VERSION, "market": market, "run_at": now.isoformat(), "row_id": row["id"],
                  "ticker": row["ticker"], "name": row.get("company_name"), "entry_ref": block.get("entry_ref"),
                  "entry_session": start, "hold_until": block.get("hold_until"), "phase": R.phase(block, R.local_today(market, now).isoformat()),
                  "index": primary, "e": main,
                  "reference": {k: v for k, v in by_index.items() if k != primary}}
        out.append(record)
        state = "unavailable" if main is None else ("FIRED" if main.get("fired") else "hold")
        logger.info("[RUNNER_RS_SHADOW][%s] %s %s state=%s rel=%s below_ma20=%s", market, row["ticker"],
                    (main or {}).get("date"), state, (main or {}).get("rel_pp"), (main or {}).get("below_ma20"))
        if dry_run:
            continue
        try:
            from observability.events import emit_event
            emit_event("runner.rs_shadow", service=f"prism-{market.lower()}-runner-hold", market=market,
                       ticker=row["ticker"], position_id=f"legacy:{market}:{row['id']}",
                       attributes={"version": VERSION, "state": state, "e": main})
        except Exception as error:  # noqa: BLE001 - logging must not fail the run
            logger.warning("[RUNNER_RS_SHADOW][%s] event skipped: %s", market, type(error).__name__)
    if out and not dry_run:
        path = ROOT / "logs" / f"runner_rs_shadow_{market.lower()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in out:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    parser.add_argument("--db", default=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    parser.add_argument("--dry-run", action="store_true", help="print only; no jsonl or event")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    market = args.market.upper()
    if not R.enabled(market):
        logger.info("[RUNNER_RS_SHADOW][%s] runner hold disabled; nothing to record", market)
        return
    for record in run(market, args.db, dry_run=args.dry_run):
        if args.dry_run:
            print(json.dumps(record, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
