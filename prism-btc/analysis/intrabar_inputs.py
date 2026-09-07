"""Strict frozen-history inputs and causal native features for intrabar research.

No broker, operational database, strategy mutation, interpolation, or current-bar
volume is used. Callers must provide immutable source snapshots for consistency
across the separately opened read-only SQLite transactions.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import pandas as pd

from analysis.replay_data import (
    LoadedBars, TIMEFRAME_MS, _bounds, _coverage, _integer, _manifest, _number,
    _read, load_bars, load_funding,
)
from backtest import engine
from core.entries import EntryInputs
from core.market_regimes import classify_regime, volatility_sequence
from engine.indicators import add_indicators
from engine.signal import trend_strength

START = 1_640_995_200_000  # 2022-01-01 UTC
END = 1_767_225_600_000  # 2026-01-01 UTC
WARMUP = 1_609_718_400_000  # 2021-01-04 UTC, Monday
TRADE_WARMUP = START - 69_600_000  # 2021-12-31 04:40 UTC


def _frame(rows):
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.open_time, unit="ms", utc=True)
    return frame


def load_mark_bars(path, start_ms=START, end_ms=END) -> LoadedBars:
    """Mark-price candles have no volume; never fabricate a volume column."""
    interval = TIMEFRAME_MS["5m"]
    expected = _bounds(start_ms, end_ms, interval)
    rows = _read(path, "SELECT timeframe, open_time, open, high, low, close, confirmed "
                 "FROM mark_klines WHERE timeframe = ? AND open_time >= ? "
                 "AND open_time < ? ORDER BY open_time", ("5m", start_ms, end_ms))
    _coverage(rows, "open_time", start_ms, end_ms, interval, expected)
    for row in rows:
        if type(row["confirmed"]) is not int or row["confirmed"] != 1:
            raise ValueError("unconfirmed mark bar")
        for key in ("open", "high", "low", "close"):
            row[key] = _number(row[key], key, positive=True)
        if not (row["low"] <= row["open"] <= row["high"]
                and row["low"] <= row["close"] <= row["high"]):
            raise ValueError("invalid mark OHLC")
    return LoadedBars(tuple(rows), _manifest(rows, "open_time", start_ms, end_ms,
        interval, "MARK_PRICE_OHLC", {"confirmed_only": True,
        "availability": "open_time + interval_ms", "volume_available": False,
        "intrabar_order": "UNKNOWN", "missing_policy": "REJECT; no interpolation"}))


@dataclass
class InputBundle:
    bars: tuple[dict, ...]
    funding: tuple[dict, ...]
    mark_bars: tuple[dict, ...] | None
    frames6TF: dict[str, pd.DataFrame]
    manifest: dict
    volatility: tuple[dict, ...]
    _contexts: dict = field(default_factory=dict, init=False, repr=False)
    _vol_times: tuple = field(default=(), init=False, repr=False)

    def __post_init__(self):
        self._vol_times = tuple(row["available_at"] for row in self.volatility)

    def context(self, ts_ms: int) -> dict:
        """Last completed 30m decision boundary, never the current 5m close.

        Entry inputs are side-specific and use the prior completed 30m close.
        Missing indicators remain None rather than synthetic price fallbacks.
        """
        _integer(ts_ms, "ts_ms")
        cutoff = ts_ms // TIMEFRAME_MS["30m"] * TIMEFRAME_MS["30m"]
        if cutoff in self._contexts:
            return self._contexts[cutoff]
        stamp = pd.Timestamp(cutoff, unit="ms", tz="UTC")
        snapshot = engine._build_snapshot_at(self.frames6TF, stamp)
        slices = {tf: engine._get_tf_slice(self.frames6TF, stamp, tf)
                  for tf in ("30m", "1h", "4h", "12h", "1d")}

        def latest(tf, index=-1):
            frame = slices[tf]
            if len(frame) < abs(index):
                return None
            raw = frame.iloc[index]
            values = {key: float(raw[key]) if pd.notna(raw[key]) else None
                      for key in ("open", "high", "low", "close", "ma10", "ma35", "atr14")}
            values["close_ts"] = int(frame.index[index].value // 1_000_000) + TIMEFRAME_MS[tf]
            return values

        prior = latest("30m")
        price = prior["close"] if prior else None
        hour = latest("1h")
        entries = {"long": None, "short": None}
        if price is not None and hour and hour["atr14"] is not None and hour["ma35"] is not None:
            for side, field_name, method in (("long", "low", "min"), ("short", "high", "max")):
                reference = float(getattr(slices["1h"][field_name].iloc[-10:], method)())
                entries[side] = EntryInputs(price, hour["atr14"], reference, hour["ma35"])
        four, trail = latest("4h"), latest("12h")
        result = {"snapshot": snapshot, "entry_inputs": entries,
                  "latest4h": four, "previous4h": latest("4h", -2),
                  "latest1d": latest("1d"), "trailing_ma12h": trail["ma10"] if trail else None,
                  "prior_close30m": price, "last4hclose_ts": four["close_ts"] if four else None,
                  "feature_cutoff_ms": cutoff}
        self._contexts[cutoff] = result
        return result

    def regime_at(self, ts_ms: int) -> dict:
        _integer(ts_ms, "ts_ms")
        snapshot = self.context(ts_ms)["snapshot"]
        position = bisect_right(self._vol_times, ts_ms) - 1
        vol = self.volatility[position] if position >= 0 else {}
        four = snapshot.tf_states.get("4h") if snapshot else None
        day = snapshot.tf_states.get("1d") if snapshot else None
        return classify_regime(ts_ms=ts_ms, trend4h=four.trend if four else None,
            trend1d=day.trend if day else None,
            strength4h=float(trend_strength(four)) if four else None,
            shock_event_ms=vol.get("shock_event_ms"), volatility_ready=vol.get("ready", False))


def prepare_inputs(market_db, trade5m_db, mark_db=None) -> InputBundle:
    """Fixed 2022–2025 financial window and explicitly separate causal warmup."""
    frames, manifests = {}, {}
    for tf in engine.ALL_TFS:
        end = END if tf != "1w" else END - 3 * TIMEFRAME_MS["1d"]
        loaded = load_bars(market_db, tf, WARMUP, end)
        frames[tf] = add_indicators(_frame(loaded.rows))
        manifests[tf] = loaded.manifest
    traded = load_bars(trade5m_db, "5m", TRADE_WARMUP, END)
    sequence = volatility_sequence(_frame(traded.rows))
    bars = tuple({"ts": row["open_time"], **{key: row[key] for key in
                  ("open", "high", "low", "close", "volume")},
                  "prior_volume": traded.rows[index - 1]["volume"]}
                 for index, row in enumerate(traded.rows) if row["open_time"] >= START)
    funding = load_funding(market_db, START, END, 8 * TIMEFRAME_MS["1h"])
    mark = load_mark_bars(mark_db) if mark_db is not None else None
    mark_rows = tuple({"ts": row["open_time"], **{key: row[key] for key in
                       ("open", "high", "low", "close")}} for row in mark.rows) if mark else None
    manifest = {"schema_version": 1, "start_ms": START, "end_ms_exclusive": END,
                "financial_bar_count": len(bars), "warmup_bar_count": len(traded.rows) - len(bars),
                "frames6TF": manifests, "trade5m": traded.manifest, "funding": funding.manifest,
                "mark": mark.manifest if mark else {"status": "MARK_MISSING", "proxy": "LAST_PROXY"},
                "features": "Closed native 6TF SMA/Wilder ATR; no current candle inputs",
                "capacity": "previous completed 5m volume only; not observed queue liquidity",
                "regimes": "Ordinal descriptive proxy, not probabilities or observed order flow; range includes mixed trends",
                "source_contract": "Caller-supplied frozen files; mode=ro/query_only SQLite read snapshots"}
    return InputBundle(bars, tuple({"ts": r["funding_time"], "rate": r["rate"]} for r in funding.rows),
                       mark_rows, frames, manifest, sequence)
