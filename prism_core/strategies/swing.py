"""SWING_V1 strategy-family definition."""

from prism_core.strategies.contracts import (
    EntryTemplate,
    Market,
    OutcomeHorizon,
    StrategyDefinition,
    StrategyId,
    StrategyVersion,
)


SWING_V1 = StrategyDefinition(
    strategy_id=StrategyId.SWING_V1,
    version=StrategyVersion("1.0.0"),
    supported_markets=(Market.KR, Market.US),
    entry_template=EntryTemplate(
        template_id="swing_v1.entry.v1",
        required_feature_names=(
            "swing_v1.price_momentum_5d",
            "swing_v1.relative_strength_20d",
            "swing_v1.volume_expansion_20d",
            "swing_v1.atr_percent_14d",
            "swing_v1.catalyst_recency_sessions",
            "swing_v1.regime_compatibility",
        ),
        threshold_names=(
            "swing_v1.min_liquidity",
            "swing_v1.min_quant_score",
            "swing_v1.max_atr_percent",
            "swing_v1.entry_breakout_buffer",
        ),
    ),
    outcome_horizons=tuple(OutcomeHorizon(value) for value in (5, 10, 20)),
)
