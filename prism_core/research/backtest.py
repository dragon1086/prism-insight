"""Deterministic point-in-time research backtester with executable-price fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from prism_core.data.contracts import CorporateActionType, SecurityId
from prism_core.research.costs import CostConfig, CostModel, TradeCosts, TradeSide
from prism_core.research.portfolio import (
    FillReason,
    PortfolioSnapshot,
    ResearchFill,
    ResearchPortfolio,
)
from prism_core.strategies.contracts import StrategyId


class BacktestInputError(ValueError):
    """Raised when research evidence cannot support PIT performance claims."""


class FutureDataError(BacktestInputError):
    """Raised when a signal claims information not knowable at generation time."""


class UniverseEvidenceKind(str, Enum):
    POINT_IN_TIME = "POINT_IN_TIME"
    CURRENT_CONSTITUENTS_ONLY = "CURRENT_CONSTITUENTS_ONLY"


@dataclass(frozen=True)
class UniverseSnapshot:
    snapshot_id: UUID
    as_of: datetime
    available_at: datetime
    members: tuple[SecurityId, ...]
    evidence_kind: UniverseEvidenceKind

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        _require_aware(self.available_at, "available_at")
        if not self.members or len(set(self.members)) != len(self.members):
            raise ValueError("universe members must be non-empty and unique")


@dataclass(frozen=True)
class ResearchBar:
    security_id: SecurityId
    data_snapshot_id: UUID
    bar_start: datetime
    bar_end: datetime
    available_at: datetime
    raw_open: Decimal
    raw_close: Decimal

    def __post_init__(self) -> None:
        _require_aware(self.bar_start, "bar_start")
        _require_aware(self.bar_end, "bar_end")
        _require_aware(self.available_at, "available_at")
        if self.bar_end <= self.bar_start:
            raise ValueError("bar_end must be after bar_start")
        if self.available_at < self.bar_end:
            raise ValueError("bar cannot be available before it ends")
        _require_positive_decimal(self.raw_open, "raw_open")
        _require_positive_decimal(self.raw_close, "raw_close")


@dataclass(frozen=True)
class ResearchSignal:
    """A research-only target change with its complete knowledge boundary."""

    strategy_id: StrategyId
    security_id: SecurityId
    generated_at: datetime
    source_bar_end: datetime
    source_available_at: datetime
    data_snapshot_id: UUID
    side: TradeSide
    quantity: int

    def __post_init__(self) -> None:
        _require_aware(self.generated_at, "generated_at")
        _require_aware(self.source_bar_end, "source_bar_end")
        _require_aware(self.source_available_at, "source_available_at")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")


@dataclass(frozen=True)
class ResearchCorporateAction:
    """Normalized economic event used by the research ledger."""

    action_id: UUID
    security_id: SecurityId
    action_type: CorporateActionType
    effective_at: datetime
    available_at: datetime
    data_snapshot_id: UUID
    ratio: Decimal | None = None
    cash_amount: Decimal | None = None

    def __post_init__(self) -> None:
        _require_aware(self.effective_at, "effective_at")
        _require_aware(self.available_at, "available_at")
        if self.action_type in {
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
            CorporateActionType.STOCK_DIVIDEND,
        }:
            if self.ratio is None:
                raise ValueError("share-changing action requires an explicit ratio")
            _require_positive_decimal(self.ratio, "ratio")
        elif self.action_type in {
            CorporateActionType.CASH_DIVIDEND,
            CorporateActionType.DELISTING,
        }:
            if self.cash_amount is None:
                raise ValueError("cash action requires an explicit cash amount")
            _require_non_negative_decimal(self.cash_amount, "cash_amount")
        else:
            raise ValueError("unsupported corporate action must fail closed")


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash_by_strategy: Mapping[StrategyId, Decimal]
    costs: CostConfig
    research_caveats: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "initial_cash_by_strategy",
            MappingProxyType(dict(self.initial_cash_by_strategy)),
        )
        if not self.research_caveats or any(
            not isinstance(item, str) or not item.strip()
            for item in self.research_caveats
        ):
            raise ValueError("at least one explicit research caveat is required")


@dataclass(frozen=True)
class BacktestResult:
    fills: tuple[ResearchFill, ...]
    portfolio: PortfolioSnapshot
    data_snapshot_ids: tuple[UUID, ...]
    applied_action_ids: tuple[UUID, ...]
    caveats: tuple[str, ...]


class PointInTimeBacktester:
    def __init__(self, config: BacktestConfig) -> None:
        if not isinstance(config, BacktestConfig):
            raise TypeError("config must be BacktestConfig")
        self.config = config
        self._cost_model = CostModel(config.costs)

    def run(
        self,
        *,
        universes: tuple[UniverseSnapshot, ...],
        bars: tuple[ResearchBar, ...],
        signals: tuple[ResearchSignal, ...],
        actions: tuple[ResearchCorporateAction, ...],
        end_at: datetime,
    ) -> BacktestResult:
        _require_aware(end_at, "end_at")
        if not universes:
            raise BacktestInputError("at least one PIT universe is required")
        if any(
            universe.evidence_kind is not UniverseEvidenceKind.POINT_IN_TIME
            for universe in universes
        ):
            raise BacktestInputError(
                "today's-constituents-only universe cannot support performance evidence"
            )
        universe_boundaries = tuple(
            (universe.as_of, universe.available_at) for universe in universes
        )
        if len(set(universe_boundaries)) != len(universe_boundaries):
            raise BacktestInputError(
                "duplicate universe as-of/availability boundaries are ambiguous"
            )
        if any(not isinstance(action, ResearchCorporateAction) for action in actions):
            raise TypeError("actions must contain ResearchCorporateAction values")
        if any(
            action.effective_at <= end_at and action.available_at > end_at
            for action in actions
        ):
            raise FutureDataError(
                "corporate action terms became available after the run end"
            )
        portfolio = ResearchPortfolio(
            initial_cash_by_strategy=self.config.initial_cash_by_strategy
        )
        planned_fills: list[ResearchFill] = []
        ordered_bars = tuple(sorted(bars, key=lambda bar: (bar.bar_start, str(bar.security_id.value))))
        for signal in sorted(signals, key=lambda item: item.generated_at):
            self._validate_signal(signal)
            if not any(
                bar.security_id == signal.security_id
                and bar.data_snapshot_id == signal.data_snapshot_id
                and bar.bar_end == signal.source_bar_end
                and bar.available_at == signal.source_available_at
                for bar in ordered_bars
            ):
                raise BacktestInputError(
                    "signal source boundary does not bind to an actual source bar"
                )
            universe = self._universe_for(signal.generated_at, universes)
            if signal.security_id not in universe.members:
                raise BacktestInputError("signal security is absent from the PIT universe")
            execution_bar = next(
                (
                    bar
                    for bar in ordered_bars
                    if bar.security_id == signal.security_id
                    and bar.bar_start > signal.source_bar_end
                    and bar.bar_start >= signal.generated_at
                    and bar.bar_start <= end_at
                ),
                None,
            )
            if execution_bar is None:
                raise BacktestInputError("signal has no later executable bar")
            fill = ResearchFill(
                strategy_id=signal.strategy_id,
                security_id=signal.security_id,
                side=signal.side,
                occurred_at=execution_bar.bar_start,
                costs=self._cost_model.calculate(
                    side=signal.side,
                    reference_price=execution_bar.raw_open,
                    quantity=signal.quantity,
                ),
            )
            planned_fills.append(fill)

        fills: list[ResearchFill] = []
        applied_action_ids: list[UUID] = []
        delisted: set[SecurityId] = set()
        events: list[tuple[datetime, int, int, ResearchCorporateAction | ResearchFill]] = [
            (action.effective_at, 0, index, action)
            for index, action in enumerate(actions)
            if action.effective_at <= end_at
        ]
        events.extend(
            (fill.occurred_at, 1, index, fill)
            for index, fill in enumerate(planned_fills)
        )
        for _, _, _, event in sorted(events, key=lambda item: item[:3]):
            if isinstance(event, ResearchFill):
                if event.security_id in delisted:
                    raise BacktestInputError("cannot fill a signal after delisting")
                portfolio.apply_fill(event)
                fills.append(event)
                continue
            self._apply_action(event, portfolio, fills)
            applied_action_ids.append(event.action_id)
            if event.action_type is CorporateActionType.DELISTING:
                delisted.add(event.security_id)

        marks: dict[SecurityId, Decimal] = {}
        for bar in ordered_bars:
            if bar.bar_end <= end_at and bar.available_at <= end_at:
                marks[bar.security_id] = bar.raw_close
        snapshot = portfolio.snapshot(marks=marks, as_of=end_at)
        snapshot_ids = tuple(
            sorted(
                {
                    *(universe.snapshot_id for universe in universes),
                    *(bar.data_snapshot_id for bar in bars),
                    *(signal.data_snapshot_id for signal in signals),
                    *(action.data_snapshot_id for action in actions),
                },
                key=str,
            )
        )
        return BacktestResult(
            fills=tuple(fills),
            portfolio=snapshot,
            data_snapshot_ids=snapshot_ids,
            applied_action_ids=tuple(applied_action_ids),
            caveats=(
                "same-close fills are prohibited",
                "performance is research evidence only and never authorizes orders",
                *self.config.research_caveats,
            ),
        )

    def _apply_action(
        self,
        action: ResearchCorporateAction,
        portfolio: ResearchPortfolio,
        fills: list[ResearchFill],
    ) -> None:
        if action.action_type in {
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
            CorporateActionType.STOCK_DIVIDEND,
        }:
            assert action.ratio is not None
            portfolio.apply_split(
                security_id=action.security_id,
                effective_at=action.effective_at,
                ratio=action.ratio,
            )
            return
        if action.action_type is CorporateActionType.CASH_DIVIDEND:
            assert action.cash_amount is not None
            portfolio.apply_cash_dividend(
                security_id=action.security_id,
                effective_at=action.effective_at,
                cash_per_share=action.cash_amount,
            )
            return
        assert action.action_type is CorporateActionType.DELISTING
        assert action.cash_amount is not None
        for strategy_id, quantity in portfolio.position_quantities(action.security_id):
            costs = (
                TradeCosts(
                    reference_price=Decimal("0"),
                    execution_price=Decimal("0"),
                    quantity=quantity,
                    notional=Decimal("0"),
                    commission=Decimal("0"),
                    tax=Decimal("0"),
                    spread_cost=Decimal("0"),
                    slippage_cost=Decimal("0"),
                )
                if action.cash_amount == 0
                else self._cost_model.calculate(
                    side=TradeSide.SELL,
                    reference_price=action.cash_amount,
                    quantity=quantity,
                )
            )
            fill = ResearchFill(
                strategy_id=strategy_id,
                security_id=action.security_id,
                side=TradeSide.SELL,
                occurred_at=action.effective_at,
                costs=costs,
                reason=FillReason.DELISTING,
            )
            portfolio.apply_delisting(fill)
            fills.append(fill)

    @staticmethod
    def _validate_signal(signal: ResearchSignal) -> None:
        if signal.source_bar_end > signal.generated_at:
            raise FutureDataError("signal source bar ends after signal generation")
        if signal.source_available_at > signal.generated_at:
            raise FutureDataError("signal source became available after signal generation")

    @staticmethod
    def _universe_for(
        at: datetime, universes: tuple[UniverseSnapshot, ...]
    ) -> UniverseSnapshot:
        candidates = tuple(
            universe
            for universe in universes
            if universe.as_of <= at and universe.available_at <= at
        )
        if not candidates:
            raise BacktestInputError("no PIT universe is available at signal time")
        universe = max(candidates, key=lambda item: (item.as_of, item.available_at))
        if universe.evidence_kind is not UniverseEvidenceKind.POINT_IN_TIME:
            raise BacktestInputError(
                "today's-constituents-only universe cannot support performance evidence"
            )
        return universe


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_positive_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be a positive finite Decimal")


def _require_non_negative_decimal(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a non-negative finite Decimal")
