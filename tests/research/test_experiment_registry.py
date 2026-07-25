from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.research.experiment_registry import (
    EvaluationWindow,
    ExperimentRegistry,
    ExperimentSpec,
    OOSExposure,
    WindowKind,
)
from prism_core.strategies.contracts import StrategyId


SNAPSHOT = UUID("00000000-0000-0000-0000-000000000010")


def dt(month: int) -> datetime:
    return datetime(2025, month, 1, tzinfo=timezone.utc)


def sealed(window_id: str = "sealed-2025") -> EvaluationWindow:
    return EvaluationWindow(
        window_id=window_id,
        train_start=dt(1),
        train_end=dt(6),
        evaluation_start=dt(7),
        evaluation_end=dt(12),
        kind=WindowKind.SEALED_OOS,
    )


def spec(
    experiment_id: int,
    *,
    config: dict[str, object],
    strategy_id: StrategyId = StrategyId.SWING_V1,
    snapshot_id: UUID = SNAPSHOT,
) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=UUID(int=experiment_id),
        strategy_id=strategy_id,
        config=config,
        data_snapshot_ids=(snapshot_id,),
        code_sha="a" * 40,
        windows=(sealed(),),
        caveats=("historical simulation is not live evidence",),
    )


def test_registry_records_reproducibility_identity_and_stable_config_hash() -> None:
    registry = ExperimentRegistry()

    first = registry.register(
        spec(1, config={"threshold": Decimal("1.5"), "lookback": 20})
    )
    second = registry.register(
        spec(2, config={"lookback": 20, "threshold": Decimal("1.5")})
    )

    assert first.config_hash == second.config_hash
    assert first.data_snapshot_ids == (SNAPSHOT,)
    assert first.code_sha == "a" * 40
    assert first.window_exposures == (("sealed-2025", OOSExposure.FRESH),)


def test_observed_sealed_oos_cannot_be_relabelled_fresh() -> None:
    registry = ExperimentRegistry()
    registry.register(spec(1, config={"lookback": 20}))

    exposed = registry.mark_observed(UUID(int=1), "sealed-2025")
    repeated = registry.register(spec(2, config={"lookback": 30}))

    assert exposed.window_exposures == (("sealed-2025", OOSExposure.EXPOSED),)
    assert repeated.window_exposures == (("sealed-2025", OOSExposure.EXPOSED),)
    with pytest.raises(ValueError, match="already exposed"):
        registry.assert_fresh(UUID(int=2), "sealed-2025")


def test_observed_oos_stays_exposed_across_data_vintages_but_not_strategies() -> None:
    registry = ExperimentRegistry()
    registry.register(spec(1, config={"lookback": 20}))
    registry.mark_observed(UUID(int=1), "sealed-2025")

    refreshed = registry.register(
        spec(2, config={"lookback": 20}, snapshot_id=UUID(int=11))
    )
    other_strategy = registry.register(
        spec(
            3,
            config={"lookback": 60},
            strategy_id=StrategyId.TREND_V1,
            snapshot_id=UUID(int=11),
        )
    )

    assert refreshed.window_exposures == (("sealed-2025", OOSExposure.EXPOSED),)
    assert other_strategy.window_exposures == (("sealed-2025", OOSExposure.FRESH),)


def test_walk_forward_windows_must_be_chronological_and_non_overlapping() -> None:
    walk_forward = EvaluationWindow(
        window_id="wf-1",
        train_start=dt(1),
        train_end=dt(4),
        evaluation_start=dt(5),
        evaluation_end=dt(6),
        kind=WindowKind.WALK_FORWARD,
    )
    overlapping = EvaluationWindow(
        window_id="wf-2",
        train_start=dt(2),
        train_end=dt(5),
        evaluation_start=dt(5),
        evaluation_end=dt(7),
        kind=WindowKind.WALK_FORWARD,
    )

    with pytest.raises(ValueError, match="evaluation windows must not overlap"):
        ExperimentSpec(
            experiment_id=UUID(int=3),
            strategy_id=StrategyId.TREND_V1,
            config={"lookback": 60},
            data_snapshot_ids=(SNAPSHOT,),
            code_sha="b" * 40,
            windows=(walk_forward, overlapping),
            caveats=("fixture",),
        )
