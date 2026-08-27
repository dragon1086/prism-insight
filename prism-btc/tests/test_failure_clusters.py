from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.round9_target_return import TargetTrade
from analysis.round10_failure_clusters import (
    FEATURE_COLUMNS,
    apply_pyramid_block,
    apply_trade_guard,
    deterministic_kmeans,
    fit_robust_scaler,
    select_harmful_clusters,
    simulate_reversal_lane,
    transform_robust,
)


def _feature_frame(values: list[list[float]]) -> pd.DataFrame:
    return pd.DataFrame(values, columns=FEATURE_COLUMNS)


def test_robust_scaler_is_fit_once_and_reused_on_validation() -> None:
    train = _feature_frame([[float(i)] * len(FEATURE_COLUMNS) for i in range(5)])
    validation = _feature_frame([[100.0] * len(FEATURE_COLUMNS)])
    center, scale = fit_robust_scaler(train)
    transformed = transform_robust(validation, center, scale)
    # Train median=2 and IQR=2; validation must not be re-centered on itself.
    assert transformed[0, 0] == 49.0


def test_deterministic_kmeans_repeats_identical_assignments() -> None:
    points = np.array([
        [-10.0, -10.0], [-9.0, -9.0],
        [0.0, 0.0], [0.5, 0.5],
        [10.0, 10.0], [11.0, 11.0],
        [20.0, -20.0], [21.0, -21.0],
    ])
    centroids_1, labels_1 = deterministic_kmeans(points, k=4)
    centroids_2, labels_2 = deterministic_kmeans(points, k=4)
    np.testing.assert_allclose(centroids_1, centroids_2)
    np.testing.assert_array_equal(labels_1, labels_2)
    assert len(set(labels_1.tolist())) == 4


def test_harmful_cluster_requires_all_preregistered_gates() -> None:
    stats = [
        {"cluster": 0, "n": 12, "profit_factor": 0.80, "avg_r": -0.20,
         "years": [2022, 2023]},
        {"cluster": 1, "n": 11, "profit_factor": 0.10, "avg_r": -1.00,
         "years": [2022, 2023]},
        {"cluster": 2, "n": 20, "profit_factor": 1.10, "avg_r": -0.20,
         "years": [2022, 2023]},
        {"cluster": 3, "n": 20, "profit_factor": 0.50, "avg_r": -0.40,
         "years": [2022]},
    ]
    assert select_harmful_clusters(stats) == {0}


def test_trade_guard_can_block_or_half_size_selected_cluster() -> None:
    base = TargetTrade(
        lane="core",
        entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        exit_time=pd.Timestamp("2024-01-02", tz="UTC"),
        side="long",
        edge_per_risk=2.0,
        heat_per_risk=0.4,
        gross_per_risk=4.0,
        source_id="core:1",
    )
    assert apply_trade_guard([base], {"core:1": 0}, {0}, mode="block") == []
    halved = apply_trade_guard([base], {"core:1": 0}, {0}, mode="half")
    assert len(halved) == 1
    assert halved[0].edge_per_risk == 1.0
    assert halved[0].heat_per_risk == 0.2
    assert halved[0].gross_per_risk == 2.0


def test_pyramid_block_preserves_initial_core_and_swing_entries() -> None:
    initial = TargetTrade(
        lane="core",
        entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
        exit_time=pd.Timestamp("2024-01-02", tz="UTC"),
        side="long",
        edge_per_risk=1.0,
        heat_per_risk=0.4,
        gross_per_risk=4.0,
        source_id="core:initial",
    )
    pyramid = replace_target(initial, heat=0.3, source_id="core:pyramid")
    swing = replace_target(initial, lane="swing", heat=1.0, source_id="swing:1")
    guarded = apply_pyramid_block(
        [initial, pyramid, swing],
        {"core:initial": 1, "core:pyramid": 1, "swing:1": 1},
        {1},
    )
    assert [trade.source_id for trade in guarded] == ["core:initial", "swing:1"]


def replace_target(
    trade: TargetTrade,
    *,
    lane: str | None = None,
    heat: float | None = None,
    source_id: str,
) -> TargetTrade:
    return TargetTrade(
        lane=lane or trade.lane,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        side=trade.side,
        edge_per_risk=trade.edge_per_risk,
        heat_per_risk=heat if heat is not None else trade.heat_per_risk,
        gross_per_risk=trade.gross_per_risk,
        source_id=source_id,
    )


def test_reversal_lane_enters_next_bar_and_assumes_stop_before_target() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="30min", tz="UTC")
    bars = pd.DataFrame({
        "open": [100.0, 100.0, 100.0, 100.0],
        "high": [100.2, 102.0, 100.2, 100.2],
        "low": [99.8, 98.0, 99.8, 99.8],
        "close": [100.0, 100.0, 100.0, 100.0],
        "atr14": [1.0, 1.0, 1.0, 1.0],
    }, index=idx)
    signals = pd.DataFrame({
        "entry_time": [idx[0]],
        "side": ["long"],
        "cluster": [2],
        "source_id": ["core:7"],
    })
    trades = simulate_reversal_lane(signals, bars, harmful_clusters={2})
    assert len(trades) == 1
    trade = trades[0]
    assert trade.entry_time == idx[1]
    assert trade.side == "short"
    assert trade.edge_per_risk < -1.0  # both hit; conservative stop wins + costs
