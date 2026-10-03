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
    assert hour["forming"]["observation_kind"] == "observed"
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


def intrabar_fixture():
    start = NOW // TIMEFRAME_MS["1h"] * TIMEFRAME_MS["1h"]
    now = start + 12*60_000
    h, p = frames(now)
    index = pd.date_range(pd.to_datetime(start-8*TIMEFRAME_MS["1h"],unit="ms",utc=True),
                          periods=8*12+2, freq="5min")
    h["5m"] = pd.DataFrame(dict(open=100.,high=102.,low=98.,close=100.,volume=10.),index=index)
    return now, h, p


def test_constant_narrow_gap_is_compressed_without_converging():
    confirmed = snapshot()["timeframes"]["1h"]["confirmed"]
    assert confirmed["convergence_bars"] == 0
    assert confirmed["compression_bars"] == 16  # 50 rows minus 34 MA warmup.
    assert confirmed["compression_duration_ms"] == 16*TIMEFRAME_MS["1h"]
    assert confirmed["compression_gap_fraction_threshold"] == .0015


def test_same_progress_complete_profiles_and_acceleration():
    now,h,p = intrabar_fixture()
    forming = snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    profile = forming["volume_projection"]["same_progress_profile"]
    assert profile["status"] == "available"
    assert profile["sample_count"] == 8
    assert profile["matched_elapsed_ms"] == 600_000
    assert profile["observed_prefix_volume"] == 20
    assert profile["projected_final_median"] == 120
    assert profile["empirical_projection_range"] == [120,120]
    assert profile["calibrated_probability"] is False
    assert forming["volume_acceleration"]["ratio"] == 1


def test_future_5m_bars_do_not_change_primary_intrabar_features():
    now,h,p = intrabar_fixture()
    baseline = snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    future = h["5m"].tail(1).copy()
    future.index += pd.Timedelta(minutes=5)
    future.loc[:,"volume"] = 999999.
    h["5m"] = pd.concat([h["5m"],future])
    assert snapshot(h,p,now=now)["timeframes"]["1h"]["forming"] == baseline


def test_oldest_primary_observation_controls_5m_availability():
    now,h,p = intrabar_fixture()
    observed = {tf:now for tf in TIMEFRAME_MS}
    observed["30m"] = now-180_000  # At9m, the5-10m bucket was not confirmed.
    result = build_scenario_snapshot(h,now,provisional_tf_data=p,observed_at_by_tf_ms=observed,
                                     max_observation_age_ms=300_000)
    profile=result["timeframes"]["1h"]["forming"]["volume_projection"]["same_progress_profile"]
    assert profile["matched_elapsed_ms"] == 300_000
    assert profile["observed_prefix_volume"] == 10
    assert profile["available_at_ms"] == now-180_000


def test_incomplete_historical_window_excluded_not_padded():
    now,h,p = intrabar_fixture()
    h["5m"] = h["5m"].drop(h["5m"].index[5])
    forming=snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    assert forming["volume_projection"]["same_progress_profile"]["sample_count"] == 7
    assert forming["volume_acceleration"]["status"] == "available"


def test_recent_5m_gap_disables_acceleration():
    now,h,p = intrabar_fixture()
    h["5m"] = h["5m"].drop(h["5m"].index[-3])
    forming=snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    assert forming["volume_acceleration"]["status"] == "unavailable"
    assert forming["volume_acceleration"]["ratio"] is None


def test_missing_5m_preserves_linear_fallback_without_gating():
    now,h,p = intrabar_fixture()
    del h["5m"]
    del p["5m"]
    result=snapshot(h,p,now=now)
    assert result["valid"]
    forming=result["timeframes"]["1h"]["forming"]
    assert forming["volume_acceleration"]["status"] == "unavailable"
    assert forming["volume_projection"]["same_progress_profile"]["status"] == "unavailable"
    assert forming["volume_projection"]["method"] == "linear_elapsed_time_heuristic"


def test_zero_volume_is_not_infinite_acceleration_or_empirical_confidence():
    now,h,p=intrabar_fixture()
    h["5m"].loc[:,"volume"]=0.
    forming=snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    assert forming["volume_acceleration"]["ratio"] is None
    assert forming["volume_projection"]["same_progress_profile"]["status"] == "unavailable"
    json.dumps(forming,allow_nan=False)


def test_acceleration_measures_recent_volume_not_price():
    now,h,p=intrabar_fixture()
    h["5m"].iloc[-3:,h["5m"].columns.get_loc("volume")]=20.
    forming=snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]
    assert forming["volume_acceleration"]["ratio"] == 2
    assert forming["volume_acceleration"]["previous_15m_volume"] == 30
    assert forming["volume_acceleration"]["recent_15m_volume"] == 60


def test_empirical_projection_range_preserves_different_historical_paths():
    now,h,p=intrabar_fixture()
    # First complete hour: same prefix20 but remaining10 buckets volume20.
    h["5m"].iloc[2:12,h["5m"].columns.get_loc("volume")]=20.
    profile=snapshot(h,p,now=now)["timeframes"]["1h"]["forming"]["volume_projection"]["same_progress_profile"]
    assert profile["projected_final_median"] == 120
    assert profile["empirical_projection_range"] == [120,220]
