#!/usr/bin/env python3
"""Loop A — high-frequency catastrophic hard-stop loop (LLM-free).

Runs as a standalone intraday cron, SEPARATE from the 2-3x/day batch sell cycle.
For each tracked holding it fetches the live price and applies ONLY the O'Neil
TIER1 hard stop (scenario stop-loss / absolute -7%). On a trigger it closes the
position the SAME way the batch does — so the simulator, the real KIS account
and the Telegram channel all stay consistent:

    1. agent.sell_stock(stock_data, reason)   # simulator close + journal + queue msg
    2. trading.async_sell_stock(ticker)       # real KIS market order
    3. agent.send_telegram_message(chat_id)   # flush the queued sell message

This is exactly the batch's sequence (stock_tracking_agent.py ~1380-1452),
reused rather than re-implemented, so there is one source of truth. Loop A only
adds the high-frequency TIER1 decision in front of it.

SAFETY (read before enabling):
  - Live selling is gated behind  LOOP_A_LIVE=true . Default = SHADOW: it logs
    what it WOULD sell, touches NO agent and places NO order. The heavy agent is
    only imported/instantiated on an actual LIVE sell.
  - LOOP_A_ENABLED=false disables the loop entirely (kill switch).
  - Separate process, so the batch's in-process asyncio locks do NOT apply:
    guards via a SQLite owner_lock (BEGIN IMMEDIATE), an inflight-order
    uniqueness guard, and a fresh KIS holding-qty reconcile before every sell.
  - Pyramided tickers (>1 holding row) are SKIPPED — the batch owns the
    fractional-sell logic; Loop A only handles clean single-row positions.

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


logger = logging.getLogger("loop_a")

# Load .env so env-driven config below (TELEGRAM_CHANNEL_ID, LOOP_A_*, journal flag)
# is visible — a fresh cron process does not inherit .env otherwise.
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ── Configuration (env-driven) ────────────────────────────────────────────────
# Canonical env prefix is HARDSTOP_ (this is the hard stop-loss loop). The legacy
# LOOP_A_ prefix is a DEPRECATED alias, still honored so existing prod .env /
# crontab survive the rename; main() warns once for any legacy key in use.
_DEPRECATED_ENV = []


def _env(suffix, default=None):
    """Read HARDSTOP_<suffix>, falling back to the deprecated LOOP_A_<suffix>."""
    val = os.getenv("HARDSTOP_" + suffix)
    if val is not None:
        return val
    legacy = os.getenv("LOOP_A_" + suffix)
    if legacy is not None:
        _DEPRECATED_ENV.append("LOOP_A_" + suffix)
        return legacy
    return default


def _env_flag(suffix, default):
    raw = _env(suffix)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


LOOP_A_ENABLED = _env_flag("ENABLED", True)            # master kill switch
LOOP_A_LIVE = _env_flag("LIVE", False)                 # False => SHADOW (no real orders)
LOCK_TTL_SEC = int(_env("LOCK_TTL_SEC", "300"))
# inflight SELL 레코드 TTL. 이보다 오래된 OPEN 레코드는 stale로 보고 중복방지 대상에서 제외한다.
# (loop_a 하드스탑은 장중 시장가라 정상 주문은 수 초 내 체결됨. fill-chaser가 SHADOW라
#  미정리된 레코드가 손절을 영구 차단하던 버그 방지.)
INFLIGHT_TTL_SEC = int(_env("INFLIGHT_TTL_SEC", "900"))  # 15분
DB_PATH = _env("DB") or os.getenv("STOCK_TRACKING_DB") \
    or str(PROJECT_ROOT / "stock_tracking_db.sqlite")
# Reuse the same channel the batch/system already broadcasts to (TELEGRAM_CHANNEL_ID).
# HARDSTOP_CHAT_ID is only an optional override.
CHAT_ID = _env("CHAT_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or None

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
    # 실제 미체결 주문(status='OPEN')만 중복방지 대상. SHADOW 레코드는 실주문이 아니므로
    # LIVE 매도를 막으면 안 된다(과거 SHADOW 레코드가 3주간 손절을 영구 차단하던 버그).
    # TTL 지난 stale OPEN 레코드도 차단에서 제외(fill-chaser 미정리로 영구 차단 방지).
    cutoff = _iso(_now() - timedelta(seconds=INFLIGHT_TTL_SEC))
    row = conn.execute(
        "SELECT 1 FROM loop_a_inflight_orders "
        "WHERE ticker=? AND market=? AND side='SELL' AND status='OPEN' "
        "AND submitted_ts >= ? LIMIT 1",
        (ticker, market, cutoff),
    ).fetchone()
    return row is not None


def claim_lock(conn: sqlite3.Connection, ticker: str, market: str, run_id: str) -> bool:
    """Atomically claim the position owner_lock. Returns True if acquired."""
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
    # 루프 매도 메시지도 배치와 동일하게 다국어 채널로 브로드캐스트되게 config 주입.
    # (미설정 시 send_telegram_message가 KR 채널로만 발송.) 채널ID는 TelegramConfig가
    # env TELEGRAM_CHANNEL_ID_{LANG}에서 자동 로드. send 시 await_broadcast=True로 완결.
    try:
        _langs = [x.strip() for x in os.getenv("LOOP_BROADCAST_LANGUAGES", "en,ja,zh,es").split(",") if x.strip()]
        if _langs:
            from telegram_config import TelegramConfig
            agent.telegram_config = TelegramConfig(use_telegram=True, broadcast_languages=_langs)
    except Exception as e:
        logger.warning("loop broadcast config init failed (non-critical): %s", e)
    return agent


# ── Core evaluation for one market ─────────────────────────────────────────────
async def run_market(market: str, run_id: str) -> Dict[str, Any]:
    """Evaluate the TIER1 hard stop for every clean single-row holding.

    Never raises: any failure degrades to a no-op for that ticker/market.
    """
    summary = {"market": market, "checked": 0, "triggered": 0, "sold": 0,
               "shadow": 0, "skipped": 0, "pyramided_skipped": 0}
    from cores.oneil_fallback import SellInputs, evaluate_tier1_hardstop
    conn = _connect()
    agent = {"ref": None}  # lazily created on first LIVE sell
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
                        stop_loss = float(h.get("stop_loss", 0) or 0)
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
                    should_sell, reason = evaluate_tier1_hardstop(
                        SellInputs(buy_price=buy_price, current_price=cur_price, stop_loss=stop_loss)
                    )
                    if not should_sell:
                        continue
                    summary["triggered"] += 1
                    h = dict(h)
                    h["current_price"] = cur_price
                    await _act_on_trigger(conn, market, ticker, h, reason, run_id, agent, summary)
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
                # send_telegram_message() itself appends the (de-duplicated)
                # portfolio summary, so do NOT generate+append it here as well —
                # doing both queued the portfolio twice (the double-send bug). Flush
                # the queue once; cross-run de-dup lives in portfolio_broadcast.
                await agent["ref"].send_telegram_message(CHAT_ID, await_broadcast=True)
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
                          reason: str, run_id: str, agent: Dict[str, Any],
                          summary: Dict[str, Any]) -> None:
    qty_hint = 0
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
            # SHADOW: log intended sell; touch no agent, place no order.
            summary["shadow"] += 1
            logger.info("[SHADOW][%s] WOULD SELL %s reason=%s (buy=%.4f cur=%.4f)",
                        market, ticker, reason, stock_data.get("buy_price", 0),
                        stock_data.get("current_price", 0))
            record_inflight(conn, ticker, market, run_id, 0, "SHADOW", reason, None)
            release_lock(conn, ticker, market, run_id, new_state="HOLDING")
            return

        # LIVE: 1) simulator close (+journal +telegram queue) via the SAME path as batch.
        if agent["ref"] is None:
            agent["ref"] = await _make_agent(market)
        ag = agent["ref"]
        logger.warning("[LIVE][%s] SELLING %s reason=%s", market, ticker, reason)
        # Loop A is the catastrophic hard-stop => always a 'stop' exit (recorded in
        # trading_history.exit_kind so the re-entry cooldown treats it as churn-risk
        # even if it tags out at a marginal profit).
        sim_ok = await ag.sell_stock(stock_data, reason, exit_kind="stop")
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
            await ag.send_telegram_message(CHAT_ID, await_broadcast=True)
        except Exception as e:
            logger.warning("[%s] %s telegram flush failed: %s", market, ticker, e)

        # 4) Broadcast the sell to subscribers (Redis/GCP). sim close is the source
        # of truth so we publish on sim_ok even if our own KIS leg failed; loop sells
        # were previously batch-only and never broadcast (subscribers diverged).
        try:
            from loop_publish import publish_loop_sell
            await publish_loop_sell(
                market=market, ticker=ticker,
                company_name=stock_data.get("company_name", ticker),
                price=float(stock_data.get("current_price", 0) or 0),
                buy_price=float(stock_data.get("buy_price", 0) or 0),
                sell_reason=reason,
                trade_result={"success": bool(ok or sold_qty == 0), "order_no": order_no},
            )
        except Exception as e:
            logger.warning("[%s] %s sell signal publish failed (non-critical): %s", market, ticker, e)

        record_inflight(conn, ticker, market, run_id, sold_qty,
                        "FILLED" if (ok or sold_qty == 0) else "REJECTED",
                        reason, str(order_no) if order_no else None)
        release_lock(conn, ticker, market, run_id, new_state="SOLD")
        summary["sold"] += 1
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
    totals: Dict[str, int] = {}
    for market in markets:
        s = await run_market(market, run_id)
        for k, v in s.items():
            if isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
        logger.info("Loop A %s summary: %s", market, s)
    logger.info("Loop A done run_id=%s mode=%s totals=%s", run_id, mode, totals)
    return 0


def _setup_logging() -> None:
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_dir / "loop_a_hardstop.log"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )
    if _DEPRECATED_ENV:
        logger.warning(
            "deprecated env keys in use (rename to HARDSTOP_*): %s",
            ", ".join(sorted(set(_DEPRECATED_ENV))),
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
        return _run_both_isolated()
    market = {"kr": "KR", "us": "US"}[args.market]
    _bootstrap_path(market)
    return asyncio.run(main_async([market]))


if __name__ == "__main__":
    raise SystemExit(main())
