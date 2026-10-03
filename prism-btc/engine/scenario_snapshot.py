"""Causal, JSON-safe market facts for scenario reasoning; no trading decisions.

Historical inputs must contain confirmed OHLCV bars. Provisional inputs must be
snapshots actually observed at ``observed_at_ms`` (not completed historical bars
relabeled with an earlier timestamp). These facts cannot prove data provenance.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from engine.indicators import atr, sma

TIMEFRAME_MS = {"30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
                "12h": 43_200_000, "1d": 86_400_000, "5m": 300_000}
_OHLCV = ["open", "high", "low", "close", "volume"]
COMPRESSION_GAP_FRACTION = 0.0015  # Descriptive feature, never an entry gate.


def _number(value):
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None


def _frame(frame, duration):
    if frame is None or frame.empty:
        return pd.DataFrame(columns=_OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("timezone_aware_datetime_index_required")
    if frame.index.has_duplicates or frame.index.hasnans:
        raise ValueError("duplicate_or_missing_timestamp")
    if not set(_OHLCV).issubset(frame.columns):
        raise ValueError("missing_ohlcv_columns")
    result = frame[_OHLCV].copy().sort_index()
    result.index = result.index.tz_convert("UTC")
    if any((result.index.asi8 // 1_000_000) % duration):
        raise ValueError("unaligned_candle_timestamp")
    return result


def _validate_values(frame):
    frame = frame.apply(pd.to_numeric, errors="coerce")
    if not frame.map(lambda x: pd.notna(x) and math.isfinite(x)).all().all():
        raise ValueError("nonfinite_ohlcv")
    if ((frame[["open", "high", "low", "close"]] <= 0).any().any()
            or (frame.volume < 0).any()
            or (frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()):
        raise ValueError("invalid_ohlcv")
    return frame


def _features(frame, duration):
    row = frame.iloc[-1]
    ma10, ma35 = sma(frame.close, 10), sma(frame.close, 35)
    gap = (ma10 - ma35) / frame.close
    absolute_gap = gap.abs()
    gap_change = absolute_gap.diff()
    decreasing = 0
    for change in reversed(gap_change.tolist()):
        if pd.isna(change) or change >= 0:
            break
        decreasing += 1
    compressed = 0
    for value in reversed(absolute_gap.tolist()):
        if pd.isna(value) or value > COMPRESSION_GAP_FRACTION:
            break
        compressed += 1
    fast, slow, price = _number(ma10.iloc[-1]), _number(ma35.iloc[-1]), float(row.close)
    position = None
    if fast is not None and slow is not None:
        position = "above" if price > max(fast, slow) else "below" if price < min(fast, slow) else "between"
    return {
        "ma10": fast, "ma35": slow, "price_position": position,
        "ma_order": None if fast is None or slow is None else "bullish" if fast > slow else "bearish" if fast < slow else "equal",
        "ma10_slope_fraction_per_bar": _number(ma10.diff().iloc[-1] / price),
        "ma35_slope_fraction_per_bar": _number(ma35.diff().iloc[-1] / price),
        "signed_gap_fraction": _number(gap.iloc[-1]),
        "absolute_gap_change_per_bar": _number(gap_change.iloc[-1]),
        "recent_absolute_gap_fractions": [_number(value) for value in absolute_gap.tail(20)],
        "convergence_bars": decreasing,
        "convergence_duration_ms": decreasing * duration,
        "convergence_definition": "consecutive strictly decreasing absolute MA gap / close; includes provisional bar when present",
        "compression_bars": compressed,
        "compression_duration_ms": compressed * duration,
        "compression_gap_fraction_threshold": COMPRESSION_GAP_FRACTION,
        "compression_definition": "consecutive abs(MA10-MA35)/close <= threshold, including constant gaps; provisional bar when present",
        "body_fraction": float((row.close - row.open) / row.open),
        "upper_wick_fraction": float((row.high - max(row.open, row.close)) / row.open),
        "lower_wick_fraction": float((min(row.open, row.close) - row.low) / row.open),
        "atr14_risk_only": _number(atr(frame).iloc[-1]),
    }


def _intrabar_volume(history, available_at):
    """Only fully elapsed 5m buckets at the oldest primary observation."""
    unavailable = dict(status="unavailable", reason="confirmed_5m_history_unavailable",
                       available_at_ms=available_at, ratio=None)
    if available_at is None:
        return None, unavailable
    try:
        frame = _frame(history, TIMEFRAME_MS["5m"])
        starts = frame.index.asi8 // 1_000_000
        frame = _validate_values(frame.loc[starts + TIMEFRAME_MS["5m"] <= available_at])
        last = frame.tail(6)
        expected_end = available_at // TIMEFRAME_MS["5m"] * TIMEFRAME_MS["5m"]
        expected = list(range(expected_end-6*TIMEFRAME_MS["5m"], expected_end, TIMEFRAME_MS["5m"]))
        if list(last.index.asi8 // 1_000_000) != expected:
            return frame, {**unavailable, "reason": "six_recent_contiguous_5m_bars_required"}
        previous, recent = float(last.volume.iloc[:3].sum()), float(last.volume.iloc[3:].sum())
        return frame, dict(status="available" if previous > 0 else "unavailable",
            reason=None if previous > 0 else "zero_previous_volume", available_at_ms=available_at,
            completed_through_ms=expected_end, previous_15m_volume=previous, recent_15m_volume=recent,
            ratio=recent/previous if previous > 0 else None,
            definition="last 3 complete contiguous 5m volumes / previous 3; not a probability")
    except (ValueError, TypeError, OverflowError):
        return None, {**unavailable, "reason": "invalid_confirmed_5m_history"}


def _same_progress_profile(frame, duration, start, available_at):
    result = dict(status="unavailable", reason="comparable_intrabar_paths_unavailable",
                  sample_count=0, available_at_ms=available_at, projected_final_median=None,
                  empirical_projection_range=None, calibrated_probability=False)
    if frame is None or available_at is None or available_at < start:
        return result
    step = TIMEFRAME_MS["5m"]
    elapsed = min(duration, (available_at-start)//step*step)
    result.update(matched_elapsed_ms=elapsed, matched_progress_fraction=elapsed/duration,
                  definition="complete historical windows at same completed-5m progress; range is empirical min/max, not a confidence interval")
    if elapsed <= 0 or elapsed >= duration:
        return {**result, "reason": "no_completed_current_5m_progress"}
    volumes = {int(timestamp.value//1_000_000): float(value) for timestamp, value in frame.volume.items()}
    current_keys = list(range(start, start+elapsed, step))
    if not all(key in volumes for key in current_keys):
        return {**result, "reason": "current_5m_path_incomplete"}
    current_volume = sum(volumes[key] for key in current_keys)
    ratios, prefixes = [], []
    for window in sorted({key//duration*duration for key in volumes}):
        if window+duration > start or window+duration > available_at:
            continue
        keys = list(range(window, window+duration, step))
        if not all(key in volumes for key in keys):
            continue
        prefix = sum(volumes[key] for key in keys[:elapsed//step])
        if prefix <= 0:
            continue
        prefixes.append(prefix)
        ratios.append(sum(volumes[key] for key in keys)/prefix)
    result.update(sample_count=len(ratios), observed_prefix_volume=current_volume)
    if len(ratios) < 5:
        return {**result, "reason": "fewer_than_five_complete_comparable_paths"}
    projections = pd.Series(ratios, dtype=float)*current_volume
    result.update(status="available", reason=None,
                  historical_prefix_volume_median=float(pd.Series(prefixes).median()),
                  projected_final_median=float(projections.median()),
                  empirical_projection_range=[float(projections.min()), float(projections.max())])
    return result


def build_scenario_snapshot(
    tf_data: Mapping[str, pd.DataFrame], current_ms: int, *,
    provisional_tf_data: Mapping[str, pd.DataFrame] | None = None,
    observed_at_ms: int | None = None, max_observation_age_ms: int = 120_000,
    observed_at_by_tf_ms: Mapping[str, int] | None = None,
) -> dict:
    """Build primary 30m/1h and optional context facts at a bounded clock.

    ``valid`` requires complete MA history plus fresh explicit forming snapshots
    on both primary frames. Missing context is reported but not a trading gate.
    Volume extrapolation is a heuristic, never a probability or confidence score.
    """
    if isinstance(current_ms, bool) or int(current_ms) != current_ms or current_ms < 0:
        raise ValueError("invalid_current_ms")
    if max_observation_age_ms < 0:
        raise ValueError("invalid_max_observation_age_ms")
    current_ms = int(current_ms)
    provisional_tf_data = provisional_tf_data or {}
    primary_observations = [observed_at_by_tf_ms.get(tf) if observed_at_by_tf_ms is not None else observed_at_ms
                            for tf in ("30m", "1h")]
    available_at = min(primary_observations) if all(type(t) is int and 0 <= t <= current_ms
                                                  for t in primary_observations) else None
    intrabar, acceleration = _intrabar_volume(tf_data.get("5m"), available_at)
    output = {"as_of_ms": current_ms, "valid": True, "issues": [], "timeframes": {}}
    for tf, duration in TIMEFRAME_MS.items():
        observation = (observed_at_by_tf_ms.get(tf) if observed_at_by_tf_ms is not None
                       else observed_at_ms)
        if tf == "5m" and tf not in tf_data and tf not in provisional_tf_data:
            continue
        issues = []
        fact = {"role": "primary" if tf in ("30m", "1h") else "execution" if tf == "5m" else "context",
                "status": "unavailable", "issues": issues, "confirmed": None, "forming": None}
        output["timeframes"][tf] = fact
        try:
            history = _frame(tf_data.get(tf), duration)
            starts = history.index.asi8 // 1_000_000
            history = _validate_values(history.loc[starts + duration <= current_ms])
            # Never silently turn a provisional candle into a confirmed candle.
            current_start = current_ms // duration * duration
            if history.empty:
                issues.append("missing_confirmed_history")
            else:
                if int(history.index[-1].value // 1_000_000) != current_start - duration:
                    issues.append("stale_confirmed_history")
                if len(history) < 36:
                    issues.append("insufficient_ma_history")
                if len(history) > 1 and any(history.index.to_series().diff().dropna() != pd.Timedelta(milliseconds=duration)):
                    issues.append("gapped_confirmed_history")
                fact["confirmed"] = {"open_time_ms": int(history.index[-1].value // 1_000_000),
                                     "is_confirmed": True, **_features(history, duration)}
                fact["recent_confirmed_bars"] = [
                    {"open_time_ms": int(timestamp.value // 1_000_000),
                     **{key: float(row[key]) for key in _OHLCV}}
                    for timestamp, row in history.tail(20).iterrows()
                ]
            provisional = _frame(provisional_tf_data.get(tf), duration)
            if provisional.empty:
                issues.append("missing_forming_snapshot")
            elif type(observation) is not int or observation < 0 or observation > current_ms:
                issues.append("missing_or_future_observation_time")
            elif current_ms - observation > max_observation_age_ms:
                issues.append("stale_forming_snapshot")
            else:
                pstarts = provisional.index.asi8 // 1_000_000
                if any(pstarts > observation):
                    issues.append("future_provisional_bar")
                current = provisional.loc[pstarts == current_start]
                if current.empty:
                    issues.append("missing_forming_snapshot")
                elif not issues or all(x in ("insufficient_ma_history", "missing_confirmed_history") for x in issues):
                    current = _validate_values(current)
                    elapsed = observation - current_start
                    volume = float(current.iloc[-1].volume)
                    projected = volume * duration / elapsed if elapsed > 0 else None
                    historical_volumes = history.volume.tail(20)
                    baseline = _number(historical_volumes.mean()) if len(historical_volumes) >= 5 else None
                    combined = pd.concat([history, current]) if not history.empty else current
                    fact["forming"] = {
                        "open_time_ms": current_start, "observed_at_ms": observation,
                        "is_confirmed": False, "elapsed_ms": elapsed,
                        "observation_kind": "observed",
                        "remaining_ms": current_start + duration - current_ms,
                        "observation_age_ms": current_ms - observation,
                        "progress_fraction_at_observation": elapsed / duration,
                        "ohlcv": {key: float(current.iloc[-1][key]) for key in _OHLCV},
                        "volume_acceleration": dict(acceleration),
                        **_features(combined, duration),
                        "volume_projection": {
                            "method": "linear_elapsed_time_heuristic",
                            "projected_final": projected,
                            "historical_final_mean": baseline,
                            "historical_sample_count": len(historical_volumes),
                            "expected_so_far_linear": baseline * elapsed / duration if baseline is not None else None,
                            "uncertainty_interval": None,
                            "uncertainty_reason": "comparable_intrabar_paths_unavailable",
                            "calibrated_probability": False,
                            "same_progress_profile": _same_progress_profile(intrabar, duration, current_start, available_at),
                        },
                    }
            fact["status"] = "ok" if not issues else "incomplete"
        except (ValueError, TypeError, OverflowError) as exc:
            issues.append(str(exc))
        output["issues"].extend(f"{tf}:{issue}" for issue in issues)
        if fact["role"] == "primary" and issues:
            output["valid"] = False
    return output
