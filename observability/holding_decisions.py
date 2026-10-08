"""Per-run hold/sell decision for every held position (fail-open, observation only).

``holding_decisions`` / ``us_holding_decisions`` keep only the latest decision
per ticker, so the history of "why we kept holding" is overwritten every batch.
``holding.evaluated`` records the final decision of each holding review — after
the runner-hold guard — with the price facts it was made on.  The trace id uses
the trading-context formula, so a position's entry, every hold review and its
exit share one trace in ClickStack.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Mapping

from observability.events import emit_event
from observability.trading_context import _stable_hex

EVENT_TYPE = "holding.evaluated"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scenario(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(value) if isinstance(value, str) else {}
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _holding_days(buy_date: Any, now: datetime) -> int | None:
    try:
        bought = datetime.strptime(str(buy_date)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    return (now.date() - bought.date()).days


def emit_holding_evaluation(
    market: str,
    stock: Mapping[str, Any],
    should_sell: Any,
    sell_reason: Any,
    *,
    source: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Append one ``holding.evaluated`` event; every failure is swallowed."""
    try:
        normalized_market = str(market or "").upper()
        position_id = (
            f"legacy:{normalized_market}:{stock['id']}" if stock.get("id") is not None else None
        )
        decision_id = str(_scenario(stock.get("scenario")).get("_decision_id") or "").strip() or None
        identity = decision_id or position_id or str(stock.get("ticker") or "").upper()
        buy_price = _number(stock.get("buy_price"))
        current_price = _number(stock.get("current_price"))
        profit = (
            round((current_price / buy_price - 1) * 100, 3)
            if buy_price and current_price
            else None
        )
        return emit_event(
            EVENT_TYPE,
            service=f"prism-{normalized_market.lower()}-sell-decision",
            market=normalized_market,
            ticker=stock.get("ticker"),
            trace_id=_stable_hex("trade-trace", normalized_market, identity, length=32),
            decision_id=decision_id,
            position_id=position_id,
            attributes={
                "decision": "SELL" if should_sell else "HOLD",
                "should_sell": bool(should_sell),
                "reason": str(sell_reason or "")[:500] or None,
                "source": source,
                "company_name": stock.get("company_name"),
                "trigger_type": stock.get("trigger_type"),
                "buy_price": buy_price,
                "current_price": current_price,
                "profit_rate_pct": profit,
                "stop_loss": _number(stock.get("stop_loss")),
                "target_price": _number(stock.get("target_price")),
                "holding_days": _holding_days(stock.get("buy_date"), now or datetime.now()),
            },
        )
    except Exception:  # noqa: BLE001 - observation must never affect a sell decision
        return None


__all__ = ["EVENT_TYPE", "emit_holding_evaluation"]
