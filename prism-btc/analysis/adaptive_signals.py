"""Outcome-independent, closed-bar signal tape for the adaptive research contract.

This module neither reads databases nor models positions, fills or profitability.
All timestamps are UTC milliseconds; input timestamps identify five-minute opens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import swing
from engine.config import TS_MIN
from engine.indicators import add_indicators

BAR = 300_000
DAY = 86_400_000
PERIODS = {"30m": 6 * BAR, "1h": 12 * BAR, "4h": 48 * BAR, "1d": DAY}


def _validate(bars, warmup_days):
    if isinstance(warmup_days, bool) or not isinstance(warmup_days, int) or warmup_days < 0:
        raise ValueError("warmup_days must be a nonnegative integer")
    data = np.asarray(bars, dtype=float)
    if data.ndim != 2 or data.shape[1] != 5 or len(data) == 0:
        raise ValueError("bars must be a nonempty Nx5 array")
    if not np.isfinite(data).all():
        raise ValueError("nonfinite bar")
    ts = data[:, 0]
    if np.any(ts != np.floor(ts)) or np.any(ts < 0) or np.any(ts >= 2**53):
        raise ValueError("timestamps must be exact nonnegative integer milliseconds")
    if ts[0] % DAY or (ts[-1] + BAR) % DAY:
        raise ValueError("coverage must contain full UTC days")
    if np.any(np.diff(ts) != BAR):
        raise ValueError("bars must be contiguous unique five-minute opens")
    o, h, low, c = data[:, 1:].T
    if np.any(data[:, 1:] <= 0) or np.any(h < np.maximum(o, c)) or np.any(low > np.minimum(o, c)):
        raise ValueError("invalid OHLC")
    # Fail closed on the known long-series pandas/Python rolling incompatibility.
    probe = pd.Series(np.ones(40_000)).rolling(35, min_periods=35).mean().iloc[34:]
    if not np.isfinite(probe).all() or not np.all(probe == 1):
        raise RuntimeError("rolling compatibility failed; use the pinned .venv-bt runtime")
    return data


def _features(data, period):
    groups = data.reshape(-1, period // BAR, 5)
    frame = pd.DataFrame({
        "available_at": (groups[:, 0, 0] + period).astype(np.int64),
        "open": groups[:, 0, 1], "high": groups[:, :, 2].max(axis=1),
        "low": groups[:, :, 3].min(axis=1), "close": groups[:, -1, 4],
    })
    frame = add_indicators(frame)
    frame["prior_high8"] = frame.high.shift(1).rolling(8, min_periods=8).max()
    frame["prior_low8"] = frame.low.shift(1).rolling(8, min_periods=8).min()
    return frame


def _ready(row):
    return row is not None and all(np.isfinite(row[k]) for k in ("ma10", "ma35", "atr14")) and row["atr14"] > 0


def _direction(row):
    return int(row["ma10"] > row["ma35"]) - int(row["ma10"] < row["ma35"])


def _background(hour, four):
    if not _ready(hour) or not _ready(four):
        return 0, False
    direction = _direction(hour)
    aligned = direction != 0 and direction == _direction(four)
    strong = aligned and abs(four["ma10"] - four["ma35"]) / four["atr14"] >= TS_MIN
    strong = strong and direction * (hour["close"] - hour["ma35"]) > 0
    return (direction if aligned else 0), bool(strong)


def _signal(lane, ts, direction, reference, distance, strong):
    return {"signal_id": f"{lane}:{ts}:{direction:+d}", "lane": lane,
            "available_at": int(ts), "direction": int(direction),
            "reference_price": float(reference), "stop_distance": float(distance),
            "strong": bool(strong), "max_hold_ms": 7_200_000 if lane == "C" else None}


def _swing_signal(ts, previous, four, daily, hour):
    if not all(_ready(row) for row in (previous, four, daily)):
        return None
    cross = swing.detect_cross(previous["ma10"], previous["ma35"], four["ma10"], four["ma35"])
    side = swing.entry_side(cross, daily["ma10"], daily["ma35"], four["close"], four["ma35"])
    if side is None:
        return None
    direction = 1 if side == "long" else -1
    background, strong = _background(hour, four)
    distance = abs(four["close"] - swing.stop_price(side, four["close"], four["atr14"]))
    return _signal("S", ts, direction, four["close"], distance, strong and background == direction)


def _scalp_signal(ts, half_hour, hour, four):
    if not _ready(half_hour):
        return None
    direction, strong = _background(hour, four)
    close = half_hour["close"]
    breakout = ((direction == 1 and close > half_hour["prior_high8"])
                or (direction == -1 and close < half_hour["prior_low8"]))
    if not breakout:
        return None
    distance = np.clip(half_hour["atr14"] * (1 if strong else .5), .006 * close, .015 * close)
    return _signal("C", ts, direction, close, distance, strong)


def build_signal_tape(bars, warmup_days=90):
    """Return deterministic signals, lane contexts and feature-availability metadata.

    The last right-boundary signal is retained even though it cannot be executed
    inside the supplied history. Consumers must enforce their execution horizon.
    Context values persist until replaced, separately for each lane.
    """
    data = _validate(bars, warmup_days)
    frames = {name: _features(data, period) for name, period in PERIODS.items()}
    rows = {name: frame.to_dict("records") for name, frame in frames.items()}
    timestamps = {name: frame.available_at.to_numpy() for name, frame in frames.items()}
    start = int(data[0, 0]) + warmup_days * DAY
    signals, contexts = [], {}
    for half_hour in rows["30m"]:
        ts = int(half_hour["available_at"])
        if ts < start:
            continue
        latest = {}
        indexes = {}
        for name in ("1h", "4h", "1d"):
            index = int(np.searchsorted(timestamps[name], ts, side="right")) - 1
            indexes[name] = index
            latest[name] = rows[name][index] if index >= 0 else None
        hour, four, daily = latest["1h"], latest["4h"], latest["1d"]
        background, strong = _background(hour, four)
        context = {"exit_long": False, "exit_short": False,
                   "trend_long": strong and background == 1,
                   "trend_short": strong and background == -1}
        contexts[ts] = {"C": context, "S": dict(context)}
        if _ready(four):
            contexts[ts]["S"].update(
                exit_long=swing.rule_exit_due("long", four["close"], four["ma35"]),
                exit_short=swing.rule_exit_due("short", four["close"], four["ma35"]))
        if ts % PERIODS["4h"] == 0:
            index = indexes["4h"]
            previous = rows["4h"][index - 1] if index > 0 else None
            signal = _swing_signal(ts, previous, four, daily, hour)
            if signal is not None:
                signals.append(signal)
        signal = _scalp_signal(ts, half_hour, hour, four)
        if signal is not None:
            signals.append(signal)
    return {"signals": signals, "contexts": contexts, "metadata": {
        "input_bars": len(data), "input_start": int(data[0, 0]),
        "input_end_exclusive": int(data[-1, 0]) + BAR, "warmup_days": warmup_days,
        "decision_start": start, "signal_counts": {lane: sum(s["lane"] == lane for s in signals) for lane in ("S", "C")},
        "features": {name: {"count": len(frame), "first_available_at": int(frame.available_at.iloc[0]),
                            "last_available_at": int(frame.available_at.iloc[-1]), "period_ms": PERIODS[name]}
                     for name, frame in frames.items()},
        "indicator_convention": "engine.indicators.add_indicators: SMA10/35, Wilder EWM ATR14 adjust=False",
        "availability": "complete UTC bars at right boundary; asof <= decision; breakout excludes current bar",
        "trend_permission": "aligned 1h/4h direction, 4h TS>=TS_MIN, 1h close on directional MA35 side",
        "position_state": "none; held/pending filtering belongs to replay", "ts_min": TS_MIN,
    }}
