# trade_history.py

"""
Past-trade lookup for the Phase 6 S6 insight image (DISPLAY-ONLY, additive).

Given a ticker + market, look up that ticker's PAST trade events from the
tracking DB (``stock_tracking_db.sqlite``) and expose them in two shapes:

1. :class:`TradeEvent` list — one event per buy and per sell, carrying a parsed
   date (``datetime``), a price (float, native currency), and a side
   (``"buy"`` / ``"sell"``). Used by the renderer to draw markers.
2. A CONCISE Korean text summary (``summarize_trades``) injected into the vision
   prompt so the analysis can reference prior trades.

Tables (per market, inspected from the live schema):
  KR  -> ``trading_history``  (closed: buy_price/buy_date/sell_price/sell_date,
                               profit_rate) + ``stock_holdings`` (open: buy_price
                               /buy_date).
  US  -> ``us_trading_history`` (same columns, USD) + ``us_stock_holdings``.

Design constraints (mirror the insight-image feature):
- NON-BLOCKING: every public entry point is wrapped so any failure (missing DB,
  missing table, bad row) returns an empty result and logs ``[INSIGHT_IMAGE]``.
  It must NEVER raise to the caller and NEVER break image generation.
- No trades -> empty list / ``None`` summary (caller no-ops).
- Read-only. Does not touch trading logic.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# Cap markers / context to the most recent N trade EVENTS (buy+sell counted
# separately). Keeps the chart readable and the prompt short.
MAX_EVENTS = 10


@dataclass(frozen=True)
class TradeEvent:
    """One past trade leg (a buy or a sell)."""

    date: datetime
    price: float
    side: str  # "buy" | "sell"
    profit_rate: float | None = None  # only meaningful on the matching sell


def _is_us(market: str | None) -> bool:
    return isinstance(market, str) and market.strip().lower() in (
        "us", "usa", "united states", "nasdaq", "nyse",
    )


def _db_path() -> str:
    """Resolve the tracking DB path (project root / stock_tracking_db.sqlite).

    This file lives at ``cores/llm/features/trade_history.py``; the project root
    is three directories up. Falls back to the CWD-relative name (matching the
    tracking agents' default) if that path does not exist.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    candidate = os.path.join(root, "stock_tracking_db.sqlite")
    if os.path.exists(candidate):
        return candidate
    return "stock_tracking_db.sqlite"


def _parse_dt(value) -> datetime | None:
    """Parse a DB date/datetime string ('YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS')."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt)
        except ValueError:
            continue
    # Last resort: take the leading date token.
    try:
        return datetime.strptime(s.split()[0], "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def get_trade_events(
    ticker: str,
    *,
    market: str | None = None,
    db_path: str | None = None,
    max_events: int = MAX_EVENTS,
) -> list[TradeEvent]:
    """Return past buy/sell events for *ticker*, newest-first, capped.

    Pulls CLOSED round-trips from the history table (each yields a buy + a sell
    event) and the CURRENTLY-OPEN position from the holdings table (a buy event
    only). Returns ``[]`` on any error or when there are no trades. Never raises.
    """
    try:
        hist_table = "us_trading_history" if _is_us(market) else "trading_history"
        hold_table = "us_stock_holdings" if _is_us(market) else "stock_holdings"
        path = db_path or _db_path()

        events: list[TradeEvent] = []
        conn = sqlite3.connect(path)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Closed round-trips: one buy event + one sell event each.
            try:
                cur.execute(
                    f"SELECT buy_price, buy_date, sell_price, sell_date, "
                    f"profit_rate FROM {hist_table} WHERE ticker = ? "
                    f"ORDER BY sell_date DESC",
                    (ticker,),
                )
                for row in cur.fetchall():
                    bdt = _parse_dt(row["buy_date"])
                    sdt = _parse_dt(row["sell_date"])
                    pr = row["profit_rate"]
                    if bdt is not None and row["buy_price"] is not None:
                        events.append(
                            TradeEvent(bdt, float(row["buy_price"]), "buy")
                        )
                    if sdt is not None and row["sell_price"] is not None:
                        events.append(
                            TradeEvent(
                                sdt, float(row["sell_price"]), "sell",
                                profit_rate=float(pr) if pr is not None else None,
                            )
                        )
            except sqlite3.Error as exc:
                logger.info("[INSIGHT_IMAGE] history table read skipped: %s", exc)

            # Currently-open position (buy leg only, no sell yet).
            try:
                cur.execute(
                    f"SELECT buy_price, buy_date FROM {hold_table} "
                    f"WHERE ticker = ?",
                    (ticker,),
                )
                for row in cur.fetchall():
                    bdt = _parse_dt(row["buy_date"])
                    if bdt is not None and row["buy_price"] is not None:
                        events.append(
                            TradeEvent(bdt, float(row["buy_price"]), "buy")
                        )
            except sqlite3.Error as exc:
                logger.info("[INSIGHT_IMAGE] holdings table read skipped: %s", exc)
        finally:
            conn.close()

        if not events:
            return []

        # Dedupe same (date-day, side, price) events; keep newest first; cap.
        seen: set[tuple] = set()
        deduped: list[TradeEvent] = []
        for ev in sorted(events, key=lambda e: e.date, reverse=True):
            key = (ev.date.date(), ev.side, round(ev.price, 4))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ev)
        return deduped[: max(1, max_events)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] get_trade_events failed for %s: %s",
                       ticker, exc)
        return []


def summarize_trades(
    events: list[TradeEvent],
    *,
    currency_symbol: str = "₩",
    price_decimals: int = 0,
    max_lines: int = 6,
) -> str | None:
    """Build a CONCISE Korean text summary of past trades for the LLM prompt.

    Example line: ``매수 2025-10-01 @86,000, 매도 2025-10-14 @95,300 (수익률 +10.8%)``.
    Returns ``None`` when there are no events. Plain text only (no ``$`` escaping
    needed — this goes into a TEXT prompt, not matplotlib). Never raises.
    """
    try:
        if not events:
            return None

        def _p(value: float) -> str:
            return f"{currency_symbol}{value:,.{price_decimals}f}"

        # Pair newest-first sells with the nearest preceding buy for readable
        # round-trip lines; surface lone buys (open position) on their own.
        ordered = sorted(events, key=lambda e: e.date)
        lines: list[str] = []
        pending_buys: list[TradeEvent] = []
        for ev in ordered:
            if ev.side == "buy":
                pending_buys.append(ev)
            else:  # sell — match the earliest pending buy
                buy = pending_buys.pop(0) if pending_buys else None
                if buy is not None:
                    pr = (
                        f" (수익률 {ev.profit_rate:+.1f}%)"
                        if ev.profit_rate is not None
                        else ""
                    )
                    lines.append(
                        f"매수 {buy.date:%Y-%m-%d} @{_p(buy.price)}, "
                        f"매도 {ev.date:%Y-%m-%d} @{_p(ev.price)}{pr}"
                    )
                else:
                    lines.append(f"매도 {ev.date:%Y-%m-%d} @{_p(ev.price)}")
        for buy in pending_buys:  # still-open positions
            lines.append(f"매수(보유중) {buy.date:%Y-%m-%d} @{_p(buy.price)}")

        if not lines:
            return None
        # Most recent lines first, capped.
        lines = lines[::-1][: max(1, max_lines)]
        return "과거 매매 이력:\n- " + "\n- ".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[INSIGHT_IMAGE] summarize_trades failed: %s", exc)
        return None
