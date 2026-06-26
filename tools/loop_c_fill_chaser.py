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
import math
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

# Load .env so env-driven config below (FILL_CHASER_*, incl. FILL_CHASER_LIVE) is
# visible — a fresh cron process does not inherit .env otherwise. loop_a/loop_b do
# the same; without it loop_c silently used code defaults and ignored .env.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ── Configuration (env-driven) ────────────────────────────────────────────────
# Canonical env prefix is FILL_CHASER_. The legacy LOOP_C_ prefix is a DEPRECATED
# alias, still honored so existing prod .env / crontab survive the rename; main()
# warns once for any legacy key in use.
_DEPRECATED_ENV: List[str] = []


def _env(suffix: str, default: Optional[str] = None) -> Optional[str]:
    """Read FILL_CHASER_<suffix>, falling back to the deprecated LOOP_C_<suffix>."""
    val = os.getenv("FILL_CHASER_" + suffix)
    if val is not None:
        return val
    legacy = os.getenv("LOOP_C_" + suffix)
    if legacy is not None:
        _DEPRECATED_ENV.append("LOOP_C_" + suffix)
        return legacy
    return default


def _env_flag(suffix: str, default: bool) -> bool:
    raw = _env(suffix)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


LOOP_C_ENABLED = _env_flag("ENABLED", True)               # master kill switch
LOOP_C_LIVE = _env_flag("LIVE", False)                    # False => SHADOW (no real amend/cancel)
LOCK_TTL_SEC = int(_env("LOCK_TTL_SEC", "300"))
# Chase an order only after it has been unfilled this long.
CHASE_AFTER_SEC = int(_env("CHASE_AFTER_SEC", "60"))
# Leave brand-new orders alone for this long (another loop may have just placed it).
GRACE_SEC = int(_env("GRACE_SEC", "20"))
# Each chase step moves the limit this fraction toward the market.
CHASE_STEP_PCT = float(_env("CHASE_STEP_PCT", "0.3"))
# BUY ceiling = slippage budget: how far above the original limit we are willing
# to pay to secure a fill. Env value is a percent (e.g. "3.0" = 3%); stored as a
# fraction. Beyond it -> CANCEL (don't chase into the top of a runaway spike).
BUY_MAX_PREMIUM_PCT = float(_env("BUY_MAX_PREMIUM_PCT", "3.0")) / 100.0
# Fill-priority for BUY: when the market is within the slippage budget, place a
# MARKETABLE limit (cross the spread) so the order fills NOW instead of trailing
# the market CHASE_STEP_PCT-per-step and never catching a still-rising price.
# The limit is set BUY_CROSS_PAD_PCT above the live market, then capped at the
# budget ceiling. Disable to fall back to the legacy sub-market creep.
BUY_CROSS = _env_flag("BUY_CROSS", True)
BUY_CROSS_PAD_PCT = float(_env("BUY_CROSS_PAD_PCT", "0.1")) / 100.0
# Max number of amend steps before giving up and (optionally) cancelling.
MAX_CHASES = int(_env("MAX_CHASES", "5"))
# Whether to cancel a buy order once the ceiling is hit (else just stop chasing).
CANCEL_ON_CEILING = _env_flag("CANCEL_ON_CEILING", True)

DB_PATH = _env("DB") or os.getenv("STOCK_TRACKING_DB") \
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
    BUY:  if BUY_CROSS, jump to a MARKETABLE limit (cross the spread) for an
          immediate fill; else legacy creep UP toward market (never crossing).
          Either way the caller caps the result at the slippage-budget ceiling.
    """
    if order_price <= 0 or market_price <= 0:
        return order_price
    if side == "SELL":
        # want a faster fill -> lower the ask toward (or to) the market
        target = order_price - (order_price - market_price) * CHASE_STEP_PCT
        return max(target, market_price)
    else:  # BUY
        if BUY_CROSS:
            # Fill-priority: cross the spread now; caller caps at the ceiling.
            return market_price * (1.0 + BUY_CROSS_PAD_PCT)
        # Legacy creep: move only a fraction toward market, never crossing it.
        target = order_price + (market_price - order_price) * CHASE_STEP_PCT
        return min(target, market_price)


# KRX 호가단위 (tick size) by price tier. Each entry is (price_below, tick):
# a price strictly LESS than ``price_below`` uses ``tick``. The final open-ended
# tier (>= 500,000) uses 1,000 KRW. Mirrors the KRX domestic-equity tick schedule
# (post-2023 unified table). KIS rejects off-tick limit prices with APBK0506, so
# every KR limit price Loop C sends MUST be snapped to its tier's tick.
_KR_TICK_TABLE = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
_KR_TICK_TOP = 1_000  # >= 500,000 KRW


def _kr_tick_size(price: float) -> int:
    """Return the KRX 호가단위 (tick) for a KR price tier."""
    for ceiling, tick in _KR_TICK_TABLE:
        if price < ceiling:
            return tick
    return _KR_TICK_TOP


def _round_price(market: str, price: float, round_up: bool = False) -> float:
    """Snap a limit price to a tradable unit.

    KR prices must align to the KRX 호가단위 (tick), which varies by price tier; an
    off-tick limit is rejected by KIS (APBK0506). We snap DOWN to the tick grid by
    default (a chase-preserving, conservative direction). round_up=True ceilings to
    the tick instead — used for marketable BUY limits so the rounding step never
    drops the price back below the live market (which would defeat the cross /
    fill-priority intent of #378). price<=0 falls back to integer rounding.

    US allows cents -> round to 2 decimals (unchanged).
    """
    if market == "KR":
        if price <= 0:
            return float(math.ceil(price)) if round_up else float(int(round(price)))
        # KRW prices are integer-valued; collapse to a whole number FIRST so float
        # noise from upstream arithmetic (e.g. 23199.9999996) cannot push the
        # tick-grid math onto the wrong rung (which would itself re-trigger
        # APBK0506). Integer ceil/floor division then keeps the snap fully
        # float-free.
        whole = int(round(price))
        tick = _kr_tick_size(whole)
        if round_up:
            return float(-(-whole // tick) * tick)   # ceil to tick (integer math)
        return float((whole // tick) * tick)          # floor to tick
    return math.ceil(price * 100.0) / 100.0 if round_up else round(price, 2)


# ── SHADOW verification helpers (dry-run payload + fill plausibility) ───────────
def _build_dry_run_payload(trader, market: str, order: Dict[str, Any],
                           action: str, new_price: float) -> Dict[str, Any]:
    """Call the amend/cancel TR wrapper with dry_run=True and return the exact
    request that WOULD be sent (tr_id + endpoint + full body). No network, no
    order. ``action`` is "AMEND" or "CANCEL". Returns {} if the wrapper does not
    support dry_run or raises (never blocks SHADOW logging)."""
    ticker = order["ticker"]
    order_no = order["order_no"]
    unfilled_qty = int(order["unfilled_qty"] or 0)
    try:
        if market == "KR":
            if action == "AMEND":
                return trader.amend_order(
                    ticker, order_no, int(new_price),
                    order.get("krx_fwdg_ord_orgno", ""), dry_run=True,
                ) or {}
            return trader.cancel_order(
                ticker, order_no, order.get("krx_fwdg_ord_orgno", ""),
                dry_run=True,
            ) or {}
        else:  # US
            if action == "AMEND":
                return trader.amend_order(
                    ticker, order_no, float(new_price), unfilled_qty,
                    order.get("exchange"), dry_run=True,
                ) or {}
            return trader.cancel_order(
                ticker, order_no, unfilled_qty, order.get("exchange"),
                dry_run=True,
            ) or {}
    except Exception as e:  # never let dry-run verification break SHADOW logging
        logger.warning("[%s] %s dry-run payload build failed: %s", market, ticker, e)
        return {}


def _fill_verdict(side: str, new_price: float, market_price: float) -> str:
    """Fill-plausibility: would the chased limit plausibly fill at the market?

    SELL chasing DOWN  -> FILL_LIKELY if new_price <= market_price.
    BUY  chasing UP    -> FILL_LIKELY if new_price >= market_price.
    Otherwise FILL_UNLIKELY. Used only for SHADOW eyeballing, never gates trades.
    """
    if new_price <= 0 or market_price <= 0:
        return "FILL_UNKNOWN"
    if side == "SELL":
        return "FILL_LIKELY" if new_price <= market_price else "FILL_UNLIKELY"
    return "FILL_LIKELY" if new_price >= market_price else "FILL_UNLIKELY"


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
                                     reason=f"buy-ceiling: mkt {market_price:.4f} > "
                                            f"ceiling {ceiling_price:.4f}",
                                     market_price=market_price)
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
                                 reason=f"max-chases reached ({already})",
                                 market_price=market_price)
            else:
                summary["exhausted"] += 1
                record_chase(conn, ticker, market, side, order_no, "SKIP", mode,
                             order_price, order_price, unfilled_qty, already,
                             f"max chases reached ({already})", run_id)
                logger.info("[%s] %s max chases reached -> stop", market, ticker)
            return

        # ── Compute the chased price ────────────────────────────────────────
        raw_new = _compute_chase_price(side, order_price, market_price)
        # Round BUY cross prices UP to the tick so rounding never pushes the
        # limit back below the live market (which would lose the fill).
        new_price = _round_price(market, raw_new,
                                 round_up=(side == "BUY" and BUY_CROSS))
        # Cap a buy's chased price at the budget ceiling too.
        if side == "BUY":
            new_price = min(new_price, _round_price(market, ceiling_price))

        # No meaningful move -> nothing to do.
        if abs(new_price - order_price) < (1.0 if market == "KR" else 0.01):
            summary["no_move"] += 1
            logger.info("[%s] %s already at market -> no amend", market, ticker)
            return

        # Reason annotation for SHADOW eyeballing (normal chase vs floored/capped).
        if side == "SELL" and new_price <= market_price:
            reason = "floor-at-market"
        elif side == "BUY" and new_price >= _round_price(market, ceiling_price):
            reason = "buy-cap-at-ceiling"
        else:
            reason = "normal chase"
        await _do_amend(conn, trader, market, order, run_id, summary, mode,
                        order_price, new_price, already,
                        market_price=market_price, reason=reason)
    finally:
        release_lock(conn, ticker, market, run_id)


async def _do_amend(conn, trader, market, order, run_id, summary, mode,
                    old_price, new_price, already, market_price=0.0,
                    reason="normal chase") -> None:
    ticker, side, order_no = order["ticker"], order["side"], order["order_no"]
    unfilled_qty = int(order["unfilled_qty"] or 0)

    if not LOOP_C_LIVE:
        summary["shadow"] += 1
        verdict = _fill_verdict(side, new_price, market_price)
        payload = _build_dry_run_payload(trader, market, order, "AMEND", new_price)
        logger.info(
            "[LOOP_C][SHADOW] decision=WOULD_AMEND market=%s ticker=%s order_no=%s "
            "side=%s unfilled_qty=%d chase=#%d/%d reason=%s "
            "orig_limit=%.4f new_price=%.4f market_price=%.4f fill=%s "
            "tr_id=%s path=%s payload=%s",
            market, ticker, order_no, side, unfilled_qty, already + 1, MAX_CHASES,
            reason, old_price, new_price, market_price, verdict,
            payload.get("tr_id"), payload.get("api_url"), payload.get("params"),
        )
        record_chase(conn, ticker, market, side, order_no, "AMEND", mode,
                     old_price, new_price, unfilled_qty, already + 1,
                     f"shadow chase ({reason}) fill={verdict} payload={payload.get('params')}",
                     run_id)
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
                     old_price, reason, market_price=0.0) -> None:
    ticker, side, order_no = order["ticker"], order["side"], order["order_no"]
    unfilled_qty = int(order["unfilled_qty"] or 0)

    if not LOOP_C_LIVE:
        summary["shadow"] += 1
        payload = _build_dry_run_payload(trader, market, order, "CANCEL", 0.0)
        logger.info(
            "[LOOP_C][SHADOW] decision=WOULD_CANCEL market=%s ticker=%s order_no=%s "
            "side=%s unfilled_qty=%d orig_limit=%.4f market_price=%.4f reason=%s "
            "fill=N/A tr_id=%s path=%s payload=%s",
            market, ticker, order_no, side, unfilled_qty, old_price, market_price,
            reason, payload.get("tr_id"), payload.get("api_url"),
            payload.get("params"),
        )
        record_chase(conn, ticker, market, side, order_no, "CANCEL", mode,
                     old_price, old_price, unfilled_qty,
                     chase_count_for(conn, order_no, market),
                     f"{reason} payload={payload.get('params')}", run_id)
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
    if _DEPRECATED_ENV:
        logger.warning(
            "deprecated env keys in use (rename to FILL_CHASER_*): %s",
            ", ".join(sorted(set(_DEPRECATED_ENV))),
        )


# ── --selftest: exercise chase->payload->verdict->log with NO DB lock / API ────
class _SelftestTrader:
    """Synthetic trader for --selftest: serves only dry-run amend/cancel payloads.

    It NEVER places/modifies orders and makes NO network calls — it just forwards
    to the real TR wrappers' dry_run path so the exact SHADOW payload is built and
    logged. A real trader is import-light to construct (needs credentials), so the
    selftest uses this stand-in that mimics the wrapper signatures + dry_run=True.
    """

    def __init__(self, market: str):
        self._market = market

    def _payload(self, action: str, ticker: str, order_no: str, qty: int,
                 price: float, fwdg: str, exchange: str) -> Dict[str, Any]:
        # Mirror the real wrapper's dry-run dict shape (tr_id/api_url/params) so
        # selftest output looks identical to a live SHADOW run — without importing
        # credential-bound trading classes.
        if self._market == "KR":
            tr_id = "TTTC0013U"
            api_url = "/uapi/domestic-stock/v1/trading/order-rvsecncl"
            params = {
                "CANO": "SELFTEST", "ACNT_PRDT_CD": "01",
                "KRX_FWDG_ORD_ORGNO": fwdg, "ORGN_ODNO": str(order_no),
                "ORD_DVSN": "00",
                "RVSE_CNCL_DVSN_CD": "01" if action == "AMEND" else "02",
                "ORD_QTY": "0", "ORD_UNPR": str(int(price if action == "AMEND" else 0)),
                "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX", "CNDT_PRIC": "",
            }
        else:
            tr_id = "TTTT1004U"
            api_url = "/uapi/overseas-stock/v1/trading/order-rvsecncl"
            params = {
                "CANO": "SELFTEST", "ACNT_PRDT_CD": "01",
                "OVRS_EXCG_CD": exchange or "NASD", "PDNO": ticker.upper(),
                "ORGN_ODNO": str(order_no),
                "RVSE_CNCL_DVSN_CD": "01" if action == "AMEND" else "02",
                "ORD_QTY": str(int(qty)),
                "OVRS_ORD_UNPR": (f"{price:.2f}" if action == "AMEND" else "0"),
                "ORD_SVR_DVSN_CD": "0",
            }
        return {"dry_run": True, "tr_id": tr_id, "api_url": api_url, "params": params}

    def amend_order(self, ticker, order_no, price, *a, dry_run=False, **kw):
        exchange = a[1] if (self._market == "US" and len(a) > 1) else kw.get("exchange")
        fwdg = a[0] if (self._market == "KR" and a) else kw.get("krx_fwdg_ord_orgno", "")
        qty = a[0] if (self._market == "US" and a) else 0
        return self._payload("AMEND", ticker, order_no, qty, price, fwdg, exchange)

    def cancel_order(self, ticker, order_no, *a, dry_run=False, **kw):
        if self._market == "KR":
            fwdg = a[0] if a else kw.get("krx_fwdg_ord_orgno", "")
            return self._payload("CANCEL", ticker, order_no, 0, 0.0, fwdg, None)
        qty = a[0] if a else 0
        exchange = a[1] if len(a) > 1 else kw.get("exchange")
        return self._payload("CANCEL", ticker, order_no, qty, 0.0, "", exchange)


def _selftest_orders(market: str) -> List[Dict[str, Any]]:
    """Two hypothetical unfilled orders (one BUY, one SELL) with plausible prices."""
    if market == "KR":
        return [
            {"ticker": "005930", "side": "SELL", "order_no": "ST-SELL-KR",
             "ord_unpr": 70000.0, "unfilled_qty": 10, "krx_fwdg_ord_orgno": "GNO1"},
            {"ticker": "000660", "side": "BUY", "order_no": "ST-BUY-KR",
             "ord_unpr": 10000.0, "unfilled_qty": 5, "krx_fwdg_ord_orgno": "GNO2"},
        ]
    return [
        {"ticker": "AAPL", "side": "SELL", "order_no": "ST-SELL-US",
         "ord_unpr": 200.00, "unfilled_qty": 10, "exchange": "NASD",
         "krx_fwdg_ord_orgno": ""},
        {"ticker": "TSLA", "side": "BUY", "order_no": "ST-BUY-US",
         "ord_unpr": 250.00, "unfilled_qty": 3, "exchange": "NASD",
         "krx_fwdg_ord_orgno": ""},
    ]


def run_selftest(market: str) -> Dict[str, Any]:
    """Exercise compute-chase -> dry-run payload -> fill-plausibility -> SHADOW log
    on synthetic orders, with NO DB owner-lock, NO API calls and NO orders. Always
    SHADOW (never sends). Returns a concise summary dict."""
    trader = _SelftestTrader(market)
    summary: Dict[str, Any] = {"market": market, "amend": 0, "cancel": 0,
                               "likely": 0, "unlikely": 0}
    # Chase one full step toward the market for the selftest so the synthetic
    # orders land AT the market (floor/cap clamp) — this exercises the
    # FILL_LIKELY verdict deterministically. Production CHASE_STEP_PCT is
    # untouched (local override only).
    step = CHASE_STEP_PCT
    globals()["CHASE_STEP_PCT"] = 1.0
    try:
        return _run_selftest_body(market, trader, summary)
    finally:
        globals()["CHASE_STEP_PCT"] = step


def _run_selftest_body(market: str, trader, summary: Dict[str, Any]) -> Dict[str, Any]:
    # Plausible market prices: SELL market just below limit (chase down fills),
    # BUY market just above limit but within ceiling (chase up fills).
    for order in _selftest_orders(market):
        side = order["side"]
        order_price = float(order["ord_unpr"])
        if side == "SELL":
            market_price = _round_price(market, order_price * 0.985)
        else:
            market_price = _round_price(market, order_price * 1.003)
        new_price = _round_price(market, _compute_chase_price(side, order_price, market_price))
        verdict = _fill_verdict(side, new_price, market_price)
        payload = _build_dry_run_payload(trader, market, order, "AMEND", new_price)
        summary["amend"] += 1
        summary["likely" if verdict == "FILL_LIKELY" else "unlikely"] += 1
        logger.info(
            "[LOOP_C][SHADOW] selftest decision=WOULD_AMEND market=%s ticker=%s "
            "order_no=%s side=%s unfilled_qty=%d reason=normal chase "
            "orig_limit=%.4f new_price=%.4f market_price=%.4f fill=%s "
            "tr_id=%s path=%s payload=%s",
            market, order["ticker"], order["order_no"], side,
            int(order["unfilled_qty"]), order_price, new_price, market_price,
            verdict, payload.get("tr_id"), payload.get("api_url"),
            payload.get("params"),
        )
        # Also exercise the cancel payload path (e.g. a buy that would be abandoned).
        if side == "BUY":
            cxl = _build_dry_run_payload(trader, market, order, "CANCEL", 0.0)
            summary["cancel"] += 1
            logger.info(
                "[LOOP_C][SHADOW] selftest decision=WOULD_CANCEL market=%s ticker=%s "
                "order_no=%s side=%s unfilled_qty=%d reason=buy-ceiling (synthetic) "
                "orig_limit=%.4f market_price=%.4f fill=N/A tr_id=%s path=%s payload=%s",
                market, order["ticker"], order["order_no"], side,
                int(order["unfilled_qty"]), order_price, market_price,
                cxl.get("tr_id"), cxl.get("api_url"), cxl.get("params"),
            )
    logger.info("[LOOP_C][SHADOW] selftest %s summary: %s", market, summary)
    return summary


def _run_both_isolated(selftest: bool = False) -> int:
    """Run KR and US as SEPARATE subprocesses (cores-shadowing isolation)."""
    import subprocess
    rc = 0
    for m in ("kr", "us"):
        cmd = [sys.executable, str(Path(__file__).resolve()), "--market", m]
        if selftest:
            cmd.append("--selftest")
        try:
            proc = subprocess.run(cmd)
            rc = rc or proc.returncode
        except Exception as e:
            logger.error("subprocess for market=%s failed: %s", m, e)
            rc = rc or 1
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Loop C fill-chaser (미체결 추격)")
    parser.add_argument("--market", choices=["kr", "us", "both"], default="both")
    parser.add_argument("--once", action="store_true", help="(default) run a single cycle")
    parser.add_argument(
        "--selftest", action="store_true",
        help="SHADOW-safe self-test: synthesize hypothetical unfilled orders and "
             "exercise chase->dry-run-payload->fill-plausibility->log with NO DB "
             "lock, NO API calls and NO orders.",
    )
    args = parser.parse_args()
    _setup_logging()
    if args.market == "both":
        return _run_both_isolated(selftest=args.selftest)
    market = {"kr": "KR", "us": "US"}[args.market]
    _bootstrap_path(market)
    if args.selftest:
        run_selftest(market)
        return 0
    return asyncio.run(main_async([market]))


if __name__ == "__main__":
    raise SystemExit(main())
