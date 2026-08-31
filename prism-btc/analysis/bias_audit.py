"""Audit PRISM-BTC indicators for lookahead bias and startup-window drift."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from backtest.engine import ALL_TFS, _build_snapshot_at, _get_tf_slice
from engine.indicators import add_indicators
from engine.regime import RegimeSnapshot, build_snapshot

AUDIT_SCHEMA_VERSION = 1
DEFAULT_STARTUP_SIZES = (40, 80, 150, 300, 1000)
_NUMERIC_FIELDS = ("ma10", "ma35", "close", "atr14")
_TEXT_FIELDS = ("trend", "candle_position")


def precompute_indicators(
    tf_data: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Return independent full-history indicator frames."""
    return {
        timeframe: add_indicators(frame.copy())
        for timeframe, frame in tf_data.items()
    }


def _prefix_snapshot(
    tf_data: Mapping[str, pd.DataFrame],
    evaluated_at: pd.Timestamp,
    startup_size: int,
) -> RegimeSnapshot | None:
    prefixes = {}
    for timeframe in ALL_TFS:
        frame = tf_data.get(timeframe)
        if frame is None:
            return None
        closed = _get_tf_slice(dict(tf_data), evaluated_at, timeframe)
        if len(closed) < 35:
            return None
        prefixes[timeframe] = closed.tail(startup_size).copy()
    try:
        return build_snapshot(
            prefixes,
            evaluated_at=evaluated_at.to_pydatetime(),
        )
    except (TypeError, ValueError, IndexError):
        return None


def _differences(
    reference: RegimeSnapshot,
    observed: RegimeSnapshot,
    *,
    tolerance: float,
    relative_tolerance_pct: float,
    startup_size: int | None,
) -> list[dict[str, Any]]:
    result = []
    alignment_diff = abs(reference.alignment_score - observed.alignment_score)
    alignment_relative = alignment_diff / max(abs(reference.alignment_score), 1e-12) * 100.0
    if alignment_diff > tolerance and alignment_relative > relative_tolerance_pct:
        result.append(
            {
                "timeframe": "all",
                "field": "alignment_score",
                "reference": reference.alignment_score,
                "observed": observed.alignment_score,
                "absolute_difference": alignment_diff,
                "relative_difference_pct": alignment_relative,
                "startup_size": startup_size,
            }
        )
    for timeframe in ALL_TFS:
        expected = reference.tf_states.get(timeframe)
        actual = observed.tf_states.get(timeframe)
        if expected is None or actual is None:
            result.append(
                {
                    "timeframe": timeframe,
                    "field": "state",
                    "reference": expected is not None,
                    "observed": actual is not None,
                    "absolute_difference": None,
                    "startup_size": startup_size,
                }
            )
            continue
        for field in _TEXT_FIELDS:
            expected_value = getattr(expected, field)
            actual_value = getattr(actual, field)
            if expected_value != actual_value:
                result.append(
                    {
                        "timeframe": timeframe,
                        "field": field,
                        "reference": expected_value,
                        "observed": actual_value,
                        "absolute_difference": None,
                        "relative_difference_pct": None,
                        "startup_size": startup_size,
                    }
                )
        for field in _NUMERIC_FIELDS:
            expected_value = float(getattr(expected, field))
            actual_value = float(getattr(actual, field))
            difference = abs(expected_value - actual_value)
            relative = difference / max(abs(expected_value), 1e-12) * 100.0
            if difference > tolerance and relative > relative_tolerance_pct:
                result.append(
                    {
                        "timeframe": timeframe,
                        "field": field,
                        "reference": expected_value,
                        "observed": actual_value,
                        "absolute_difference": difference,
                        "relative_difference_pct": relative,
                        "startup_size": startup_size,
                    }
                )
    return result


def audit_snapshots(
    tf_data: Mapping[str, pd.DataFrame],
    evaluation_times: Iterable[pd.Timestamp],
    *,
    startup_sizes: Iterable[int] = DEFAULT_STARTUP_SIZES,
    tolerance: float = 1e-9,
    relative_tolerance_pct: float = 0.01,
    required_startup_size: int = 300,
    precomputed_tf_data: Mapping[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Compare full precomputation with prefix-only and startup-window snapshots."""
    times = [pd.Timestamp(value) for value in evaluation_times]
    times = [value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC") for value in times]
    sizes = tuple(sorted({int(value) for value in startup_sizes if int(value) >= 35}))
    if not sizes:
        raise ValueError("startup_sizes must contain at least one value >= 35")
    if required_startup_size not in sizes:
        raise ValueError("required_startup_size must be present in startup_sizes")
    precomputed = dict(precomputed_tf_data or precompute_indicators(tf_data))
    lookahead_differences = []
    recursive_differences = []
    missing = 0
    baseline_size = max(sizes)
    for evaluated_at in times:
        full_snapshot = _build_snapshot_at(precomputed, evaluated_at)
        prefix_reference = _prefix_snapshot(tf_data, evaluated_at, baseline_size)
        if full_snapshot is None or prefix_reference is None:
            missing += 1
            continue
        for item in _differences(
            prefix_reference,
            full_snapshot,
            tolerance=tolerance,
            relative_tolerance_pct=relative_tolerance_pct,
            startup_size=None,
        ):
            lookahead_differences.append(
                {"evaluated_at": evaluated_at.isoformat(), **item}
            )
        for size in sizes:
            observed = _prefix_snapshot(tf_data, evaluated_at, size)
            if observed is None:
                recursive_differences.append(
                    {
                        "evaluated_at": evaluated_at.isoformat(),
                        "timeframe": "all",
                        "field": "snapshot_missing",
                        "reference": True,
                        "observed": False,
                        "absolute_difference": None,
                        "relative_difference_pct": None,
                        "startup_size": size,
                    }
                )
                continue
            for item in _differences(
                prefix_reference,
                observed,
                tolerance=tolerance,
                relative_tolerance_pct=relative_tolerance_pct,
                startup_size=size,
            ):
                recursive_differences.append(
                    {"evaluated_at": evaluated_at.isoformat(), **item}
                )
    drift_by_startup = Counter(
        int(item["startup_size"])
        for item in recursive_differences
        if item.get("startup_size") is not None
    )
    stable_sizes = [size for size in sizes if drift_by_startup[size] == 0]
    required_drift_count = drift_by_startup[required_startup_size]
    return {
        "audit_schema_version": AUDIT_SCHEMA_VERSION,
        "sample_count": len(times),
        "startup_sizes": list(sizes),
        "tolerance": tolerance,
        "relative_tolerance_pct": relative_tolerance_pct,
        "required_startup_size": required_startup_size,
        "minimum_stable_startup_size": min(stable_sizes) if stable_sizes else None,
        "missing_snapshot_count": missing,
        "lookahead_bias_count": len(lookahead_differences),
        "recursive_drift_count": len(recursive_differences),
        "required_recursive_drift_count": required_drift_count,
        "recursive_drift_distribution": {
            str(size): drift_by_startup[size] for size in sizes
        },
        "lookahead_differences": lookahead_differences[:100],
        "recursive_differences": recursive_differences[:100],
        "passed": not lookahead_differences and required_drift_count == 0 and missing == 0,
    }


def _read_tf_data(path: Path) -> dict[str, pd.DataFrame]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    result = {}
    for timeframe in ALL_TFS:
        frame = pd.read_sql_query(
            "SELECT open_time, open, high, low, close, volume, turnover "
            "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time",
            connection,
            params=(timeframe,),
        )
        frame.index = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
        result[timeframe] = frame
    connection.close()
    return result


def _sample_times(tf_data: Mapping[str, pd.DataFrame], count: int) -> list[pd.Timestamp]:
    four_hour = tf_data["4h"]
    closed = four_hour.index + pd.Timedelta(hours=4)
    weekly = tf_data["1w"]
    if len(weekly) >= 50:
        first_valid = weekly.index[49] + pd.Timedelta(days=7)
        closed = closed[closed >= first_valid]
    if len(closed) <= 100:
        return list(closed[-max(1, count) :])
    candidates = closed
    indices = sorted(
        {round(index) for index in np.linspace(0, len(candidates) - 1, count)}
    )
    return [candidates[index] for index in indices]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-db", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--startup-sizes", default="40,80,150,300,1000")
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--relative-tolerance-pct", type=float, default=0.01)
    parser.add_argument("--required-startup-size", type=int, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    sizes = tuple(int(value) for value in args.startup_sizes.split(",") if value.strip())
    tf_data = _read_tf_data(args.market_db)
    result = audit_snapshots(
        tf_data,
        _sample_times(tf_data, max(1, args.samples)),
        startup_sizes=sizes,
        tolerance=args.tolerance,
        relative_tolerance_pct=args.relative_tolerance_pct,
        required_startup_size=args.required_startup_size,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
