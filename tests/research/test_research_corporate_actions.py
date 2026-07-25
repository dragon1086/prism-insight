from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data.contracts import CorporateActionType, SecurityId
from prism_core.research.backtest import (
    BacktestConfig,
    FutureDataError,
    PointInTimeBacktester,
    ResearchBar,
    ResearchCorporateAction,
    ResearchSignal,
    UniverseEvidenceKind,
    UniverseSnapshot,
)
from prism_core.research.costs import CostConfig, TradeSide
from prism_core.research.portfolio import FillReason
from prism_core.strategies.contracts import StrategyId


SECURITY = SecurityId(value=UUID("00000000-0000-0000-0000-000000000001"))
SNAPSHOT = UUID("00000000-0000-0000-0000-000000000010")


def dt(day: int, hour: int) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def source_bar() -> ResearchBar:
    return ResearchBar(
        security_id=SECURITY,
        data_snapshot_id=SNAPSHOT,
        bar_start=dt(2, 9),
        bar_end=dt(2, 16),
        available_at=dt(2, 16),
        raw_open=Decimal("98"),
        raw_close=Decimal("100"),
    )


def test_backtest_applies_actions_and_forces_explicit_delisting_recovery() -> None:
    config = BacktestConfig(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("10000"),
            StrategyId.TREND_V1: Decimal("10000"),
        },
        costs=CostConfig(
            commission_bps=Decimal("0"),
            sell_tax_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        research_caveats=("delisting recovery is fixture-supplied",),
    )
    universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    bars = (
        source_bar(),
        ResearchBar(
            security_id=SECURITY,
            data_snapshot_id=SNAPSHOT,
            bar_start=dt(5, 9),
            bar_end=dt(5, 16),
            available_at=dt(5, 16),
            raw_open=Decimal("100"),
            raw_close=Decimal("100"),
        ),
        ResearchBar(
            security_id=SECURITY,
            data_snapshot_id=SNAPSHOT,
            bar_start=dt(8, 9),
            bar_end=dt(8, 16),
            available_at=dt(8, 16),
            raw_open=Decimal("40"),
            raw_close=Decimal("40"),
        ),
    )
    signal = ResearchSignal(
        strategy_id=StrategyId.SWING_V1,
        security_id=SECURITY,
        generated_at=dt(2, 16),
        source_bar_end=dt(2, 16),
        source_available_at=dt(2, 16),
        data_snapshot_id=SNAPSHOT,
        side=TradeSide.BUY,
        quantity=10,
    )
    actions = (
        ResearchCorporateAction(
            action_id=UUID("00000000-0000-0000-0000-000000000101"),
            security_id=SECURITY,
            action_type=CorporateActionType.SPLIT,
            effective_at=dt(6, 0),
            available_at=dt(5, 16),
            data_snapshot_id=SNAPSHOT,
            ratio=Decimal("2"),
        ),
        ResearchCorporateAction(
            action_id=UUID("00000000-0000-0000-0000-000000000102"),
            security_id=SECURITY,
            action_type=CorporateActionType.CASH_DIVIDEND,
            effective_at=dt(7, 0),
            available_at=dt(5, 16),
            data_snapshot_id=SNAPSHOT,
            cash_amount=Decimal("1"),
        ),
        ResearchCorporateAction(
            action_id=UUID("00000000-0000-0000-0000-000000000103"),
            security_id=SECURITY,
            action_type=CorporateActionType.DELISTING,
            effective_at=dt(8, 0),
            available_at=dt(5, 16),
            data_snapshot_id=SNAPSHOT,
            cash_amount=Decimal("40"),
        ),
    )

    result = PointInTimeBacktester(config).run(
        universes=(universe,),
        bars=bars,
        signals=(signal,),
        actions=actions,
        end_at=dt(8, 16),
    )

    swing = result.portfolio.book(StrategyId.SWING_V1)
    assert swing.positions == ()
    assert swing.cash == Decimal("9820")
    assert swing.realized_pnl == Decimal("-180")
    assert result.fills[-1].reason is FillReason.DELISTING
    assert result.applied_action_ids == tuple(action.action_id for action in actions)


def test_backtest_models_zero_recovery_delisting_as_a_total_loss() -> None:
    config = BacktestConfig(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("10000"),
            StrategyId.TREND_V1: Decimal("10000"),
        },
        costs=CostConfig(
            commission_bps=Decimal("10"),
            sell_tax_bps=Decimal("20"),
            spread_bps=Decimal("40"),
            slippage_bps=Decimal("10"),
        ),
        research_caveats=("zero recovery is fixture-supplied",),
    )
    universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    execution_bar = ResearchBar(
        security_id=SECURITY,
        data_snapshot_id=SNAPSHOT,
        bar_start=dt(5, 9),
        bar_end=dt(5, 16),
        available_at=dt(5, 16),
        raw_open=Decimal("100"),
        raw_close=Decimal("100"),
    )
    signal = ResearchSignal(
        strategy_id=StrategyId.SWING_V1,
        security_id=SECURITY,
        generated_at=dt(2, 16),
        source_bar_end=dt(2, 16),
        source_available_at=dt(2, 16),
        data_snapshot_id=SNAPSHOT,
        side=TradeSide.BUY,
        quantity=10,
    )
    delisting = ResearchCorporateAction(
        action_id=UUID("00000000-0000-0000-0000-000000000104"),
        security_id=SECURITY,
        action_type=CorporateActionType.DELISTING,
        effective_at=dt(8, 0),
        available_at=dt(5, 16),
        data_snapshot_id=SNAPSHOT,
        cash_amount=Decimal("0"),
    )

    result = PointInTimeBacktester(config).run(
        universes=(universe,),
        bars=(source_bar(), execution_bar),
        signals=(signal,),
        actions=(delisting,),
        end_at=dt(8, 16),
    )

    swing = result.portfolio.book(StrategyId.SWING_V1)
    assert swing.positions == ()
    assert swing.cash == Decimal("8995.99700")
    assert swing.realized_pnl == Decimal("-1004.00300")
    assert result.fills[-1].costs.execution_price == Decimal("0")
    assert result.fills[-1].reason is FillReason.DELISTING


def test_corporate_action_terms_must_be_available_by_run_end() -> None:
    action = ResearchCorporateAction(
        action_id=UUID("00000000-0000-0000-0000-000000000105"),
        security_id=SECURITY,
        action_type=CorporateActionType.CASH_DIVIDEND,
        effective_at=dt(7, 0),
        available_at=dt(9, 0),
        data_snapshot_id=SNAPSHOT,
        cash_amount=Decimal("1"),
    )
    config = BacktestConfig(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("10000"),
            StrategyId.TREND_V1: Decimal("10000"),
        },
        costs=CostConfig(
            commission_bps=Decimal("0"),
            sell_tax_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        research_caveats=("fixture",),
    )

    with pytest.raises(FutureDataError, match="corporate action"):
        PointInTimeBacktester(config).run(
            universes=(
                UniverseSnapshot(
                    snapshot_id=SNAPSHOT,
                    as_of=dt(2, 16),
                    available_at=dt(2, 16),
                    members=(SECURITY,),
                    evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
                ),
            ),
            bars=(),
            signals=(),
            actions=(action,),
            end_at=dt(8, 16),
        )
