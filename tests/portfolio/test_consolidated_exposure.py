from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from prism_core.data.contracts import SecurityId
from prism_core.portfolio import (
    BookKind,
    ConsolidatedRiskPolicy,
    ExposureDimension,
    ExposureLimits,
    OpenOrderExposure,
    StrategyPosition,
)
from prism_core.strategies.contracts import Market, StrategyId


AAPL_ID = SecurityId(value=uuid4())
MSFT_ID = SecurityId(value=uuid4())
SAMSUNG_ID = SecurityId(value=uuid4())
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _position(
    *,
    book_id: str,
    book_kind: BookKind,
    strategy_id: StrategyId,
    security_id: SecurityId,
    symbol: str,
    market: Market,
    currency: str,
    sector: str,
    quantity: int,
    current_price: str,
) -> StrategyPosition:
    return StrategyPosition(
        book_id=book_id,
        book_kind=book_kind,
        strategy_id=strategy_id,
        security_id=security_id,
        symbol=symbol,
        sector=sector,
        market=market,
        currency=currency,
        base_currency="USD",
        fx_rate_to_base=Decimal("1"),
        quantity=quantity,
        average_entry_price=Decimal(current_price) - Decimal("10"),
        current_price=Decimal(current_price),
        stop_price=Decimal(current_price) - Decimal("20"),
    )


def _portfolio_inputs() -> tuple[tuple[StrategyPosition, ...], tuple[OpenOrderExposure, ...]]:
    positions = (
        _position(
            book_id="swing-shadow",
            book_kind=BookKind.VIRTUAL,
            strategy_id=StrategyId.SWING_V1,
            security_id=AAPL_ID,
            symbol="AAPL",
            market=Market.US,
            currency="USD",
            sector="TECH",
            quantity=10,
            current_price="100",
        ),
        _position(
            book_id="trend-shadow",
            book_kind=BookKind.VIRTUAL,
            strategy_id=StrategyId.TREND_V1,
            security_id=AAPL_ID,
            symbol="AAPL",
            market=Market.US,
            currency="USD",
            sector="TECH",
            quantity=5,
            current_price="120",
        ),
        _position(
            book_id="swing-actual",
            book_kind=BookKind.ACTUAL,
            strategy_id=StrategyId.SWING_V1,
            security_id=MSFT_ID,
            symbol="MSFT",
            market=Market.US,
            currency="USD",
            sector="TECH",
            quantity=2,
            current_price="200",
        ),
    )
    orders = (
        OpenOrderExposure(
            book_id="trend-shadow",
            book_kind=BookKind.VIRTUAL,
            strategy_id=StrategyId.TREND_V1,
            security_id=AAPL_ID,
            symbol="AAPL",
            sector="TECH",
            market=Market.US,
            currency="USD",
            base_currency="USD",
            fx_rate_to_base=Decimal("1"),
            potential_notional=Decimal("300"),
        ),
        OpenOrderExposure(
            book_id="trend-kr-shadow",
            book_kind=BookKind.VIRTUAL,
            strategy_id=StrategyId.TREND_V1,
            security_id=SAMSUNG_ID,
            symbol="005930",
            sector="IT",
            market=Market.KR,
            currency="KRW",
            base_currency="USD",
            fx_rate_to_base=Decimal("0.001"),
            potential_notional=Decimal("500000"),
        ),
    )
    return positions, orders


def test_aggregates_positions_and_open_orders_across_strategy_books() -> None:
    positions, orders = _portfolio_inputs()
    exposure = ConsolidatedRiskPolicy().consolidate(
        positions=positions, open_orders=orders, base_currency="USD"
    )

    assert exposure.position_exposure == Decimal("2000")
    assert exposure.open_order_exposure == Decimal("800")
    assert exposure.gross_exposure == Decimal("2800")
    assert exposure.total_for(ExposureDimension.SYMBOL, "AAPL") == Decimal("1900")
    assert exposure.total_for(ExposureDimension.SECTOR, "TECH") == Decimal("2300")
    assert exposure.total_for(ExposureDimension.MARKET, "US") == Decimal("2300")
    assert exposure.total_for(ExposureDimension.CURRENCY, "USD") == Decimal("2300")
    assert exposure.total_for(ExposureDimension.CURRENCY, "KRW") == Decimal("500")
    assert exposure.base_currency == "USD"
    assert exposure.total_for(ExposureDimension.STRATEGY, "SWING_V1") == Decimal("1400")
    assert exposure.total_for(ExposureDimension.STRATEGY, "TREND_V1") == Decimal("1400")


def test_keeps_actual_and_virtual_strategy_books_separate() -> None:
    positions, orders = _portfolio_inputs()
    exposure = ConsolidatedRiskPolicy().consolidate(
        positions=positions, open_orders=orders, base_currency="USD"
    )

    assert exposure.total_for(ExposureDimension.BOOK, "VIRTUAL:swing-shadow:SWING_V1") == Decimal("1000")
    assert exposure.total_for(ExposureDimension.BOOK, "VIRTUAL:trend-shadow:TREND_V1") == Decimal("900")
    assert exposure.total_for(ExposureDimension.BOOK, "ACTUAL:swing-actual:SWING_V1") == Decimal("400")


def test_one_policy_reports_consolidated_limit_breaches() -> None:
    positions, orders = _portfolio_inputs()
    policy = ConsolidatedRiskPolicy()
    exposure = policy.consolidate(
        positions=positions, open_orders=orders, base_currency="USD"
    )
    decision = policy.evaluate(
        exposure=exposure,
        limits=ExposureLimits(
            max_gross_exposure=Decimal("2700"),
            max_symbol_exposure=Decimal("1800"),
            max_sector_exposure=Decimal("2200"),
            max_market_exposure=Decimal("2200"),
            max_currency_exposure=Decimal("2200"),
            max_open_order_exposure=Decimal("700"),
        ),
    )

    assert not decision.accepted
    assert {(item.dimension, item.key) for item in decision.breaches} == {
        (ExposureDimension.GROSS, "ALL"),
        (ExposureDimension.OPEN_ORDER, "ALL"),
        (ExposureDimension.SYMBOL, "AAPL"),
        (ExposureDimension.SECTOR, "TECH"),
        (ExposureDimension.MARKET, "US"),
        (ExposureDimension.CURRENCY, "USD"),
    }


def test_consolidated_policy_tests_are_explicitly_enforced_in_ci() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Run deterministic position and consolidated portfolio policy tests" in workflow
    assert "python -m pytest tests/policy tests/portfolio -q" in workflow


def test_empty_portfolio_retains_explicit_base_currency() -> None:
    exposure = ConsolidatedRiskPolicy().consolidate(
        positions=(), open_orders=(), base_currency="USD"
    )

    assert exposure.base_currency == "USD"
    assert exposure.gross_exposure == Decimal("0")


def test_conflicting_fx_rates_for_same_currency_fail_closed() -> None:
    positions, orders = _portfolio_inputs()
    conflicting_orders = (
        *orders,
        replace(
            orders[0],
            security_id=MSFT_ID,
            symbol="MSFT",
            fx_rate_to_base=Decimal("0.99"),
        ),
    )

    with pytest.raises(ValueError, match="conflicting fx rate for currency"):
        ConsolidatedRiskPolicy().consolidate(
            positions=positions,
            open_orders=conflicting_orders,
            base_currency="USD",
        )
