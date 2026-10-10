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


_DECISION_KO = {"buy": "매수", "sell": "매도·보유"}
# One maintenance alert per (market, decision) per process; later ones are events only.
_alerted: set[tuple[str, str]] = set()


async def note_codex_fallback(market: str, decision: str, ticker: Any, cause: Any, *,
                              timeout_s: Any = None, alert_sender: Any = None) -> None:
    """Record that a BUY/SELL decision left the configured Codex model for the mcp-agent fallback.

    ``cause`` is the exception from the Codex call or the string ``"parse_failed"``.
    Emits ``codex.decision_fallback`` and sends one maintenance alert per batch and
    side. Never raises: the fallback decision must still run.
    """
    try:
        from prism_core.codex_config import CodexFastError, CodexFastTimeout

        market, decision = str(market).upper(), str(decision).lower()
        if isinstance(cause, CodexFastTimeout):
            reason = "timeout"
        elif isinstance(cause, str):
            reason = cause
        else:
            reason = "error"
        # CodexFastError text is allowlisted by the backend; other exception text may not be.
        detail = str(cause)[:200] if isinstance(cause, CodexFastError) else None
        emit_event(
            "codex.decision_fallback",
            service=f"prism-{market.lower()}-{decision}-decision",
            market=market,
            ticker=str(ticker) if ticker else None,
            severity="WARNING",
            attributes={
                "decision": decision,
                "reason": reason,
                "error_type": None if isinstance(cause, str) else type(cause).__name__,
                "detail": detail,
                "timeout_s": timeout_s,
                "fallback": "mcp-agent",
            },
        )
        if (market, decision) in _alerted:
            return
        _alerted.add((market, decision))
        if alert_sender is None:
            from messaging.publish_guard import signal_publishing_disabled
            if signal_publishing_disabled():  # test runs never reach Telegram
                return
            from prism_core.ops_alert import send_ops_alert as alert_sender
        reason_ko = {"timeout": f"제한 시간({timeout_s}초) 초과", "parse_failed": "응답 해석 실패"}.get(reason, "오류")
        await alert_sender(
            f"[PRISM] {market} {_DECISION_KO.get(decision, decision)} 판단: {ticker} — 설정 모델(Codex)이 "
            f"{reason_ko}로 실패해 예비 모델로 판단했습니다. 같은 실행의 추가 건은 "
            "codex.decision_fallback 이벤트로만 남깁니다.")
    except Exception:  # noqa: BLE001 - observability never blocks the fallback decision
        return


__all__ = ["note_codex_fallback", "note_kis_rate_limit", "note_sell_fallback"]
