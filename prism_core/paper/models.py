"""Deterministic internal-paper contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


class OrderState(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class InvalidOrderTransition(ValueError):
    """Raised when an order event is not valid from its durable state."""


class DuplicateOrder(ValueError):
    """Raised when a logical order identity is reused with different data."""


class DuplicateFill(ValueError):
    """Raised when a fill identity is reused with different immutable data."""


class InsufficientCash(ValueError):
    """Raised when a simulated buy cannot be funded by its strategy book."""


class InsufficientPosition(ValueError):
    """Raised when a simulated sell exceeds its strategy-book position."""


class ReconciliationMismatch(ValueError):
    """Raised when an observed state contradicts durable paper accounting."""


def require_utc(value: datetime, field: str = "datetime") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def require_decimal(value: Decimal, field: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"{field} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive")
    return value


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    book_id: str
    proposal_id: str
    security_id: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    occurred_at: datetime

    def __post_init__(self) -> None:
        for field in ("order_id", "book_id", "proposal_id", "security_id"):
            if not getattr(self, field):
                raise ValueError(f"{field} must not be empty")
        require_decimal(self.quantity, "quantity", positive=True)
        require_decimal(self.limit_price, "limit_price", positive=True)
        require_utc(self.occurred_at, "occurred_at")


@dataclass(frozen=True)
class OrderEvent:
    order_id: str
    book_id: str
    proposal_id: str
    security_id: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    state: OrderState
    occurred_at: datetime
    event_id: str
    filled_quantity: Decimal = Decimal("0")


@dataclass(frozen=True)
class PositionSnapshot:
    book_id: str
    security_id: str
    quantity: Decimal
    average_cost: Decimal
    as_of_at: datetime | None


@dataclass(frozen=True)
class NavSnapshot:
    snapshot_id: str
    book_id: str
    nav: Decimal
    cash: Decimal
    as_of_at: datetime
