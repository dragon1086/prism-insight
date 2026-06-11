# core/entries.py — Pure entry-decision logic (결정-집행 분리)
#
# evaluate_entry() is a PURE function: given a generated Signal, the equity, the
# current pyramid/tranche context, the re-entry cooldown state, and precomputed
# pandas-derived inputs (1h ATR / swing ref / MA35), it returns an OpenIntent or
# None. It never mutates state and never touches pandas — the adapter owns the
# DataFrames and slices the indicator inputs, then hands them in here.
#
# The decision sequence mirrors backtest/engine.py's original inline entry block
# EXACTLY (tranche==0 cooldown gate → compute_sizing → rejection check; or
# pyramid can_add_tranche → compute_sizing → rejection check), so the backtest is
# behavior-preserving. The same function will drive the live daemon's entries.
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from engine.signal import Signal
from engine.sizing import (
    compute_sizing,
    can_add_tranche,
    TRANCHE_FRACS,
)
import engine.sizing as _sizing  # RISK_PER_TRADE 단일 소스 (런타임 조회)

from core.actions import OpenIntent

Side = Literal["long", "short"]


@dataclass(frozen=True)
class EntryInputs:
    """Precomputed, pandas-free inputs for one entry evaluation.

    The adapter slices its 1h frame and computes these before calling. Values
    match the original inline engine derivation:
      atr_1h    — 14-period ATR on 1h close (entry*0.02 fallback)
      swing_ref — recent 10-bar 1h low (long) / high (short) (±2% fallback)
      ma35_1h   — 35-period MA on 1h close (entry fallback)
    """
    entry_price: float
    atr_1h: float
    swing_ref: float
    ma35_1h: float


@dataclass(frozen=True)
class CooldownState:
    """Re-entry cooldown inputs for the signal's side (tranche 0 only).

    bars_since_close — bar_idx - last_close_bar[side]
    cooldown_bars    — SL_REENTRY_COOLDOWN_BARS if last close was a SL, else
                       REENTRY_COOLDOWN_BARS (resolved by the adapter).
    """
    bars_since_close: int
    cooldown_bars: int


def evaluate_entry(
    sig: Signal,
    equity: float,
    current_tranche: int,
    *,
    inputs: EntryInputs,
    cooldown: Optional[CooldownState] = None,
    avg_entry: Optional[float] = None,
    current_price: Optional[float] = None,
) -> Optional[OpenIntent]:
    """Return an OpenIntent to place next bar, or None.

    current_tranche == number of same-side open positions.
      0       → fresh entry: apply re-entry cooldown gate, then size.
      1 or 2  → pyramid: apply can_add_tranche gate, then size.
      >= 3    → no add (original caps at <3).

    `cooldown` is required for the tranche-0 path; `avg_entry`/`current_price`
    are required for the pyramid path (can_add_tranche inputs). The adapter is
    responsible for the upstream 4h-cadence gate and per-4h hardcap — those are
    execution-cadence concerns, not part of the sizing decision.
    """
    if sig.side == "none":
        return None

    if current_tranche == 0:
        # P1-1 re-entry cooldown: same-direction re-entry needs N bars since last
        # close of that side (16 if last close was a SL, else 8).
        if cooldown is not None and cooldown.bars_since_close < cooldown.cooldown_bars:
            return None

        sz = compute_sizing(
            side=sig.side,
            entry=inputs.entry_price,
            abs_score=sig.strength,
            equity=equity,
            atr_1h=inputs.atr_1h,
            swing_ref=inputs.swing_ref,
            ma35_1h=inputs.ma35_1h,
            tranche_index=0,
        )
        if not sz.rejected and sz.qty > 0:
            risk_cap = equity * _sizing.RISK_PER_TRADE * TRANCHE_FRACS[0]
            return OpenIntent(
                side=sig.side,
                limit_price=inputs.entry_price,
                sizing=sz,
                initial_risk=risk_cap,
                tranche_index=0,
            )
        return None

    if current_tranche < 3:
        # Pyramid: only add if can_add_tranche passes for the averaged entry.
        if avg_entry is None or current_price is None:
            return None
        if not can_add_tranche(current_tranche, avg_entry, current_price, sig.side):
            return None

        sz = compute_sizing(
            side=sig.side,
            entry=inputs.entry_price,
            abs_score=sig.strength,
            equity=equity,
            atr_1h=inputs.atr_1h,
            swing_ref=inputs.swing_ref,
            ma35_1h=inputs.ma35_1h,
            tranche_index=current_tranche,
        )
        if not sz.rejected and sz.qty > 0:
            risk_cap = equity * _sizing.RISK_PER_TRADE * TRANCHE_FRACS[current_tranche]
            return OpenIntent(
                side=sig.side,
                limit_price=inputs.entry_price,
                sizing=sz,
                initial_risk=risk_cap,
                tranche_index=current_tranche,
            )
        return None

    return None
