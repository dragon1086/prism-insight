"""Explicit injected transaction-cost assumptions for research fills."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


_BPS = Decimal("10000")


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class CostConfig:
    """Research assumptions; no production or market-specific defaults exist."""

    commission_bps: Decimal
    sell_tax_bps: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a non-negative finite Decimal")


@dataclass(frozen=True)
class TradeCosts:
    reference_price: Decimal
    execution_price: Decimal
    quantity: int
    notional: Decimal
    commission: Decimal
    tax: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal

    @property
    def explicit_costs(self) -> Decimal:
        return self.commission + self.tax

    @property
    def implementation_shortfall(self) -> Decimal:
        return self.spread_cost + self.slippage_cost + self.explicit_costs


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        if not isinstance(config, CostConfig):
            raise TypeError("config must be CostConfig")
        self.config = config

    def calculate(
        self, *, side: TradeSide, reference_price: Decimal, quantity: int
    ) -> TradeCosts:
        if not isinstance(side, TradeSide):
            raise TypeError("side must be TradeSide")
        if (
            not isinstance(reference_price, Decimal)
            or not reference_price.is_finite()
            or reference_price <= 0
        ):
            raise ValueError("reference_price must be a positive finite Decimal")
        if type(quantity) is not int or quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        half_spread_rate = self.config.spread_bps / (Decimal("2") * _BPS)
        slippage_rate = self.config.slippage_bps / _BPS
        direction = Decimal("1") if side is TradeSide.BUY else Decimal("-1")
        execution_price = reference_price * (
            Decimal("1") + direction * (half_spread_rate + slippage_rate)
        )
        notional = execution_price * quantity
        commission = notional * self.config.commission_bps / _BPS
        tax = (
            notional * self.config.sell_tax_bps / _BPS
            if side is TradeSide.SELL
            else Decimal("0")
        )
        return TradeCosts(
            reference_price=reference_price,
            execution_price=execution_price,
            quantity=quantity,
            notional=notional,
            commission=commission,
            tax=tax,
            spread_cost=reference_price * quantity * half_spread_rate,
            slippage_cost=reference_price * quantity * slippage_rate,
        )
