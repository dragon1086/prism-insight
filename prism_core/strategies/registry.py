"""Deterministic lookup for approved strategy-family definitions."""

from __future__ import annotations

from prism_core.strategies.contracts import Market, StrategyDefinition, StrategyId
from prism_core.strategies.swing import SWING_V1
from prism_core.strategies.trend import TREND_V1


class StrategyRegistry:
    """Registry of one active definition per strategy-family identity.

    Historical and concurrent-version research belongs in persisted experiment
    records, not in this active-definition lookup.
    """

    def __init__(self, definitions: tuple[StrategyDefinition, ...]) -> None:
        if not definitions:
            raise ValueError("at least one strategy definition is required")
        by_id = {definition.strategy_id: definition for definition in definitions}
        if len(by_id) != len(definitions):
            raise ValueError("duplicate strategy_id")
        self._definitions = definitions
        self._by_id = by_id

    @property
    def strategy_ids(self) -> tuple[StrategyId, ...]:
        return tuple(definition.strategy_id for definition in self._definitions)

    def get(self, strategy_id: StrategyId) -> StrategyDefinition:
        if not isinstance(strategy_id, StrategyId):
            raise TypeError("strategy_id must be a StrategyId")
        try:
            return self._by_id[strategy_id]
        except KeyError as exc:
            raise KeyError(f"strategy is not registered: {strategy_id.value}") from exc

    def enabled_for(self, market: Market) -> tuple[StrategyDefinition, ...]:
        if not isinstance(market, Market):
            raise TypeError("market must be a Market")
        return tuple(
            definition
            for definition in self._definitions
            if market in definition.supported_markets
        )


DEFAULT_STRATEGY_REGISTRY = StrategyRegistry((SWING_V1, TREND_V1))
