"""Pure order-time-window classification (issue #412 Phase 1-b).

Extracted from ``trading/domestic_stock_trading.py`` so the exchange-session
calculation lives in a dependency-free core module that can be unit-tested in
isolation. Behavior is preserved byte-for-byte; ``domestic_stock_trading`` now
delegates to this function. Only the *calculation* is extracted here — order
guards, ledger writes and broadcasts stay in the trading adapter (per the
migration plan, those move no earlier than Phase 2-3).
"""
import datetime
import os
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime.datetime:
    """Return timezone-aware current time in Korea Standard Time."""
    return datetime.datetime.now(KST)


def regular_session(day: datetime.date) -> tuple:
    """KRX regular-session (open, close) for ``day``: 09:00-15:30, unless ``KRX_SESSION_OVERRIDE``
    names the day, e.g. ``KRX_SESSION_OVERRIDE=2026-11-19=10:00-16:30`` (comma-separated days).
    Used for the delayed open on the college entrance exam day; after-hours closing-price trading
    and the reserved window shift with the close. Malformed entries are ignored (default hours)."""
    for item in os.getenv("KRX_SESSION_OVERRIDE", "").split(","):
        try:
            date_text, hours = item.strip().split("=")
            if datetime.date.fromisoformat(date_text) != day:
                continue
            open_text, close_text = hours.split("-")
            return datetime.time.fromisoformat(open_text), datetime.time.fromisoformat(close_text)
        except ValueError:
            continue
    return datetime.time(9, 0), datetime.time(15, 30)


def _shift(t: datetime.time, minutes: int) -> datetime.time:
    return (datetime.datetime.combine(datetime.date(2000, 1, 1), t) + datetime.timedelta(minutes=minutes)).time()


def domestic_order_window(now: Optional[datetime.datetime] = None) -> str:
    """Classify the Korean domestic stock order window using KST.

    Returns one of:
    - regular: 09:00~15:30 market orders
    - closing: 15:40~16:00 after-hours closing-price orders
    - reserved: KIS reserved-order window, excluding 23:40~00:10
    - unavailable: gaps where neither regular/closing nor reserved orders are accepted
    """
    now = now or now_kst()
    local = now.astimezone(KST) if now.tzinfo else now
    current_time = local.time()
    open_at, close_at = regular_session(local.date())
    closing_from = _shift(close_at, 10)
    closing_to = _shift(close_at, 30)

    if open_at <= current_time <= close_at:
        return "regular"
    if closing_from <= current_time <= closing_to:
        return "closing"
    if closing_to < current_time <= datetime.time(23, 40):
        return "reserved"
    if datetime.time(0, 10) <= current_time <= datetime.time(7, 30):
        return "reserved"
    return "unavailable"
