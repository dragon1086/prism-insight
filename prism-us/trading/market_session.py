"""Pure exchange-calendar session checks used by US order routing."""

from __future__ import annotations


def is_exchange_session_open(now, calendar, timezone) -> bool:
    """Return whether ``now`` falls within today's actual exchange schedule."""
    day = now.date().isoformat()
    schedule = calendar.schedule(start_date=day, end_date=day)
    if schedule.empty:
        return False
    session = schedule.iloc[0]
    market_open = session["market_open"].tz_convert(timezone).to_pydatetime()
    market_close = session["market_close"].tz_convert(timezone).to_pydatetime()
    return market_open <= now <= market_close
