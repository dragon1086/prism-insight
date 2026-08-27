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


@dataclass(frozen=True)
class EntryEvaluation:
    """A pure entry result plus an audit-safe reason when it is rejected."""

    intent: Optional[OpenIntent]
    reason: str


def evaluate_entry_with_reason(
    sig: Signal,
    equity: float,
    current_tranche: int,
    *,
    inputs: EntryInputs,
    cooldown: Optional[CooldownState] = None,
    avg_entry: Optional[float] = None,
    current_price: Optional[float] = None,
) -> EntryEvaluation:
    """Evaluate an entry and preserve the first deterministic rejection reason.

    ``evaluate_entry`` remains the compatibility wrapper used by backtests and
    callers that only need an intent.  Live adapters use this richer result to
    distinguish signal rejection, cooldown, pyramid gating, and sizing/buffer
    rejection in ``btc_events``.
    """
    if sig.side == "none":
        return EntryEvaluation(None, "signal_none")

    if current_tranche == 0:
        if cooldown is not None and cooldown.bars_since_close < cooldown.cooldown_bars:
            return EntryEvaluation(
                None,
                f"cooldown {cooldown.bars_since_close}/{cooldown.cooldown_bars}",
            )

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
        if sz.rejected:
            return EntryEvaluation(None, sz.reject_reason or "sizing_rejected")
        if sz.qty <= 0:
            return EntryEvaluation(None, "sizing_zero_qty")
        risk_cap = equity * _sizing.RISK_PER_TRADE * TRANCHE_FRACS[0]
        return EntryEvaluation(
            OpenIntent(
                side=sig.side,
                limit_price=inputs.entry_price,
                sizing=sz,
                initial_risk=risk_cap,
                tranche_index=0,
            ),
            "accepted",
        )

    if current_tranche < 3:
        if avg_entry is None or current_price is None:
            return EntryEvaluation(None, "pyramid_inputs_missing")
        if not can_add_tranche(current_tranche, avg_entry, current_price, sig.side):
            return EntryEvaluation(None, "pyramid_not_in_profit")

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
        if sz.rejected:
            return EntryEvaluation(None, sz.reject_reason or "sizing_rejected")
        if sz.qty <= 0:
            return EntryEvaluation(None, "sizing_zero_qty")
        risk_cap = equity * _sizing.RISK_PER_TRADE * TRANCHE_FRACS[current_tranche]
        return EntryEvaluation(
            OpenIntent(
                side=sig.side,
                limit_price=inputs.entry_price,
                sizing=sz,
                initial_risk=risk_cap,
                tranche_index=current_tranche,
            ),
            "accepted",
        )

    return EntryEvaluation(None, "max_tranches")


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
    return evaluate_entry_with_reason(
        sig, equity, current_tranche,
        inputs=inputs, cooldown=cooldown,
        avg_entry=avg_entry, current_price=current_price,
    ).intent
