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
# Enforce the NEW exit-kind-driven block — a stop/trend-exit re-entry that is NOT a
# loss (tagged out at a marginal profit) — only when this is ALSO on. Default False
# = SHADOW for the new branch: it is logged (WOULD_BLOCK … risk_only=True) but not
# vetoed, while legacy loss-based blocks keep obeying COOLDOWN_LIVE unchanged. Lets
# the exit-kind churn guard be observed for a few sessions before enforcing.
COOLDOWN_RISK_EXIT_LIVE = str(os.getenv("REENTRY_COOLDOWN_RISK_EXIT_LIVE", "false")).strip().lower() in ("1", "true", "yes", "on")

_TABLE = {"KR": "trading_history", "US": "us_trading_history"}

# Exit kinds that are churn-risk regardless of realised P&L sign. A stop-loss or
# trend-exit close that happens to land at a marginal PROFIT (e.g. bought below
# the stop, tagged out at +0.4%) is still a risk exit — re-buying it immediately
# is the churn we want to block. These are matched against us_trading_history.exit_kind.
RISK_EXIT_KINDS = {"stop", "trend_exit"}


def classify_exit_kind(sell_reason: str, explicit: Optional[str] = None) -> str:
    """Normalise a sell into a compact exit_kind: stop | trend_exit | target | ai.

    Callers that know the kind deterministically (loop_a=stop, loop_b=trend_exit)
    pass `explicit`; otherwise it is inferred from the free-form sell_reason the
    orchestrator already produces (KR '손절 조건 도달…', US 'Stop-loss condition
    reached…', tier strings 'TIER1_STOPLOSS'/'TIER1.5_MA50', etc.). Best-effort,
    never raises; unknown => 'ai' (a deliberate exit, not treated as churn-risk).
    """
    if explicit:
        return explicit
    r = (sell_reason or "").lower()
    # Trend-exit tiers first so 'TIER1.5' isn't swallowed by the generic 'tier1' stop match.
    if ("tier1.5" in r or "ma50" in r or "ma_50" in r or "50-day" in r
            or "50일선" in r or "trend" in r or "추세" in r):
        return "trend_exit"
    if ("stop-loss" in r or "stop_loss" in r or "stoploss" in r or "tier1" in r
            or "abs7" in r or "-7%" in r or "hard stop" in r or "손절" in r):
        return "stop"
    if "target" in r or "목표가" in r:
        return "target"
    return "ai"


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


def _query_last_sell(path: str, table: str, ticker: str,
                     account_key: Optional[str]) -> Optional[tuple]:
    """Most recent completed sell -> (sell_date, profit_rate, exit_kind) or None.

    exit_kind is None when the column does not exist yet (DB not migrated) so
    callers transparently fall back to the legacy P&L-sign behaviour. Fail-open.
    """
    where = "WHERE ticker=? AND sell_date IS NOT NULL AND sell_date<>'' "
    params = [ticker]
    if account_key:
        where += "AND account_key=? "
        params.append(account_key)
    tail = "ORDER BY sell_date DESC LIMIT 1"
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            try:
                row = conn.execute(
                    f"SELECT sell_date, profit_rate, exit_kind FROM {table} {where}{tail}",
                    params,
                ).fetchone()
                return (row[0], row[1], row[2]) if row else None
            except sqlite3.OperationalError:
                # exit_kind column absent (pre-migration) -> legacy fallback.
                row = conn.execute(
                    f"SELECT sell_date, profit_rate FROM {table} {where}{tail}",
                    params,
                ).fetchone()
                return (row[0], row[1], None) if row else None
        finally:
            conn.close()
    except Exception:
        return None  # fail-open


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
    row = _query_last_sell(path, table, ticker, account_key)
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
    exit_kind = (row[2] or "") if len(row) > 2 else ""
    # A stop/trend-exit is churn-risk even at a marginal PROFIT, so it gets the
    # (longer) LOSS window regardless of P&L sign. NULL exit_kind (pre-migration
    # rows) => risk_exit False => legacy sign-based behaviour (backward compatible).
    risk_exit = exit_kind in RISK_EXIT_KINDS
    after_loss = last_ret < 0
    window = COOLDOWN_LOSS_HOURS if (after_loss or risk_exit) else COOLDOWN_HOURS
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
        "exit_kind": exit_kind or None,
        "risk_exit": risk_exit,
    }


def recent_loss(ticker: str, market: str | None = None) -> Optional[dict]:
    """Return the most-recent sell's gap info if that sell was a loss, else None.

    Used by the journal score adjustment to penalise fresh stop-outs regardless
    of whether the (longer) COOLDOWN_LOSS_HOURS window has elapsed.

    Returns: {"gap_hours": float, "last_ret": float, "last_sell": str} or None.
    Fail-open: returns None on any error.
    """
    return recent_risk_exit(ticker, market, _loss_only=True)


def recent_risk_exit(ticker: str, market: str | None = None,
                     _loss_only: bool = False) -> Optional[dict]:
    """Return the most-recent sell's gap info if it was a churn-risk exit, else None.

    A "churn-risk exit" = the sell was a LOSS **or** its exit_kind is a stop/
    trend-exit (a risk close that may have landed at a marginal profit). NULL
    exit_kind (pre-migration rows) falls back to loss-only. `_loss_only=True`
    reproduces the legacy `recent_loss` semantics (loss sign only).

    Returns: {"gap_hours", "last_ret", "last_sell", "exit_kind"} or None. Fail-open.
    """
    table = _TABLE.get(((market or "KR") or "KR").upper())
    if not table or not ticker:
        return None
    row = _query_last_sell(_db_path(), table, ticker, None)
    if not row or not row[0]:
        return None
    sell_dt = _parse_dt(row[0])
    if sell_dt is None:
        return None
    try:
        last_ret = float(row[1]) if row[1] is not None else 0.0
    except (TypeError, ValueError):
        last_ret = 0.0
    exit_kind = (row[2] or "") if len(row) > 2 else ""
    is_risk = (last_ret < 0) if _loss_only else (last_ret < 0 or exit_kind in RISK_EXIT_KINDS)
    if not is_risk:
        return None  # not a churn-risk exit — no penalty
    gap_hours = (datetime.now() - sell_dt).total_seconds() / 3600.0
    if gap_hours < 0:
        return None  # clock skew
    return {
        "gap_hours": round(gap_hours, 2),
        "last_ret": last_ret,
        "last_sell": row[0],
        "exit_kind": exit_kind or None,
    }
