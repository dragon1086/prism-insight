"""Exchange-session resolution for daily point-in-time provider quality.

The exchange calendar, rather than weekday or civil-holiday heuristics, decides
which session was most recently completed at an evaluation timestamp.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import lru_cache
from typing import Any


class ExchangeCalendarUnavailableError(RuntimeError):
    """Raised when the required exchange-calendar capability is unavailable."""


class ExchangeMarket(str, Enum):
    KRX = "XKRX"
    NYSE = "XNYS"


@lru_cache(maxsize=len(ExchangeMarket))
def _calendar(market: ExchangeMarket) -> Any:
    try:
        import exchange_calendars
    except ImportError:
        raise ExchangeCalendarUnavailableError(
            "exchange calendar dependency is unavailable"
        ) from None
    try:
        return exchange_calendars.get_calendar(market.value)
    except Exception:
        raise ExchangeCalendarUnavailableError(
            f"exchange calendar is unavailable for {market.name}"
        ) from None


def latest_completed_session(market: ExchangeMarket, as_of: datetime) -> date:
    """Return the last exchange session whose official close is not after ``as_of``."""

    if not isinstance(market, ExchangeMarket):
        raise TypeError("market must be ExchangeMarket")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    calendar = _calendar(market)
    evaluation_utc = as_of.astimezone(timezone.utc)
    end_date = evaluation_utc.date()
    start_date = end_date - timedelta(days=31)
    try:
        sessions = calendar.sessions_in_range(start_date, end_date)
        completed = [
            session
            for session in sessions
            if calendar.session_close(session).to_pydatetime() <= evaluation_utc
        ]
    except Exception:
        raise ExchangeCalendarUnavailableError(
            f"exchange calendar lookup failed for {market.name}"
        ) from None
    if not completed:
        raise ExchangeCalendarUnavailableError(
            f"no completed exchange session was found for {market.name}"
        )
    return completed[-1].date()
