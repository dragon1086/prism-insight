"""Consolidated deterministic exposure aggregation and limit evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable

from prism_core.portfolio.models import OpenOrderExposure, StrategyPosition


class ExposureDimension(str, Enum):
    GROSS = "GROSS"
    OPEN_ORDER = "OPEN_ORDER"
    BOOK = "BOOK"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    SECTOR = "SECTOR"
    MARKET = "MARKET"
    CURRENCY = "CURRENCY"


@dataclass(frozen=True)
class ExposureBreakdown:
    dimension: ExposureDimension
    key: str
    position_notional: Decimal
    open_order_notional: Decimal

    @property
    def total(self) -> Decimal:
        return self.position_notional + self.open_order_notional


@dataclass(frozen=True)
class ConsolidatedExposure:
    base_currency: str
    position_exposure: Decimal
    open_order_exposure: Decimal
    gross_exposure: Decimal
    breakdowns: tuple[ExposureBreakdown, ...]

    def total_for(self, dimension: ExposureDimension, key: str) -> Decimal:
        matches = tuple(
            item
            for item in self.breakdowns
            if item.dimension is dimension and item.key == key
        )
        if not matches:
            return Decimal("0")
        if len(matches) != 1:
            raise ValueError("duplicate consolidated exposure breakdown")
        return matches[0].total


@dataclass(frozen=True)
class ExposureLimits:
    """Explicit injected research/SHADOW limits with no production defaults."""

    max_gross_exposure: Decimal
    max_symbol_exposure: Decimal
    max_sector_exposure: Decimal
    max_market_exposure: Decimal
    max_currency_exposure: Decimal
    max_open_order_exposure: Decimal

    def __post_init__(self) -> None:
        for label, value in self.__dict__.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{label} must be a non-negative finite Decimal")


@dataclass(frozen=True)
class RiskBreach:
    dimension: ExposureDimension
    key: str
    exposure: Decimal
    limit: Decimal


@dataclass(frozen=True)
class ConsolidatedRiskDecision:
    accepted: bool
    breaches: tuple[RiskBreach, ...]


class ConsolidatedRiskPolicy:
    """Apply one limit policy across actual/virtual and SWING/TREND books."""

    def consolidate(
        self,
        *,
        positions: tuple[StrategyPosition, ...],
        open_orders: tuple[OpenOrderExposure, ...],
        base_currency: str,
    ) -> ConsolidatedExposure:
        if not isinstance(positions, tuple) or any(
            not isinstance(item, StrategyPosition) for item in positions
        ):
            raise TypeError("positions must be a tuple of StrategyPosition")
        if not isinstance(open_orders, tuple) or any(
            not isinstance(item, OpenOrderExposure) for item in open_orders
        ):
            raise TypeError("open_orders must be a tuple of OpenOrderExposure")
        if not isinstance(base_currency, str) or len(base_currency) != 3 or not base_currency.isupper():
            raise ValueError("base_currency must be a three-letter uppercase code")
        if any(item.base_currency != base_currency for item in (*positions, *open_orders)):
            raise ValueError("all exposures must use the requested base_currency")
        _validate_security_metadata((*positions, *open_orders))

        totals: dict[tuple[ExposureDimension, str], list[Decimal]] = {}
        position_exposure = Decimal("0")
        open_order_exposure = Decimal("0")
        for position in positions:
            notional = position.base_market_value
            position_exposure += notional
            _accumulate(totals, position, notional, slot=0)
        for order in open_orders:
            notional = order.base_potential_notional
            open_order_exposure += notional
            _accumulate(totals, order, notional, slot=1)
        breakdowns = tuple(
            ExposureBreakdown(
                dimension=dimension,
                key=key,
                position_notional=amounts[0],
                open_order_notional=amounts[1],
            )
            for (dimension, key), amounts in sorted(
                totals.items(), key=lambda item: (item[0][0].value, item[0][1])
            )
        )
        return ConsolidatedExposure(
            base_currency=base_currency,
            position_exposure=position_exposure,
            open_order_exposure=open_order_exposure,
            gross_exposure=position_exposure + open_order_exposure,
            breakdowns=breakdowns,
        )

    def evaluate(
        self, *, exposure: ConsolidatedExposure, limits: ExposureLimits
    ) -> ConsolidatedRiskDecision:
        if not isinstance(exposure, ConsolidatedExposure):
            raise TypeError("exposure must be ConsolidatedExposure")
        if not isinstance(limits, ExposureLimits):
            raise TypeError("limits must be ExposureLimits")
        breaches: list[RiskBreach] = []
        _append_breach(
            breaches,
            ExposureDimension.GROSS,
            "ALL",
            exposure.gross_exposure,
            limits.max_gross_exposure,
        )
        _append_breach(
            breaches,
            ExposureDimension.OPEN_ORDER,
            "ALL",
            exposure.open_order_exposure,
            limits.max_open_order_exposure,
        )
        dimension_limits = {
            ExposureDimension.SYMBOL: limits.max_symbol_exposure,
            ExposureDimension.SECTOR: limits.max_sector_exposure,
            ExposureDimension.MARKET: limits.max_market_exposure,
            ExposureDimension.CURRENCY: limits.max_currency_exposure,
        }
        for item in exposure.breakdowns:
            limit = dimension_limits.get(item.dimension)
            if limit is not None:
                _append_breach(breaches, item.dimension, item.key, item.total, limit)
        breaches.sort(key=lambda item: (item.dimension.value, item.key))
        return ConsolidatedRiskDecision(accepted=not breaches, breaches=tuple(breaches))


def _accumulate(
    totals: dict[tuple[ExposureDimension, str], list[Decimal]],
    item: StrategyPosition | OpenOrderExposure,
    notional: Decimal,
    *,
    slot: int,
) -> None:
    book_key = f"{item.book_kind.value}:{item.book_id}:{item.strategy_id.value}"
    keys = (
        (ExposureDimension.BOOK, book_key),
        (ExposureDimension.STRATEGY, item.strategy_id.value),
        (ExposureDimension.SYMBOL, item.symbol),
        (ExposureDimension.SECTOR, item.sector),
        (ExposureDimension.MARKET, item.market.value),
        (ExposureDimension.CURRENCY, item.currency),
    )
    for key in keys:
        amounts = totals.setdefault(key, [Decimal("0"), Decimal("0")])
        amounts[slot] += notional


def _validate_security_metadata(
    items: Iterable[StrategyPosition | OpenOrderExposure],
) -> None:
    by_security: dict[object, tuple[str, str, object, str]] = {}
    by_symbol: dict[tuple[object, str], object] = {}
    fx_by_currency: dict[tuple[str, str], Decimal] = {}
    for item in items:
        metadata = (item.symbol, item.sector, item.market, item.currency)
        previous = by_security.setdefault(item.security_id, metadata)
        if previous != metadata:
            raise ValueError("conflicting metadata for consolidated security")
        symbol_key = (item.market, item.symbol)
        prior_security = by_symbol.setdefault(symbol_key, item.security_id)
        if prior_security != item.security_id:
            raise ValueError("symbol maps to multiple securities in one exposure snapshot")
        currency_key = (item.currency, item.base_currency)
        prior_rate = fx_by_currency.setdefault(currency_key, item.fx_rate_to_base)
        if prior_rate != item.fx_rate_to_base:
            raise ValueError("conflicting fx rate for currency in one exposure snapshot")


def _append_breach(
    breaches: list[RiskBreach],
    dimension: ExposureDimension,
    key: str,
    value: Decimal,
    limit: Decimal,
) -> None:
    if value > limit:
        breaches.append(
            RiskBreach(dimension=dimension, key=key, exposure=value, limit=limit)
        )
