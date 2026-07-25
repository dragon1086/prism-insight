"""Immutable position and exposure contracts for consolidated portfolio policy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import Market, StrategyId


class BookKind(str, Enum):
    """Whether a strategy book represents actual or virtual exposure."""

    ACTUAL = "ACTUAL"
    VIRTUAL = "VIRTUAL"


@dataclass(frozen=True)
class StrategyPosition:
    """One long-only position retained in its strategy-specific book."""

    book_id: str
    book_kind: BookKind
    strategy_id: StrategyId
    security_id: SecurityId
    symbol: str
    sector: str
    market: Market
    currency: str
    base_currency: str
    fx_rate_to_base: Decimal
    quantity: int
    average_entry_price: Decimal
    current_price: Decimal
    stop_price: Decimal

    def __post_init__(self) -> None:
        _require_non_empty("book_id", self.book_id)
        _require_type("book_kind", self.book_kind, BookKind)
        _require_type("strategy_id", self.strategy_id, StrategyId)
        _require_type("security_id", self.security_id, SecurityId)
        _require_non_empty("symbol", self.symbol)
        _require_non_empty("sector", self.sector)
        _require_type("market", self.market, Market)
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.base_currency, str) or len(self.base_currency) != 3 or not self.base_currency.isupper():
            raise ValueError("base_currency must be a three-letter uppercase code")
        _require_positive_decimal("fx_rate_to_base", self.fx_rate_to_base)
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        for label, value in (
            ("average_entry_price", self.average_entry_price),
            ("current_price", self.current_price),
            ("stop_price", self.stop_price),
        ):
            _require_positive_decimal(label, value)


    @property
    def market_value(self) -> Decimal:
        return self.current_price * self.quantity

    @property
    def base_market_value(self) -> Decimal:
        return self.market_value * self.fx_rate_to_base

    @property
    def unrealized_pnl(self) -> Decimal:
        return (self.current_price - self.average_entry_price) * self.quantity


@dataclass(frozen=True)
class OpenOrderExposure:
    """Potential long exposure from an open order; this is not an OrderIntent."""

    book_id: str
    book_kind: BookKind
    strategy_id: StrategyId
    security_id: SecurityId
    symbol: str
    sector: str
    market: Market
    currency: str
    base_currency: str
    fx_rate_to_base: Decimal
    potential_notional: Decimal

    def __post_init__(self) -> None:
        _require_non_empty("book_id", self.book_id)
        _require_type("book_kind", self.book_kind, BookKind)
        _require_type("strategy_id", self.strategy_id, StrategyId)
        _require_type("security_id", self.security_id, SecurityId)
        _require_non_empty("symbol", self.symbol)
        _require_non_empty("sector", self.sector)
        _require_type("market", self.market, Market)
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be a three-letter uppercase code")
        if not isinstance(self.base_currency, str) or len(self.base_currency) != 3 or not self.base_currency.isupper():
            raise ValueError("base_currency must be a three-letter uppercase code")
        _require_positive_decimal("fx_rate_to_base", self.fx_rate_to_base)
        _require_positive_decimal("potential_notional", self.potential_notional)

    @property
    def base_potential_notional(self) -> Decimal:
        return self.potential_notional * self.fx_rate_to_base


def _require_non_empty(label: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def _require_positive_decimal(label: str, value: object) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a positive finite Decimal")


def _require_type(label: str, value: object, expected_type: type[object]) -> None:
    if not isinstance(value, expected_type):
        raise TypeError(f"{label} must be a {expected_type.__name__}")
