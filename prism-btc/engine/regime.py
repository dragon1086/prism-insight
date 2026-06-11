# engine/regime.py — Multi-TF regime tagging and alignment score
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal

import pandas as pd

from engine.config import (
    TF_WEIGHTS,
    MAX_WEIGHT_SUM,
    FLAT_THRESHOLD,
    TOUCH_TOL,
    CANDLE_BONUS_FRAC,
)
from engine.indicators import add_indicators

log = logging.getLogger(__name__)

TrendState = Literal["up", "down", "flat"]
CandlePosition = Literal[
    "break_ma10_up",
    "break_ma10_down",
    "break_ma35_up",
    "break_ma35_down",
    "support_ma10",
    "resist_ma10",
    "support_ma35",
    "resist_ma35",
    "above_all",
    "below_all",
    "between",
]


@dataclass
class TFState:
    trend: TrendState
    candle_position: CandlePosition
    ma10: float
    ma35: float
    close: float
    atr14: float


@dataclass
class RegimeSnapshot:
    tf_states: dict[str, TFState]
    alignment_score: float  # -100 to +100
    evaluated_at: str  # ISO8601 UTC

    def to_dict(self) -> dict:
        return {
            "alignment_score": round(self.alignment_score, 2),
            "evaluated_at": self.evaluated_at,
            "tf_states": {
                tf: asdict(s) for tf, s in self.tf_states.items()
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trend(ma10: float, ma35: float, close: float) -> TrendState:
    gap_ratio = abs(ma10 - ma35) / close
    if gap_ratio < FLAT_THRESHOLD:
        return "flat"
    return "up" if ma10 > ma35 else "down"


def _candle_position(
    open_: float,
    high: float,
    low: float,
    close: float,
    ma10: float,
    ma35: float,
) -> CandlePosition:
    """
    Classify last confirmed candle relative to MA10 and MA35.

    Rules (applied in priority order):
    1. break_ma10_up   : prev close < ma10 and current close > ma10 (crossover up)
       — simplified here: open < ma10 and close > ma10
    2. break_ma10_down : open > ma10 and close < ma10
    3. break_ma35_up   : open < ma35 and close > ma35
    4. break_ma35_down : open > ma35 and close < ma35
    5. above_all       : low > max(ma10, ma35)
    6. below_all       : high < min(ma10, ma35)
    7. support_ma10    : candle body above ma10, low touches ma10 band
    8. resist_ma10     : candle body below ma10, high touches ma10 band
    9. support_ma35    : candle body above ma35, low touches ma35 band
    10. resist_ma35    : candle body below ma35, high touches ma35 band
    11. between        : catch-all
    """
    body_low = min(open_, close)
    body_high = max(open_, close)

    ma10_lo = ma10 * (1 - TOUCH_TOL)
    ma10_hi = ma10 * (1 + TOUCH_TOL)
    ma35_lo = ma35 * (1 - TOUCH_TOL)
    ma35_hi = ma35 * (1 + TOUCH_TOL)

    # Crossover / breakdown through MA10
    if open_ < ma10 and close > ma10:
        return "break_ma10_up"
    if open_ > ma10 and close < ma10:
        return "break_ma10_down"
    # Crossover / breakdown through MA35
    if open_ < ma35 and close > ma35:
        return "break_ma35_up"
    if open_ > ma35 and close < ma35:
        return "break_ma35_down"

    # Above both MAs
    if low > max(ma10, ma35):
        return "above_all"
    # Below both MAs
    if high < min(ma10, ma35):
        return "below_all"

    # MA10 support: body is above ma10, low dips into ma10 band
    if body_low >= ma10 and low <= ma10_hi:
        return "support_ma10"
    # MA10 resistance: body is below ma10, high probes into ma10 band
    if body_high <= ma10 and high >= ma10_lo:
        return "resist_ma10"
    # MA35 support: body is above ma35, low dips into ma35 band
    if body_low >= ma35 and low <= ma35_hi:
        return "support_ma35"
    # MA35 resistance: body is below ma35, high probes into ma35 band
    if body_high <= ma35 and high >= ma35_lo:
        return "resist_ma35"

    return "between"


def _candle_aligns_with_trend(position: CandlePosition, trend: TrendState) -> int:
    """
    +1 if candle position supports the trend direction.
    -1 if opposes.
     0 if neutral/flat.
    """
    if trend == "flat":
        return 0

    bullish_positions = {
        "break_ma10_up", "break_ma35_up", "support_ma10", "support_ma35", "above_all"
    }
    bearish_positions = {
        "break_ma10_down", "break_ma35_down", "resist_ma10", "resist_ma35", "below_all"
    }

    if trend == "up":
        if position in bullish_positions:
            return 1
        if position in bearish_positions:
            return -1
    else:  # down
        if position in bearish_positions:
            return 1
        if position in bullish_positions:
            return -1
    return 0


def _score_tf(tf: str, state: TFState) -> float:
    """Weighted contribution of one TF to alignment score (before normalization).

    Base score = trend_direction * weight.
    Bonus = trend_direction * |candle_align| * weight * BONUS_FRAC when aligned,
            trend_direction * candle_align * weight * BONUS_FRAC when opposed
    (i.e. bonus always reinforces or penalises in the trend_direction axis).
    """
    w = TF_WEIGHTS[tf]
    trend_direction = 1 if state.trend == "up" else (-1 if state.trend == "down" else 0)
    candle_align = _candle_aligns_with_trend(state.candle_position, state.trend)
    # candle_align: +1 = supports trend, -1 = opposes, 0 = neutral
    # Multiply by trend_direction so the bonus always points in the trend axis
    base = trend_direction * w
    bonus = trend_direction * candle_align * w * CANDLE_BONUS_FRAC
    return base + bonus


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_tf_state(df: pd.DataFrame) -> TFState:
    """
    Compute TFState from a DataFrame of confirmed klines (oldest first).
    df must have columns: open, high, low, close. At least 35 rows needed.
    """
    # Fast path: skip recomputation when indicators are precomputed (backtest).
    # Rolling SMA/ATR are causal (past-only), so precomputed prefix values are
    # identical to computing on the slice — no look-ahead introduced.
    if "ma10" not in df.columns or "atr14" not in df.columns:
        df = add_indicators(df)
    last = df.iloc[-1]
    ma10 = last["ma10"]
    ma35 = last["ma35"]
    close = last["close"]
    atr14 = last["atr14"]

    if pd.isna(ma10) or pd.isna(ma35) or pd.isna(atr14):
        raise ValueError("Insufficient data to compute indicators (need >= 35 rows)")

    trend = _trend(ma10, ma35, close)
    position = _candle_position(
        open_=last["open"],
        high=last["high"],
        low=last["low"],
        close=close,
        ma10=ma10,
        ma35=ma35,
    )
    return TFState(
        trend=trend,
        candle_position=position,
        ma10=round(ma10, 4),
        ma35=round(ma35, 4),
        close=round(close, 4),
        atr14=round(atr14, 4),
    )


def compute_alignment_score(tf_states: dict[str, TFState]) -> float:
    """
    Compute alignment score in [-100, +100].

    Sum of (trend_direction × weight + candle_bonus) for each TF, then
    normalize by the theoretical maximum achievable score.
    """
    raw = sum(_score_tf(tf, state) for tf, state in tf_states.items())
    # Max possible raw = sum of w * (1 + CANDLE_BONUS_FRAC) for all TFs
    max_raw = MAX_WEIGHT_SUM * (1 + CANDLE_BONUS_FRAC)
    score = (raw / max_raw) * 100.0
    return max(-100.0, min(100.0, score))


def build_snapshot(
    tf_dfs: dict[str, pd.DataFrame],
    evaluated_at: datetime | None = None,
) -> RegimeSnapshot:
    """
    Build a RegimeSnapshot from a dict of {tf: DataFrame}.
    Each DataFrame should have columns open, high, low, close (oldest first).
    """
    if evaluated_at is None:
        evaluated_at = datetime.now(timezone.utc)

    tf_states: dict[str, TFState] = {}
    for tf, df in tf_dfs.items():
        try:
            tf_states[tf] = build_tf_state(df)
        except ValueError as exc:
            log.warning("Skipping %s: %s", tf, exc)

    score = compute_alignment_score(tf_states)
    return RegimeSnapshot(
        tf_states=tf_states,
        alignment_score=score,
        evaluated_at=evaluated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
