# tests/test_core_exits.py — Unit tests for the pure exit-decision core.
#
# These exercise core.exits.evaluate_exits directly (no pandas, no DB): hand-built
# PositionView/BarView/ExitContext in, ordered Action list out. Covers the core
# paths the backtest engine relies on: SL hit, trail update, BE activation, TP1
# partial, and funding sign.
from __future__ import annotations

from core.exits import PositionView, BarView, ExitContext, evaluate_exits
from core.actions import (
    ChargeFunding,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
)

# Defaults mirroring engine constants used by the exit logic.
BE_R = 1.5
LIQ_FRAC = 0.50


def _pos(**over):
    base = dict(
        side="long",
        entry_price=100.0,
        qty=10.0,
        sl_price=95.0,
        tp1_price=105.0,   # 1R = 5
        liq_price=80.0,    # entry→liq gap 20 (large → no breach in these bars)
        trailing_active=False,
        be_stop_set=False,
        tp1_hit=False,
        liq_breach_flagged=False,
    )
    base.update(over)
    return PositionView(**base)


def _ctx(**over):
    base = dict(
        funding_due=False,
        funding_rate=0.0,
        funding_sign_aware=False,
        trailing_ma=None,
        be_trail_activate_r=BE_R,
        liq_monitor_frac=LIQ_FRAC,
    )
    base.update(over)
    return ExitContext(**base)


def test_sl_hit_long_closes_with_sl_reason():
    pos = _pos(sl_price=95.0)
    bar = BarView(idx=10, high=101.0, low=94.0, close=96.0)  # low pierces 95
    acts = evaluate_exits(pos, bar, _ctx())
    closes = [a for a in acts if isinstance(a, ClosePosition)]
    assert len(closes) == 1
    assert closes[0].reason == "sl"
    assert closes[0].price == 95.0
    # ClosePosition short-circuits: no TP/BE actions after it.
    assert not any(isinstance(a, (BookPartial, ActivateBETrail)) for a in acts)


def test_be_stop_close_classified_be():
    # be_stop_set with SL exactly at entry → reason "be", not "sl".
    pos = _pos(sl_price=100.0, be_stop_set=True, entry_price=100.0)
    bar = BarView(idx=10, high=101.0, low=99.0, close=100.0)
    acts = evaluate_exits(pos, bar, _ctx())
    closes = [a for a in acts if isinstance(a, ClosePosition)]
    assert len(closes) == 1
    assert closes[0].reason == "be"


def test_trail_update_tightens_stop_long():
    pos = _pos(trailing_active=True, sl_price=96.0)
    bar = BarView(idx=10, high=110.0, low=99.0, close=108.0)  # no SL hit at 98
    acts = evaluate_exits(pos, bar, _ctx(trailing_ma=98.0))
    ups = [a for a in acts if isinstance(a, UpdateStop)]
    assert ups and ups[0].new_stop == 98.0  # max(96, 98)


def test_trail_never_loosens_stop_long():
    pos = _pos(trailing_active=True, sl_price=99.0)
    bar = BarView(idx=10, high=110.0, low=100.0, close=108.0)
    acts = evaluate_exits(pos, bar, _ctx(trailing_ma=97.0))
    ups = [a for a in acts if isinstance(a, UpdateStop)]
    assert ups and ups[0].new_stop == 99.0  # max(99, 97) — keeps tighter 99


def test_be_activation_at_1_5R_long():
    # 1R = 5, 1.5R reach at 107.5; high 108 triggers BE activation.
    pos = _pos(sl_price=95.0, tp1_price=105.0, tp1_hit=True)
    bar = BarView(idx=10, high=108.0, low=101.0, close=107.0)
    acts = evaluate_exits(pos, bar, _ctx())
    assert any(isinstance(a, ActivateBETrail) for a in acts)
    ups = [a for a in acts if isinstance(a, UpdateStop)]
    assert ups and ups[-1].new_stop == 100.0  # BE = max(95, entry 100)


def test_no_be_activation_below_1_5R():
    pos = _pos(sl_price=95.0, tp1_price=105.0)
    bar = BarView(idx=10, high=107.0, low=101.0, close=106.0)  # < 107.5
    acts = evaluate_exits(pos, bar, _ctx())
    assert not any(isinstance(a, ActivateBETrail) for a in acts)


def test_tp1_partial_books_one_third():
    pos = _pos(tp1_price=105.0, tp1_hit=False)
    bar = BarView(idx=10, high=106.0, low=101.0, close=105.5)  # high >= tp1
    acts = evaluate_exits(pos, bar, _ctx())
    parts = [a for a in acts if isinstance(a, BookPartial)]
    assert len(parts) == 1
    assert abs(parts[0].fraction - 1.0 / 3.0) < 1e-12
    assert parts[0].price == 105.0
    assert parts[0].fee_kind == "maker"


def test_tp1_not_rebooked_when_already_hit():
    pos = _pos(tp1_price=105.0, tp1_hit=True)
    bar = BarView(idx=10, high=106.0, low=101.0, close=105.5)
    acts = evaluate_exits(pos, bar, _ctx())
    assert not any(isinstance(a, BookPartial) for a in acts)


def test_funding_sign_long_pays_positive_rate():
    pos = _pos(side="long", qty=10.0)
    bar = BarView(idx=0, high=101.0, low=99.5, close=100.0)
    acts = evaluate_exits(pos, bar, _ctx(funding_due=True, funding_rate=0.0001,
                                        funding_sign_aware=True))
    fund = [a for a in acts if isinstance(a, ChargeFunding)]
    assert fund and fund[0].amount > 0  # long pays positive funding (deduct)


def test_funding_sign_short_receives_positive_rate():
    pos = _pos(side="short", entry_price=100.0, sl_price=105.0,
               tp1_price=95.0, liq_price=120.0, qty=10.0)
    bar = BarView(idx=0, high=100.5, low=99.0, close=100.0)
    acts = evaluate_exits(pos, bar, _ctx(funding_due=True, funding_rate=0.0001,
                                        funding_sign_aware=True))
    fund = [a for a in acts if isinstance(a, ChargeFunding)]
    assert fund and fund[0].amount < 0  # short receives (negative deduct)
