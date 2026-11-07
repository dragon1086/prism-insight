"""ClickStack ledger for #288 pyramid decisions, KR/US (2026-11-08). Observation only, fail-open.

Events (docs/PRISM_OBSERVABILITY.md "Pyramid rows"):
- ``pyramid.gate_evaluated``: every time an already-held ticker reaches the pyramid gate, on the
  trace of the ticker's first row. Inputs that are unknown stay null, never 0.
- ``pyramid.row_opened``: a pyramid row was recorded; on the new row's own trace with
  ``origin_trace_id`` (first row) and ``parent_trace_ids`` (every earlier row).
- ``entry.slot_blocked`` (KR): a new name passed every gate but the holding rows were full.
  (US already records this skip in ``candidate.evaluated``; ``slot_context`` extends it.)
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)
_ACCOUNT_ROWS_SQL = {
    "KR": "SELECT id, ticker, scenario FROM stock_holdings WHERE account_key = ? ORDER BY id",
    "US": "SELECT id, ticker, scenario FROM us_stock_holdings WHERE account_key = ? ORDER BY id",
}


def _account_rows(agent, market):
    """[(id, ticker, scenario)] of the active account, or None when unreadable."""
    try:
        from prism_core.micro_split_live import _own_cursor
        return [tuple(row) for row in _own_cursor(agent).execute(
            _ACCOUNT_ROWS_SQL[str(market).upper()], (agent._account_scope()[0],)).fetchall()]
    except Exception:  # noqa: BLE001 - unknown stays null
        return None


def _row_trace(market, row_id, scenario):
    from prism_core.trace_ids import decision_of, trade_trace
    return trade_trace(market, decision_id=decision_of(scenario), position_id=f"legacy:{market}:{row_id}")


def lineage(agent, market, ticker):
    """{"entry_no", "parent_row_ids", "origin_trace_id", "parent_trace_ids"} of a new row of ``ticker``."""
    market = str(market).upper()
    rows = [r for r in (_account_rows(agent, market) or []) if r[1] == ticker]
    traces = [_row_trace(market, r[0], r[2]) for r in rows]
    return {"entry_no": len(rows) + 1, "parent_row_ids": [r[0] for r in rows],
            "origin_trace_id": traces[0] if traces else None, "parent_trace_ids": traces}


def slot_context(rows, max_slots):
    """Rows held, distinct tickers, pyramid rows (rows beyond a ticker's first), free rows, reserve."""
    if rows is None:
        return {"rows_held": None, "distinct_tickers": None, "pyramid_rows_held": None, "free_rows": None,
                "reserve_applied": None}
    from prism_core.micro_split_live import (PYRAMID_RESERVE_BELOW_DEFAULT, PYRAMID_RESERVE_DEFAULT,
                                             _setting)
    names = len({r[1] for r in rows})
    try:
        reserve = _setting("PYRAMID_RESERVE_SLOTS", PYRAMID_RESERVE_DEFAULT)
        below = _setting("PYRAMID_RESERVE_BELOW_TICKERS", PYRAMID_RESERVE_BELOW_DEFAULT)
        applied = reserve if names < below else 0
    except (TypeError, ValueError):
        applied = None
    return {"rows_held": len(rows), "distinct_tickers": names, "pyramid_rows_held": len(rows) - names,
            "free_rows": int(max_slots) - len(rows), "reserve_applied": applied}


def reason_code(allowed, reason):
    """Stable code for a gate reason string."""
    text = str(reason or "")
    if allowed:
        return "ALLOWED"
    for prefix, code in (("regime", "REGIME_NOT_ALLOWED"), ("row count", "MAX_ROWS"),
                         ("profit", "PROFIT_BELOW_MIN"), ("insufficient price", "PRICE_MISSING")):
        if text.startswith(prefix):
            return code
    head = text.split(":", 1)[0]
    return head if head.replace("_", "").isalpha() and head.isupper() else "OTHER"


def _skip(agent):
    return getattr(agent, "_no_order_effects", None) is not None  # isolated replay: no ledger writes


def emit_gate(agent, *, market, ticker, stage, allowed, reason, existing=None, current_price=None,
              regime=None, planned_allocation=None, decision_id=None):
    """``pyramid.gate_evaluated``; ``stage`` is pre_gate (before the LLM), gate, or sizing (after the B3 plan)."""
    if _skip(agent):
        return None
    try:
        from observability.events import emit_event
        from prism_core.micro_split_live import pyramid_rows_enabled
        market = str(market).upper()
        rows = _account_rows(agent, market)
        mine = [r for r in rows or [] if r[1] == ticker]
        avg = (existing or {}).get("avg_buy_price")
        try:
            profit = (float(current_price) / float(avg) - 1) * 100 if avg and float(avg) > 0 and current_price \
                and float(current_price) > 0 else None
        except (TypeError, ValueError):
            profit = None
        flag = pyramid_rows_enabled()
        origin = _row_trace(market, mine[0][0], mine[0][2]) if mine else None
        attributes = {"stage": stage, "allowed": bool(allowed), "reason_code": reason_code(allowed, reason),
                      "reason": str(reason or "")[:300], "flag_enabled": flag, "regime": regime or None,
                      "entry_basis": "cost_weighted" if flag else "simple_mean",
                      "existing_entry": avg if avg else None, "current_price": current_price or None,
                      "profit_pct": profit, "ticker_rows": (existing or {}).get("row_count", len(mine) if rows else None),
                      "ownership": (existing or {}).get("pyramid_ownership"),
                      "planned_allocation": planned_allocation, "candidate_decision_id": decision_id,
                      **slot_context(rows, getattr(agent, "max_slots", 10))}
        return emit_event("pyramid.gate_evaluated", service=f"prism-{market.lower()}-pyramid", market=market,
                          ticker=ticker, trace_id=origin, decision_id=decision_id, attributes=attributes)
    except Exception as error:  # noqa: BLE001 - observability never affects trading
        _LOG.debug("pyramid gate event skipped: %s", error)
        return None


def emit_row_opened(agent, *, market, ticker, position_id, scenario):
    """``pyramid.row_opened`` on the new row's trace, linked to the earlier rows."""
    if _skip(agent):
        return None
    try:
        from observability.events import emit_event
        from prism_core.micro_split_live import record
        from prism_core.slot_weight import slot_fraction
        from prism_core.trace_ids import decision_of, trade_trace
        market = str(market).upper()
        block = record(scenario) or {}
        marker = block.get("pyramid_row") if isinstance(block.get("pyramid_row"), dict) else {}
        row_id = str(position_id or "").rsplit(":", 1)[-1] or None
        if not marker:  # legacy full-slot pyramid row (flag off): lineage from the rows held now
            marker = lineage(agent, market, ticker)
            marker["parent_row_ids"] = [r for r in marker["parent_row_ids"] if str(r) != str(row_id)]
            marker["entry_no"] = len(marker["parent_row_ids"]) + 1
        decision = decision_of(scenario)
        attributes = {"row_id": row_id, "row_number": marker.get("entry_no"),
                      "allocation": slot_fraction(scenario), "micro_split": bool(block),
                      "stop_loss": (scenario or {}).get("stop_loss"),
                      "parent_row_ids": marker.get("parent_row_ids"),
                      "origin_trace_id": marker.get("origin_trace_id"),
                      "parent_trace_ids": marker.get("parent_trace_ids")}
        return emit_event("pyramid.row_opened", service=f"prism-{market.lower()}-pyramid", market=market,
                          ticker=ticker, position_id=position_id, decision_id=decision,
                          trace_id=trade_trace(market, decision_id=decision, position_id=position_id),
                          attributes=attributes)
    except Exception as error:  # noqa: BLE001
        _LOG.debug("pyramid row event skipped: %s", error)
        return None


def emit_slot_blocked(agent, *, market, ticker, score, price, decision_id, limit):
    """``entry.slot_blocked``: a new name crowded out by full rows (``limit`` MAX_SLOTS or AI_CAP)."""
    if _skip(agent):
        return None
    try:
        from observability.events import emit_event
        from prism_core.trace_ids import trade_trace
        market = str(market).upper()
        attributes = {"limit": limit, "buy_score": score, "decision_price": price or None,
                      "slots_max": getattr(agent, "max_slots", None),
                      **slot_context(_account_rows(agent, market), getattr(agent, "max_slots", 10))}
        return emit_event("entry.slot_blocked", service=f"prism-{market.lower()}-pyramid", market=market,
                          ticker=ticker, decision_id=decision_id,
                          trace_id=trade_trace(market, decision_id=decision_id, ticker=ticker),
                          attributes=attributes)
    except Exception as error:  # noqa: BLE001
        _LOG.debug("slot blocked event skipped: %s", error)
        return None
