"""TREND_V1 strategy-family definition."""

from prism_core.strategies.contracts import (
    EntryTemplate,
    Market,
    OutcomeHorizon,
    StrategyDefinition,
    StrategyId,
    StrategyVersion,
)


TREND_V1 = StrategyDefinition(
    strategy_id=StrategyId.TREND_V1,
    version=StrategyVersion("1.0.0"),
    supported_markets=(Market.KR, Market.US),
    entry_template=EntryTemplate(
        template_id="trend_v1.entry.v1",
        required_feature_names=(
            "trend_v1.price_above_200d",
            "trend_v1.moving_average_alignment",
            "trend_v1.relative_strength_60d",
            "trend_v1.earnings_trend",
            "trend_v1.industry_leadership",
            "trend_v1.regime_compatibility",
        ),
        threshold_names=(
            "trend_v1.min_liquidity",
            "trend_v1.min_quant_score",
            "trend_v1.min_trend_strength",
            "trend_v1.max_pullback_from_high",
        ),
    ),
    outcome_horizons=tuple(OutcomeHorizon(value) for value in (20, 60, 120)),
)
