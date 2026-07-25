"""Cash, position, PnL, and consolidated accounting for research only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from prism_core.data.contracts import SecurityId
from prism_core.research.costs import TradeCosts, TradeSide
from prism_core.strategies.contracts import StrategyId


class FillReason(str, Enum):
    SIGNAL = "SIGNAL"
    DELISTING = "DELISTING"


@dataclass(frozen=True)
class ResearchFill:
    """A simulated research fill; it is not an order or OrderIntent."""

    strategy_id: StrategyId
    security_id: SecurityId
    side: TradeSide
    occurred_at: datetime
    costs: TradeCosts
    reason: FillReason = FillReason.SIGNAL

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True)
class PositionSnapshot:
    security_id: SecurityId
    quantity: int
    average_entry_price: Decimal
    mark_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class BookSnapshot:
    strategy_id: StrategyId
    cash: Decimal
    nav: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    transaction_costs: Decimal
    positions: tuple[PositionSnapshot, ...]


@dataclass(frozen=True)
class PortfolioSnapshot:
    as_of: datetime
    books: tuple[BookSnapshot, ...]
    cash: Decimal
    nav: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    transaction_costs: Decimal

    def book(self, strategy_id: StrategyId) -> BookSnapshot:
        matches = tuple(book for book in self.books if book.strategy_id is strategy_id)
        if len(matches) != 1:
            raise ValueError("strategy book is missing or duplicated")
        return matches[0]


@dataclass
class _Position:
    quantity: int
    average_entry_price: Decimal


@dataclass
class _Book:
    cash: Decimal
    positions: dict[SecurityId, _Position]
    gross_realized_pnl: Decimal = Decimal("0")
    income: Decimal = Decimal("0")
    transaction_costs: Decimal = Decimal("0")


class ResearchPortfolio:
    """Separate strategy books with one deterministic consolidated view."""

    def __init__(self, *, initial_cash_by_strategy: Mapping[StrategyId, Decimal]) -> None:
        required = set(StrategyId)
        if set(initial_cash_by_strategy) != required:
            raise ValueError("initial cash must be supplied for every strategy book")
        self._initial_cash = MappingProxyType(dict(initial_cash_by_strategy))
        self._books: dict[StrategyId, _Book] = {}
        for strategy_id, cash in initial_cash_by_strategy.items():
            if not isinstance(cash, Decimal) or not cash.is_finite() or cash < 0:
                raise ValueError("initial cash must be a non-negative finite Decimal")
            self._books[strategy_id] = _Book(cash=cash, positions={})

    def apply_fill(self, fill: ResearchFill) -> None:
        if not isinstance(fill, ResearchFill):
            raise TypeError("fill must be ResearchFill")
        book = self._books[fill.strategy_id]
        costs = fill.costs
        if fill.side is TradeSide.BUY:
            cash_required = costs.notional + costs.explicit_costs
            if cash_required > book.cash:
                raise ValueError("research book has insufficient cash")
            book.cash -= cash_required
            current = book.positions.get(fill.security_id)
            if current is None:
                book.positions[fill.security_id] = _Position(
                    quantity=costs.quantity,
                    average_entry_price=costs.execution_price,
                )
            else:
                total_quantity = current.quantity + costs.quantity
                current.average_entry_price = (
                    current.average_entry_price * current.quantity
                    + costs.execution_price * costs.quantity
                ) / total_quantity
                current.quantity = total_quantity
        else:
            self._apply_sell(book, fill)
        book.transaction_costs += costs.explicit_costs

    def apply_split(
        self, *, security_id: SecurityId, effective_at: datetime, ratio: Decimal
    ) -> None:
        _require_effect_time(effective_at)
        if not isinstance(ratio, Decimal) or not ratio.is_finite() or ratio <= 0:
            raise ValueError("split ratio must be a positive finite Decimal")
        for book in self._books.values():
            position = book.positions.get(security_id)
            if position is None:
                continue
            adjusted_quantity = Decimal(position.quantity) * ratio
            if adjusted_quantity != adjusted_quantity.to_integral_value():
                raise ValueError("split creates a fractional share without an explicit policy")
            position.quantity = int(adjusted_quantity)
            position.average_entry_price /= ratio

    def apply_cash_dividend(
        self,
        *,
        security_id: SecurityId,
        effective_at: datetime,
        cash_per_share: Decimal,
    ) -> None:
        _require_effect_time(effective_at)
        if (
            not isinstance(cash_per_share, Decimal)
            or not cash_per_share.is_finite()
            or cash_per_share < 0
        ):
            raise ValueError("cash_per_share must be a non-negative finite Decimal")
        for book in self._books.values():
            position = book.positions.get(security_id)
            if position is None:
                continue
            income = cash_per_share * position.quantity
            book.cash += income
            book.income += income

    def apply_delisting(self, fill: ResearchFill) -> None:
        """Close one book at an explicitly supplied recovery price and cost model."""
        if fill.side is not TradeSide.SELL:
            raise ValueError("delisting treatment requires a sell fill")
        if fill.reason is not FillReason.DELISTING:
            raise ValueError("delisting treatment requires an explicit fill reason")
        book = self._books[fill.strategy_id]
        position = book.positions.get(fill.security_id)
        if position is None or position.quantity != fill.costs.quantity:
            raise ValueError("delisting treatment must close the full strategy position")
        self.apply_fill(fill)

    def position_quantities(
        self, security_id: SecurityId
    ) -> tuple[tuple[StrategyId, int], ...]:
        return tuple(
            (strategy_id, book.positions[security_id].quantity)
            for strategy_id, book in self._books.items()
            if security_id in book.positions
        )

    @staticmethod
    def _apply_sell(book: _Book, fill: ResearchFill) -> None:
        position = book.positions.get(fill.security_id)
        if position is None or position.quantity < fill.costs.quantity:
            raise ValueError("research sell exceeds the strategy position")
        proceeds = fill.costs.notional - fill.costs.explicit_costs
        book.cash += proceeds
        book.gross_realized_pnl += (
            fill.costs.execution_price - position.average_entry_price
        ) * fill.costs.quantity
        position.quantity -= fill.costs.quantity
        if position.quantity == 0:
            del book.positions[fill.security_id]

    def snapshot(
        self, *, marks: Mapping[SecurityId, Decimal], as_of: datetime
    ) -> PortfolioSnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        book_snapshots: list[BookSnapshot] = []
        for strategy_id in StrategyId:
            book = self._books[strategy_id]
            positions: list[PositionSnapshot] = []
            for security_id, position in sorted(
                book.positions.items(), key=lambda item: str(item[0].value)
            ):
                try:
                    mark = marks[security_id]
                except KeyError as exc:
                    raise ValueError("every open position requires an as-of mark") from exc
                if not isinstance(mark, Decimal) or not mark.is_finite() or mark <= 0:
                    raise ValueError("marks must be positive finite Decimals")
                market_value = mark * position.quantity
                unrealized = (
                    mark - position.average_entry_price
                ) * position.quantity
                positions.append(
                    PositionSnapshot(
                        security_id=security_id,
                        quantity=position.quantity,
                        average_entry_price=position.average_entry_price,
                        mark_price=mark,
                        market_value=market_value,
                        unrealized_pnl=unrealized,
                    )
                )
            unrealized_pnl = sum(
                (position.unrealized_pnl for position in positions), Decimal("0")
            )
            realized_pnl = (
                book.gross_realized_pnl + book.income - book.transaction_costs
            )
            nav = book.cash + sum(
                (position.market_value for position in positions), Decimal("0")
            )
            book_snapshots.append(
                BookSnapshot(
                    strategy_id=strategy_id,
                    cash=book.cash,
                    nav=nav,
                    realized_pnl=realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    transaction_costs=book.transaction_costs,
                    positions=tuple(positions),
                )
            )
        books = tuple(book_snapshots)
        return PortfolioSnapshot(
            as_of=as_of,
            books=books,
            cash=sum((book.cash for book in books), Decimal("0")),
            nav=sum((book.nav for book in books), Decimal("0")),
            realized_pnl=sum((book.realized_pnl for book in books), Decimal("0")),
            unrealized_pnl=sum(
                (book.unrealized_pnl for book in books), Decimal("0")
            ),
            transaction_costs=sum(
                (book.transaction_costs for book in books), Decimal("0")
            ),
        )


def _require_effect_time(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("effective_at must be timezone-aware")
