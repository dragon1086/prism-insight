# core/ — Pure decision logic (결정-집행 분리).
#
# Decisions are expressed as immutable Action objects (core.actions) produced by
# pure functions (core.exits.evaluate_exits, core.entries.evaluate_entry). The
# backtest engine and the live daemon both interpret the same Actions, so they
# share one decision brain. Accounting / IO live in the adapter, not here.
from core.actions import (  # noqa: F401
    Action,
    Action_ExitT,
    ChargeFunding,
    ForceReduce,
    ClearBreachFlag,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
    OpenIntent,
)
from core.exits import (  # noqa: F401
    PositionView,
    BarView,
    ExitContext,
    evaluate_exits,
)
from core.entries import (  # noqa: F401
    EntryInputs,
    CooldownState,
    evaluate_entry,
)
