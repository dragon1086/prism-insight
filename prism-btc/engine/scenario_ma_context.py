"""Bounded causal MA facts, not signals, authorization, or broker observations."""
from __future__ import annotations

import math

from engine.scenario_snapshot import TIMEFRAME_MS, candle_start


def _number(value, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0))


def _position(close, fast, slow):
    if close == fast == slow:
        return "AT_BOTH"
    if close == fast or close == slow:
        return "AT_MA10" if close == fast else "AT_MA35"
    return "ABOVE" if close > max(fast, slow) else "BELOW" if close < min(fast, slow) else "BETWEEN"


def _point(source, duration, as_of, confirmed):
    if not isinstance(source, dict) or source.get("observation_kind") == "synthetic_boundary":
        return None
    values = {key: source.get(key) for key in ("open", "high", "low", "close", "ma10", "ma35")}
    start = source.get("open_time_ms")
    observed = start + duration if _number(start) and confirmed else source.get("observed_at_ms")
    if (not all(_number(v, positive=True) for v in values.values())
            or not _number(start) or not _number(observed)
            or not start <= observed <= as_of
            or (not confirmed and (not start <= observed < start + duration
                                   or as_of - observed > 120_000))):
        return None
    fast, slow, close = values["ma10"], values["ma35"], values["close"]
    return {**values, "open_time_ms": start, "as_of_ms": observed,
            "is_confirmed": confirmed, "price_basis": "LAST_TRADE_OHLC_SMA",
            "price_position": _position(close, fast, slow),
            "ma_order": "BULLISH" if fast > slow else "BEARISH" if fast < slow else "EQUAL",
            "absolute_gap_price": abs(fast - slow)}


def _frame_points(item, duration, as_of):
    path = item.get("ma_path")
    aggregate_valid = False
    if isinstance(path, dict):
        if (path.get("status") == "unavailable" or type(path.get("version")) is not int
                or path["version"] != 1
                or "as_of_ms" in path and (not _number(path["as_of_ms"]) or path["as_of_ms"] > as_of)
                or "duration_ms" in path and (type(path["duration_ms"]) is not int or path["duration_ms"] != duration)):
            return [], None, "ma_path", False
        raw = path.get("confirmed_points", [])
        raw = raw[-3:] if isinstance(raw, list) else []
        confirmed = [_point(p, duration, as_of, True) for p in raw]
        aggregate_valid = bool(confirmed) and all(p is not None for p in confirmed)
        confirmed = [p for p in confirmed if p is not None]
        forming = _point(path.get("forming_point"), duration, as_of, False)
        if path.get("forming_point") is not None and forming is None:
            aggregate_valid = False
        if "as_of_ms" in path and any(p["as_of_ms"] > path["as_of_ms"]
                                      for p in confirmed + ([forming] if forming else [])):
            return [], None, "ma_path", False
        source = "ma_path"
    else:
        latest = item.get("confirmed")
        bars = item.get("recent_confirmed_bars", [])
        # Exact timestamp join; never apply today's MA to earlier bars.
        matches = [b for b in bars[-20:] if isinstance(b, dict) and isinstance(latest, dict)
                   and b.get("open_time_ms") == latest.get("open_time_ms")] if isinstance(bars, list) else []
        point = _point({**matches[0], **latest}, duration, as_of, True) if len(matches) == 1 else None
        confirmed = [point] if point else []
        raw_forming = item.get("forming")
        forming = (_point({**raw_forming, **raw_forming.get("ohlcv", {})}, duration, as_of, False)
                   if isinstance(raw_forming, dict) and isinstance(raw_forming.get("ohlcv"), dict) else None)
        source = "legacy_latest_only"
    confirmed = sorted(confirmed, key=lambda p: p["open_time_ms"])
    current_start = candle_start(as_of, duration)
    if (confirmed and (confirmed[-1]["as_of_ms"] != current_start
                      or any(b["open_time_ms"] - a["open_time_ms"] != duration
                             for a, b in zip(confirmed, confirmed[1:])))):
        confirmed = []
        aggregate_valid = False
    if forming and forming["open_time_ms"] != current_start:
        forming = None
        aggregate_valid = False
    return confirmed, forming, source, aggregate_valid


def _gap_stats(path, key):
    raw = path.get(key) if isinstance(path, dict) else None
    if not isinstance(raw, dict):
        return None
    fields = ("absolute_price", "normalized_fraction", "change_price", "state",
              "consecutive_narrowing_bars", "preceding_narrowing_bars", "compression_bars",
              "preceding_compression_bars",
              "compression_threshold", "preceding_confirmed_narrowing_bars",
              "preceding_confirmed_compression_bars", "provisional")
    result = {key: raw[key] for key in fields if key in raw
            and (type(raw[key]) in (int, float) and math.isfinite(raw[key])
                 or key == "state" and raw[key] in ("WIDENING", "NARROWING", "UNCHANGED")
                 or key == "provisional" and type(raw[key]) is bool)}
    bounds = raw.get("count_lower_bounds")
    if isinstance(bounds, dict):
        result["count_lower_bounds"] = {key: bounds[key] for key in fields
                                         if key in bounds and type(bounds[key]) is bool}
    return result


def build_ma_structure_context(snapshot: dict, context: dict) -> dict:
    """Use only supplied own-time MA values; missing history never becomes a gate."""
    result = {"version": 1, "status": "unavailable", "primary": {}, "higher_frames": {},
              "long_upward_obstacles": [], "short_downward_obstacles": [],
              "equal_reference_levels": [], "levels": {},
              "levels_price_basis": "LAST_TRADE_OHLC_SMA", "reference": None}
    as_of, now = snapshot.get("as_of_ms"), context.get("now")
    if not _number(as_of) or not _number(now) or as_of > now * 1000:
        return result
    frames = snapshot.get("timeframes")
    if not isinstance(frames, dict):
        return result
    mark = context.get("mark_price")
    if _number(mark, positive=True):
        result["reference"] = {"kind": "CONTEXT_MARK_PRICE", "price": mark, "context_now_seconds": now,
                               "observed_at_seconds": None,
                               "ma_basis": "LAST_TRADE_OHLC_SMA", "basis_difference_not_adjusted": True}
        captured = context.get("account_captured_at")
        if _number(captured) and captured <= now:
            result["reference"]["account_captured_at_seconds"] = captured
    for frame, duration in TIMEFRAME_MS.items():
        item = frames.get(frame)
        item = item if isinstance(item, dict) else {}
        confirmed, forming, source, aggregate_valid = _frame_points(item, duration, as_of)
        points = confirmed + ([forming] if forming else [])
        facts = {"status": "available" if points else "unavailable", "source": source,
                 "history_limited": source != "ma_path", "duration_ms": duration,
                 "price_basis": "LAST_TRADE_OHLC_SMA",
                 "points": [{key: value for key, value in point.items()
                             if key not in ("open", "high", "low", "price_basis", "absolute_gap_price")}
                            for point in points]}
        path = item.get("ma_path")
        if isinstance(path, dict) and aggregate_valid:
            for key in ("source_history_count", "valid_confirmed_ma_points"):
                if type(path.get(key)) is int and path[key] >= 0:
                    facts[key] = path[key]
        if frame in ("15m", "30m", "1h"):
            transitions = []
            for before, after in zip(points, points[1:]):
                gap_change = after["absolute_gap_price"] - before["absolute_gap_price"]
                transitions.append({"from_as_of_ms": before["as_of_ms"], "to_as_of_ms": after["as_of_ms"],
                    "from_position": before["price_position"], "to_position": after["price_position"],
                    "from_ma_order": before["ma_order"], "to_ma_order": after["ma_order"],
                    "bar_steps": (after["open_time_ms"] - before["open_time_ms"]) / duration,
                    "close_change_price": after["close"] - before["close"],
                    "ma10_change_price": after["ma10"] - before["ma10"],
                    "ma35_change_price": after["ma35"] - before["ma35"],
                    "gap_change_price": gap_change,
                    "gap_state": "WIDENING" if gap_change > 0 else "NARROWING" if gap_change < 0 else "UNCHANGED",
                    "provisional": not after["is_confirmed"]})
            facts.update(transitions=transitions,
                         confirmed_gap=_gap_stats(path, "confirmed_gap") if confirmed and aggregate_valid else None,
                         forming_gap=_gap_stats(path, "forming_gap") if forming and aggregate_valid else None)
            result["primary"][frame] = facts
        else:
            levels = []
            for point in confirmed[-1:] + ([forming] if forming else []):
                for ma in ("ma10", "ma35"):
                    level_id = frame + "." + ma + (".confirmed" if point["is_confirmed"] else ".forming")
                    level = {"timeframe": frame, "ma": ma, "price": point[ma],
                             "is_confirmed": point["is_confirmed"], "as_of_ms": point["as_of_ms"],
                             "same_line_group": frame + "." + ma}
                    if result["reference"]:
                        delta = point[ma] - mark
                        level.update(distance_price=delta, distance_fraction=delta / mark,
                                     relative_to_mark="ABOVE" if delta > 0 else "BELOW" if delta < 0 else "EQUAL")
                        bucket = "long_upward_obstacles" if delta > 0 else "short_downward_obstacles" if delta < 0 else "equal_reference_levels"
                        result[bucket].append(level_id)
                    result["levels"][level_id] = level
                    levels.append(level_id)
            result["higher_frames"][frame] = {"status": facts["status"], "source": source, "level_ids": levels}
    result["long_upward_obstacles"].sort(key=lambda key: result["levels"][key]["price"])
    result["short_downward_obstacles"].sort(key=lambda key: -result["levels"][key]["price"])
    result["status"] = "available" if any(p["status"] == "available" for p in result["primary"].values()) else "unavailable"
    return result
