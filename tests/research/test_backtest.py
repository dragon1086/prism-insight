from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data.contracts import SecurityId
from prism_core.research.backtest import (
    BacktestInputError,
    BacktestConfig,
    FutureDataError,
    PointInTimeBacktester,
    ResearchBar,
    ResearchSignal,
    UniverseEvidenceKind,
    UniverseSnapshot,
)
from prism_core.research.costs import CostConfig, TradeSide
from prism_core.strategies.contracts import StrategyId


SECURITY = SecurityId(value=UUID("00000000-0000-0000-0000-000000000001"))
SNAPSHOT = UUID("00000000-0000-0000-0000-000000000010")


def dt(day: int, hour: int) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def test_backtest_fills_signal_at_next_bar_open_not_same_close() -> None:
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
        research_caveats=("fixture prices do not model market impact",),
    )
    universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    bars = (
        ResearchBar(
            security_id=SECURITY,
            data_snapshot_id=SNAPSHOT,
            bar_start=dt(2, 9),
            bar_end=dt(2, 16),
            available_at=dt(2, 16),
            raw_open=Decimal("98"),
            raw_close=Decimal("100"),
        ),
        ResearchBar(
            security_id=SECURITY,
            data_snapshot_id=SNAPSHOT,
            bar_start=dt(5, 9),
            bar_end=dt(5, 16),
            available_at=dt(5, 16),
            raw_open=Decimal("101"),
            raw_close=Decimal("105"),
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

    result = PointInTimeBacktester(config).run(
        universes=(universe,), bars=bars, signals=(signal,), actions=(), end_at=dt(5, 16)
    )

    assert len(result.fills) == 1
    assert result.fills[0].costs.reference_price == Decimal("101")
    assert result.fills[0].occurred_at == dt(5, 9)
    assert result.portfolio.book(StrategyId.SWING_V1).unrealized_pnl == Decimal("40")
    assert "same-close fills are prohibited" in result.caveats


@pytest.fixture
def future_data_trap() -> ResearchSignal:
    return ResearchSignal(
        strategy_id=StrategyId.SWING_V1,
        security_id=SECURITY,
        generated_at=dt(2, 16),
        source_bar_end=dt(2, 16),
        source_available_at=dt(5, 16),
        data_snapshot_id=SNAPSHOT,
        side=TradeSide.BUY,
        quantity=1,
    )


@pytest.fixture
def todays_constituents_only() -> UniverseSnapshot:
    return UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.CURRENT_CONSTITUENTS_ONLY,
    )


def _minimal_config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash_by_strategy={
            StrategyId.SWING_V1: Decimal("1000"),
            StrategyId.TREND_V1: Decimal("1000"),
        },
        costs=CostConfig(
            commission_bps=Decimal("0"),
            sell_tax_bps=Decimal("0"),
            spread_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        ),
        research_caveats=("fixture",),
    )


def _next_bar() -> ResearchBar:
    return ResearchBar(
        security_id=SECURITY,
        data_snapshot_id=SNAPSHOT,
        bar_start=dt(5, 9),
        bar_end=dt(5, 16),
        available_at=dt(5, 16),
        raw_open=Decimal("100"),
        raw_close=Decimal("100"),
    )


def test_future_data_trap_fails_closed(future_data_trap: ResearchSignal) -> None:
    pit_universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )

    with pytest.raises(FutureDataError, match="available after signal"):
        PointInTimeBacktester(_minimal_config()).run(
            universes=(pit_universe,),
            bars=(_next_bar(),),
            signals=(future_data_trap,),
            actions=(),
            end_at=dt(5, 16),
        )


def test_todays_constituents_only_are_rejected_as_performance_evidence(
    todays_constituents_only: UniverseSnapshot,
) -> None:
    with pytest.raises(BacktestInputError, match="today's-constituents-only"):
        PointInTimeBacktester(_minimal_config()).run(
            universes=(todays_constituents_only,),
            bars=(),
            signals=(),
            actions=(),
            end_at=dt(5, 16),
        )


def test_terminal_mark_uses_latest_close_available_by_the_as_of_time() -> None:
    universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    delayed_close = ResearchBar(
        security_id=SECURITY,
        data_snapshot_id=SNAPSHOT,
        bar_start=dt(5, 9),
        bar_end=dt(5, 16),
        available_at=dt(6, 16),
        raw_open=Decimal("100"),
        raw_close=Decimal("110"),
    )
    signal = ResearchSignal(
        strategy_id=StrategyId.SWING_V1,
        security_id=SECURITY,
        generated_at=dt(2, 16),
        source_bar_end=dt(2, 16),
        source_available_at=dt(2, 16),
        data_snapshot_id=SNAPSHOT,
        side=TradeSide.BUY,
        quantity=1,
    )
    source_bar = ResearchBar(
        security_id=SECURITY,
        data_snapshot_id=SNAPSHOT,
        bar_start=dt(2, 9),
        bar_end=dt(2, 16),
        available_at=dt(2, 16),
        raw_open=Decimal("98"),
        raw_close=Decimal("100"),
    )

    result = PointInTimeBacktester(_minimal_config()).run(
        universes=(universe,),
        bars=(source_bar, delayed_close),
        signals=(signal,),
        actions=(),
        end_at=dt(5, 16),
    )

    position = result.portfolio.book(StrategyId.SWING_V1).positions[0]
    assert position.mark_price == Decimal("100")
    assert position.unrealized_pnl == Decimal("0")


def test_signal_source_boundary_must_bind_to_an_actual_bar() -> None:
    universe = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    signal = ResearchSignal(
        strategy_id=StrategyId.SWING_V1,
        security_id=SECURITY,
        generated_at=dt(2, 16),
        source_bar_end=dt(2, 16),
        source_available_at=dt(2, 16),
        data_snapshot_id=SNAPSHOT,
        side=TradeSide.BUY,
        quantity=1,
    )

    with pytest.raises(BacktestInputError, match="source bar"):
        PointInTimeBacktester(_minimal_config()).run(
            universes=(universe,),
            bars=(_next_bar(),),
            signals=(signal,),
            actions=(),
            end_at=dt(5, 16),
        )


def test_duplicate_universe_boundaries_fail_closed() -> None:
    first = UniverseSnapshot(
        snapshot_id=SNAPSHOT,
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )
    duplicate = UniverseSnapshot(
        snapshot_id=UUID(int=11),
        as_of=dt(2, 16),
        available_at=dt(2, 16),
        members=(SECURITY,),
        evidence_kind=UniverseEvidenceKind.POINT_IN_TIME,
    )

    with pytest.raises(BacktestInputError, match="duplicate universe"):
        PointInTimeBacktester(_minimal_config()).run(
            universes=(first, duplicate),
            bars=(),
            signals=(),
            actions=(),
            end_at=dt(5, 16),
        )
