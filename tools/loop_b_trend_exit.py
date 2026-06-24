#!/usr/bin/env python3
"""Loop B — closing-confirmation trend-exit loop (LLM-free).

Runs as a standalone intraday cron, SEPARATE from both the 2-3x/day batch sell
cycle and Loop A (the high-frequency catastrophic hard-stop). Where Loop A owns
the fast TIER1 hard stop, Loop B owns the *slower* O'Neil trend-exit tiers that
must NOT whipsaw on a single intraday dip:

    TIER1.5_MA50   — price below the 50-day MA while the position is losing
    TIER2_TRAIL    — trailing stop off the post-entry peak (regime-aware band)
    TIER3_TARGET   — target reached in a weak regime (profit-take)

TIER1 (pure catastrophic stop, scenario stop-loss / absolute -7%) is DELIBERATELY
ignored here — Loop A handles that at higher frequency. Loop B matches on the
`evaluate_oneil_sell` reason-string prefix and skips anything starting `TIER1:`
(i.e. `TIER1_STOPLOSS` / `TIER1_ABS7`).

THE CORE OF LOOP B — close-confirmation / consecutive-breach gate (anti-whipsaw):
  The 50-day MA is constant intraday, so one dip below it is noise, not a trend
  break. Loop B therefore does NOT act on a single breach. Instead, per ticker:
    - A Loop-B-owned sell signal increments a daily `breach_streak`
      (guarded to once per calendar day per ticker via `last_breach_date`).
    - No signal this cycle  ->  `breach_streak` resets to 0 (recovery).
    - We ACT (run the real sell sequence) only when:
        breach_streak >= LOOP_B_CONFIRM_CHECKS            (N consecutive days)
      OR
        LOOP_B_CLOSE_WINDOW=true AND a signal exists now  (session-close confirm)
  i.e. "N consecutive checkpoint breaches OR a session-close confirmation."

On an actual ACT it closes the position the SAME way the batch and Loop A do, so
the simulator, the real KIS account and the Telegram channel all stay consistent:

    1. agent.sell_stock(stock_data, reason)   # simulator close + journal + queue msg
    2. trading.async_sell_stock(ticker)       # real KIS market order
    3. agent.send_telegram_message(chat_id)   # flush the queued sell message

SAFETY (read before enabling):
  - Live selling is gated behind  LOOP_B_LIVE=true . Default = SHADOW: it logs
    what it WOULD sell, touches NO agent and places NO order. The heavy agent is
    only imported/instantiated on an actual LIVE sell.
  - LOOP_B_ENABLED=false disables the loop entirely (kill switch).
  - Separate process, so the batch's in-process asyncio locks do NOT apply:
    guards via a SQLite owner_lock (BEGIN IMMEDIATE), an inflight-order
    uniqueness guard, and a fresh KIS holding-qty reconcile before every sell.
  - Pyramided tickers (>1 holding row) are SKIPPED — the batch owns the
    fractional-sell logic; Loop B only handles clean single-row positions.
  - Loop B owns ONLY loop_b_* tables. It never touches existing tables nor
    Loop A's loop_a_* tables.
  - ma_50 fetch failure / <50 closes -> ma_50=0.0, which makes TIER1.5 dormant
    (safe: only the trailing/target tiers can then fire). regime fetch failure
    -> regime_is_live=False, which makes trailing conservative (-10% band).

Usage:
    python tools/loop_b_trend_exit.py [--market kr|us|both] [--once]

Intended cron (SHADOW until reviewed) — KR and US as SEPARATE processes
(cores-shadowing isolation; --market both fans out to these two automatically).
Run a periodic checkpoint cadence PLUS a dedicated close-window line that sets
LOOP_B_CLOSE_WINDOW=true so a single session-close breach confirms immediately:
    # KR checkpoints (every 10 min during the session)
    */10 9-15 * * 1-5  cd /root/prism-insight && python tools/loop_b_trend_exit.py --market kr
    # KR close-window confirm (~15:10-15:20 KST)
    10-20/5 15 * * 1-5 cd /root/prism-insight && LOOP_B_CLOSE_WINDOW=true python tools/loop_b_trend_exit.py --market kr
    # US checkpoints (every 10 min during the session, ET via server tz)
    */10 22-23,0-4 * * 1-5  cd /root/prism-insight && python tools/loop_b_trend_exit.py --market us
    # US close-window confirm (~15:50-16:00 ET)
    50-59/3 4 * * 2-6  cd /root/prism-insight && LOOP_B_CLOSE_WINDOW=true python tools/loop_b_trend_exit.py --market us
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


logger = logging.getLogger("loop_b")

# Load .env so env-driven config below (TELEGRAM_CHANNEL_ID, LOOP_B_*, journal flag)
# is visible — a fresh cron process does not inherit .env otherwise.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ── Configuration (env-driven) ────────────────────────────────────────────────
def _env_bool(name: str, default: bool) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


LOOP_B_ENABLED = _env_bool("LOOP_B_ENABLED", True)      # master kill switch
LOOP_B_LIVE = _env_bool("LOOP_B_LIVE", False)           # False => SHADOW (no real orders)
LOOP_B_CONFIRM_CHECKS = int(os.getenv("LOOP_B_CONFIRM_CHECKS", "2"))   # N consecutive day-breaches to act
LOOP_B_CLOSE_WINDOW = _env_bool("LOOP_B_CLOSE_WINDOW", False)          # set true on the close-time cron line
LOCK_TTL_SEC = int(os.getenv("LOOP_B_LOCK_TTL_SEC", "300"))
DB_PATH = os.getenv("LOOP_B_DB") or os.getenv("STOCK_TRACKING_DB") \
    or str(PROJECT_ROOT / "stock_tracking_db.sqlite")
# Reuse the same channel the batch/system already broadcasts to (TELEGRAM_CHANNEL_ID).
# LOOP_B_CHAT_ID is only an optional override.
CHAT_ID = os.getenv("LOOP_B_CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or None

_HOLDINGS_TABLE = {"KR": "stock_holdings", "US": "us_stock_holdings"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _today() -> str:
    return _now().date().isoformat()


def _is_loop_b_signal(reason: str) -> bool:
    """True if `reason` is a Loop-B-owned sell tier (NOT the pure TIER1 hard stop).

    evaluate_oneil_sell returns reasons like:
      TIER1_STOPLOSS:..  TIER1_ABS7:..          -> Loop A owns these (skip)
      TIER1.5_MA50:..  TIER2_TRAIL:..  TIER3_TARGET:..  -> Loop B owns these
    We match on the prefix: anything starting "TIER1:" / "TIER1_" (but NOT
    "TIER1.5") is the catastrophic hard stop and is skipped here.
    """
    r = (reason or "").strip()
    if not r.startswith("TIER"):
        return False  # HOLD / invalid -> not a signal
    if r.startswith("TIER1.5"):
        return True
    if r.startswith("TIER1"):
        return False  # pure TIER1 hard stop -> Loop A's job
    return True       # TIER2_TRAIL / TIER3_TARGET


# ── SQLite state (loop_b_* tables only; never touches existing tables) ─────────
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS loop_b_position_state (
            ticker          TEXT NOT NULL,
            market          TEXT NOT NULL,
            state           TEXT NOT NULL DEFAULT 'HOLDING',  -- HOLDING/SELLING/SOLD
            owner_lock      TEXT,
            lock_expires_at TEXT,
            last_eval_ts    TEXT,
            breach_streak   INTEGER NOT NULL DEFAULT 0,
            last_breach_date TEXT,
            last_eval_date  TEXT,
            PRIMARY KEY (ticker, market)
        );
        CREATE TABLE IF NOT EXISTS loop_b_inflight_orders (
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


def load_holdings_by_ticker(conn: sqlite3.Connection, market: str) -> Dict[str, List[Dict[str, Any]]]:
    """All holding rows (full dicts) grouped by ticker. Empty on error."""
    table = _HOLDINGS_TABLE[market]
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for row in conn.execute(f"SELECT * FROM {table}"):
            d = dict(row)
            ticker = str(d.get("ticker") or "").strip()
            if ticker:
                out.setdefault(ticker, []).append(d)
    except sqlite3.Error as e:
        logger.warning("holdings load failed (%s): %s", market, e)
    return out


def has_open_inflight(conn: sqlite3.Connection, ticker: str, market: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM loop_b_inflight_orders "
        "WHERE ticker=? AND market=? AND side='SELL' AND status IN ('OPEN','SHADOW') LIMIT 1",
        (ticker, market),
    ).fetchone()
    return row is not None


def claim_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str) -> bool:
    """Atomically claim the position owner_lock. Returns True if acquired."""
    now = _now()
    expires = _iso(now + timedelta(seconds=LOCK_TTL_SEC))
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO loop_b_position_state (ticker, market, state) VALUES (?,?, 'HOLDING')",
            (ticker, market),
        )
        cur = conn.execute(
            "UPDATE loop_b_position_state SET owner_lock=?, lock_expires_at=?, last_eval_ts=? "
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
                "UPDATE loop_b_position_state SET owner_lock=NULL, lock_expires_at=NULL, state=? "
                "WHERE ticker=? AND market=? AND owner_lock=?",
                (new_state, ticker, market, run_id),
            )
        else:
            conn.execute(
                "UPDATE loop_b_position_state SET owner_lock=NULL, lock_expires_at=NULL "
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
            "INSERT OR IGNORE INTO loop_b_inflight_orders "
            "(ticker, market, side, loop_run_id, order_no, qty, status, reason, submitted_ts) "
            "VALUES (?,?, 'SELL', ?,?,?,?,?,?)",
            (ticker, market, run_id, order_no, qty, status, reason, _iso(_now())),
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.warning("inflight record failed %s/%s: %s", ticker, market, e)


def get_breach_streak(conn: sqlite3.Connection, ticker: str, market: str) -> int:
    row = conn.execute(
        "SELECT breach_streak FROM loop_b_position_state WHERE ticker=? AND market=?",
        (ticker, market),
    ).fetchone()
    return int(row["breach_streak"]) if row and row["breach_streak"] is not None else 0


def update_breach_streak(conn: sqlite3.Connection, ticker: str, market: str,
                         had_signal: bool) -> int:
    """Update and return the breach_streak for one ticker/market this cycle.

    On a Loop-B signal: increment, but at most once per calendar day (guarded by
    last_breach_date) so multiple intraday checkpoints in the same day count once.
    On NO signal: reset to 0 (recovery).
    """
    today = _today()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO loop_b_position_state (ticker, market, state) VALUES (?,?, 'HOLDING')",
            (ticker, market),
        )
        row = conn.execute(
            "SELECT breach_streak, last_breach_date FROM loop_b_position_state "
            "WHERE ticker=? AND market=?",
            (ticker, market),
        ).fetchone()
        streak = int(row["breach_streak"] or 0) if row else 0
        last_breach_date = (row["last_breach_date"] if row else None) or ""

        if not had_signal:
            streak = 0
        elif last_breach_date != today:
            streak += 1  # first breach of a new calendar day

        conn.execute(
            "UPDATE loop_b_position_state SET breach_streak=?, last_breach_date=?, last_eval_date=? "
            "WHERE ticker=? AND market=?",
            (streak, today if had_signal else last_breach_date, today, ticker, market),
        )
        conn.commit()
        return streak
    except sqlite3.Error as e:
        logger.warning("breach streak update failed %s/%s: %s", ticker, market, e)
        return 0


# ── Per-ticker 50-day MA + per-cycle LIVE regime (Loop B decision inputs) ──────
def _fetch_ma50(market: str, ticker: str) -> float:
    """50-day simple MA of daily CLOSES for one ticker. 0.0 on any failure /
    <50 closes (-> TIER1.5 stays dormant, which is the safe default).

    Network-bound; isolated in its own function so tests monkeypatch it cleanly.
    """
    try:
        closes: List[float] = []
        if market == "US":
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="4mo")
            series = hist["Close"].dropna()
            closes = [float(x) for x in series.tolist()]
        else:  # KR via pykrx (clean close series, pykrx-compatible)
            from pykrx import stock
            end = _now().date()
            start = end - timedelta(days=80)  # ~80 calendar days -> >=50 trading closes
            df = stock.get_market_ohlcv_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
            )
            if df is not None and not df.empty:
                col = "종가" if "종가" in df.columns else ("Close" if "Close" in df.columns else None)
                if col is not None:
                    closes = [float(x) for x in df[col].dropna().tolist()]
        if len(closes) < 50:
            return 0.0
        last50 = closes[-50:]
        return sum(last50) / 50.0
    except Exception as e:
        logger.warning("[%s] %s ma_50 fetch failed: %s", market, ticker, e)
        return 0.0


def _compute_live_regime(market: str) -> Optional[str]:
    """Compute the LIVE market regime ONCE per market per cycle, the same way the
    batch does. Returns a regime string (e.g. 'moderate_bull') or None on failure.

    None -> callers pass regime_is_live=False, making trailing conservative
    (-10% band). This is acceptable and documented; it never over-sells.
    """
    try:
        if market == "US":
            import yfinance as yf
            from cores.data_prefetch import _compute_us_regime
            sp = yf.Ticker("^GSPC").history(period="1y")
            computed = _compute_us_regime(sp)
        else:
            from pykrx import stock
            from cores.data_prefetch import _compute_kr_regime
            end = _now().date()
            start = end - timedelta(days=400)  # ~250 trading days for 120MA
            kospi = stock.get_index_ohlcv_by_date(
                start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001"
            )
            ohlcv = {idx.strftime("%Y-%m-%d"): row.to_dict() for idx, row in kospi.iterrows()}
            computed = _compute_kr_regime(ohlcv)
        regime = (computed or {}).get("market_regime")
        return str(regime) if regime else None
    except Exception as e:
        logger.warning("[%s] live regime compute failed: %s", market, e)
        return None


# ── Trader context + agent factories (KR / US) ────────────────────────────────
def _open_context(market: str, account_name: Optional[str] = None):
    if market == "KR":
        from trading.domestic_stock_trading import AsyncTradingContext
        return AsyncTradingContext(account_name=account_name)
    from us_stock_trading import AsyncUSTradingContext
    return AsyncUSTradingContext(account_name=account_name)


async def _make_agent(market: str):
    """Instantiate + lightweight-init the tracking agent (LLM agent skipped).

    Only called for a LIVE sell, so SHADOW runs never pull in the agent deps.
    """
    if market == "KR":
        from stock_tracking_agent import StockTrackingAgent
        agent = StockTrackingAgent(db_path=DB_PATH)
    else:
        from us_stock_tracking_agent import USStockTrackingAgent
        agent = USStockTrackingAgent(db_path=DB_PATH)
    await agent.initialize(skip_llm_agent=True)
    return agent


# ── Core evaluation for one market ─────────────────────────────────────────────
async def run_market(market: str, run_id: str) -> Dict[str, Any]:
    """Evaluate the O'Neil trend-exit tiers (TIER1.5/2/3) for every clean
    single-row holding, applying the close-confirmation / consecutive-breach gate.

    Never raises: any failure degrades to a no-op for that ticker/market.
    """
    summary = {"market": market, "checked": 0, "signaled": 0, "acted": 0,
               "sold": 0, "shadow": 0, "skipped": 0, "pyramided_skipped": 0,
               "gated": 0}
    from cores.oneil_fallback import SellInputs, evaluate_oneil_sell
    conn = _connect()
    agent = {"ref": None}  # lazily created on first LIVE sell
    ma50_cache: Dict[str, float] = {}  # one fetch per ticker per cycle
    regime: Dict[str, Any] = {"value": None, "computed": False}
    try:
        _ensure_schema(conn)
        by_ticker = load_holdings_by_ticker(conn, market)
        if not by_ticker:
            return summary
        try:
            async with _open_context(market) as trader:  # primary ctx, prices only (account-agnostic)
                for ticker, rows in by_ticker.items():
                    if len(rows) > 1:
                        # Pyramided position -> leave to the batch's fractional logic.
                        summary["pyramided_skipped"] += 1
                        logger.info("[%s] %s pyramided (%d rows) -> skip (batch handles)",
                                    market, ticker, len(rows))
                        continue
                    h = rows[0]
                    try:
                        buy_price = float(h.get("buy_price", 0) or 0)
                    except (TypeError, ValueError):
                        continue
                    if buy_price <= 0:
                        continue
                    try:
                        info = await asyncio.to_thread(trader.get_current_price, ticker)
                        cur_price = float((info or {}).get("current_price", 0) or 0)
                    except Exception as e:
                        logger.warning("[%s] %s price fetch failed: %s", market, ticker, e)
                        continue
                    if cur_price <= 0:
                        continue
                    summary["checked"] += 1

                    # Compute LIVE regime once per market per cycle (lazy, best-effort).
                    if not regime["computed"]:
                        regime["value"] = await asyncio.to_thread(_compute_live_regime, market)
                        regime["computed"] = True
                    # Per-ticker 50MA, cached for the cycle.
                    if ticker not in ma50_cache:
                        ma50_cache[ticker] = await asyncio.to_thread(_fetch_ma50, market, ticker)
                    ma_50 = ma50_cache[ticker]

                    highest = float(h.get("highest_price", 0) or 0)
                    inp = SellInputs(
                        buy_price=buy_price,
                        current_price=cur_price,
                        stop_loss=float(h.get("stop_loss", 0) or 0),
                        target_price=float(h.get("target_price", 0) or 0),
                        highest_price=highest,
                        market_condition=str(regime["value"] or ""),
                        regime_is_live=bool(regime["value"]),
                        ma_50=ma_50,
                    )
                    should_sell, reason = evaluate_oneil_sell(inp)
                    had_signal = bool(should_sell) and _is_loop_b_signal(reason)

                    # Update the daily breach streak (increment once/day or reset).
                    streak = update_breach_streak(conn, ticker, market, had_signal)
                    if not had_signal:
                        continue
                    summary["signaled"] += 1

                    # Close-confirmation / consecutive-breach gate.
                    gate_open = (streak >= LOOP_B_CONFIRM_CHECKS) or LOOP_B_CLOSE_WINDOW
                    if not gate_open:
                        summary["gated"] += 1
                        logger.info("[%s] %s signal (%s) streak=%d < %d, not close-window -> gated",
                                    market, ticker, reason, streak, LOOP_B_CONFIRM_CHECKS)
                        continue
                    summary["acted"] += 1
                    h = dict(h)
                    h["current_price"] = cur_price
                    await _act_on_trigger(conn, market, ticker, h, reason, streak,
                                          run_id, agent, summary)
        except Exception as e:  # context/credential failure -> skip whole market safely
            logger.warning("%s trading context failed: %s", market, e)
    finally:
        # Run-end: if anything sold this run, send ONE realtime portfolio summary
        # (matches the batch flow). Done at run-end — NOT inside the per-sell
        # action — so generate_report_summary's DB reads never corrupt the
        # per-sell cursor state (the bug behind the reverted #372). Sent exactly
        # once per run. Fully wrapped; never breaks the loop.
        if summary.get("sold", 0) > 0 and agent["ref"] is not None:
            try:
                _ag = agent["ref"]
                _msg = await _ag.generate_report_summary()
                if _msg:
                    _ag.message_queue.append(_msg)
                    await _ag.send_telegram_message(CHAT_ID)
            except Exception as _e:
                logger.warning("[%s] run-end portfolio summary failed: %s", market, _e)
        conn.close()
        if agent["ref"] is not None:
            try:
                if getattr(agent["ref"], "conn", None):
                    agent["ref"].conn.close()
            except Exception:
                pass
    return summary


async def _act_on_trigger(conn, market: str, ticker: str, stock_data: Dict[str, Any],
                          reason: str, streak: int, run_id: str, agent: Dict[str, Any],
                          summary: Dict[str, Any]) -> None:
    # Guard 1: an inflight SELL for this ticker already exists -> leave it alone.
    if has_open_inflight(conn, ticker, market):
        summary["skipped"] += 1
        logger.info("[%s] %s gate open but inflight order exists -> skip (%s)", market, ticker, reason)
        return
    # Guard 2: claim the owner_lock (serialises against other loop processes).
    if not claim_lock(conn, ticker, market, run_id):
        summary["skipped"] += 1
        logger.info("[%s] %s gate open but owner_lock held -> skip (%s)", market, ticker, reason)
        return
    try:
        if not LOOP_B_LIVE:
            # SHADOW: log intended sell; touch no agent, place no order.
            summary["shadow"] += 1
            logger.info("[SHADOW][%s] WOULD SELL %s streak=%d reason=%s (buy=%.4f cur=%.4f)",
                        market, ticker, streak, reason, stock_data.get("buy_price", 0),
                        stock_data.get("current_price", 0))
            record_inflight(conn, ticker, market, run_id, 0, "SHADOW", reason, None)
            release_lock(conn, ticker, market, run_id, new_state="HOLDING")
            return

        # LIVE: 1) simulator close (+journal +telegram queue) via the SAME path as batch.
        if agent["ref"] is None:
            agent["ref"] = await _make_agent(market)
        ag = agent["ref"]
        logger.warning("[LIVE][%s] SELLING %s streak=%d reason=%s", market, ticker, streak, reason)
        sim_ok = await ag.sell_stock(stock_data, reason)
        if not sim_ok:
            logger.error("[%s] %s sell_stock (sim) failed -> aborting, no KIS order", market, ticker)
            release_lock(conn, ticker, market, run_id, new_state="HOLDING")
            return

        # 2) real KIS market order on the holding's own account; reconcile qty first.
        order_no, ok, sold_qty = None, False, 0
        try:
            async with _open_context(market, account_name=stock_data.get("account_name")) as seller:
                live_qty = await asyncio.to_thread(seller.get_holding_quantity, ticker)
                sold_qty = int(live_qty or 0)
                if sold_qty <= 0:
                    logger.info("[%s] %s already flat at KIS (qty=0); sim closed", market, ticker)
                else:
                    result = await seller.async_sell_stock(ticker, quantity=sold_qty)
                    ok = bool(result and result.get("success"))
                    order_no = (result or {}).get("order_no")
                    logger.warning("[LIVE][%s] %s KIS sell success=%s order_no=%s msg=%s",
                                   market, ticker, ok, order_no, (result or {}).get("message"))
        except Exception as e:
            logger.error("[%s] %s KIS sell failed after sim close: %s", market, ticker, e)

        # 3) flush the queued telegram message (instant notification).
        try:
            await ag.send_telegram_message(CHAT_ID)
        except Exception as e:
            logger.warning("[%s] %s telegram flush failed: %s", market, ticker, e)

        record_inflight(conn, ticker, market, run_id, sold_qty,
                        "FILLED" if (ok or sold_qty == 0) else "REJECTED",
                        reason, str(order_no) if order_no else None)
        release_lock(conn, ticker, market, run_id, new_state="SOLD")
        summary["sold"] += 1
    except Exception as e:
        logger.error("[%s] %s sell action failed: %s", market, ticker, e)
        release_lock(conn, ticker, market, run_id, new_state="HOLDING")


async def main_async(markets: List[str]) -> int:
    if not LOOP_B_ENABLED:
        logger.info("LOOP_B_ENABLED=false -> loop disabled, exiting.")
        return 0
    run_id = uuid.uuid4().hex[:12]
    mode = "LIVE" if LOOP_B_LIVE else "SHADOW"
    logger.info("Loop B start run_id=%s mode=%s markets=%s confirm=%d close_window=%s db=%s",
                run_id, mode, markets, LOOP_B_CONFIRM_CHECKS, LOOP_B_CLOSE_WINDOW, DB_PATH)
    totals: Dict[str, int] = {}
    for market in markets:
        s = await run_market(market, run_id)
        for k, v in s.items():
            if isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
        logger.info("Loop B %s summary: %s", market, s)
    logger.info("Loop B done run_id=%s mode=%s totals=%s", run_id, mode, totals)
    return 0


def _setup_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_dir / "loop_b_trend_exit.log"))
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
    parser = argparse.ArgumentParser(description="Loop B closing-confirmation trend-exit loop")
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
