#!/usr/bin/env python3
"""Loop C — fill chaser / 미체결 추격 (LLM-free).

Runs as a standalone intraday cron, SEPARATE from the 2-3x/day batch sell cycle
and from Loop A (hard-stop) / Loop B (trend-exit). Loops A and B only ever PLACE
new sell orders; Loop C is the SINGLE owner of *open-order management*: it
reconciles in-flight orders against the live KIS unfilled-order inquiry and, when
an order has sat unfilled past a threshold, amends its limit price toward the
market ("chases") — within a ceiling — or cancels it.

WHY this loop exists (architecture §3, tasks/loop_architecture_design.md):
  - Limit sells placed by the batch / Loop A / Loop B can sit unfilled while the
    price walks away. A short fill-chaser materially reduces realised slippage.
  - The single source of truth for order state MUST be the live KIS inquiry, NOT
    an optimistic local cache — partial fills and external cancels happen.

PER CYCLE, PER MARKET:
  1. Inquire OPEN/unfilled orders from KIS (KR get_revisable_orders /
     US get_unfilled_orders) — the single source of truth.
  2. For each unfilled order older than LOOP_C_CHASE_AFTER_SEC:
       - SELL orders → chase the limit DOWN toward the market (we want the fill;
         this is a stop intent, downward chase is fine; floored at the market).
       - BUY orders → chase the limit UP toward the market, but NEVER above
         LOOP_C_BUY_MAX_PREMIUM_PCT over the order's original price (ceiling).
         If the ceiling is hit → CANCEL (do not chase into a bad fill).
  3. Reconcile partial fills off the live inquiry into loop_c_chase_log.

SAFETY (read before enabling):
  - Amend/cancel is gated behind LOOP_C_LIVE=true. Default = SHADOW: it logs
    what it WOULD amend/cancel and places NO real TR. The trading context is only
    opened for price/inquiry reads in SHADOW; no amend/cancel TR is ever sent.
  - LOOP_C_ENABLED=false disables the loop entirely (kill switch).
  - ⚠️ The KIS amend/cancel/unfilled-inquiry TR wrappers this loop depends on were
    mirrored from existing order wrappers + the KIS sample repo but were NOT
    validated against a live KIS account. DO NOT set LOOP_C_LIVE=true until the
    live-validation checklist in tasks/loop_c_design_notes.md is signed off.
  - Separate process → no in-process asyncio locks apply. Concurrency guarded by
    a SQLite owner_lock (BEGIN IMMEDIATE) per ticker, reusing Loop A's
    loop_a_position_state table so all loops serialise on the SAME lock.
  - Grace window: an order placed within LOOP_C_GRACE_SEC is left alone (another
    loop may have just placed it; let it breathe before chasing).
  - Only Loop C amends/cancels. Loops A/B only place new sells.

Usage:
    python tools/loop_c_fill_chaser.py [--market kr|us|both] [--once]

Intended cron (SHADOW until reviewed; NOT installed) — KR and US as SEPARATE
processes (cores-shadowing isolation; --market both fans out automatically):
    */2 9-15 * * 1-5      cd /root/prism-insight && python tools/loop_c_fill_chaser.py --market kr
    */2 22-23,0-5 * * 1-5 cd /root/prism-insight && python tools/loop_c_fill_chaser.py --market us
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent


# ── Market-aware path bootstrap (cores-shadowing safety) ──────────────────────
# The `cores` package is imported once per process and cached, and the US runtime
# resolves `from cores.X` to prism-us/cores while KR resolves to the root cores.
# A single process therefore CANNOT serve both markets without cross-wiring KR/US
# modules. Each market runs in its own process (main(): `both` spawns two
# subprocesses), and we set sys.path so the active market's modules win.
def _bootstrap_path(market: str) -> None:
    root = str(PROJECT_ROOT)
    us = str(PROJECT_ROOT / "prism-us")
    us_trading = str(PROJECT_ROOT / "prism-us" / "trading")
    if market == "US":
        for p in (root, us_trading, us):
            sys.path.insert(0, p)
    else:  # KR
        for p in (us, root):
            sys.path.insert(0, p)


logger = logging.getLogger("loop_c")


# ── Configuration (env-driven) ────────────────────────────────────────────────
def _env_bool(name: str, default: bool) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


LOOP_C_ENABLED = _env_bool("LOOP_C_ENABLED", True)        # master kill switch
LOOP_C_LIVE = _env_bool("LOOP_C_LIVE", False)             # False => SHADOW (no real amend/cancel)
LOCK_TTL_SEC = int(os.getenv("LOOP_C_LOCK_TTL_SEC", "300"))
# Chase an order only after it has been unfilled this long.
CHASE_AFTER_SEC = int(os.getenv("LOOP_C_CHASE_AFTER_SEC", "60"))
# Leave brand-new orders alone for this long (another loop may have just placed it).
GRACE_SEC = int(os.getenv("LOOP_C_GRACE_SEC", "20"))
# Each chase step moves the limit this fraction toward the market.
CHASE_STEP_PCT = float(os.getenv("LOOP_C_CHASE_STEP_PCT", "0.3"))
# BUY ceiling: never chase a buy limit more than this PERCENT over its original
# price. Env value is a percent (e.g. "0.5" = 0.5%); stored as a fraction.
BUY_MAX_PREMIUM_PCT = float(os.getenv("LOOP_C_BUY_MAX_PREMIUM_PCT", "0.5")) / 100.0
# Max number of amend steps before giving up and (optionally) cancelling.
MAX_CHASES = int(os.getenv("LOOP_C_MAX_CHASES", "5"))
# Whether to cancel a buy order once the ceiling is hit (else just stop chasing).
CANCEL_ON_CEILING = _env_bool("LOOP_C_CANCEL_ON_CEILING", True)

DB_PATH = os.getenv("LOOP_C_DB") or os.getenv("STOCK_TRACKING_DB") \
    or str(PROJECT_ROOT / "stock_tracking_db.sqlite")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── SQLite state (loop_c_* table + read/lock on Loop A's shared lock) ──────────
# Loop C creates its OWN loop_c_chase_log table and reuses Loop A's
# loop_a_position_state owner_lock so all loops serialise on one lock per ticker.
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Shared owner-lock table (created by Loop A; create-if-absent here so
        -- Loop C can run standalone). NEVER drops / alters existing rows.
        CREATE TABLE IF NOT EXISTS loop_a_position_state (
            ticker          TEXT NOT NULL,
            market          TEXT NOT NULL,
            state           TEXT NOT NULL DEFAULT 'HOLDING',
            owner_lock      TEXT,
            lock_expires_at TEXT,
            last_eval_ts    TEXT,
            PRIMARY KEY (ticker, market)
        );
        -- Loop C's own audit log of chase decisions (SHADOW + LIVE).
        CREATE TABLE IF NOT EXISTS loop_c_chase_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker        TEXT NOT NULL,
            market        TEXT NOT NULL,
            side          TEXT NOT NULL,           -- BUY/SELL
            order_no      TEXT,
            action        TEXT NOT NULL,           -- AMEND/CANCEL/SKIP
            mode          TEXT NOT NULL,           -- SHADOW/LIVE
            old_price     REAL,
            new_price     REAL,
            unfilled_qty  INTEGER,
            chase_count   INTEGER,
            reason        TEXT,
            loop_run_id   TEXT NOT NULL,
            logged_ts     TEXT NOT NULL
        );
        """
    )
    conn.commit()


def claim_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str) -> bool:
    """Atomically claim the shared owner_lock (BEGIN IMMEDIATE). True if acquired."""
    now = _now()
    expires = _iso(now + timedelta(seconds=LOCK_TTL_SEC))
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO loop_a_position_state (ticker, market, state) VALUES (?,?, 'HOLDING')",
            (ticker, market),
        )
        cur = conn.execute(
            "UPDATE loop_a_position_state SET owner_lock=?, lock_expires_at=?, last_eval_ts=? "
            "WHERE ticker=? AND market=? "
            "AND (owner_lock IS NULL OR lock_expires_at IS NULL OR lock_expires_at < ?)",
            (run_id, expires, _iso(now), ticker, market, _iso(now)),
        )
        conn.commit()
        return cur.rowcount == 1
    except sqlite3.Error as e:
        conn.rollback()
        logger.warning("lock claim failed %s/%s: %s", ticker, market, e)
        return False


def release_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str) -> None:
    try:
        conn.execute(
            "UPDATE loop_a_position_state SET owner_lock=NULL, lock_expires_at=NULL "
            "WHERE ticker=? AND market=? AND owner_lock=?",
            (ticker, market, run_id),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("lock release failed %s/%s: %s", ticker, market, e)


def record_chase(conn: sqlite3.Connection, ticker: str, market: str, side: str,
                 order_no: Optional[str], action: str, mode: str,
                 old_price: float, new_price: float, unfilled_qty: int,
                 chase_count: int, reason: str, run_id: str) -> None:
    try:
        conn.execute(
            "INSERT INTO loop_c_chase_log "
            "(ticker, market, side, order_no, action, mode, old_price, new_price, "
            " unfilled_qty, chase_count, reason, loop_run_id, logged_ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, market, side, order_no, action, mode, old_price, new_price,
             unfilled_qty, chase_count, reason, run_id, _iso(_now())),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("chase log failed %s/%s: %s", ticker, market, e)


def chase_count_for(conn: sqlite3.Connection, order_no: str, market: str) -> int:
    """How many AMENDs Loop C has already logged for this order_no."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM loop_c_chase_log "
            "WHERE order_no=? AND market=? AND action='AMEND'",
            (order_no, market),
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def first_seen_ts(conn: sqlite3.Connection, order_no: str, market: str) -> Optional[datetime]:
    """Earliest time Loop C logged anything (incl. SEEN) for this order. None if new."""
    try:
        row = conn.execute(
            "SELECT MIN(logged_ts) FROM loop_c_chase_log WHERE order_no=? AND market=?",
            (order_no, market),
        ).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
    except (sqlite3.Error, ValueError):
        pass
    return None


def record_seen(conn: sqlite3.Connection, ticker: str, market: str, side: str,
                order_no: str, price: float, unfilled_qty: int, run_id: str) -> None:
    """First-sighting marker so the grace window has a basis (no submit ts from KIS)."""
    record_chase(conn, ticker, market, side, order_no, "SEEN",
                 "LIVE" if LOOP_C_LIVE else "SHADOW",
                 price, price, unfilled_qty, 0, "first seen", run_id)


# ── Trader context (KR / US) — read-only price + inquiry in SHADOW ─────────────
def _open_context(market: str, account_name: Optional[str] = None):
    if market == "KR":
        from trading.domestic_stock_trading import AsyncTradingContext
        return AsyncTradingContext(account_name=account_name)
    from us_stock_trading import AsyncUSTradingContext
    return AsyncUSTradingContext(account_name=account_name)


# ── Order-state normalisation across KR / US inquiry wrappers ──────────────────
def _is_sell(side_code: str) -> bool:
    """KIS sll_buy_dvsn_cd: 01 = sell, 02 = buy (both markets)."""
    return str(side_code).strip() == "01"


async def _inquire_open_orders(trader, market: str) -> List[Dict[str, Any]]:
    """Return normalised open/unfilled orders. Empty list on any failure.

    Normalised dict keys (market-agnostic):
        order_no, ticker, side ('SELL'/'BUY'), unfilled_qty, ord_unpr,
        krx_fwdg_ord_orgno (KR only; '' for US).
    """
    out: List[Dict[str, Any]] = []
    try:
        if market == "KR":
            rows = await asyncio.to_thread(trader.get_revisable_orders)
            for r in rows:
                remaining = int(r.get("psbl_qty") or 0)
                if remaining <= 0:
                    continue
                out.append({
                    "order_no": r.get("order_no", ""),
                    "ticker": r.get("stock_code", ""),
                    "side": "SELL" if _is_sell(r.get("sll_buy_dvsn_cd")) else "BUY",
                    "unfilled_qty": remaining,
                    "ord_unpr": float(r.get("ord_unpr") or 0),
                    "krx_fwdg_ord_orgno": r.get("krx_fwdg_ord_orgno", ""),
                })
        else:  # US
            rows = await asyncio.to_thread(trader.get_unfilled_orders)
            for r in rows:
                remaining = int(r.get("nccs_qty") or 0)
                if remaining <= 0:
                    continue
                out.append({
                    "order_no": r.get("order_no", ""),
                    "ticker": r.get("ticker", ""),
                    "side": "SELL" if _is_sell(r.get("sll_buy_dvsn_cd")) else "BUY",
                    "unfilled_qty": remaining,
                    "ord_unpr": float(r.get("ord_unpr") or 0),
                    "exchange": r.get("exchange", "NASD"),
                    "krx_fwdg_ord_orgno": "",
                })
    except Exception as e:
        logger.warning("[%s] open-order inquiry failed: %s -> no-op", market, e)
        return []
    return out


def _compute_chase_price(side: str, order_price: float, market_price: float) -> float:
    """Move the limit a CHASE_STEP_PCT fraction toward the market price.

    SELL: chase DOWN toward market (floored at market — never below).
    BUY:  chase UP toward market (capped at market — never above).
    """
    if order_price <= 0 or market_price <= 0:
        return order_price
    if side == "SELL":
        # want a faster fill -> lower the ask toward (or to) the market
        target = order_price - (order_price - market_price) * CHASE_STEP_PCT
        return max(target, market_price)
    else:  # BUY
        target = order_price + (market_price - order_price) * CHASE_STEP_PCT
        return min(target, market_price)


def _round_price(market: str, price: float) -> float:
    """KR limit prices are integers (KRW); US allows cents."""
    if market == "KR":
        return float(int(round(price)))
    return round(price, 2)


# ── Core evaluation for one market ─────────────────────────────────────────────
async def _act_on_order(conn, trader, market: str, order: Dict[str, Any],
                        run_id: str, summary: Dict[str, Any]) -> None:
    """Decide + (LIVE) execute amend/cancel for one unfilled order. Never raises."""
    ticker = order["ticker"]
    side = order["side"]
    order_no = order["order_no"]
    order_price = float(order["ord_unpr"] or 0)
    unfilled_qty = int(order["unfilled_qty"] or 0)
    mode = "LIVE" if LOOP_C_LIVE else "SHADOW"

    if not ticker or not order_no or unfilled_qty <= 0 or order_price <= 0:
        return

    # Serialise against all other loops on the SAME owner_lock per ticker.
    if not claim_lock(conn, ticker, market, run_id):
        summary["skipped"] += 1
        logger.info("[%s] %s owner_lock held -> skip chase", market, ticker)
        return

    try:
        # Grace window: KIS unfilled inquiry gives no reliable submit timestamp,
        # so we mark first-sighting in the log and refuse to chase an order until
        # it has been visible to Loop C for at least GRACE_SEC. This stops Loop C
        # from amending an order another loop placed moments ago.
        seen = first_seen_ts(conn, order_no, market)
        if seen is None:
            record_seen(conn, ticker, market, side, order_no, order_price,
                        unfilled_qty, run_id)
            summary["grace_skipped"] += 1
            logger.info("[%s] %s order=%s first seen -> grace skip", market, ticker, order_no)
            return
        if (_now() - seen).total_seconds() < GRACE_SEC:
            summary["grace_skipped"] += 1
            logger.info("[%s] %s order=%s within grace window -> skip", market, ticker, order_no)
            return

        # Single source of truth for the market price = live KIS read.
        try:
            info = await asyncio.to_thread(trader.get_current_price, ticker)
            market_price = float((info or {}).get("current_price", 0) or 0)
        except Exception as e:
            logger.warning("[%s] %s price fetch failed: %s -> no-op", market, ticker, e)
            return
        if market_price <= 0:
            return

        already = chase_count_for(conn, order_no, market)
        ceiling_price = order_price * (1.0 + BUY_MAX_PREMIUM_PCT)

        # ── BUY ceiling enforcement ──────────────────────────────────────────
        if side == "BUY":
            # If the market has run above our premium ceiling, chasing would buy
            # too expensively -> stop. Cancel (default) or leave for the batch.
            if market_price > ceiling_price:
                if CANCEL_ON_CEILING:
                    await _do_cancel(conn, trader, market, order, run_id, summary,
                                     mode, order_price,
                                     reason=f"buy ceiling hit (mkt {market_price:.4f} > "
                                            f"ceiling {ceiling_price:.4f})")
                else:
                    summary["ceiling_skipped"] += 1
                    record_chase(conn, ticker, market, side, order_no, "SKIP", mode,
                                 order_price, order_price, unfilled_qty, already,
                                 "buy ceiling hit (no cancel)", run_id)
                    logger.info("[%s] %s BUY ceiling hit -> skip (no cancel)", market, ticker)
                return

        # ── Exhausted chase budget -> stop (sell) / cancel-or-stop (buy) ──────
        if already >= MAX_CHASES:
            if side == "BUY" and CANCEL_ON_CEILING:
                await _do_cancel(conn, trader, market, order, run_id, summary,
                                 mode, order_price,
                                 reason=f"max chases reached ({already})")
            else:
                summary["exhausted"] += 1
                record_chase(conn, ticker, market, side, order_no, "SKIP", mode,
                             order_price, order_price, unfilled_qty, already,
                             f"max chases reached ({already})", run_id)
                logger.info("[%s] %s max chases reached -> stop", market, ticker)
            return

        # ── Compute the chased price ────────────────────────────────────────
        raw_new = _compute_chase_price(side, order_price, market_price)
        new_price = _round_price(market, raw_new)
        # Cap a buy's chased price at the ceiling too.
        if side == "BUY":
            new_price = min(new_price, _round_price(market, ceiling_price))

        # No meaningful move -> nothing to do.
        if abs(new_price - order_price) < (1.0 if market == "KR" else 0.01):
            summary["no_move"] += 1
            logger.info("[%s] %s already at market -> no amend", market, ticker)
            return

        await _do_amend(conn, trader, market, order, run_id, summary, mode,
                        order_price, new_price, already)
    finally:
        release_lock(conn, ticker, market, run_id)


async def _do_amend(conn, trader, market, order, run_id, summary, mode,
                    old_price, new_price, already) -> None:
    ticker, side, order_no = order["ticker"], order["side"], order["order_no"]
    unfilled_qty = int(order["unfilled_qty"] or 0)

    if not LOOP_C_LIVE:
        summary["shadow"] += 1
        logger.info("[SHADOW][%s] WOULD AMEND %s %s order=%s qty=%d %.4f -> %.4f (chase #%d)",
                    market, side, ticker, order_no, unfilled_qty, old_price, new_price, already + 1)
        record_chase(conn, ticker, market, side, order_no, "AMEND", mode,
                     old_price, new_price, unfilled_qty, already + 1,
                     "shadow chase", run_id)
        return

    logger.warning("[LIVE][%s] AMEND %s %s order=%s %.4f -> %.4f",
                   market, side, ticker, order_no, old_price, new_price)
    try:
        if market == "KR":
            result = await asyncio.to_thread(
                trader.amend_order, ticker, order_no, int(new_price),
                order.get("krx_fwdg_ord_orgno", ""),
            )
        else:
            result = await asyncio.to_thread(
                trader.amend_order, ticker, order_no, float(new_price),
                unfilled_qty, order.get("exchange"),
            )
        ok = bool(result and result.get("success"))
        summary["amended"] += 1 if ok else 0
        record_chase(conn, ticker, market, side, order_no, "AMEND", mode,
                     old_price, new_price, unfilled_qty, already + 1,
                     (result or {}).get("message", ""), run_id)
        logger.warning("[LIVE][%s] %s amend success=%s msg=%s",
                       market, ticker, ok, (result or {}).get("message"))
    except Exception as e:
        logger.error("[%s] %s amend failed: %s", market, ticker, e)


async def _do_cancel(conn, trader, market, order, run_id, summary, mode,
                     old_price, reason) -> None:
    ticker, side, order_no = order["ticker"], order["side"], order["order_no"]
    unfilled_qty = int(order["unfilled_qty"] or 0)

    if not LOOP_C_LIVE:
        summary["shadow"] += 1
        logger.info("[SHADOW][%s] WOULD CANCEL %s %s order=%s qty=%d (%s)",
                    market, side, ticker, order_no, unfilled_qty, reason)
        record_chase(conn, ticker, market, side, order_no, "CANCEL", mode,
                     old_price, old_price, unfilled_qty,
                     chase_count_for(conn, order_no, market), reason, run_id)
        return

    logger.warning("[LIVE][%s] CANCEL %s %s order=%s (%s)",
                   market, side, ticker, order_no, reason)
    try:
        if market == "KR":
            result = await asyncio.to_thread(
                trader.cancel_order, ticker, order_no,
                order.get("krx_fwdg_ord_orgno", ""),
            )
        else:
            result = await asyncio.to_thread(
                trader.cancel_order, ticker, order_no, unfilled_qty,
                order.get("exchange"),
            )
        ok = bool(result and result.get("success"))
        summary["cancelled"] += 1 if ok else 0
        record_chase(conn, ticker, market, side, order_no, "CANCEL", mode,
                     old_price, old_price, unfilled_qty,
                     chase_count_for(conn, order_no, market), reason, run_id)
        logger.warning("[LIVE][%s] %s cancel success=%s msg=%s",
                       market, ticker, ok, (result or {}).get("message"))
    except Exception as e:
        logger.error("[%s] %s cancel failed: %s", market, ticker, e)


async def run_market(market: str, run_id: str) -> Dict[str, Any]:
    """Reconcile + chase every unfilled order for one market.

    Never raises: any failure degrades to a no-op for that order/market. The live
    KIS inquiry is the single source of truth — an empty/failed inquiry means
    "nothing to chase", NEVER "everything filled".
    """
    summary = {"market": market, "open_orders": 0, "evaluated": 0, "shadow": 0,
               "amended": 0, "cancelled": 0, "skipped": 0, "no_move": 0,
               "ceiling_skipped": 0, "exhausted": 0, "grace_skipped": 0}
    conn = _connect()
    try:
        _ensure_schema(conn)
        try:
            async with _open_context(market) as trader:
                orders = await _inquire_open_orders(trader, market)
                summary["open_orders"] = len(orders)
                for order in orders:
                    summary["evaluated"] += 1
                    await _act_on_order(conn, trader, market, order, run_id, summary)
        except Exception as e:  # context/credential failure -> skip whole market safely
            logger.warning("%s trading context failed: %s", market, e)
    finally:
        conn.close()
    return summary


async def main_async(markets: List[str]) -> int:
    if not LOOP_C_ENABLED:
        logger.info("LOOP_C_ENABLED=false -> loop disabled, exiting.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    mode = "LIVE" if LOOP_C_LIVE else "SHADOW"
    logger.info("Loop C start run_id=%s mode=%s markets=%s db=%s", run_id, mode, markets, DB_PATH)
    totals: Dict[str, int] = {}
    for market in markets:
        s = await run_market(market, run_id)
        for k, v in s.items():
            if isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
        logger.info("Loop C %s summary: %s", market, s)
    logger.info("Loop C done run_id=%s mode=%s totals=%s", run_id, mode, totals)
    return 0


def _setup_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_dir / "loop_c_fill_chaser.log"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def _run_both_isolated() -> int:
    """Run KR and US as SEPARATE subprocesses (cores-shadowing isolation)."""
    import subprocess
    rc = 0
    for m in ("kr", "us"):
        try:
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--market", m])
            rc = rc or proc.returncode
        except Exception as e:
            logger.error("subprocess for market=%s failed: %s", m, e)
            rc = rc or 1
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Loop C fill-chaser (미체결 추격)")
    parser.add_argument("--market", choices=["kr", "us", "both"], default="both")
    parser.add_argument("--once", action="store_true", help="(default) run a single cycle")
    args = parser.parse_args()
    _setup_logging()
    if args.market == "both":
        return _run_both_isolated()
    market = {"kr": "KR", "us": "US"}[args.market]
    _bootstrap_path(market)
    return asyncio.run(main_async([market]))


if __name__ == "__main__":
    raise SystemExit(main())
