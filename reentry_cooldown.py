"""Re-entry cooldown gate (deterministic, SHADOW-first).

Diagnosis (2026-06-25, prod trading_history): churn is systemic — 31 KR intraday
round-trips averaging -5.6%, and same-ticker re-buys within ~1 day of a losing
sell (e.g. 000660 sold -10% then re-bought 0.78d later). MU (US) was representative.

This gate blocks re-buying a ticker too soon after selling it — longer after a
LOSS (revenge-trade prevention) than after a normal/win sell. It is a pure veto
on the *fresh entry* (pyramiding adds are exempt); it never changes sizing/stops.

Self-contained (stdlib + sqlite only, no project imports) so it is import-safe
under both the root and the prism-us cores-shadowed runtimes.

Fail-open: any error returns None (= allow the buy). A bug here must never block a
legitimate entry; at worst it falls back to the old (no-gate) behavior.

Default is SHADOW: `reentry_block()` returns the block verdict for logging, and
COOLDOWN_LIVE controls whether the caller actually skips the buy.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

ENABLED = str(os.getenv("REENTRY_COOLDOWN_ENABLED", "true")).strip().lower() in ("1", "true", "yes", "on")
# Enforce (skip the buy) when LIVE; otherwise SHADOW = log only, buy proceeds.
COOLDOWN_LIVE = str(os.getenv("REENTRY_COOLDOWN_LIVE", "false")).strip().lower() in ("1", "true", "yes", "on")
# Cooldown after a normal/winning sell (default 0 = OFF: re-entering a name you
# sold at a PROFIT is often legitimate momentum continuation, and prod history
# showed those re-buys happen 0.1-0.4h after a +25%/+10% win — not churn), and
# the (longer) cooldown after a LOSS (the revenge re-entry we actually want to
# block: prod showed -5%/-7% sells re-bought within ~24h).
COOLDOWN_HOURS = float(os.getenv("REENTRY_COOLDOWN_HOURS", "0"))
COOLDOWN_LOSS_HOURS = float(os.getenv("REENTRY_COOLDOWN_LOSS_HOURS", "24"))

_TABLE = {"KR": "trading_history", "US": "us_trading_history"}


def _db_path() -> str:
    return (
        os.getenv("REENTRY_COOLDOWN_DB")
        or os.getenv("STOCK_TRACKING_DB")
        or str(Path(__file__).resolve().parent / "stock_tracking_db.sqlite")
    )


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def reentry_block(market: str, ticker: str, account_key: Optional[str] = None,
                  db_path: Optional[str] = None, now: Optional[datetime] = None) -> Optional[dict]:
    """If `ticker` is still inside its re-entry cooldown (relative to its most
    recent completed sell), return a verdict dict; else None.

    Verdict: {action, market, ticker, last_sell, last_ret, gap_hours, window_hours,
              after_loss}. Fail-open: returns None on any error or when disabled.
    """
    if not ENABLED:
        return None
    table = _TABLE.get((market or "").upper())
    if not table or not ticker:
        return None
    now = now or datetime.now()
    path = db_path or _db_path()
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            sql = (
                f"SELECT sell_date, profit_rate FROM {table} "
                f"WHERE ticker=? AND sell_date IS NOT NULL AND sell_date<>'' "
            )
            params = [ticker]
            if account_key:
                sql += "AND account_key=? "
                params.append(account_key)
            sql += "ORDER BY sell_date DESC LIMIT 1"
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
    except Exception:
        return None  # fail-open

    if not row or not row[0]:
        return None
    sell_dt = _parse_dt(row[0])
    if sell_dt is None:
        return None
    gap_hours = (now - sell_dt).total_seconds() / 3600.0
    if gap_hours < 0:
        return None  # clock skew -> don't block
    try:
        last_ret = float(row[1]) if row[1] is not None else 0.0
    except (TypeError, ValueError):
        last_ret = 0.0
    after_loss = last_ret < 0
    window = COOLDOWN_LOSS_HOURS if after_loss else COOLDOWN_HOURS
    if gap_hours >= window:
        return None
    return {
        "action": "WOULD_BLOCK",
        "market": (market or "").upper(),
        "ticker": ticker,
        "last_sell": row[0],
        "last_ret": last_ret,
        "gap_hours": round(gap_hours, 2),
        "window_hours": window,
        "after_loss": after_loss,
    }
