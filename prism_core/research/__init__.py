"""Point-in-time research and backtesting foundations."""

from prism_core.research.backtest import (
    BacktestConfig,
    BacktestInputError,
    BacktestResult,
    FutureDataError,
    PointInTimeBacktester,
    ResearchBar,
    ResearchCorporateAction,
    ResearchSignal,
    UniverseEvidenceKind,
    UniverseSnapshot,
)
from prism_core.research.costs import (
    CostConfig,
    CostModel,
    TradeCosts,
    TradeSide,
)
from prism_core.research.experiment_registry import (
    EvaluationWindow,
    ExperimentRecord,
    ExperimentRegistry,
    ExperimentSpec,
    OOSExposure,
    WindowKind,
)
from prism_core.research.portfolio import (
    BookSnapshot,
    FillReason,
    PortfolioSnapshot,
    PositionSnapshot,
    ResearchFill,
    ResearchPortfolio,
)

__all__ = [
    "BacktestConfig",
    "BacktestInputError",
    "BacktestResult",
    "BookSnapshot",
    "CostConfig",
    "CostModel",
    "EvaluationWindow",
    "ExperimentRecord",
    "ExperimentRegistry",
    "ExperimentSpec",
    "FillReason",
    "FutureDataError",
    "OOSExposure",
    "PointInTimeBacktester",
    "PortfolioSnapshot",
    "PositionSnapshot",
    "ResearchBar",
    "ResearchCorporateAction",
    "ResearchFill",
    "ResearchPortfolio",
    "ResearchSignal",
    "TradeCosts",
    "TradeSide",
    "UniverseEvidenceKind",
    "UniverseSnapshot",
    "WindowKind",
]
