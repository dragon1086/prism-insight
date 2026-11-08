"""Value types shared by every layer. Unknown values are ``None``, never 0."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# Actions a decision can carry.
ENTER, HOLD, EXIT, SKIP = "ENTER", "HOLD", "EXIT", "SKIP"

# Lanes are judged separately (user decision 2026-11-09): index/sector vs single stock.
LANE_INDEX, LANE_STOCK = "index", "stock"


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None  # None = unknown (e.g. KIS US index volume 0)


@dataclass(frozen=True)
class Instrument:
    market: str                      # KR | US
    code: str                        # 069500, SPY, 000660, NVDA
    kind: str                        # index | etf | stock
    exchange: Optional[str] = None   # US KIS code (NAS/NYS/AMS); never guessed


@dataclass(frozen=True)
class EtfSpec:
    code: str
    market: str
    name: str
    multiplier: int                  # -1 or -2 (3x excluded by design)
    scope: str                       # index | sector | stock
    underlying: Instrument           # the series every rule reads (not the ETF chart)
    listed: date
    exchange: Optional[str] = None

    @property
    def lane(self) -> str:
        return LANE_STOCK if self.scope == "stock" else LANE_INDEX


@dataclass(frozen=True)
class EtfSnapshot:
    price: Optional[float] = None
    tick: Optional[float] = None             # price increment at this price
    value_20d: Optional[float] = None        # 20-day median traded value, local currency
    premium_pct: Optional[float] = None      # |price/NAV - 1| in %
    tracking_error: Optional[float] = None   # as published by the data source

    @property
    def tick_cost(self) -> Optional[float]:
        """One tick as a fraction of price: the floor of a round trip's spread cost."""
        if self.tick is None or not self.price:
            return None
        return self.tick / self.price


@dataclass(frozen=True)
class LeaderState:
    code: str
    status: Optional[str]            # None | broken | recovered | rebroken
    since: Optional[date]            # day the current status started
    prior_gain: Optional[float] = None
    drop_pct: Optional[float] = None
    volume_ratio: Optional[float] = None

    @property
    def is_broken(self) -> bool:
        return self.status in ("broken", "rebroken")


@dataclass(frozen=True)
class Position:
    position_id: str
    etf: str
    lane: str
    multiplier: int
    qty: int
    entry_price: float               # ETF fill price
    entry_underlying: float
    stop_underlying: float           # exit when the underlying closes at/above this
    target_underlying: Optional[float]
    opened: date
    attempt_no: int = 1
    underlying: Optional[str] = None


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    lane: str
    etf: Optional[str] = None
    underlying: Optional[str] = None
    underlying_close: Optional[float] = None
    stop_underlying: Optional[float] = None
    target_underlying: Optional[float] = None
    qty: Optional[int] = None
    position_id: Optional[str] = None
    inputs: dict = field(default_factory=dict)
