"""Synthetic signal tests: no historical outcomes or parameter selection."""
import numpy as np
import pytest

from analysis import adaptive_signals as a
from core import swing
from engine.indicators import add_indicators


def bars(days=100):
    ts = np.arange(0, days * a.DAY, a.BAR)
    close = 100 + .001 * np.arange(len(ts)) + 4 * np.sin(np.arange(len(ts)) / 220)
    return np.column_stack((ts, close, close + .1, close - .1, close))


def feature(**updates):
    return dict(ma10=110., ma35=100., atr14=2., close=115., prior_high8=114., prior_low8=90.) | updates


@pytest.mark.parametrize("mutation,message", [
    (lambda x: x[:-1], "full UTC"),
    (lambda x: x[1:], "full UTC"),
    (lambda x: np.delete(x, 12, axis=0), "contiguous"),
    (lambda x: np.vstack((x[:12], x[11:])), "contiguous"),
    (lambda x: x[:, :4], "Nx5"),
])
def test_malformed_coverage(mutation, message):
    with pytest.raises(ValueError, match=message):
        a.build_signal_tape(mutation(bars(1)), 0)


@pytest.mark.parametrize("column,value", [(1, np.nan), (2, np.inf), (1, -1), (2, 1), (3, 999), (0, .5)])
def test_bad_values(column, value):
    data = bars(1)
    data[10, column] = value
    with pytest.raises(ValueError):
        a.build_signal_tape(data, 0)


@pytest.mark.parametrize("warmup", [-1, True, .5])
def test_invalid_warmup(warmup):
    with pytest.raises(ValueError, match="warmup"):
        a.build_signal_tape(bars(1), warmup)


def test_aggregation_right_edge_prior_window_and_exact_indicator_formula():
    data = bars(10)
    frame = a._features(data, a.PERIODS["30m"])
    assert frame.available_at.iloc[0] == 6 * a.BAR
    assert frame.available_at.iloc[-1] == 10 * a.DAY
    assert frame.open.iloc[0] == data[0, 1]
    assert frame.close.iloc[0] == data[5, 4]
    assert frame.high.iloc[0] == max(data[:6, 2])
    assert frame.low.iloc[0] == min(data[:6, 3])
    assert frame.prior_high8.iloc[8] == max(frame.high.iloc[:8])
    assert frame.prior_low8.iloc[8] == min(frame.low.iloc[:8])
    assert np.isnan(frame.prior_high8.iloc[7])
    expected = add_indicators(frame[["open", "high", "low", "close"]])
    for name in ("ma10", "ma35", "atr14"):
        np.testing.assert_array_equal(frame[name], expected[name])


def test_future_append_and_mutation_preserve_prefix():
    data = bars(100)
    prefix = a.build_signal_tape(data[:95 * 288])
    full = a.build_signal_tape(data)
    boundary = 95 * a.DAY
    assert prefix["signals"] == [s for s in full["signals"] if s["available_at"] <= boundary]
    assert prefix["contexts"] == {t: c for t, c in full["contexts"].items() if t <= boundary}
    data[95 * 288:, 1:] *= 3
    changed = a.build_signal_tape(data)
    assert prefix["signals"] == [s for s in changed["signals"] if s["available_at"] <= boundary]
    assert prefix["contexts"] == {t: c for t, c in changed["contexts"].items() if t <= boundary}
    assert all(s["available_at"] >= 90 * a.DAY for s in full["signals"])
    assert len({s["signal_id"] for s in full["signals"]}) == len(full["signals"])
    assert full["signals"] == sorted(full["signals"], key=lambda s: (s["available_at"], s["lane"] != "S"))


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("daily_equal", [False, True])
def test_swing_exact_core_entry_and_stop(direction, daily_equal):
    previous = feature(ma10=100.)
    four = feature(ma10=100 + direction, close=100 + 10 * direction)
    daily = feature(ma10=100 if daily_equal else 100 + direction)
    hour = feature(ma10=100 - direction)  # Opposite 1h must NOT hard-gate S.
    expected = swing.entry_side(swing.detect_cross(previous["ma10"], previous["ma35"], four["ma10"], four["ma35"]),
                                daily["ma10"], daily["ma35"], four["close"], four["ma35"])
    signal = a._swing_signal(a.DAY, previous, four, daily, hour)
    if expected is None:
        assert signal is None
    else:
        assert signal["direction"] == direction
        assert signal["stop_distance"] == abs(four["close"] - swing.stop_price(expected, four["close"], four["atr14"]))
        assert not signal["strong"]
        assert signal["max_hold_ms"] is None


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("strong", [False, True])
@pytest.mark.parametrize("atr", [.01, 1., 100.])
def test_scalp_weak_strong_stop_clamps(direction, strong, atr):
    hour = feature(ma10=100 + direction, close=100 + direction)
    four = feature(ma10=100 + direction * (10 if strong else 1))
    half = feature(close=100., prior_high8=99 if direction == 1 else 110,
                   prior_low8=101 if direction == -1 else 90, atr14=atr)
    result = a._scalp_signal(a.DAY, half, hour, four)
    assert result["direction"] == direction
    assert result["strong"] is strong
    assert result["stop_distance"] == pytest.approx(np.clip(atr * (1 if strong else .5), .6, 1.5))
    assert result["max_hold_ms"] == 7_200_000


def test_scalp_strict_breakout_and_alignment_not_daily_gate():
    assert a._scalp_signal(0, feature(close=114.), feature(), feature()) is None
    assert a._scalp_signal(0, feature(), feature(ma10=100.), feature()) is None
    assert a._scalp_signal(0, feature(), feature(ma10=90.), feature()) is None
    assert a._scalp_signal(0, feature(prior_high8=np.nan), feature(), feature()) is None
    assert a._scalp_signal(0, feature(), feature(), feature()) is not None


def test_context_closed_asof_and_core_exit_parity():
    data = bars(100)
    tape = a.build_signal_tape(data)
    frames = {n: a._features(data, p) for n, p in a.PERIODS.items()}
    for ts, contexts in tape["contexts"].items():
        latest = {n: f[f.available_at <= ts].iloc[-1] for n, f in frames.items() if n in ("1h", "4h")}
        direction, strong = a._background(latest["1h"], latest["4h"])
        assert contexts["C"] == dict(exit_long=False, exit_short=False,
                                     trend_long=strong and direction == 1, trend_short=strong and direction == -1)
        assert contexts["S"]["trend_long"] == contexts["C"]["trend_long"]
        assert contexts["S"]["trend_short"] == contexts["C"]["trend_short"]
        f = latest["4h"]
        assert contexts["S"]["exit_long"] == swing.rule_exit_due("long", f.close, f.ma35)
        assert contexts["S"]["exit_short"] == swing.rule_exit_due("short", f.close, f.ma35)


def test_swing_background_updates_between_four_hour_closes(monkeypatch):
    original = a._features
    def controlled(data, period):
        frame = original(data, period)
        frame["ma10"], frame["ma35"], frame["atr14"], frame["close"] = 110., 100., 2., 115.
        if period == a.PERIODS["1h"]:
            frame.loc[frame.available_at >= 5 * a.PERIODS["1h"], "ma10"] = 90.
        if period == a.PERIODS["4h"]:
            # This future 8h close must not change the 5h rule-exit context.
            frame.loc[frame.available_at >= 8 * a.PERIODS["1h"], "close"] = 90.
        return frame
    monkeypatch.setattr(a, "_features", controlled)
    tape = a.build_signal_tape(bars(1), 0)
    hour = a.PERIODS["1h"]
    assert tape["contexts"][4 * hour]["S"]["trend_long"]
    assert tape["contexts"][4 * hour + hour // 2]["S"]["trend_long"]
    assert not tape["contexts"][5 * hour]["S"]["trend_long"]
    assert not tape["contexts"][5 * hour]["S"]["exit_long"]
    assert tape["contexts"][8 * hour]["S"]["exit_long"]
    assert not any(s["lane"] == "S" and s["available_at"] == 5 * hour for s in tape["signals"])


def test_whole_tape_swing_is_exact_core_mirror():
    data = bars(100)
    tape = a.build_signal_tape(data, 35)
    four, daily = a._features(data, a.PERIODS["4h"]), a._features(data, a.DAY)
    expected = []
    for i in range(1, len(four)):
        prev, now = four.iloc[i - 1], four.iloc[i]
        if now.available_at < 35 * a.DAY:
            continue
        day = daily[daily.available_at <= now.available_at].iloc[-1]
        cross = swing.detect_cross(prev.ma10, prev.ma35, now.ma10, now.ma35)
        side = swing.entry_side(cross, day.ma10, day.ma35, now.close, now.ma35)
        if side:
            expected.append((int(now.available_at), 1 if side == "long" else -1))
    actual = [(s["available_at"], s["direction"]) for s in tape["signals"] if s["lane"] == "S"]
    assert actual == expected
    assert actual  # Test is not vacuous.


def test_long_rolling_guard_fails_closed(monkeypatch):
    original = a.pd.Series.rolling
    def broken(series, *args, **kwargs):
        result = original(series, *args, **kwargs)
        if len(series) == 40_000:
            result.mean = lambda: a.pd.Series(np.full(40_000, np.nan))
        return result
    monkeypatch.setattr(a.pd.Series, "rolling", broken)
    with pytest.raises(RuntimeError, match="rolling compatibility"):
        a.build_signal_tape(bars(1), 0)
