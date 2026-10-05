"""Read-only, causal historical inputs for the production scenario snapshot.

Decision time is the instant AFTER source bars ending at that time close, BEFORE
the next source bar trades. A zero-progress forming candle is a synthetic
last-known-close marker, not an observed trade. Never read the next row's open.
This is M2.1/M2.2 data plumbing, not a profitability or execution claim.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot, _validate_values, candle_start

OHLCV = ["open", "high", "low", "close", "volume"]


def validate_frame(frame: pd.DataFrame, interval_ms: int) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("timezone_aware_datetime_index_required")
    if frame.empty or frame.index.hasnans or frame.index.has_duplicates:
        raise ValueError("empty_duplicate_or_missing_timestamp")
    if not set(OHLCV).issubset(frame.columns):
        raise ValueError("missing_ohlcv_columns")
    result = _validate_values(frame[OHLCV].copy()).sort_index()
    result.index = result.index.tz_convert("UTC")
    starts = result.index.asi8 // 1_000_000
    if any(candle_start(ts, interval_ms) != ts for ts in starts) or any(result.index.asi8 % 1_000_000):
        raise ValueError("unaligned_source_timestamp")
    if any(b - a != interval_ms for a, b in zip(starts, starts[1:])):
        raise ValueError("source_gap")
    return result


def _records_frame(records) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if "open_time" not in frame:
        raise ValueError("normalized_open_time_epoch_ms_required")
    times = pd.to_numeric(frame.pop("open_time"), errors="raise")
    if times.isna().any() or (times % 1).any():
        raise ValueError("integer_open_time_required")
    frame.index = pd.to_datetime(times, unit="ms", utc=True)
    return frame


def load_market_data(path, *, timeframe="5m") -> pd.DataFrame:
    """Explicit local file only. SQLite accepts collector/store.py market schema.

    CSV/JSONL must contain open_time (epoch milliseconds) and OHLCV. SQLite is
    opened mode=ro; mixed market/trading databases are rejected, never mutated.
    """
    path = Path(path).resolve(strict=True)
    interval = {"1m": 60_000, "5m": 300_000, **TIMEFRAME_MS}[timeframe]
    if path.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            allowed = {"klines", "funding", "open_interest", "sqlite_sequence"}
            if "klines" not in tables or tables - allowed:
                raise ValueError("dedicated_market_database_required")
            rows = conn.execute("SELECT open_time,open,high,low,close,volume FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time", (timeframe,)).fetchall()
            records = [dict(zip(["open_time"] + OHLCV, row)) for row in rows]
        frame = _records_frame(records)
    elif path.suffix.lower() == ".csv":
        frame = _records_frame(pd.read_csv(path).to_dict("records"))
    elif path.suffix.lower() == ".jsonl":
        with path.open() as stream:
            frame = _records_frame([json.loads(line) for line in stream if line.strip()])
    else:
        raise ValueError("supported_formats_sqlite_csv_jsonl")
    return validate_frame(frame, interval)


def frame_inventory(frame: pd.DataFrame, interval_ms: int) -> dict:
    """Summarize even gapped input; validation remains a separate fail-closed gate."""
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("timezone_aware_datetime_index_required")
    starts = sorted(int(t.value // 1_000_000) for t in frame.index)
    gaps = [{"after_ms": a, "before_ms": b, "missing_bars": (b-a)//interval_ms-1}
            for a, b in zip(starts, starts[1:]) if b-a > interval_ms]
    canonical = [[int(t.value//1_000_000)] + [float(row[k]) for k in OHLCV]
                 for t, row in frame.sort_index().iterrows()]
    return {"count": len(starts), "first_open_ms": starts[0] if starts else None,
            "last_close_ms": starts[-1]+interval_ms if starts else None,
            "interval_ms": interval_ms, "timezone": "UTC", "duplicate_count": int(frame.index.duplicated().sum()),
            "gaps": gaps, "missing_bar_count": sum(g["missing_bars"] for g in gaps),
            "sha256": hashlib.sha256(json.dumps(canonical, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}


def _aggregate(frame, duration):
    groups = [candle_start(ts, duration) for ts in frame.index.asi8 // 1_000_000]
    result = frame.groupby(groups).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    result.index = pd.to_datetime(result.index, unit="ms", utc=True)
    return result


class HistoricalScenarioData:
    def __init__(self, source, *, source_interval_ms=300_000, warmup=None, mark=None, funding=None):
        if source_interval_ms not in (60_000, 300_000):
            raise ValueError("source_must_be_1m_or_5m")
        self.interval_ms = source_interval_ms
        self.source = validate_frame(source, source_interval_ms)
        # Legacy 5m warmup is not a decision input; retain strict rejection of
        # other unknown timeframe keys rather than silently hiding bad data.
        self.warmup = {tf: validate_frame(frame, TIMEFRAME_MS[tf]) for tf, frame in (warmup or {}).items() if tf != "5m"}
        self.mark = validate_frame(mark, source_interval_ms) if mark is not None else None
        self.funding = list(funding) if funding is not None else None
        self._source_inventory = frame_inventory(self.source, self.interval_ms)

    def manifest(self):
        mark_coverage = {"status": "missing", "complete": False}
        if self.mark is not None:
            mark_coverage = {"status": "provided", "complete": bool(self.source.index.isin(self.mark.index).all()),
                             **frame_inventory(self.mark, self.interval_ms)}
        return {"source": self._source_inventory,
                "warmup": {tf: frame_inventory(frame, TIMEFRAME_MS[tf]) for tf, frame in self.warmup.items()},
                "mark": mark_coverage,
                "funding": {"status": "provided_unverified_schedule" if self.funding is not None else "missing",
                            "count": len(self.funding or []), "complete": False},
                "observation_convention": "closed_source_bars_only; zero_progress_is_synthetic_last_close",
                "intrabar_path_known": False}

    def decision_times(self, start_ms, end_ms):
        return list(range((start_ms+299_999)//300_000*300_000, end_ms+1, 300_000))

    def snapshot(self, now_ms):
        if type(now_ms) is not int or now_ms % 300_000:
            raise ValueError("decision_requires_aligned_5m_epoch_ms")
        starts = self.source.index.asi8 // 1_000_000
        known = self.source.loc[starts + self.interval_ms <= now_ms]
        if known.empty or int(known.index[-1].value//1_000_000)+self.interval_ms != now_ms:
            raise ValueError("source_not_complete_through_decision")
        history, forming, synthetic = {}, {}, []
        first = int(known.index[0].value//1_000_000)
        for tf, duration in TIMEFRAME_MS.items():
            current_start = candle_start(now_ms, duration)
            complete_start = candle_start(first+duration-1, duration)
            aggregate = _aggregate(known, duration)
            ast = aggregate.index.asi8//1_000_000
            confirmed = aggregate.loc[(ast >= complete_start) & (ast+duration <= now_ms)]
            if tf in self.warmup:
                warm = self.warmup[tf]
                wst = warm.index.asi8//1_000_000
                # Only use warmup that is both closed and before full source coverage.
                warm = warm.loc[(wst+duration <= now_ms) & (wst < complete_start)]
                confirmed = pd.concat([warm, confirmed]).sort_index()
            history[tf] = confirmed
            if now_ms == current_start:
                price = float(known.iloc[-1].close)
                forming[tf] = pd.DataFrame([[price, price, price, price, 0.]], columns=OHLCV,
                    index=pd.to_datetime([current_start], unit="ms", utc=True))
                synthetic.append(tf)
            elif first <= current_start:
                forming[tf] = aggregate.loc[ast == current_start]
            # A partial source prefix is never represented as a complete forming bar.
        result = build_scenario_snapshot(history, now_ms, provisional_tf_data=forming, observed_at_ms=now_ms)
        for tf, fact in result["timeframes"].items():
            if fact["forming"] is not None:
                fact["forming"]["observation_kind"] = (
                    "synthetic_boundary" if tf in synthetic else "historical_completed_subbars")
        result["historical_replay"] = {"source_interval_ms": self.interval_ms,
            "closed_source_through_ms": now_ms, "zero_progress_synthetic_timeframes": synthetic,
            "boundary_convention": "after_previous_close_before_next_trade"}
        return result
