"""Offline evidence packet for observational BTC OHLCV factors.

This module never changes strategy configuration.  It labels captured decision
snapshots with later 4h returns, then reports purged chronological correlations.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.causal_validation import PurgedSplit, purged_chronological_splits

EVIDENCE_SCHEMA_VERSION = 1
DEFAULT_TIMEFRAME = "4h"


def _finite(value: float) -> float | None:
    return round(float(value), 8) if math.isfinite(float(value)) else None


def _correlation(rows: list[dict[str, Any]], factor: str) -> dict[str, Any]:
    paired = [
        (float(row["factors"][factor]), float(row["future_return"]))
        for row in rows
        if row["factors"].get(factor) is not None
    ]
    entries = [
        (
            float(row["factors"][factor]),
            float(row["future_return"])
            * (1.0 if row["signal_side"] == "long" else -1.0),
        )
        for row in rows
        if row["signal_side"] in {"long", "short"}
        and row["factors"].get(factor) is not None
    ]

    def calculate(values: list[tuple[float, float]]) -> float | None:
        if len(values) < 3:
            return None
        left = np.asarray([value[0] for value in values], dtype=float)
        right = np.asarray([value[1] for value in values], dtype=float)
        if float(np.std(left)) == 0.0 or float(np.std(right)) == 0.0:
            return None
        return _finite(float(np.corrcoef(left, right)[0, 1]))

    return {
        "n": len(paired),
        "correlation": calculate(paired),
        "directional_entry_n": len(entries),
        "directional_entry_correlation": calculate(entries),
    }


def _parse_decisions(
    decisions: list[dict[str, Any]], timeframe: str
) -> list[dict[str, Any]]:
    parsed = []
    for row in decisions:
        try:
            market = row["market_snapshot"]
            if isinstance(market, str):
                market = json.loads(market)
            factor_block = market["ohlcv_factors"]["timeframes"][timeframe]
            if factor_block.get("status") != "ok":
                continue
            as_of = pd.Timestamp(factor_block["as_of_open_time"])
            factors = {
                str(name): float(value) if value is not None else None
                for name, value in factor_block.get("values", {}).items()
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        parsed.append({
            "decision_id": str(row["decision_id"]),
            "ts": str(row["ts"]),
            "signal_side": str(row.get("signal_side") or "none"),
            "as_of_open_time": as_of,
            "factors": factors,
        })
    return sorted(parsed, key=lambda item: item["as_of_open_time"])


def _label_forward_returns(
    rows: list[dict[str, Any]],
    closes: pd.Series,
    *,
    horizon_bars: int,
    execution_lag: int,
) -> list[dict[str, Any]]:
    series = closes.dropna().sort_index().astype(float)
    labeled = []
    for row in rows:
        at = row["as_of_open_time"]
        location = int(series.index.searchsorted(at, side="left"))
        if location >= len(series.index) or series.index[location] != at:
            continue
        start = location + execution_lag
        end = start + horizon_bars
        if start >= len(series) or end >= len(series):
            continue
        start_price = float(series.iloc[start])
        end_price = float(series.iloc[end])
        if start_price <= 0:
            continue
        labeled.append({
            **row,
            "future_return": end_price / start_price - 1.0,
        })
    return labeled


def _split_evidence(
    rows: list[dict[str, Any]], split: PurgedSplit, split_number: int
) -> dict[str, Any]:
    ranges = {
        "train": split.train,
        "validation": split.validation,
        "test": split.test,
    }
    factors = sorted({name for row in rows for name in row["factors"]})
    factor_results = {}
    for factor in factors:
        factor_results[factor] = {
            name: _correlation(rows[start:end], factor)
            for name, (start, end) in ranges.items()
        }
    return {
        "split_number": split_number,
        "ranges": {name: list(bounds) for name, bounds in ranges.items()},
        "factors": factor_results,
    }


def build_evidence_packet(
    decisions: list[dict[str, Any]],
    closes: pd.Series,
    *,
    timeframe: str = DEFAULT_TIMEFRAME,
    horizon_bars: int = 6,
    execution_lag: int = 1,
    train_size: int = 180,
    validation_size: int = 60,
    test_size: int = 60,
    step_size: int = 60,
) -> dict[str, Any]:
    """Build a non-promoting evidence packet from captured decisions."""
    parsed = _parse_decisions(decisions, timeframe)
    labeled = _label_forward_returns(
        parsed,
        closes,
        horizon_bars=horizon_bars,
        execution_lag=execution_lag,
    )
    splits = purged_chronological_splits(
        len(labeled),
        train_size=train_size,
        validation_size=validation_size,
        test_size=test_size,
        label_horizon=horizon_bars,
        execution_lag=execution_lag,
        step_size=step_size,
    )
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": "ready" if splits else "insufficient",
        "timeframe": timeframe,
        "horizon_bars": horizon_bars,
        "execution_lag": execution_lag,
        "embargo_size": horizon_bars + execution_lag,
        "captured_decisions": len(parsed),
        "labeled_decisions": len(labeled),
        "promotion_allowed": False,
        "note": "관측 전용. 상관관계만으로 전략·게이트를 변경하지 않음.",
        "splits": [
            _split_evidence(labeled, split, number)
            for number, split in enumerate(splits, start=1)
        ],
    }


def _load_decisions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    return [
        dict(row) for row in connection.execute(
            "SELECT decision_id, ts, signal_side, market_snapshot "
            "FROM btc_decision_log WHERE schema_version>=2 ORDER BY ts"
        )
    ]


def _load_closes(connection: sqlite3.Connection, timeframe: str) -> pd.Series:
    rows = connection.execute(
        "SELECT open_time, close FROM klines "
        "WHERE timeframe=? AND confirmed=1 ORDER BY open_time",
        (timeframe,),
    ).fetchall()
    index = pd.to_datetime([row[0] for row in rows], unit="ms", utc=True)
    return pd.Series([float(row[1]) for row in rows], index=index, dtype=float)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-db", default=str(root / "stock_tracking_db.sqlite"))
    parser.add_argument(
        "--market-db", default=str(root / "prism-btc" / "state" / "btc_market.db")
    )
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--horizon-bars", type=int, default=6)
    parser.add_argument("--execution-lag", type=int, default=1)
    parser.add_argument("--train-size", type=int, default=180)
    parser.add_argument("--validation-size", type=int, default=60)
    parser.add_argument("--test-size", type=int, default=60)
    parser.add_argument("--step-size", type=int, default=60)
    args = parser.parse_args()

    tracking = sqlite3.connect(f"file:{args.tracking_db}?mode=ro", uri=True)
    market = sqlite3.connect(f"file:{args.market_db}?mode=ro", uri=True)
    try:
        packet = build_evidence_packet(
            _load_decisions(tracking),
            _load_closes(market, args.timeframe),
            timeframe=args.timeframe,
            horizon_bars=args.horizon_bars,
            execution_lag=args.execution_lag,
            train_size=args.train_size,
            validation_size=args.validation_size,
            test_size=args.test_size,
            step_size=args.step_size,
        )
    finally:
        tracking.close()
        market.close()
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
