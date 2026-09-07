"""Causal descriptive market cells, not a trading gate or probability estimate."""
from __future__ import annotations

import math

import pandas as pd

from engine.config import TS_MIN
from engine.indicators import atr

SHOCK_HOLD_MS = 3_600_000
BAR_MS = 300_000


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def classify_regime(*, ts_ms: int, trend4h, trend1d, strength4h,
                    shock_event_ms=None, volatility_ready=True) -> dict:
    """Label information available at ``ts_ms``; range includes mixed trends."""
    if type(ts_ms) is not int or ts_ms < 0:
        raise ValueError("invalid regime timestamp")
    if shock_event_ms is not None and (
            type(shock_event_ms) is not int or not 0 <= shock_event_ms <= ts_ms):
        raise ValueError("invalid or future shock event")
    valid = (trend4h in ("up", "down", "flat")
             and trend1d in ("up", "down", "flat")
             and _finite(strength4h) and strength4h >= 0
             and volatility_ready is True)
    trend = "unknown"
    if valid:
        trend = trend4h if trend4h == trend1d and trend4h != "flat" and strength4h >= TS_MIN else "range"
    shock = shock_event_ms is not None and ts_ms - shock_event_ms < SHOCK_HOLD_MS
    volatility = ("shock" if shock else "normal") if valid else "unknown"
    return {"trend": trend, "volatility": volatility,
            "label": f"{trend}_{volatility}" if valid else "unknown",
            "feature_cutoff_ms": ts_ms, "shock_event_ms": shock_event_ms}


def volatility_sequence(frame: pd.DataFrame) -> tuple[dict, ...]:
    """Completed 5m TR / PRIOR Wilder ATR; timestamps are bar availability times.

    Input is validated contiguous OHLC history from the strict input loader.
    Full-series vector calculations are causal; later values cannot alter a prefix.
    """
    previous = frame["close"].shift(1)
    tr = pd.concat([frame.high - frame.low, (frame.high - previous).abs(),
                    (frame.low - previous).abs()], axis=1).max(axis=1)
    baseline = atr(frame, 14).shift(1)
    event = None
    rows = []
    for stamp, value, denominator in zip(frame.index, tr, baseline):
        available = int(stamp.value // 1_000_000) + BAR_MS
        ready = bool(pd.notna(denominator) and math.isfinite(denominator) and denominator > 0)
        if ready and value >= 3 * denominator:
            event = available
        rows.append({"available_at": available, "ready": ready,
                     "shock_event_ms": event,
                     "prior_atr14": float(denominator) if ready else None,
                     "true_range": float(value)})
    return tuple(rows)
