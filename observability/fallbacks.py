"""Fail-open evidence for degraded trading paths (AI sell fallback, KIS rate limits).

These paths used to leave only free-text log lines (or a stdout print), which the
two-week review (docs/TWO_WEEK_REVIEW_ko.md) cannot count reliably once dated logs
are compressed or truncated. Each helper appends one event and returns its input
unchanged, so call sites keep their behavior.
"""
from __future__ import annotations

from typing import Any

from observability.events import emit_event

# The legacy fallback still carries the old "+10% take-profit" rule (KR/US wording).
_LEGACY_TEN_PCT = ("Return over 10%", "Return exceeds 10%", "수익률 10% 이상")


def note_sell_fallback(market: str, stock_data: Any, trigger: str, result: Any) -> Any:
    """Record that the AI sell decision fell back to rules; returns ``result`` as is."""
    stock = stock_data if isinstance(stock_data, dict) else {}
    should_sell, reason = (result if isinstance(result, tuple) and len(result) == 2 else (None, None))
    text = str(reason or "")
    emit_event(
        "sell.fallback_used",
        service=f"prism-{str(market).lower()}-sell-decision",
        market=str(market).upper(),
        ticker=stock.get("ticker"),
        position_id=(f"legacy:{str(market).upper()}:{stock['id']}" if stock.get("id") is not None else None),
        severity="WARNING",
        attributes={
            "trigger": trigger,
            "should_sell": should_sell,
            "sell_reason": text[:300],
            "legacy_ten_pct_rule": any(marker in text for marker in _LEGACY_TEN_PCT),
            "current_price": stock.get("current_price"),
            "buy_price": stock.get("buy_price"),
        },
    )
    return result


def note_kis_rate_limit(url: str, status_code: Any, body: Any) -> None:
    """Record a KIS 'too many requests per second' rejection (EGW00201)."""
    text = str(body or "")
    if "EGW00201" not in text:
        return
    path = str(url or "").split("?", 1)[0]
    emit_event(
        "kis.rate_limited",
        service="prism-kis-api",
        severity="WARNING",
        attributes={"path": path[-120:], "status_code": str(status_code), "body": text[:200]},
    )


__all__ = ["note_kis_rate_limit", "note_sell_fallback"]
