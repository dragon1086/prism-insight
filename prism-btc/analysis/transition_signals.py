"""Causal MA10/35 transition tapes; no positions, outcomes, or database access."""
from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.adaptive_signals import BAR, DAY, PERIODS, _features, _validate

HALF = PERIODS["30m"]


def _cross(previous, current):
    if not np.isfinite(previous) or not np.isfinite(current):
        return 0
    return int(previous <= 0 < current) - int(previous >= 0 > current)


def _snapshots(data, frame, period, forming):
    """Arrays at each closed 5m edge; boundary uses completed indicators exactly."""
    times = data[:, 0].astype(np.int64) + BAR
    ends = frame.available_at.to_numpy()
    indexes = np.searchsorted(ends, times, side="right") - 1
    valid = indexes >= 0
    safe = np.maximum(indexes, 0)
    out = {name: np.where(valid, frame[name].to_numpy()[safe], np.nan)
           for name in ("ma10", "ma35", "atr14", "close")}
    if forming:
        interior = times % period != 0
        for length, name in ((10, "ma10"), (35, "ma35")):
            sums = frame.close.rolling(length - 1, min_periods=length - 1).sum().to_numpy()
            values = (sums[safe] + data[:, 4]) / length
            out[name] = np.where(interior & valid, values, out[name])
        out["close"] = data[:, 4].copy()
    out["gap"] = out["ma10"] - out["ma35"]
    out["norm"] = np.divide(out["gap"], out["atr14"],
                            out=np.full(len(data), np.nan), where=out["atr14"] > 0)
    return out


def _compression(norm, raw_gap):
    magnitude = pd.Series(np.abs(norm))
    recent = magnitude.rolling(3, min_periods=3).median()
    prior = recent.shift(3)
    raw_recent = pd.Series(np.abs(raw_gap)).rolling(3, min_periods=3).median()
    return ((recent <= .25) & (recent < prior) & (raw_recent < raw_recent.shift(3))).to_numpy()


class _Episode:
    """One squeeze may emit at most once, even when the resulting order fails."""

    def __init__(self, variant):
        self.variant = variant
        self.state = "WAIT"
        self.current = None
        self.records = []
        self.consumed_at = None
        self.direction = 0

    def _consume(self, ts, reason):
        self.current.update(ended_at=int(ts), reason=reason)
        self.state, self.consumed_at = "CONSUMED", ts

    def observe(self, ts, gap, previous_gap, ma_delta, norm):
        """Evaluate BEFORE registering the current completed-bar squeeze."""
        cross = _cross(previous_gap, gap)
        if self.state == "CROSSED":
            if ts - self.current["cross_at"] > 90 * 60_000:
                self._consume(ts, "EXPIRED")
            elif self.direction * gap <= 0:
                self._consume(ts, "OPPOSITE_CROSS" if cross == -self.direction else "NEUTRALIZED")
        if self.state == "READY" and cross and cross * ma_delta > 0:
            age = ts - self.current["squeeze_at"]
            if 0 < age <= 2 * 3_600_000:
                self.state, self.direction = "CROSSED", cross
                self.current.update(cross_at=int(ts), direction=cross)
        if (self.state == "CROSSED" and self.direction * gap > 0
                and self.direction * norm >= .10
                and self.direction * (gap - previous_gap) > 0
                and self.direction * ma_delta > 0):
            event = dict(self.current)
            self._consume(ts, "EMITTED")
            return event
        return None

    def completed(self, ts, compressed):
        if self.state == "CONSUMED":
            if ts > self.consumed_at and not compressed:
                self.state = "WAIT"
            return
        if not compressed or self.state == "CROSSED":
            return
        if self.state == "WAIT":
            self.current = {"episode_id": f"{self.variant}:{ts}",
                            "created_at": int(ts), "squeeze_at": int(ts)}
            self.records.append(self.current)
            self.state = "READY"
        else:
            self.current["squeeze_at"] = int(ts)


def _confidence(direction, backgrounds, last_cross, ts):
    aligned = [backgrounds[name] == direction for name in ("1h", "4h")]
    ages = {name: (int(ts - last_cross[name][direction])
                   if last_cross[name][direction] is not None else None)
            for name in ("1h", "4h")}
    fresh = (all(aligned) and ages["1h"] is not None and ages["4h"] is not None
             and ages["1h"] <= 2 * 3_600_000 and ages["4h"] <= 4 * 3_600_000)
    grade = "high" if fresh else "medium" if any(aligned) else "low"
    return grade, {"high": 1., "medium": .5, "low": .25}[grade], ages


def _native_deltas(data, frame, snapshot):
    """Compare to last completed native bar, or its predecessor at its close."""
    times = data[:, 0].astype(np.int64) + BAR
    ends = frame.available_at.to_numpy()
    baseline = np.searchsorted(ends, times, side="left") - 1
    valid, safe = baseline >= 0, np.maximum(baseline, 0)
    ma = frame.ma10.to_numpy()
    gap = ma - frame.ma35.to_numpy()
    return dict(native_baseline_at=np.where(valid, ends[safe], -1),
                ma10_delta=np.where(valid, snapshot["ma10"] - ma[safe], np.nan),
                gap_delta=np.where(valid, snapshot["gap"] - gap[safe], np.nan))


class _NativeTurn:
    """Native completed non-turn rearming prevents provisional freshness resets."""

    def __init__(self):
        self.armed = {-1: False, 1: False}
        self.started = {-1: None, 1: None}
        self.turning = {-1: False, 1: False}

    def observe(self, ts, ma_delta, gap_delta, completed):
        valid = np.isfinite(ma_delta) and np.isfinite(gap_delta)
        for direction in (-1, 1):
            turning = bool(valid and direction * ma_delta > 0 and direction * gap_delta > 0)
            self.turning[direction] = turning
            if turning and self.armed[direction]:
                self.started[direction], self.armed[direction] = int(ts), False
            elif valid and completed and not turning:
                self.armed[direction] = True


def _turn_confidence(direction, trackers, ts):
    turning = [trackers[name].turning[direction] for name in ("1h", "4h")]
    fresh = all(turning) and all(
        trackers[name].started[direction] is not None
        and ts - trackers[name].started[direction] <= window
        for name, window in (("1h", 2 * 3_600_000), ("4h", 4 * 3_600_000)))
    grade = "high" if fresh else "medium" if any(turning) else "low"
    return grade, {"high": 1., "medium": .5, "low": .25}[grade]


def _phase(gaps, norms, index):
    if index < 1:
        return False, False
    cross = _cross(gaps[index - 1], gaps[index])
    flags = []
    for direction in (1, -1):
        recent = direction * gaps[max(0, index - 2):index + 1]
        contracting = (len(recent) == 3 and np.all(recent > 0)
                       and recent[0] > recent[1] > recent[2]
                       and np.max(direction * norms[index - 2:index + 1]) >= .25)
        flags.append(bool(cross == -direction or contracting))
    return tuple(flags)


def _build_mode(data, frames, start, forming):
    names = ("30m", "1h", "4h")
    snapshots = {name: _snapshots(data, frames[name], PERIODS[name], forming) for name in names}
    native = {name: _native_deltas(data, frames[name], snapshots[name]) for name in ("1h", "4h")} if forming else {}
    turns = {name: _NativeTurn() for name in ("1h", "4h")}
    completed = frames["30m"]
    gaps = (completed.ma10 - completed.ma35).to_numpy()
    norms = gaps / completed.atr14.to_numpy()
    squeezed = _compression(norms, gaps)
    closed_values = {name: completed[name].to_numpy() for name in ("ma10", "ma35", "atr14", "available_at")}
    phases = [_phase(gaps, norms, j) for j in range(len(completed))]
    episode = _Episode("X3" if forming else "X1")
    signals, raw_signals, contexts = [], [], {}
    last_cross = {name: {-1: None, 1: None} for name in ("1h", "4h")}
    previous = {name: np.nan for name in names}
    previous_ma = np.nan
    indexes = range(len(data)) if forming else range(5, len(data), 6)
    for i in indexes:
        ts = int(data[i, 0]) + BAR
        boundary = ts % HALF == 0
        half_index = (i + 1) // 6 - 1
        backgrounds = {}
        for name in ("1h", "4h"):
            gap = snapshots[name]["gap"][i]
            cross = _cross(previous[name], gap)
            if cross:
                last_cross[name][cross] = ts
            backgrounds[name] = int(gap > 0) - int(gap < 0)
            previous[name] = gap
            if forming:
                turns[name].observe(ts, native[name]["ma10_delta"][i], native[name]["gap_delta"][i],
                                    ts % PERIODS[name] == 0)
        half = snapshots["30m"]
        gap, norm, ma = half["gap"][i], half["norm"][i], half["ma10"][i]
        delta = ma - previous_ma
        direction = int(gap > 0) - int(gap < 0)
        cross = _cross(previous["30m"], gap)
        event = episode.observe(ts, gap, previous["30m"], delta, norm)
        if ts >= start:
            aligned_long = all(value == 1 for value in backgrounds.values())
            aligned_short = all(value == -1 for value in backgrounds.values())
            context = {"exit_long": False, "exit_short": False,
                       "trend_long": aligned_long, "trend_short": aligned_short,
                       "allow_entry_long": direction == 1, "allow_entry_short": direction == -1}
            if half_index >= 0:
                for side, aligned, sign in (("long", aligned_long, -1), ("short", aligned_short, 1)):
                    anchor = closed_values["ma35" if aligned else "ma10"][half_index]
                    trail = anchor + sign * .25 * closed_values["atr14"][half_index]
                    if np.isfinite(trail) and trail > 0:
                        context[f"trail_{side}"] = float(trail)
                phase_long, phase_short = phases[half_index]
                context.update(phase_exit_long=phase_long, phase_exit_short=phase_short,
                               phase_at=int(closed_values["available_at"][half_index]))
                if np.isfinite(gaps[half_index]):
                    context.update(phase_valid_long=bool(gaps[half_index] > 0),
                                   phase_valid_short=bool(gaps[half_index] < 0))
            else:
                context.update(phase_exit_long=False, phase_exit_short=False, phase_at=0)
            contexts[ts] = {"C": context}
            candidates = []
            if event is not None:
                candidates.append(("X3" if forming else "X1", event["direction"], event))
            if not forming and cross and cross * delta > 0:
                candidates.append(("X0", cross, {"episode_id": None, "cross_at": ts, "squeeze_at": None}))
            for variant, side, info in candidates:
                if not np.isfinite(half["atr14"][i]) or half["atr14"][i] <= 0:
                    continue
                grade, share, ages = _confidence(side, backgrounds, last_cross, ts)
                price = float(half["close"][i])
                signal = {"signal_id": f"C:{variant}:{ts}:{side:+d}", "lane": "C",
                          "available_at": ts, "direction": side, "reference_price": price,
                          "stop_distance": float(np.clip(half["atr14"][i], .006 * price, .015 * price)),
                          "strong": grade == "high", "max_hold_ms": 7_200_000,
                          "risk_share": share if forming else 1., "cancel_if_context_invalid": True,
                          "metadata": {"confidence": grade, "graded_risk_share": share,
                                       "episode_id": info["episode_id"], "cross_at": info["cross_at"],
                                       "squeeze_at": info["squeeze_at"], "provisional": forming and not boundary,
                                       "htf_provisional": {name: forming and ts % PERIODS[name] != 0
                                                           for name in ("1h", "4h")},
                                       "htf_cross_ages_ms": ages,
                                       "htf_aligned": {name: backgrounds[name] == side for name in ("1h", "4h")}}}
                if forming:
                    turn_grade, turn_share = _turn_confidence(side, turns, ts)
                    signal["metadata"].update(turn_confidence=turn_grade, turn_risk_share=turn_share,
                        turn_proxy="native MA10 slope and raw-gap acceleration, not an MA cross",
                        htf_turn={name: dict(
                            native_baseline_at=int(native[name]["native_baseline_at"][i]) if native[name]["native_baseline_at"][i] >= 0 else None,
                            ma10_delta=float(native[name]["ma10_delta"][i]) if np.isfinite(native[name]["ma10_delta"][i]) else None,
                            gap_delta=float(native[name]["gap_delta"][i]) if np.isfinite(native[name]["gap_delta"][i]) else None,
                            turning=turns[name].turning[side], turn_started_at=turns[name].started[side])
                            for name in ("1h", "4h")})
                (raw_signals if variant == "X0" else signals).append(signal)
        # Strict ordering: the cross cannot claim its own completed-bar squeeze.
        if boundary:
            episode.completed(ts, bool(squeezed[half_index]))
        previous["30m"], previous_ma = gap, ma
    return signals, raw_signals, contexts, episode.records


def build_transition_tapes(bars, warmup_days=90):
    """Build fixed primary X0..X3 and secondary X4 allocation-only variants."""
    data = _validate(bars, warmup_days)
    frames = {name: _features(data, PERIODS[name]) for name in ("30m", "1h", "4h")}
    start = int(data[0, 0]) + warmup_days * DAY
    x1, x0, closed_contexts, closed_episodes = _build_mode(data, frames, start, False)
    x3, _, forming_contexts, forming_episodes = _build_mode(data, frames, start, True)
    variants = {
        "X0": {"signals": x0, "contexts": closed_contexts},
        "X1": {"signals": x1, "contexts": closed_contexts},
        "X1A": {"signals": [s for s in x1 if all(s["metadata"]["htf_aligned"].values())],
                 "contexts": closed_contexts},
        "X2": {"signals": [dict(s, risk_share=s["metadata"]["graded_risk_share"]) for s in x1],
                "contexts": closed_contexts},
        "X3": {"signals": x3, "contexts": forming_contexts},
        "X4": {"signals": [dict(s, risk_share=s["metadata"]["turn_risk_share"]) for s in x3],
                "contexts": forming_contexts},
    }
    return {"variants": variants, "episodes": closed_episodes + forming_episodes,
            "metadata": {"input_bars": len(data), "input_start": int(data[0, 0]),
                         "input_end_exclusive": int(data[-1, 0]) + BAR,
                         "warmup_days": warmup_days, "decision_start": start,
                         "signal_counts": {name: len(value["signals"]) for name, value in variants.items()},
                         "indicator_convention": "SMA10/35; engine Wilder EWM ATR14 adjust=False",
                         "compression_source": "strictly prior completed 30m observations",
                         "forming_availability": "closed 5m close; completed TF boundary not double counted",
                         "confidence": "ordinal alignment/freshness, not independent probabilities",
                         "position_state": "none; no outcome-dependent signal regeneration"}}
