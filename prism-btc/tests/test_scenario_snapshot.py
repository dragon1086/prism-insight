import json

import pandas as pd
import pytest

from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot


NOW = 1_800_000_000_000


def frames(now=NOW):
    history, provisional = {}, {}
    for tf, duration in TIMEFRAME_MS.items():
        start = now // duration * duration
        index = pd.to_datetime([start - i * duration for i in range(50, 0, -1)], unit="ms", utc=True)
        history[tf] = pd.DataFrame({"open": 100., "high": 102., "low": 98., "close": 100., "volume": 20.}, index=index)
        provisional[tf] = pd.DataFrame({"open": 100., "high": 105., "low": 99., "close": 104., "volume": 5.}, index=pd.to_datetime([start], unit="ms", utc=True))
    return history, provisional


def snapshot(history=None, provisional=None, now=NOW + 300_000, observed=None):
    h, p = frames(now)
    return build_scenario_snapshot(h if history is None else history, now,
                                   provisional_tf_data=p if provisional is None else provisional,
                                   observed_at_ms=now if observed is None else observed)


def test_primary_and_context_are_separate_json_safe_facts():
    result = snapshot()
    assert result["valid"]
    json.dumps(result, allow_nan=False)
    hour = result["timeframes"]["1h"]
    assert hour["role"] == "primary"
    assert result["timeframes"]["4h"]["role"] == "context"
    assert result["timeframes"]["5m"]["role"] == "execution"
    assert hour["confirmed"]["ma10"] == 100
    assert hour["forming"]["ma10"] == pytest.approx(100.4)
    assert hour["forming"]["price_position"] == "above"
    assert hour["forming"]["is_confirmed"] is False
    assert hour["forming"]["volume_projection"]["uncertainty_interval"] is None


def test_sequential_fetch_preserves_per_timeframe_observation():
    now = NOW + 300_000
    h, p = frames(now)
    observed = {tf: now-30_000 if tf == "30m" else now for tf in TIMEFRAME_MS}
    result = build_scenario_snapshot(h, now, provisional_tf_data=p, observed_at_by_tf_ms=observed)
    assert result["valid"]
    first = result["timeframes"]["30m"]["forming"]
    assert first["observed_at_ms"] == now-30_000
    assert first["observation_age_ms"] == 30_000
    assert first["volume_projection"]["projected_final"] == pytest.approx(5 * TIMEFRAME_MS["30m"] / first["elapsed_ms"])


def test_future_completed_bar_is_never_used_as_forming_data():
    h, p = frames(NOW + 300_000)
    baseline = snapshot(h, p)
    h["1h"] = pd.concat([h["1h"], p["1h"] * 10000])
    future = p["1h"].copy()
    future.index += pd.Timedelta(days=2)
    h["1h"] = pd.concat([h["1h"], future])
    assert snapshot(h, p) == baseline


def test_missing_provisional_cannot_be_inferred_from_history():
    h, p = frames(NOW + 300_000)
    h["1h"] = pd.concat([h["1h"], p["1h"]])
    result = snapshot(h, {})
    assert not result["valid"]
    assert result["timeframes"]["1h"]["forming"] is None


@pytest.mark.parametrize("delta,issue", [(1, "missing_or_future_observation_time"), (-120001, "stale_forming_snapshot")])
def test_observation_freshness(delta, issue):
    result = snapshot(observed=NOW + 300_000 + delta)
    assert not result["valid"]
    assert issue in result["timeframes"]["1h"]["issues"]


def test_missing_observation_timestamp():
    h, p = frames()
    result = build_scenario_snapshot(h, NOW, provisional_tf_data=p)
    assert not result["valid"]


def test_boundary_has_zero_elapsed_no_infinite_volume():
    now = NOW // 3_600_000 * 3_600_000
    result = snapshot(now=now)
    forming = result["timeframes"]["1h"]["forming"]
    assert forming["elapsed_ms"] == 0
    assert forming["remaining_ms"] == 3_600_000
    assert forming["volume_projection"]["projected_final"] is None
    json.dumps(result, allow_nan=False)


def test_old_provisional_not_promoted_after_close():
    now = NOW // 3_600_000 * 3_600_000
    h, p = frames(now - 1)
    result = snapshot(h, p, now=now)
    assert not result["valid"]
    assert result["timeframes"]["1h"]["forming"] is None
    assert "stale_confirmed_history" in result["timeframes"]["1h"]["issues"]


@pytest.mark.parametrize("problem", ["missing", "stale", "gap", "invalid", "short", "missing_column", "future"])
def test_incomplete_primary_not_valid(problem):
    h, p = frames(NOW + 300_000)
    if problem == "missing":
        del h["30m"]
    elif problem == "stale":
        h["30m"] = h["30m"].iloc[:-1]
    elif problem == "gap":
        h["30m"] = h["30m"].drop(h["30m"].index[20])
    elif problem == "invalid":
        p["30m"].loc[:, "close"] = float("nan")
    elif problem == "short":
        h["30m"] = h["30m"].tail(2)
    elif problem == "missing_column":
        h["30m"] = h["30m"].drop(columns="volume")
    else:
        p["30m"].index += pd.Timedelta(minutes=30)
    assert not snapshot(h, p)["valid"]


def test_missing_context_not_primary_gate_and_inputs_unchanged():
    h, p = frames(NOW + 300_000)
    before = h["1h"].copy(deep=True)
    del h["4h"]
    del p["4h"]
    assert snapshot(h, p)["valid"]
    pd.testing.assert_frame_equal(h["1h"], before)


def test_short_history_marks_uncertainty_and_no_ma():
    h, p = frames(NOW + 300_000)
    h["1h"] = h["1h"].tail(2)
    forming = snapshot(h, p)["timeframes"]["1h"]["forming"]
    assert forming["ma35"] is None
    assert forming["volume_projection"]["historical_final_mean"] is None


def test_volume_extrapolation_uses_observed_elapsed_not_new_clock():
    now = NOW // 3_600_000 * 3_600_000 + 600_000
    forming = snapshot(now=now, observed=now - 60_000)["timeframes"]["1h"]["forming"]
    assert forming["volume_projection"]["projected_final"] == pytest.approx(5 * 3_600_000 / 540_000)
    assert forming["observation_age_ms"] == 60_000
