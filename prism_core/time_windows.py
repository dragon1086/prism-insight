"""Pure order-time-window classification (issue #412 Phase 1-b).

Extracted from ``trading/domestic_stock_trading.py`` so the exchange-session
calculation lives in a dependency-free core module that can be unit-tested in
isolation. Behavior is preserved byte-for-byte; ``domestic_stock_trading`` now
delegates to this function. Only the *calculation* is extracted here — order
guards, ledger writes and broadcasts stay in the trading adapter (per the
migration plan, those move no earlier than Phase 2-3).
"""
import datetime
from typing import Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime.datetime:
    """Return timezone-aware current time in Korea Standard Time."""
    return datetime.datetime.now(KST)


def domestic_order_window(now: Optional[datetime.datetime] = None) -> str:
    """Classify the Korean domestic stock order window using KST.

    Returns one of:
    - regular: 09:00~15:30 market orders
    - closing: 15:40~16:00 after-hours closing-price orders
    - reserved: KIS reserved-order window, excluding 23:40~00:10
    - unavailable: gaps where neither regular/closing nor reserved orders are accepted
    """
    now = now or now_kst()
    current_time = now.astimezone(KST).time() if now.tzinfo else now.time()

    if datetime.time(9, 0) <= current_time <= datetime.time(15, 30):
        return "regular"
    if datetime.time(15, 40) <= current_time <= datetime.time(16, 0):
        return "closing"
    if datetime.time(16, 0) < current_time <= datetime.time(23, 40):
        return "reserved"
    if datetime.time(0, 10) <= current_time <= datetime.time(7, 30):
        return "reserved"
    return "unavailable"
