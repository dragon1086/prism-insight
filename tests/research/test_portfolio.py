from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from prism_core.data.contracts import SecurityId
from prism_core.research.costs import CostConfig, CostModel, TradeSide
from prism_core.research.portfolio import FillReason, ResearchFill, ResearchPortfolio
from prism_core.strategies.contracts import StrategyId


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
SECURITY = SecurityId(value=UUID("00000000-0000-0000-0000-000000000001"))


def _cost_model() -> CostModel:
    return CostModel(
        CostConfig(
            commission_bps=Decimal("10"),
            sell_tax_bps=Decimal("20"),
            spread_bps=Decimal("40"),
            slippage_bps=Decimal("10"),
        )
    )


def test_portfolio_keeps_strategy_books_separate_and_consolidates_nav() -> None:
    portfolio = ResearchPortfolio(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("10000"),
            StrategyId.TREND_V1: Decimal("20000"),
        }
    )
    costs = _cost_model().calculate(
        side=TradeSide.BUY, reference_price=Decimal("100"), quantity=10
    )

    portfolio.apply_fill(
        ResearchFill(
            strategy_id=StrategyId.SWING_V1,
            security_id=SECURITY,
            side=TradeSide.BUY,
            occurred_at=NOW,
            costs=costs,
        )
    )
    snapshot = portfolio.snapshot(marks={SECURITY: Decimal("110")}, as_of=NOW)

    swing = snapshot.book(StrategyId.SWING_V1)
    trend = snapshot.book(StrategyId.TREND_V1)
    assert swing.cash == Decimal("8995.99700")
    assert swing.unrealized_pnl == Decimal("97.00")
    assert swing.realized_pnl == Decimal("-1.00300")
    assert swing.nav == Decimal("10095.99700")
    assert trend.cash == Decimal("20000")
    assert trend.nav == Decimal("20000")
    assert snapshot.cash == Decimal("28995.99700")
    assert snapshot.nav == Decimal("30095.99700")


def test_portfolio_applies_split_dividend_and_explicit_delisting_recovery() -> None:
    portfolio = ResearchPortfolio(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("10000"),
            StrategyId.TREND_V1: Decimal("10000"),
        }
    )
    no_costs = CostModel(
        CostConfig(
            commission_bps=Decimal("0"),
            sell_tax_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
    )
    portfolio.apply_fill(
        ResearchFill(
            strategy_id=StrategyId.SWING_V1,
            security_id=SECURITY,
            side=TradeSide.BUY,
            occurred_at=NOW,
            costs=no_costs.calculate(
                side=TradeSide.BUY, reference_price=Decimal("100"), quantity=10
            ),
        )
    )

    portfolio.apply_split(
        security_id=SECURITY, effective_at=NOW, ratio=Decimal("2")
    )
    portfolio.apply_cash_dividend(
        security_id=SECURITY,
        effective_at=NOW,
        cash_per_share=Decimal("1"),
    )
    portfolio.apply_delisting(
        ResearchFill(
            strategy_id=StrategyId.SWING_V1,
            security_id=SECURITY,
            side=TradeSide.SELL,
            occurred_at=NOW,
            costs=no_costs.calculate(
                side=TradeSide.SELL, reference_price=Decimal("40"), quantity=20
            ),
            reason=FillReason.DELISTING,
        )
    )
    snapshot = portfolio.snapshot(marks={}, as_of=NOW)

    swing = snapshot.book(StrategyId.SWING_V1)
    assert swing.positions == ()
    assert swing.cash == Decimal("9820")
    assert swing.realized_pnl == Decimal("-180")
    assert swing.nav == Decimal("9820")
