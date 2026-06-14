# core/actions.py — Immutable decision objects (결정-집행 분리)
#
# The pure decision functions in core/exits.py and core/entries.py never mutate
# state or touch equity/TradeLog. They return ORDERED lists of these Action
# dataclasses. An adapter (backtest/engine.py for backtests, a live daemon for
# production) interprets the actions and performs the actual accounting/IO.
#
# Ordering matters: the engine executes actions in list order, mirroring the
# exact sequence of the original inline loop. Do not reorder actions emitted by
# a single evaluate_* call.
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

from engine.sizing import SizingResult

Side = Literal["long", "short"]


# ---------------------------------------------------------------------------
# Exit-side actions (emitted by core.exits.evaluate_exits)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChargeFunding:
    """Charge funding for one position leg this bar (every FUNDING_INTERVAL_BARS).

    `amount` is the signed $ to DEDUCT from equity (positive = pay, negative =
    receive). Pre-computed by core so the adapter only books it.
    """
    amount: float


@dataclass(frozen=True)
class ForceReduce:
    """Liquidation-buffer breach: book a forced partial reduction at `price`.

    fraction == fraction of CURRENT remaining qty to close (0.5 in v3).
    gross == pre-cost price PnL realized on the reduced qty (for liq_forced_reduce
    instrumentation). first_breach flags the once-per-breach count + flag set.
    """
    fraction: float
    price: float
    gross: float
    first_breach: bool


@dataclass(frozen=True)
class ClearBreachFlag:
    """Mark-price left the 50% liq buffer band: clear the breach flag."""


@dataclass(frozen=True)
class UpdateStop:
    """Set the position stop to `new_stop` (trailing MA / BE). Monotone tighten
    is already applied by core, so the adapter assigns unconditionally."""
    new_stop: float


@dataclass(frozen=True)
class ClosePosition:
    """Close the entire remaining qty at `price` for `reason`."""
    price: float
    reason: str


@dataclass(frozen=True)
class BookPartial:
    """Close `fraction` of CURRENT remaining qty at `price` (TP1 partial leg)."""
    fraction: float
    price: float
    fee_kind: Literal["maker", "taker", "taker_sl"]
    reason: str


@dataclass(frozen=True)
class ActivateBETrail:
    """Set BE stop (already folded into a preceding UpdateStop) and turn the
    trailing flag on. Emitted at BE_TRAIL_ACTIVATE_R."""


# ---------------------------------------------------------------------------
# Entry-side action (emitted by core.entries.evaluate_entry)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OpenIntent:
    """Intent to place a post-only entry order next bar.

    Carries everything the adapter needs to build a PendingOrder. `initial_risk`
    is the risk-capital cap (equity × RISK_PER_TRADE × tranche_frac).
    """
    side: Side
    limit_price: float
    sizing: SizingResult
    initial_risk: float
    tranche_index: int


# ---------------------------------------------------------------------------
# Union aliases (typing only) — the ordered action streams each evaluator emits.
# ---------------------------------------------------------------------------

Action_ExitT = Union[
    ChargeFunding,
    ForceReduce,
    ClearBreachFlag,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
]

Action = Union[Action_ExitT, OpenIntent]
