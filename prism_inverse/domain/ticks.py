"""Price increments, used for the one-tick cost check and replay fills."""

from __future__ import annotations


def etf_tick(market: str, price: float) -> float:
    if market == "US":
        return 0.01
    # KRX ETF/ETN: 1 won below 2,000 won, 5 won at or above (KR 252670 at 100 won = 1% per tick).
    return 1.0 if price < 2000 else 5.0
