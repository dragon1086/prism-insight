from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from analysis.round9_target_return import (
    PortfolioLimits,
    TargetTrade,
    admission_scale,
    drawdown_risk_multiplier,
    simulate_portfolio,
    target_cagr,
)


def _ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def test_1590_pct_target_requires_about_102_8_pct_cagr() -> None:
    assert target_cagr(1590.0, 4.0) == pytest.approx(1.02755, rel=1e-4)


@pytest.mark.parametrize(
    ("equity", "expected"),
    [
        (100.0, 1.00),
        (90.01, 1.00),
        (90.0, 0.75),
        (85.0, 0.50),
        (80.0, 0.25),
        (75.0, 0.00),
    ],
)
def test_drawdown_governor_uses_only_current_high_watermark(
    equity: float,
    expected: float,
) -> None:
    assert drawdown_risk_multiplier(equity, 100.0) == expected


def test_admission_scale_enforces_heat_and_gross_caps() -> None:
    limits = PortfolioLimits(max_heat=0.10, max_gross_leverage=8.0)
    admitted = admission_scale(
        open_heat=0.08,
        open_gross=7.0,
        requested_heat=0.04,
        requested_gross=4.0,
        limits=limits,
    )
    # Heat leaves 0.02/0.04=50%, gross leaves 1/4=25%; stricter cap wins.
    assert admitted == pytest.approx(0.25)


def test_shared_equity_simulator_scales_overlapping_second_lane() -> None:
    limits = PortfolioLimits(max_heat=0.10, max_gross_leverage=8.0)
    trades = [
        TargetTrade(
            lane="core",
            entry_time=_ts("2024-01-01"),
            exit_time=_ts("2024-01-10"),
            side="long",
            edge_per_risk=1.0,
            heat_per_risk=1.0,
            gross_per_risk=50.0,
        ),
        TargetTrade(
            lane="swing",
            entry_time=_ts("2024-01-02"),
            exit_time=_ts("2024-01-05"),
            side="long",
            edge_per_risk=1.0,
            heat_per_risk=1.0,
            gross_per_risk=50.0,
        ),
    ]
    result = simulate_portfolio(
        trades,
        lane_risk={"core": 0.08, "swing": 0.08},
        limits=limits,
        initial_equity=10_000.0,
        use_drawdown_governor=False,
    )

    assert result.cap_scaled_entries == 1
    assert result.max_open_heat <= limits.max_heat + 1e-12
    assert result.max_open_gross <= limits.max_gross_leverage + 1e-12
    # First trade earns 8%; second is limited to 2% heat by the shared cap.
    assert result.final_equity == pytest.approx(11_000.0)


def test_realized_drawdown_reduces_only_later_entries() -> None:
    trades = [
        TargetTrade(
            lane="core",
            entry_time=_ts("2024-01-01"),
            exit_time=_ts("2024-01-02"),
            side="long",
            edge_per_risk=-3.0,
            heat_per_risk=1.0,
            gross_per_risk=10.0,
        ),
        TargetTrade(
            lane="core",
            entry_time=_ts("2024-01-03"),
            exit_time=_ts("2024-01-04"),
            side="long",
            edge_per_risk=1.0,
            heat_per_risk=1.0,
            gross_per_risk=10.0,
        ),
    ]
    result = simulate_portfolio(
        trades,
        lane_risk={"core": 0.04},
        initial_equity=10_000.0,
        use_drawdown_governor=True,
    )

    # First trade loses 12%, so only the later entry receives the 0.75 scale.
    assert result.governor_scaled_entries == 1
    assert result.final_equity == pytest.approx(9_064.0)


def test_same_bar_swing_stop_enters_before_its_exit() -> None:
    trade = TargetTrade(
        lane="swing",
        entry_time=_ts("2024-01-01 04:00"),
        exit_time=_ts("2024-01-01 04:00"),
        side="long",
        edge_per_risk=-1.0,
        heat_per_risk=1.0,
        gross_per_risk=10.0,
    )
    result = simulate_portfolio(
        [trade],
        lane_risk={"swing": 0.01},
        initial_equity=10_000.0,
        use_drawdown_governor=False,
    )
    assert result.admitted_trades == 1
    assert result.final_equity == pytest.approx(9_900.0)


def test_compact_result_is_json_serializable_with_numpy_trade_inputs() -> None:
    trade = TargetTrade(
        lane="core",
        entry_time=_ts("2024-01-01"),
        exit_time=_ts("2024-01-02"),
        side="long",
        edge_per_risk=np.float64(1.0),
        heat_per_risk=np.float64(1.0),
        gross_per_risk=np.float64(10.0),
    )
    result = simulate_portfolio(
        [trade],
        lane_risk={"core": 0.01},
        use_drawdown_governor=False,
    )
    json.dumps(result.compact())
