#!/usr/bin/env python3
"""Post-close fill confirmation for the day's buys and micro-split adds.

For every BUY the order ledger says was sent that market-local day, read the broker's
daily execution inquiry for the account that sent it and emit one ``entry.fill_reconciled``
(CONFIRMED / PARTIAL / NOT_FILLED / MISSING) on the trade's trace. Only inquiry TRs are
called (KR TTTC0081R, US TTTS3035R). Nothing is ordered, amended or cancelled. The one
write (2026-11-14): a final fill quantity is stored as ``filled_qty`` on the micro-split
ledger leg that order bought (the row's INITIAL leg, or the add leg with its decision id),
so per-row stop quantities count shares bought, not shares ordered; MISSING writes nothing.
Account numbers never leave the broker call: the event carries only ``execution_profile_ref``.

Usage:
    python tools/reconcile_fills.py --market KR [--date YYYYMMDD] [--dry-run]
    python tools/reconcile_fills.py --market US [--date YYYYMMDD] [--dry-run]

KR and US run as separate processes (cores/trading package shadowing). Without --dry-run
the run refuses to start before the market close of --date (an open order is not
"not filled"). Suggested cron (KST): KR 10 16 * * 1-5, US 10 7 * * 2-6.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("reconcile_fills")


def _bootstrap_path(market: str) -> None:
    """Same order as tools/fill_chaser.py so the active market's packages win."""
    root, us, us_trading = str(ROOT), str(ROOT / "prism-us"), str(ROOT / "prism-us" / "trading")
    for path in ((root, us_trading, us) if market == "US" else (us, root)):
        sys.path.insert(0, path)


def _accounts(market: str) -> dict[str, dict]:
    if market == "KR":
        from trading import kis_auth as ka
    else:
        sys.path.insert(0, str(ROOT / "trading"))
        import kis_auth as ka  # the module prism-us/trading/us_stock_trading.py uses
    return {account["account_key"]: account for account in ka.get_configured_accounts()}


def _service(market: str, account_name: str):
    from prism_core.execution_service import ExecutionService
    return (ExecutionService.domestic if market == "KR" else ExecutionService.us)(account_name=account_name)


def _query_range(market: str, day: str) -> tuple[str, str]:
    if market == "KR":
        return day, day
    # Live rows (10/09, 10/14) carry the US session date with a KST order time; the next day stays in
    # range so a row dated the KST calendar day is not missed (exact order-number match decides).
    nxt = (datetime.strptime(day, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    return day, nxt


async def _account_rows(market, account, day, service_factory=_service):
    """(authoritative, normalized rows, reason) for one account; never raises."""
    from observability.fill_reconciliation import normalize_rows
    try:
        async with service_factory(market, account["name"]) as service:
            if getattr(service, "account_key", None) != account["account_key"]:
                return False, [], "ACCOUNT_SCOPE_MISMATCH"
            start, end = _query_range(market, day)
            ok, raw = await asyncio.to_thread(service.get_daily_executions_checked, start, end)
    except Exception as error:  # noqa: BLE001 - an inquiry failure is MISSING, not a fill result
        logger.warning("[FILL_RECONCILE][%s] inquiry unavailable: %s", market, type(error).__name__)
        return False, [], "BROKER_QUERY_FAILED"
    if not ok:
        return False, [], "BROKER_QUERY_FAILED"
    parsed_ok, rows = normalize_rows(market, raw)
    return parsed_ok, rows, None if parsed_ok else "BROKER_ROWS_INVALID"


def _leg_key(market, target):
    """(holding row id, add decision id or None for the INITIAL leg) this buy filled, or None."""
    parts = str(target.get("source_position_id") or "").split(":")
    if len(parts) != 3 or parts[0] != "legacy" or parts[1] != market or not parts[2].isdigit():
        return None
    return int(parts[2]), (target.get("source_decision_id") if target["is_add"] else None)


async def run(market, day, *, db_path, dry_run=False, state_path=None, accounts=None,
              service_factory=_service, spool_path=None):
    from observability import fill_reconciliation as fr
    from observability.trading_context import execution_profile_ref
    from prism_core.micro_split_live import record_leg_fill

    targets = fr.load_targets(db_path, market, day)
    accounts = _accounts(market) if accounts is None else accounts
    state = fr.EmittedState(state_path)
    by_account = defaultdict(list)
    for target in targets:
        by_account[target["account_id"]].append(target)
    report, counts = [], Counter()
    leg_keys = {target["id"]: _leg_key(market, target) for target in targets}
    key_uses = Counter(key for key in leg_keys.values() if key)  # two buys on one leg: ambiguous, skip
    ledger = None if dry_run else sqlite3.connect(db_path, timeout=30)
    for account_key, group in by_account.items():
        account = accounts.get(account_key)
        if account is None:
            ok, rows, reason = False, [], "ACCOUNT_NOT_CONFIGURED"
        else:
            ok, rows, reason = await _account_rows(market, account, day, service_factory)
        profile = execution_profile_ref(account_key)
        for target in group:
            result = fr.reconcile_target(target, rows, market=market, authoritative=ok, reason=reason)
            attributes = fr.build_attributes(target, result, market=market, execution_profile_ref=profile)
            status = result["fill_status"]
            emitted = None
            if not dry_run and state.should_emit(target["id"], status):
                emitted = fr.emit(target, attributes, market=market, spool_path=spool_path)
                if emitted is not None:
                    state.mark(target["id"], status)
            key = leg_keys[target["id"]]
            leg_recorded = bool(
                ledger is not None and key and key_uses[key] == 1 and status in fr.FINAL_STATUSES
                and result["filled_qty"] is not None
                and record_leg_fill(ledger, market=market, row_id=key[0], symbol=target["symbol"],
                                    account_key=account_key, decision_id=key[1], filled_qty=result["filled_qty"]))
            counts[status] += 1
            report.append({"symbol": target["symbol"], "entry_kind": attributes["entry_kind"],
                           "intent_source": target.get("source"), "fill_status": status,
                           "reason_code": result["reason_code"], "match_basis": result["match_basis"],
                           "filled_qty": result["filled_qty"], "ordered_qty": result["ordered_qty"],
                           "avg_fill_price": result["avg_fill_price"],
                           "submitted_limit_price": attributes["submitted_limit_price"],
                           "trace_linked": bool(target.get("trace_decision_id")),
                           "emitted": emitted is not None, "ledger_fill_recorded": leg_recorded})
    if ledger is not None:
        ledger.close()
    if not dry_run:
        state.save()
    return {"market": market, "date": day, "dry_run": dry_run, "targets": len(targets),
            "statuses": dict(counts), "orders": report}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--market", required=True, type=str.upper, choices=("KR", "US"))
    parser.add_argument("--date", help="market-local order date YYYYMMDD (default: today there)")
    parser.add_argument("--dry-run", action="store_true", help="query and print, emit nothing")
    parser.add_argument("--db", default=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    _bootstrap_path(args.market)
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("[FILL_RECONCILE] python-dotenv unavailable; using the process environment")
    else:
        load_dotenv(ROOT / ".env")
    from observability.fill_reconciliation import MARKET_TZ, after_close
    day = args.date or datetime.now(MARKET_TZ[args.market]).strftime("%Y%m%d")
    if not args.dry_run and not after_close(args.market, day, datetime.now(timezone.utc)):
        logger.info("[FILL_RECONCILE][%s] %s market not closed yet; nothing emitted", args.market, day)
        return 0
    summary = asyncio.run(run(args.market, day, db_path=args.db, dry_run=args.dry_run))
    logger.info("[FILL_RECONCILE][%s] %s statuses=%s", args.market, day, summary["statuses"])
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
