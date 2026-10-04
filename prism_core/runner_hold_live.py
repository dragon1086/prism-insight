"""Runner hold rule wiring for the KR/US trackers and the trend-exit loop (I/O side).

``prism_core.runner_hold`` holds the pure rule. This module fetches completed
daily bars, persists the runner block into the holding's scenario JSON (fresh read
under BEGIN IMMEDIATE, so a micro-split worker write is never clobbered), raises
the row's stop_loss to the initial entry (tools/hardstop_seller.py reads that column;
a higher AI trailing stop is reset to the entry because the runner rule replaces the
trailing stop), applies the deterministic sell guard and emits events.

Failure policy: the runner layer never blocks a sell path from running. A failed
bar fetch leaves the stored record in force (sells other than the hard stop,
corporate events and verified runner exits stay blocked for a stored runner).
Isolated strategy runtimes (``_no_order_effects``) are left untouched.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from prism_core import runner_hold as R

_LOG = logging.getLogger(__name__)
CTX_KEY = "_runner_ctx"
HISTORY_DAYS = 200  # calendar days: 50-day MA at a trigger up to ~80 sessions after the entry

_ROW_SQL = {
    "KR": "SELECT scenario, stop_loss FROM stock_holdings WHERE id=?",
    "US": "SELECT scenario, stop_loss FROM us_stock_holdings WHERE id=?",
}
_SCENARIO_SQL = {
    "KR": "UPDATE stock_holdings SET scenario=? WHERE id=?",
    "US": "UPDATE us_stock_holdings SET scenario=? WHERE id=?",
}
_STOP_SQL = {
    "KR": "UPDATE stock_holdings SET stop_loss=? WHERE id=?",
    "US": "UPDATE us_stock_holdings SET stop_loss=? WHERE id=?",
}


def enabled(market):
    return R.enabled(market)


def _now():
    return datetime.now(timezone.utc)


def _emit(event_type, *, market, ticker, row_id, attributes, severity="INFO"):
    from observability.events import emit_event
    emit_event(event_type, service=f"prism-{str(market).lower()}-runner-hold", market=str(market).upper(),
               ticker=ticker, position_id=f"legacy:{str(market).upper()}:{row_id}" if row_id is not None else None,
               attributes=attributes, severity=severity)


def fetch_daily_bars(market, ticker, now=None):
    """Raw (unadjusted where the provider allows) daily OHLCV [{date, open, high, low, close, volume}].

    Blocking network call; callers run it in a thread. KR uses the repository KIS
    market data, US uses yfinance with auto_adjust=False.
    """
    end = (now or _now()).date()
    start = end - timedelta(days=HISTORY_DAYS)
    if str(market).upper() == "US":
        import yfinance as yf
        frame = yf.Ticker(ticker).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                          auto_adjust=False)
    else:
        from cores import market_data
        frame = market_data.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker,
                                                     adjusted=False)
    if frame is None or frame.empty:
        return []
    columns = {str(c).lower(): c for c in frame.columns}
    close = columns.get("close") or columns.get("종가")
    volume = columns.get("volume") or columns.get("거래량")
    if close is None:
        return []
    return [{"date": index.date().isoformat(), "close": float(row[close]),
             "volume": float(row[volume]) if volume is not None else None}
            for index, row in frame.iterrows()]


def fetch_bars_safe(market, ticker):
    """fetch_daily_bars that logs and returns [] on any provider failure."""
    try:
        return fetch_daily_bars(market, ticker)
    except Exception as error:  # noqa: BLE001 - provider outage keeps the stored record in force
        _LOG.warning("[RUNNER_HOLD][%s] %s daily bars unavailable: %s", market, ticker, error)
        return []


def needs_bars(market, row, *, now=None, window="batch"):
    """Whether the row can be or is a protected runner, so its daily bars are worth fetching.

    batch: a stored runner, or no record yet and still inside the 40-session
    window (retroactive detection after a deploy). trend_exit (read-only): a stored
    protected runner, or no record and at most one session past the detection window.
    """
    now = now or _now()
    block = R.record(row.get("scenario"))
    today = R.local_today(market, now)
    if block is not None:
        return block.get("status") == R.RUNNER
    try:
        start = R.entry_session(market, row.get("buy_date"))
    except (TypeError, ValueError):
        return False
    horizon = R.HOLD_SESSIONS if window == "batch" else R.DETECT_SESSIONS + 1
    return today <= R.session_after(market, start, horizon)


def evaluate(market, row, bars, *, current_price, now=None):
    """Runner view of one holding row from its stored block or in-memory detection (no writes)."""
    now = now or _now()
    scenario = R.load_scenario(row.get("scenario"))
    stored = R.record(scenario)
    entry_ref = (stored or {}).get("entry_ref") or R.entry_reference(scenario, row.get("buy_price"))
    today = R.local_today(market, now).isoformat()
    done = R.completed_bars(bars, market=market, now=now)
    view = {"record": stored, "stored": stored is not None, "phase": None, "exit": None, "exit_facts": {},
            "today": today, "detected": None, "facts": {}}
    try:
        start = R.entry_session(market, row.get("buy_date"))
    except (TypeError, ValueError):
        return view
    block = stored
    if block is None and entry_ref:
        detection = R.detect(done, entry_ref=entry_ref, entry_session=start)
        if detection is not None:
            block = R.new_record(detection, market=market, entry_ref=entry_ref, entry_session=start,
                                 detected_at=now.isoformat())
            view["detected"] = detection
    view["record"] = block
    if block is None or block.get("status") != R.RUNNER:
        return view
    peak = R.peak_close(done, start)
    if peak is not None and peak > float(block.get("peak_close") or 0):
        block = dict(block, peak_close=peak)
        view["record"] = block
    view["phase"] = R.phase(block, today)
    view["exit"], view["exit_facts"] = R.runner_exit(done, block.get("entry_ref"), view["phase"])
    price = R._num(current_price)
    view["facts"] = {"current_price": price, "stop_loss": row.get("stop_loss"),
                     "gain_now_pct": round((price / float(block["entry_ref"]) - 1) * 100, 2) if price else None,
                     "sessions_since_entry": (R.sessions_since(done, start, done[-1]["date"]) if done
                                              and done[0]["date"] <= str(start) else None)}
    return view


def view_for_row(market, row, current_price, fetch, *, window="trend_exit", now=None):
    """evaluate() with bars from ``fetch(ticker)`` only when the row needs them (trend-exit, read-only)."""
    now = now or _now()
    if not needs_bars(market, row, now=now, window=window):
        return None
    return evaluate(market, row, fetch(row.get("ticker")), current_price=current_price, now=now)


def persist(conn, market, row_id, view, *, current_price, now=None):
    """Write the runner block (and the entry stop) to the row: fresh read under BEGIN IMMEDIATE.

    Returns {"detected": bool, "excluded": bool, "stop_set": (old, new)|None,
    "stop_loss": float|None, "scenario": text|None}. The stored block is the
    authority: a block already on the row is only updated (peak, stop), never replaced.
    """
    market = str(market).upper()
    now = now or _now()
    out = {"detected": False, "excluded": False, "stop_set": None, "stop_loss": None, "scenario": None}
    block = view.get("record")
    if row_id is None or block is None:
        return out
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(_ROW_SQL[market], (row_id,)).fetchone()
        if row is None:
            conn.rollback()
            return out
        scenario = R.load_scenario(row[0])
        current_stop = float(row[1] or 0)
        fresh = R.record(scenario)
        if fresh is None:
            updated = dict(block)
            out["detected"] = updated.get("status") == R.RUNNER
            out["excluded"] = updated.get("status") == R.EXCLUDED
        else:
            updated = dict(fresh)
            if updated.get("status") == R.RUNNER:
                if float(block.get("peak_close") or 0) > float(updated.get("peak_close") or 0):
                    updated["peak_close"] = block["peak_close"]
        target = None
        if updated.get("status") == R.RUNNER:
            target = R.stop_target(updated, current_stop, current_price)
        if target is not None:
            conn.execute(_STOP_SQL[market], (target, row_id))
            updated.update(stop_set_to=target, stop_set_from=current_stop, stop_set_at=now.isoformat())
            out["stop_set"], out["stop_loss"] = (current_stop, target), target
        if updated != fresh:
            scenario[R.SCENARIO_KEY] = updated
            out["scenario"] = json.dumps(scenario, ensure_ascii=False)
            conn.execute(_SCENARIO_SQL[market], (out["scenario"], row_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    view["record"] = updated
    return out


async def review_holding(agent, market, stock, *, logger=None, now=None):
    """Batch pre-hook before the sell decision; returns a forced runner-exit reason or None.

    Fetches bars when needed, persists detection/peak/end and the stop raise,
    updates ``stock`` in memory (scenario, stop_loss, CTX_KEY) and emits events.
    """
    log = logger or _LOG
    market = str(market).upper()
    if not R.enabled(market) or getattr(agent, "_no_order_effects", None) is not None:
        return None
    ticker, row_id = stock.get("ticker"), stock.get("id")
    try:
        now = now or _now()
        if not needs_bars(market, stock, now=now, window="batch"):
            return None
        bars = await asyncio.to_thread(fetch_bars_safe, market, ticker)
        view = evaluate(market, stock, bars, current_price=stock.get("current_price"), now=now)
        if view["record"] is None:
            return None
        written = persist(agent.conn, market, row_id, view, current_price=stock.get("current_price"), now=now)
    except Exception as error:  # noqa: BLE001 - the runner layer never blocks the sell review
        log.warning("[RUNNER_HOLD][%s] %s review skipped: %s", market, ticker, error)
        return None
    if written["scenario"] is not None:
        stock["scenario"] = written["scenario"]
    if written["stop_loss"] is not None:
        stock["stop_loss"] = written["stop_loss"]
        view["facts"]["stop_loss"] = written["stop_loss"]
    stock[CTX_KEY] = view
    block = view["record"]
    detail = {k: block.get(k) for k in ("status", "reason", "since", "session", "gain_pct", "entry_ref",
                                        "ma50_at_trigger", "ma50_extension", "hold_until")}
    detail["current_price"] = R._num(stock.get("current_price"))
    if written["excluded"]:
        log.warning("[RUNNER_HOLD][%s] %s spike excluded reason=%s session=%s gain=%s ext=%s", market, ticker,
                    block.get("reason"), block.get("session"), block.get("gain_pct"), block.get("ma50_extension"))
        _emit("runner.spike_excluded", market=market, ticker=ticker, row_id=row_id, attributes=detail)
    if written["detected"]:
        log.warning("[RUNNER_HOLD][%s] %s runner detected session=%s gain=%s hold_until=%s", market, ticker,
                    block.get("session"), block.get("gain_pct"), block.get("hold_until"))
        _emit("runner.detected", market=market, ticker=ticker, row_id=row_id, attributes=detail)
    if written["stop_set"] is not None:
        before, after = written["stop_set"]
        event = "runner.stop_raised" if after > before else "runner.stop_reset_to_entry"
        log.warning("[RUNNER_HOLD][%s] %s stop %s -> %s (initial entry; runner rule replaces trailing)", market,
                    ticker, before, after)
        _emit(event, market=market, ticker=ticker, row_id=row_id,
              attributes={"stop_loss": after, "previous_stop_loss": before, "entry_ref": block.get("entry_ref"),
                          "current_price": R._num(stock.get("current_price"))})
    if view["phase"] is not None and view["exit"]:
        language = "en" if market == "US" else "ko"
        reason = R.exit_reason(view, market, language)
        log.warning("[RUNNER_HOLD][%s] %s runner exit code=%s", market, ticker, view["exit"])
        _emit("runner.exit_forced", market=market, ticker=ticker, row_id=row_id,
              attributes={"code": view["exit"], "phase": view["phase"],
                          **{k: view["exit_facts"].get(k) for k in ("date", "close", "ma50", "ma20")},
                          **_price_facts(stock, block)})
        return reason
    return None


def _protected(stock):
    view = stock.get(CTX_KEY) if isinstance(stock, dict) else None
    if view is None:
        block = R.record((stock or {}).get("scenario"))
        if block is None or block.get("status") != R.RUNNER:
            return None
        # Stored runner without a fresh review (bars unavailable): still protected.
        view = {"record": block, "phase": R.HOLD, "exit": None}
    return view if view.get("phase") else None


def _price_facts(stock, block):
    """Price evidence at the moment of a runner decision, so a later close can judge it."""
    block = block or {}
    price, entry = R._num(stock.get("current_price")), R._num(block.get("entry_ref") or stock.get("buy_price"))
    return {"current_price": price, "entry_ref": entry, "buy_price": R._num(stock.get("buy_price")),
            "stop_loss": R._num(stock.get("stop_loss")), "peak_close": R._num(block.get("peak_close")),
            "hold_until": block.get("hold_until"),
            "gain_now_pct": round((price / entry - 1) * 100, 2) if price and entry else None}


def _block(market, stock, reason, code, source, log, view=None):
    ticker = stock.get("ticker")
    view = view or {}
    log.warning("[RUNNER_HOLD] blocked sell reason=%s source=%s market=%s ticker=%s price=%s detail=%s", code, source,
                market, ticker, stock.get("current_price"), " ".join(str(reason or "").split())[:200])
    _emit("runner.sell_blocked", market=market, ticker=ticker, row_id=stock.get("id"),
          attributes={"code": code, "source": source, "sell_reason": str(reason or "")[:300],
                      "phase": view.get("phase"), **_price_facts(stock, view.get("record"))})


def guard_result(agent, market, stock, should_sell, reason, *, logger=None):
    """Final deterministic guard at the tracker choke point: (should_sell, reason)."""
    market = str(market).upper()
    if (not should_sell or not R.enabled(market)
            or getattr(agent, "_no_order_effects", None) is not None):
        return should_sell, reason
    view = _protected(stock)
    if view is None:
        return should_sell, reason
    allow, code = R.sell_verdict(protected=True, exit_code=view.get("exit"), current_price=stock.get("current_price"),
                                 stop_loss=stock.get("stop_loss"), buy_price=stock.get("buy_price"), reason=reason)
    if allow:
        return should_sell, reason
    _block(market, stock, reason, code, "final", logger or _LOG, view)
    return False, R.blocked_reason(code, reason, "en" if market == "US" else "ko")


def guard_decision(agent, market, stock, decision, *, logger=None):
    """Apply the guard to the parsed LLM sell decision before its side effects (hold branch, add plan).

    A blocked sell becomes a hold; any portfolio_adjustment (trailing stop, target) is ignored while protected.
    """
    market = str(market).upper()
    if (not isinstance(decision, dict) or not R.enabled(market)
            or getattr(agent, "_no_order_effects", None) is not None):
        return decision
    view = _protected(stock)
    if view is None:
        return decision
    log = logger or _LOG
    decision = dict(decision)
    adjustment = decision.get("portfolio_adjustment")
    if isinstance(adjustment, dict) and (adjustment.get("needed") or adjustment.get("new_stop_loss") not in
                                         (None, "", 0) or adjustment.get("new_target_price") not in (None, "", 0)):
        log.warning("[RUNNER_HOLD] stop_adjust_ignored market=%s ticker=%s new_stop_loss=%s new_target_price=%s "
                    "(runner stop/target are system-managed)", market, stock.get("ticker"),
                    adjustment.get("new_stop_loss"), adjustment.get("new_target_price"))
        _emit("runner.stop_adjust_ignored", market=market, ticker=stock.get("ticker"), row_id=stock.get("id"),
              attributes={"new_stop_loss": adjustment.get("new_stop_loss"),
                          "new_target_price": adjustment.get("new_target_price"),
                          "reason": str(adjustment.get("reason") or "")[:200]})
        decision["portfolio_adjustment"] = dict(adjustment, needed=False, new_stop_loss=None, new_target_price=None)
    if not decision.get("should_sell"):
        return decision
    reason = decision.get("sell_reason")
    allow, code = R.sell_verdict(protected=True, exit_code=view.get("exit"), current_price=stock.get("current_price"),
                                 stop_loss=stock.get("stop_loss"), buy_price=stock.get("buy_price"), reason=reason)
    if allow:
        return decision
    _block(market, stock, reason, code, "llm", log, view)
    decision["should_sell"] = False
    decision["sell_reason"] = R.blocked_reason(code, reason, "en" if market == "US" else "ko")
    return decision


def prompt_block(stock, *, market, language):
    """Per-holding sell-review appendix for a protected runner; '' otherwise."""
    if not R.enabled(market):
        return ""
    view = stock.get(CTX_KEY) if isinstance(stock, dict) else None
    return R.prompt_block(view, market=market, language=language) if view else ""
