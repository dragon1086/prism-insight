#!/usr/bin/env python3
"""Loop A — high-frequency catastrophic hard-stop loop (LLM-free).

Runs as a standalone intraday cron, SEPARATE from the 2-3x/day batch sell cycle.
For each real holding it fetches the live price and applies ONLY the O'Neil
TIER1 hard stop (scenario stop-loss / absolute -7%). On a trigger it sells at
market, so a stop that the slow batch cadence would only catch at -12~-15% is
hit much closer to its intended level.

SAFETY (read before enabling):
  - Live selling is gated behind  LOOP_A_LIVE=true .  Default = SHADOW mode:
    it logs exactly what it WOULD sell and places NO orders. Turn it on only
    after reviewing the slippage backtest.
  - LOOP_A_ENABLED=false  disables the loop entirely (kill switch).
  - Loop A runs in its own process, so the batch's in-process asyncio locks do
    NOT apply. We guard against double-selling with (a) a SQLite owner_lock
    claimed via BEGIN IMMEDIATE, (b) an inflight-order uniqueness guard, and
    (c) a fresh KIS holding-quantity reconcile immediately before every live
    sell (KIS is the single source of truth; if the batch already sold, qty is
    0 and we skip).

Usage:
    python tools/loop_a_hardstop.py [--market kr|us|both] [--once]

Intended cron (SHADOW until reviewed) — KR and US as SEPARATE processes
(cores-shadowing isolation; --market both fans out to these two automatically):
    */7 9-15 * * 1-5  cd /root/prism-insight && python tools/loop_a_hardstop.py --market kr
    */7 22-23,0-5 * * 1-5  cd /root/prism-insight && python tools/loop_a_hardstop.py --market us
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
from typing import Any, Dict, List, Optional, Tuple

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent

# ── Market-aware path bootstrap (cores-shadowing safety) ──────────────────────
# The `cores` package is imported once per process and cached, and the US runtime
# resolves `from cores.X` to prism-us/cores while KR resolves to the root cores.
# A single process therefore CANNOT serve both markets without cross-wiring KR/US
# modules. Each market runs in its own process (see main(): `both` spawns two
# subprocesses), and we set sys.path so the active market's modules win.
def _bootstrap_path(market: str) -> None:
    root = str(PROJECT_ROOT)
    us = str(PROJECT_ROOT / "prism-us")
    us_trading = str(PROJECT_ROOT / "prism-us" / "trading")
    if market == "US":
        # prism-us must be highest priority so `cores` == prism-us/cores and the
        # bare `import kis_auth` inside us_stock_trading resolves to prism-us/trading.
        for p in (root, us_trading, us):
            sys.path.insert(0, p)
    else:  # KR: root highest priority so `cores`/`trading` == repo root.
        for p in (us, root):
            sys.path.insert(0, p)


logger = logging.getLogger("loop_a")

# ── Configuration (env-driven) ────────────────────────────────────────────────
def _env_bool(name: str, default: bool) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


LOOP_A_ENABLED = _env_bool("LOOP_A_ENABLED", True)     # master kill switch
LOOP_A_LIVE = _env_bool("LOOP_A_LIVE", False)          # False => SHADOW (no real orders)
LOCK_TTL_SEC = int(os.getenv("LOOP_A_LOCK_TTL_SEC", "300"))
DB_PATH = os.getenv("LOOP_A_DB") or os.getenv("STOCK_TRACKING_DB") \
    or str(PROJECT_ROOT / "stock_tracking_db.sqlite")

_HOLDINGS_TABLE = {"KR": "stock_holdings", "US": "us_stock_holdings"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── SQLite state (loop_a_* tables only; never touches existing tables) ─────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS loop_a_position_state (
            ticker          TEXT NOT NULL,
            market          TEXT NOT NULL,
            state           TEXT NOT NULL DEFAULT 'HOLDING',  -- HOLDING/SELLING/SOLD
            owner_lock      TEXT,
            lock_expires_at TEXT,
            last_eval_ts    TEXT,
            PRIMARY KEY (ticker, market)
        );
        CREATE TABLE IF NOT EXISTS loop_a_inflight_orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT NOT NULL,
            market       TEXT NOT NULL,
            side         TEXT NOT NULL DEFAULT 'SELL',
            loop_run_id  TEXT NOT NULL,
            order_no     TEXT,
            qty          INTEGER,
            status       TEXT NOT NULL,    -- SHADOW/OPEN/FILLED/REJECTED
            reason       TEXT,
            submitted_ts TEXT NOT NULL,
            UNIQUE (ticker, market, side, loop_run_id)
        );
        """
    )
    conn.commit()


def load_stop_map(conn: sqlite3.Connection, market: str) -> Dict[str, float]:
    """ticker -> scenario stop_loss from the holdings table (any account)."""
    table = _HOLDINGS_TABLE[market]
    out: Dict[str, float] = {}
    try:
        for row in conn.execute(
            f"SELECT ticker, MAX(COALESCE(stop_loss, 0)) AS sl FROM {table} GROUP BY ticker"
        ):
            try:
                out[str(row["ticker"]).strip()] = float(row["sl"] or 0)
            except (TypeError, ValueError):
                continue
    except sqlite3.Error as e:
        logger.warning("stop_loss map load failed (%s): %s", market, e)
    return out


def has_open_inflight(conn: sqlite3.Connection, ticker: str, market: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM loop_a_inflight_orders "
        "WHERE ticker=? AND market=? AND side='SELL' AND status IN ('OPEN','SHADOW') LIMIT 1",
        (ticker, market),
    ).fetchone()
    return row is not None


def claim_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str) -> bool:
    """Atomically claim the position owner_lock. Returns True if acquired.

    BEGIN IMMEDIATE serialises competing writers (other loops/processes). A lock
    older than LOCK_TTL_SEC is treated as stale and may be re-claimed.
    """
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


def release_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str,
                 new_state: Optional[str] = None) -> None:
    try:
        if new_state:
            conn.execute(
                "UPDATE loop_a_position_state SET owner_lock=NULL, lock_expires_at=NULL, state=? "
                "WHERE ticker=? AND market=? AND owner_lock=?",
                (new_state, ticker, market, run_id),
            )
        else:
            conn.execute(
                "UPDATE loop_a_position_state SET owner_lock=NULL, lock_expires_at=NULL "
                "WHERE ticker=? AND market=? AND owner_lock=?",
                (ticker, market, run_id),
            )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("lock release failed %s/%s: %s", ticker, market, e)


def record_inflight(conn: sqlite3.Connection, ticker: str, market: str, run_id: str,
                    qty: int, status: str, reason: str, order_no: Optional[str]) -> None:
    try:
        conn.execute(
            "INSERT OR IGNORE INTO loop_a_inflight_orders "
            "(ticker, market, side, loop_run_id, order_no, qty, status, reason, submitted_ts) "
            "VALUES (?,?, 'SELL', ?,?,?,?,?,?)",
            (ticker, market, run_id, order_no, qty, status, reason, _iso(_now())),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("inflight record failed %s/%s: %s", ticker, market, e)


# ── Trader context factories (KR / US) ────────────────────────────────────────
def _open_context(market: str):
    if market == "KR":
        from trading.domestic_stock_trading import AsyncTradingContext
        return AsyncTradingContext()
    from us_stock_trading import AsyncUSTradingContext
    return AsyncUSTradingContext()


def _ticker_of(holding: Dict[str, Any], market: str) -> str:
    return str(holding.get("ticker") or holding.get("stock_code") or "").strip()


# ── Core evaluation for one market ─────────────────────────────────────────────
async def run_market(market: str, run_id: str) -> Dict[str, Any]:
    """Evaluate TIER1 hard stop for every real holding in one market.

    Never raises: any failure degrades to a no-op for that ticker/market.
    """
    summary = {"market": market, "checked": 0, "triggered": 0, "sold": 0, "shadow": 0, "skipped": 0}
    # Lazy import after path bootstrap so the correct (KR vs US) cores wins.
    from cores.oneil_fallback import SellInputs, evaluate_tier1_hardstop
    conn = _connect()
    try:
        _ensure_schema(conn)
        stop_map = load_stop_map(conn, market)
        try:
            async with _open_context(market) as trader:
                try:
                    portfolio: List[Dict[str, Any]] = await asyncio.to_thread(trader.get_portfolio)
                except Exception as e:
                    logger.warning("%s get_portfolio failed: %s", market, e)
                    return summary
                for h in (portfolio or []):
                    ticker = _ticker_of(h, market)
                    if not ticker:
                        continue
                    try:
                        qty = int(h.get("quantity", 0) or 0)
                        avg_price = float(h.get("avg_price", 0) or 0)
                        cur_price = float(h.get("current_price", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if qty <= 0 or avg_price <= 0 or cur_price <= 0:
                        continue
                    summary["checked"] += 1
                    inp = SellInputs(
                        buy_price=avg_price,
                        current_price=cur_price,
                        stop_loss=stop_map.get(ticker, 0.0),
                    )
                    should_sell, reason = evaluate_tier1_hardstop(inp)
                    if not should_sell:
                        continue
                    summary["triggered"] += 1
                    await _act_on_trigger(conn, trader, market, ticker, qty, reason, run_id, summary)
        except Exception as e:  # context/credential failure -> skip whole market safely
            logger.warning("%s trading context failed: %s", market, e)
    finally:
        conn.close()
    return summary


async def _act_on_trigger(conn, trader, market: str, ticker: str, qty: int,
                          reason: str, run_id: str, summary: Dict[str, Any]) -> None:
    # Guard 1: an inflight SELL for this ticker already exists -> leave it alone.
    if has_open_inflight(conn, ticker, market):
        summary["skipped"] += 1
        logger.info("[%s] %s trigger but inflight order exists -> skip (%s)", market, ticker, reason)
        return
    # Guard 2: claim the owner_lock (serialises against other loop processes).
    if not claim_lock(conn, ticker, market, run_id):
        summary["skipped"] += 1
        logger.info("[%s] %s trigger but owner_lock held -> skip (%s)", market, ticker, reason)
        return
    try:
        if not LOOP_A_LIVE:
            # SHADOW: log intended sell, place no order.
            summary["shadow"] += 1
            logger.info("[SHADOW][%s] WOULD SELL %s qty=%d reason=%s", market, ticker, qty, reason)
            record_inflight(conn, ticker, market, run_id, qty, "SHADOW", reason, None)
            release_lock(conn, ticker, market, run_id, new_state="HOLDING")
            return
        # LIVE: reconcile against KIS (single source of truth) right before selling.
        try:
            live_qty = await asyncio.to_thread(trader.get_holding_quantity, ticker)
        except Exception as e:
            logger.warning("[%s] %s holding reconcile failed, aborting sell: %s", market, ticker, e)
            release_lock(conn, ticker, market, run_id, new_state="HOLDING")
            return
        sell_qty = min(qty, int(live_qty or 0))
        if sell_qty <= 0:
            logger.info("[%s] %s already flat at KIS (qty=0) -> skip", market, ticker)
            release_lock(conn, ticker, market, run_id, new_state="SOLD")
            return
        logger.warning("[LIVE][%s] SELLING %s qty=%d reason=%s", market, ticker, sell_qty, reason)
        result = await trader.async_sell_stock(ticker, quantity=sell_qty)  # market order
        ok = bool(result and result.get("success"))
        order_no = (result or {}).get("order_no")
        record_inflight(conn, ticker, market, run_id, sell_qty,
                        "FILLED" if ok else "REJECTED", reason, str(order_no) if order_no else None)
        release_lock(conn, ticker, market, run_id, new_state="SOLD" if ok else "HOLDING")
        summary["sold"] += 1 if ok else 0
        logger.warning("[LIVE][%s] %s sell result success=%s order_no=%s msg=%s",
                       market, ticker, ok, order_no, (result or {}).get("message"))
    except Exception as e:
        logger.error("[%s] %s sell action failed: %s", market, ticker, e)
        release_lock(conn, ticker, market, run_id, new_state="HOLDING")


async def main_async(markets: List[str]) -> int:
    if not LOOP_A_ENABLED:
        logger.info("LOOP_A_ENABLED=false -> loop disabled, exiting.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    mode = "LIVE" if LOOP_A_LIVE else "SHADOW"
    logger.info("Loop A start run_id=%s mode=%s markets=%s db=%s", run_id, mode, markets, DB_PATH)
    totals = {"checked": 0, "triggered": 0, "sold": 0, "shadow": 0, "skipped": 0}
    for market in markets:
        s = await run_market(market, run_id)
        for k in totals:
            totals[k] += s.get(k, 0)
        logger.info("Loop A %s summary: %s", market, s)
    logger.info("Loop A done run_id=%s mode=%s totals=%s", run_id, mode, totals)
    return 0


def _setup_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_dir / "loop_a_hardstop.log"))
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
    parser = argparse.ArgumentParser(description="Loop A high-frequency hard-stop loop")
    parser.add_argument("--market", choices=["kr", "us", "both"], default="both")
    parser.add_argument("--once", action="store_true", help="(default) run a single cycle")
    args = parser.parse_args()
    _setup_logging()
    if args.market == "both":
        # Cannot serve both markets in one process (cores package is cached) -> fan out.
        return _run_both_isolated()
    market = {"kr": "KR", "us": "US"}[args.market]
    _bootstrap_path(market)
    return asyncio.run(main_async([market]))


if __name__ == "__main__":
    raise SystemExit(main())
