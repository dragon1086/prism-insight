"""Live lane slots (S19, user decision 2026-11-11): one account slot, split into ``slots`` parts.

KR: one position at a time (S19 one-slot: payoff 2.33 find / 2.22 confirm). US: the slot split in
three, at most three sectors, only sectors with a liquid real inverse product (S19 9-sector run:
payoff 2.27 / 3.23). Swapping a losing position for a new signal had no effect there, so there is
no swap. A product already held (or ordered) is never bought twice; the sell at the next open does
not free the slot for a buy at that same open (the research ``busy`` rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional, Sequence

ORDERED_BUY, OPEN, ORDERED_SELL, CLOSED, REJECTED = "ORDERED_BUY", "OPEN", "ORDERED_SELL", "CLOSED", "REJECTED"
OCCUPYING = (ORDERED_BUY, OPEN, ORDERED_SELL)


@dataclass(frozen=True)
class LaneRules:
    slots: int
    timeout_days: int      # calendar days after the fill when the position is sold even if its virtual trade is stuck


KR_LANE, US_LANE = LaneRules(slots=1, timeout_days=21), LaneRules(slots=3, timeout_days=35)   # virtual max hold 10 / 20 sessions


@dataclass(frozen=True)
class Product:
    code: str
    multiplier: int                  # -1, -2, -3 (a -3x product is sized at 2/3 to match the -2x research)
    exchange: Optional[str] = None   # KIS US exchange; None for KR

    @property
    def size_factor(self) -> float:
        return min(1.0, 2.0 / abs(self.multiplier)) if abs(self.multiplier) > 1 else 1.0


@dataclass(frozen=True)
class LivePosition:
    position_id: str
    account: str
    market: str
    trade_id: str                    # the virtual trade this position mirrors (its exits drive ours)
    family: str
    product: str
    status: str = ORDERED_BUY
    qty: Optional[int] = None
    entry_price: Optional[float] = None
    opened: Optional[date] = None    # order day
    filled: Optional[date] = None
    order_no: Optional[str] = None
    exit_reason: Optional[str] = None
    exit_ordered: Optional[date] = None   # sell order day; a fill is only judged on a later day
    exit_order_no: Optional[str] = None
    closed: Optional[date] = None
    error: Optional[str] = None


def plan_entries(rules: LaneRules, open_positions: Sequence[LivePosition], candidates: Sequence[tuple],
                 priority: Callable[[tuple], object]) -> list[tuple]:
    """Which (trade, product) candidates get a buy order today. ``priority``: sort key, lowest first."""
    held = {p.product for p in open_positions if p.status in OCCUPYING}
    free = rules.slots - len(held)
    out = []
    for cand in sorted(candidates, key=priority):
        product = cand[1]
        if free <= 0:
            break
        if product.code in held:
            continue
        held.add(product.code)
        out.append(cand)
        free -= 1
    return out
