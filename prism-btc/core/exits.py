# core/exits.py — Pure exit-decision logic (결정-집행 분리)
#
# evaluate_exits() is a PURE function: given an immutable view of one open
# position, the current bar, and a precomputed context, it returns the ORDERED
# list of Actions the adapter must execute for that position this bar. It never
# mutates state, touches equity, or emits TradeLogs.
#
# The decision sequence mirrors backtest/engine.py's original inline position
# loop EXACTLY (funding → liq-breach force-reduce → trail update → SL → TP1 →
# BE/trail activation), so the backtest is behavior-preserving. The same
# function will drive the live daemon's exit logic.
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from core.actions import (
    Action_ExitT,
    ChargeFunding,
    ForceReduce,
    ClearBreachFlag,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
)

Side = Literal["long", "short"]


# ---------------------------------------------------------------------------
# Immutable inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PositionView:
    """Immutable snapshot of the position fields the exit logic reads.

    A view, not the live Position: core never mutates it. The adapter builds
    this from its own Position object before each evaluate_exits call.
    """
    side: Side
    entry_price: float
    qty: float
    sl_price: float
    tp1_price: float
    liq_price: float
    trailing_active: bool
    be_stop_set: bool
    tp1_hit: bool
    liq_breach_flagged: bool


@dataclass(frozen=True)
class BarView:
    """The current 30m bar OHLC + bar index."""
    idx: int
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ExitContext:
    """Precomputed, pandas-free context for one bar.

    funding_due: True iff this bar is a funding boundary (bar_idx % 16 == 0).
    funding_rate: the resolved rate to apply when funding_due. Sign-aware path
        passes the looked-up rate; fallback path passes abs(FUNDING_RATE). The
        adapter resolves which (it owns the funding table); core only applies
        amount = qty * close * funding_rate * sign for the sign-aware path, or
        amount = qty * close * funding_rate for the fallback path.
    funding_sign_aware: selects the funding formula (see above).
    trailing_ma: the TRAILING_TF MA10 value this bar, or None if unavailable
        (insufficient rows / NaN). Injected so core stays pandas-free.
    be_trail_activate_r: BE_TRAIL_ACTIVATE_R constant (passed in, not imported,
        to keep core decoupled from engine fee/threshold constants).
    liq_monitor_frac: LIQ_MONITOR_FRAC constant.
    """
    funding_due: bool
    funding_rate: float
    funding_sign_aware: bool
    trailing_ma: Optional[float]
    be_trail_activate_r: float
    liq_monitor_frac: float


# ---------------------------------------------------------------------------
# Pure exit evaluation
# ---------------------------------------------------------------------------

def evaluate_exits(
    pos: PositionView,
    bar: BarView,
    ctx: ExitContext,
) -> list[Action_ExitT]:
    """Return the ordered exit Actions for `pos` on `bar`.

    Mirrors the original engine inline loop. The adapter executes the returned
    actions in order and STOPS applying further actions for this position once a
    ClosePosition (or qty-exhausting ForceReduce) is realized — exactly as the
    original `continue` short-circuits did. To make that explicit, this function
    itself stops emitting once it determines the position closes.
    """
    actions: list[Action_ExitT] = []

    # --- 1. Funding every FUNDING_INTERVAL_BARS, attributed to this leg ---
    if ctx.funding_due:
        if ctx.funding_sign_aware:
            sign = 1.0 if pos.side == "long" else -1.0
            amount = pos.qty * bar.close * ctx.funding_rate * sign
        else:
            amount = pos.qty * bar.close * ctx.funding_rate
        actions.append(ChargeFunding(amount=amount))

    # --- 2. Liq-approach: mark breaching 50% of entry→liq gap → forced reduce ---
    gap = abs(pos.entry_price - pos.liq_price)
    if gap > 0:
        adverse_mark = bar.low if pos.side == "long" else bar.high
        if pos.side == "long":
            mark_to_liq = adverse_mark - pos.liq_price
        else:
            mark_to_liq = pos.liq_price - adverse_mark
        in_breach = (mark_to_liq / gap) < ctx.liq_monitor_frac
        if in_breach and not pos.liq_breach_flagged:
            reduce_qty = pos.qty * 0.5
            if reduce_qty > 0:
                if pos.side == "long":
                    reduce_gross = (adverse_mark - pos.entry_price) * reduce_qty
                else:
                    reduce_gross = (pos.entry_price - adverse_mark) * reduce_qty
                actions.append(ForceReduce(
                    fraction=0.5,
                    price=adverse_mark,
                    gross=reduce_gross,
                    first_breach=True,
                ))
                # If halving exhausts qty the original closed with
                # "liq_forced_reduce" and `continue`d. qty after a 0.5 reduce is
                # only <= 0 when qty was already 0, which can't reach here
                # (reduce_qty > 0). So no same-call close path; the adapter
                # detects qty<=0 post-reduce identically. Stop emitting further
                # exit actions only if the adapter would have continued — which
                # it does NOT here, so fall through to trail/SL/TP as original.
        elif not in_breach:
            actions.append(ClearBreachFlag())

    # --- 3. Trailing stop: track TRAILING_TF MA10 once active ---
    if pos.trailing_active and ctx.trailing_ma is not None:
        if pos.side == "long":
            new_sl = max(pos.sl_price, ctx.trailing_ma)
        else:
            new_sl = min(pos.sl_price, ctx.trailing_ma)
        actions.append(UpdateStop(new_stop=new_sl))
        sl_for_check = new_sl
    else:
        sl_for_check = pos.sl_price

    # --- 4. SL hit ---
    sl_hit = False
    if pos.side == "long" and bar.low <= sl_for_check:
        sl_hit = True
    elif pos.side == "short" and bar.high >= sl_for_check:
        sl_hit = True
    if sl_hit:
        reason = "be" if (pos.be_stop_set and sl_for_check == pos.entry_price) else "sl"
        actions.append(ClosePosition(price=sl_for_check, reason=reason))
        return actions  # original `continue` — no TP/BE this bar

    # --- 5. TP1 partial (1/3 at 1R), once ---
    if not pos.tp1_hit:
        tp1_hit = False
        if pos.side == "long" and bar.high >= pos.tp1_price:
            tp1_hit = True
        elif pos.side == "short" and bar.low <= pos.tp1_price:
            tp1_hit = True
        if tp1_hit:
            actions.append(BookPartial(
                fraction=1.0 / 3.0,
                price=pos.tp1_price,
                fee_kind="maker",
                reason="tp1",
            ))

    # --- 6. BE stop + trailing activation at BE_TRAIL_ACTIVATE_R ---
    if not pos.trailing_active:
        r_dist = abs(pos.tp1_price - pos.entry_price)  # tp1 == 1R
        if r_dist > 0:
            if pos.side == "long":
                reached = bar.high >= pos.entry_price + ctx.be_trail_activate_r * r_dist
            else:
                reached = bar.low <= pos.entry_price - ctx.be_trail_activate_r * r_dist
            if reached:
                if pos.side == "long":
                    be_stop = max(sl_for_check, pos.entry_price)
                else:
                    be_stop = min(sl_for_check, pos.entry_price)
                actions.append(UpdateStop(new_stop=be_stop))
                actions.append(ActivateBETrail())

    return actions
