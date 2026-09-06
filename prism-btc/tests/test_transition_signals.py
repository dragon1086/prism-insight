"""Synthetic-only causal and contract tests, independent of financial outcomes."""
import json

import numpy as np
import pytest

from analysis import transition_signals as t


def bars(days=12):
    ts = np.arange(0, days * t.DAY, t.BAR)
    x = np.arange(len(ts))
    close = 100 + 2 * np.sin(x / 61) + .5 * np.sin(x / 17)
    return np.column_stack((ts, close, close + .2, close - .2, close))


def ready():
    state = t._Episode("test")
    state.completed(t.HALF, True)
    return state


def test_squeeze_is_strict_recent_three_median_vs_prior_three():
    values = [.5, .6, .7, .2, .25, .1]
    assert t._compression(values, values).tolist() == [False] * 5 + [True]
    assert not t._compression([.1] * 6, [.1] * 6)[-1]
    assert not t._compression([.5] * 3 + [.3] * 3, values)[-1]


@pytest.mark.parametrize("raw", [[1.] * 6, [1.] * 3 + [2.] * 3])
def test_atr_increase_alone_does_not_arm_compression(raw):
    normalized = [.5] * 3 + [.2] * 3
    assert not t._compression(normalized, raw)[-1]
    assert t._compression(normalized, [1.] * 3 + [.8] * 3)[-1]


def test_cross_cannot_supply_own_precompression():
    state = t._Episode("test")
    assert state.observe(t.HALF, .2, -.1, .1, .2) is None
    state.completed(t.HALF, True)
    assert state.observe(2 * t.HALF, .3, .2, .1, .3) is None
    assert state.state == "READY"


@pytest.mark.parametrize("direction", [-1, 1])
def test_cross_can_emit_first_divergence_and_consumes_once(direction):
    state = ready()
    event = state.observe(2 * t.HALF, direction * .2, -direction * .1, direction * .1, direction * .2)
    assert event["direction"] == direction
    assert event["squeeze_at"] < event["cross_at"]
    assert state.state == "CONSUMED"
    assert state.observe(3 * t.HALF, direction * .3, direction * .2, direction * .1, direction * .3) is None


def test_rearm_requires_later_closed_noncompression_then_new_squeeze():
    state = ready()
    state.observe(2 * t.HALF, .2, -.1, .1, .2)
    state.completed(2 * t.HALF, False)
    assert state.state == "CONSUMED"
    state.completed(3 * t.HALF, True)
    assert state.state == "CONSUMED"
    state.completed(4 * t.HALF, False)
    assert state.state == "WAIT"
    state.completed(5 * t.HALF, True)
    assert state.state == "READY"
    assert len(state.records) == 2


def test_ready_refreshes_same_episode_but_cross_cannot_use_stale_squeeze():
    state = ready()
    identifier = state.current["episode_id"]
    state.completed(2 * t.HALF, True)
    assert state.current["episode_id"] == identifier
    assert state.current["squeeze_at"] == 2 * t.HALF
    assert state.observe(7 * t.HALF, .2, -.1, .1, .2) is None
    assert state.state == "READY"


@pytest.mark.parametrize("next_gap,reason", [(-.1, "OPPOSITE_CROSS"), (0., "NEUTRALIZED")])
def test_pending_cross_invalidates_on_opposite_or_neutral(next_gap, reason):
    state = ready()
    assert state.observe(2 * t.HALF, .05, -.1, .1, .05) is None
    assert state.state == "CROSSED"
    assert state.observe(2 * t.HALF + t.BAR, next_gap, .05, -.1, next_gap) is None
    assert state.current["reason"] == reason


@pytest.mark.parametrize("offset,emits", [(90 * 60_000, True), (90 * 60_000 + t.BAR, False)])
def test_expiry_uses_absolute_cross_time_not_observation_count(offset, emits):
    state = ready()
    cross_at = 2 * t.HALF
    state.observe(cross_at, .05, -.1, .1, .05)
    # More 5m observations cannot refresh or shrink the 90m deadline.
    for elapsed in range(t.BAR, offset, t.BAR):
        state.observe(cross_at + elapsed, .05, .05, .01, .05)
        if elapsed % t.HALF == 0:
            state.completed(cross_at + elapsed, True)
    result = state.observe(cross_at + offset, .2, .05, .1, .2)
    assert (result is not None) == emits
    assert state.current["cross_at"] == cross_at


def test_atr_shrink_and_wrong_ma_slope_do_not_generate_divergence():
    state = ready()
    state.observe(2 * t.HALF, .05, -.1, .1, .05)
    assert state.observe(2 * t.HALF + t.BAR, .05, .05, .1, .2) is None
    assert state.observe(2 * t.HALF + 2 * t.BAR, .2, .05, -.1, .2) is None
    assert state.state == "CROSSED"


@pytest.mark.parametrize("direction", [-1, 1])
def test_confidence_requires_current_alignment_and_direction_specific_freshness(direction):
    now = 10 * t.DAY
    crosses = {name: {direction: now - t.HALF, -direction: now} for name in ("1h", "4h")}
    bg = {"1h": direction, "4h": direction}
    assert t._confidence(direction, bg, crosses, now)[:2] == ("high", 1.)
    crosses["1h"][direction] = now - 2 * 3_600_000 - 1
    assert t._confidence(direction, bg, crosses, now)[:2] == ("medium", .5)
    assert t._confidence(direction, {"1h": -direction, "4h": 0}, crosses, now)[:2] == ("low", .25)
    crosses["1h"][direction] = None
    assert t._confidence(direction, bg, crosses, now)[2]["1h"] is None


@pytest.mark.parametrize("period", [t.HALF, t.PERIODS["1h"], t.PERIODS["4h"]])
def test_forming_boundaries_equal_closed_and_interiors_use_previous_completions(period):
    data = bars()
    frame = t._features(data, period)
    snapshots = t._snapshots(data, frame, period, True)
    width = period // t.BAR
    for name in ("ma10", "ma35", "atr14", "close"):
        np.testing.assert_array_equal(snapshots[name][width - 1::width], frame[name].to_numpy())
    completed_index = 40
    i = (completed_index + 1) * width
    for count, name in ((10, "ma10"), (35, "ma35")):
        expected = (frame.close.iloc[completed_index - count + 2:completed_index + 1].sum() + data[i, 4]) / count
        assert snapshots[name][i] == pytest.approx(expected)
    assert snapshots["atr14"][i] == frame.atr14.iloc[completed_index]


@pytest.mark.parametrize("direction", [-1, 1])
def test_phase_contracts_only_same_direction_and_threshold(direction):
    gaps = direction * np.array([.4, .3, .2])
    result = t._phase(gaps, gaps, 2)
    assert result[0 if direction == 1 else 1]
    assert not t._phase(gaps, gaps / 10, 2)[0 if direction == 1 else 1]
    assert not t._phase(direction * np.array([.4, .2, .3]), gaps, 2)[0 if direction == 1 else 1]


def test_tapes_are_causal_json_safe_and_x2_changes_only_risk_share():
    data = bars(14)
    cutoff = 12 * t.DAY
    prefix = t.build_transition_tapes(data[:12 * 288], 0)
    full = t.build_transition_tapes(data, 0)
    data[12 * 288:, 1:] *= 3
    changed = t.build_transition_tapes(data, 0)
    for variant in prefix["variants"]:
        p = prefix["variants"][variant]
        for other in (full, changed):
            actual = other["variants"][variant]
            assert p["signals"] == [s for s in actual["signals"] if s["available_at"] <= cutoff]
            assert p["contexts"] == {ts: c for ts, c in actual["contexts"].items() if ts <= cutoff}
    x1, x2 = full["variants"]["X1"], full["variants"]["X2"]
    assert x1["signals"], "synthetic fixture must exercise transitions"
    assert x1["contexts"] == x2["contexts"]
    assert len(x1["signals"]) == len(x2["signals"])
    for a, b in zip(x1["signals"], x2["signals"]):
        assert a == dict(b, risk_share=1.)
        assert a["metadata"]["squeeze_at"] < a["metadata"]["cross_at"] <= a["available_at"]
        assert a["max_hold_ms"] == 7_200_000
        assert .006 * a["reference_price"] <= a["stop_distance"] <= .015 * a["reference_price"]
    x3, x4 = full["variants"]["X3"], full["variants"]["X4"]
    assert x3["signals"] and len(x3["signals"]) == len(x4["signals"])
    assert x3["contexts"] is x4["contexts"]
    for a, b in zip(x3["signals"], x4["signals"]):
        assert a == dict(b, risk_share=a["risk_share"])
        assert b["risk_share"] == a["metadata"]["turn_risk_share"]
    assert all(s in x1["signals"] for s in full["variants"]["X1A"]["signals"])
    for variant in full["variants"].values():
        assert len({s["signal_id"] for s in variant["signals"]}) == len(variant["signals"])
    json.dumps(full, allow_nan=False)


def test_forming_trails_use_latest_closed_anchor_and_phase_timestamp():
    data = bars()
    tape = t.build_transition_tapes(data, 0)
    frame = t._features(data, t.HALF)
    ts = 10 * t.DAY + t.BAR
    context = tape["variants"]["X3"]["contexts"][ts]["C"]
    row = frame[frame.available_at <= ts].iloc[-1]
    for side, sign in (("long", -1), ("short", 1)):
        anchor = row.ma35 if context[f"trend_{side}"] else row.ma10
        assert context[f"trail_{side}"] == anchor + sign * .25 * row.atr14
    assert context["phase_at"] == row.available_at < ts


def test_forming_atr_does_not_read_current_unfinished_tf_high():
    data = bars()
    period = t.PERIODS["4h"]
    i = 10 * 288 + 10
    original = t._snapshots(data, t._features(data, period), period, True)
    data[i, 2] *= 10
    changed = t._snapshots(data, t._features(data, period), period, True)
    for key in ("ma10", "ma35", "atr14", "gap", "norm"):
        np.testing.assert_array_equal(original[key][:i + 1], changed[key][:i + 1])
    boundary_index = ((i // 48) + 1) * 48 - 1
    assert changed["atr14"][boundary_index] > original["atr14"][boundary_index]


@pytest.mark.parametrize("hour_age,four_age,grade", [
    (2 * 3_600_000, 4 * 3_600_000, "high"),
    (2 * 3_600_000 + 1, 4 * 3_600_000, "medium"),
    (2 * 3_600_000, 4 * 3_600_000 + 1, "medium"),
])
def test_htf_freshness_inclusive_absolute_deadlines(hour_age, four_age, grade):
    now = 20 * t.DAY
    crosses = {"1h": {1: now - hour_age, -1: now}, "4h": {1: now - four_age, -1: now}}
    assert t._confidence(1, {"1h": 1, "4h": 1}, crosses, now)[0] == grade


def test_forming_previous_snapshot_cross_not_repeated_previous_closed_cross():
    gaps = [-.2, .1, .2, .3, -.1, -.2]
    assert [t._cross(a, b) for a, b in zip(gaps, gaps[1:])] == [1, 0, 0, -1, 0]


def test_cross_requires_directional_ma10_slope():
    state = ready()
    assert state.observe(2 * t.HALF, .2, -.1, -.1, .2) is None
    assert state.state == "READY"


@pytest.mark.parametrize("period", [t.PERIODS["1h"], t.PERIODS["4h"]])
def test_native_baseline_changes_only_after_boundary_without_double_count(period):
    data = bars()
    frame = t._features(data, period)
    snapshots = t._snapshots(data, frame, period, True)
    native = t._native_deltas(data, frame, snapshots)
    width = period // t.BAR
    k = 40
    boundary_i = (k + 1) * width - 1
    assert native["native_baseline_at"][boundary_i] == frame.available_at.iloc[k - 1]
    assert native["ma10_delta"][boundary_i] == frame.ma10.iloc[k] - frame.ma10.iloc[k - 1]
    assert native["gap_delta"][boundary_i] == ((frame.ma10 - frame.ma35).iloc[k] - (frame.ma10 - frame.ma35).iloc[k - 1])
    assert native["native_baseline_at"][boundary_i + 1] == frame.available_at.iloc[k]
    assert native["ma10_delta"][boundary_i + 1] == snapshots["ma10"][boundary_i + 1] - frame.ma10.iloc[k]


@pytest.mark.parametrize("direction", [-1, 1])
def test_native_turn_needs_completed_rearm_and_ignores_within_bar_oscillation(direction):
    turn = t._NativeTurn()
    turn.observe(t.BAR, direction, direction, False)
    assert turn.started[direction] is None  # No completed nonturn observation yet.
    turn.observe(t.PERIODS["1h"], 0., 0., True)
    start = t.PERIODS["1h"] + t.BAR
    turn.observe(start, direction, direction, False)
    assert turn.started[direction] == start
    turn.observe(start + t.BAR, -direction, -direction, False)
    assert not turn.turning[direction]
    turn.observe(start + 2 * t.BAR, direction, direction, False)
    assert turn.turning[direction] and turn.started[direction] == start
    turn.observe(2 * t.PERIODS["1h"], direction, direction, True)
    assert turn.started[direction] == start
    turn.observe(3 * t.PERIODS["1h"], 0., 0., True)
    turn.observe(3 * t.PERIODS["1h"] + t.BAR, direction, direction, False)
    assert turn.started[direction] == 3 * t.PERIODS["1h"] + t.BAR


def test_unchanged_price_within_native_bar_cannot_refresh_turn_start():
    data = bars()
    begin = 10 * 288
    data[begin:begin + 12, 1:] = np.array([105., 105.2, 104.8, 105.])
    frame = t._features(data, t.PERIODS["1h"])
    snapshot = t._snapshots(data, frame, t.PERIODS["1h"], True)
    native = t._native_deltas(data, frame, snapshot)
    # All eleven unfinished snapshots have the same completed native baseline.
    np.testing.assert_array_equal(native["ma10_delta"][begin:begin + 11],
                                  np.repeat(native["ma10_delta"][begin], 11))
    np.testing.assert_array_equal(native["gap_delta"][begin:begin + 11],
                                  np.repeat(native["gap_delta"][begin], 11))


@pytest.mark.parametrize("direction", [-1, 1])
def test_native_turn_grade_requires_current_turn_and_nonrefreshed_fresh_start(direction):
    trackers = {name: t._NativeTurn() for name in ("1h", "4h")}
    for turn in trackers.values():
        turn.observe(t.DAY, 0., 0., True)
        turn.observe(t.DAY + t.BAR, direction, direction, False)
    ts = t.DAY + t.BAR
    assert t._turn_confidence(direction, trackers, ts) == ("high", 1.)
    assert t._turn_confidence(direction, trackers, ts + 2 * 3_600_000) == ("high", 1.)
    assert t._turn_confidence(direction, trackers, ts + 2 * 3_600_000 + 1) == ("medium", .5)
    trackers["1h"].observe(ts + t.BAR, -direction, -direction, False)
    assert t._turn_confidence(direction, trackers, ts + t.BAR) == ("medium", .5)
    assert trackers["1h"].started[direction] == ts
    trackers["4h"].observe(ts + t.BAR, 0., 0., False)
    assert t._turn_confidence(direction, trackers, ts + t.BAR) == ("low", .25)


def test_missing_native_history_cannot_arm_a_turn():
    turn = t._NativeTurn()
    turn.observe(t.DAY, np.nan, np.nan, True)
    turn.observe(t.DAY + t.BAR, 1., 1., False)
    assert turn.started[1] is None and not turn.armed[1]


def test_phase_validity_uses_latest_closed_direction_and_never_future_close():
    data = bars()
    tape = t.build_transition_tapes(data, 0)
    frame = t._features(data, t.HALF)
    gaps = (frame.ma10 - frame.ma35).to_numpy()
    crosses = [i for i in range(35, len(frame)) if gaps[i - 1] * gaps[i] < 0]
    assert crosses, "synthetic fixture must have a closed direction change"
    for index in crosses[:4]:
        at = int(frame.available_at.iloc[index])
        before = tape["variants"]["X3"]["contexts"][at - t.BAR]["C"]
        closed = tape["variants"]["X3"]["contexts"][at]["C"]
        assert before["phase_at"] == at - t.HALF
        assert closed["phase_at"] == at
        assert before["phase_valid_long"] == bool(gaps[index - 1] > 0)
        assert before["phase_valid_short"] == bool(gaps[index - 1] < 0)
        assert closed["phase_valid_long"] == bool(gaps[index] > 0)
        assert closed["phase_valid_short"] == bool(gaps[index] < 0)


def test_zero_closed_gap_invalidates_both_directions_but_warmup_is_unknown():
    data = bars(2)
    data[:, 1:] = np.array([100., 100.2, 99.8, 100.])
    tape = t.build_transition_tapes(data, 0)
    for variant in ("X1", "X3", "X4"):
        contexts = tape["variants"][variant]["contexts"]
        warmup = contexts[t.HALF]["C"]
        assert "phase_valid_long" not in warmup and "phase_valid_short" not in warmup
        valid = contexts[35 * t.HALF]["C"]
        assert valid["phase_valid_long"] is False and valid["phase_valid_short"] is False
        assert valid["phase_at"] == 35 * t.HALF


@pytest.mark.parametrize("warmup", [-1, True, .5])
def test_invalid_warmup_rejected(warmup):
    with pytest.raises(ValueError):
        t.build_transition_tapes(bars(1), warmup)
