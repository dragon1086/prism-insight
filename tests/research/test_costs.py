from decimal import Decimal

from prism_core.research.costs import CostConfig, CostModel, TradeSide


def test_cost_model_applies_adverse_spread_slippage_fees_and_sell_tax() -> None:
    model = CostModel(
        CostConfig(
            commission_bps=Decimal("10"),
            sell_tax_bps=Decimal("20"),
            spread_bps=Decimal("40"),
            slippage_bps=Decimal("10"),
        )
    )

    buy = model.calculate(side=TradeSide.BUY, reference_price=Decimal("100"), quantity=10)
    sell = model.calculate(side=TradeSide.SELL, reference_price=Decimal("100"), quantity=10)

    assert buy.execution_price == Decimal("100.30")
    assert buy.commission == Decimal("1.00300")
    assert buy.tax == Decimal("0")
    assert sell.execution_price == Decimal("99.70")
    assert sell.commission == Decimal("0.99700")
    assert sell.tax == Decimal("1.99400")
