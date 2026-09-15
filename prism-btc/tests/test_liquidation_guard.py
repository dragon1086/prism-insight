# Reuse the integration fixture; test parameters intentionally shadow its import.
# ruff: noqa: F811
from dataclasses import replace
from types import SimpleNamespace

import pytest
from live import liquidation_guard as guard
from live.entry_reservations import EntryReservationStore
from live.shared_entry_coordinator import LaneSnapshot

from .test_shared_entry_coordinator import payload, reply, setup  # noqa: F401


def lanes():
    data = {"fx": 1., "tiers": ((300000., .0033, 150.),), "liq": None, "started": 0.,
            "margin_balance": 10000., "available": 9000., "current_mm": 0.}
    return [LaneSnapshot(name, 0., None, 0., 100., 0., (), dict(data)) for name in ("main", "swing")]


@pytest.mark.parametrize("lane", ["main", "swing"])
def test_flat_positive(lane):
    assert 0 < guard.validate(10000., lanes(), (), lane, "long", 1., 100., 95., 10.) < 10


def test_no_profitable_stop_credit_and_pending_remaining_only():
    rows = lanes()
    rows[0] = replace(rows[0], qty=1., side="long", entry=80., stop=95.)
    pending = SimpleNamespace(lane="main", remaining_qty=2., requested_qty=1000., side="long",
                              price_bound=100., stop=95.)
    value = guard.validate(10000., rows, (pending,), "swing", "short", 1., 100., 105.)
    assert value > 24  # Four units stress independently, not opposite-side netted.
    assert value < 30  # Requested rather than remaining quantity must not count.


def test_main_add_and_existing_liq_veto():
    rows = lanes()
    rows[0] = replace(rows[0], qty=1., side="long", entry=80., stop=95.)
    assert guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.) > 12
    rows[0].liquidation["liq"] = 96.
    with pytest.raises(ValueError, match="existing_stop_buffer"):
        guard.validate(10000., rows, (), "main", "long", 1., 100., 95.)


def test_main_capital_once_and_swing_wallet_cannot_rescue():
    with pytest.raises(ValueError, match="shared_capital_exhausted"):
        guard.validate(5., lanes(), (), "swing", "long", 1., 100., 95.)


@pytest.mark.parametrize("side,entry,stop", [("long", 110., 95.), ("short", 90., 105.)])
def test_swing_accrued_loss_is_debited_once_not_main_or_profits(side, entry, stop):
    rows = lanes()
    rows[1] = replace(rows[1], qty=1., side=side, entry=entry, stop=stop)
    losing = guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.)
    rows[1] = replace(rows[1], entry=100.)
    baseline = guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.)
    assert losing == pytest.approx(baseline + 10.)
    rows[1] = replace(rows[1], entry=200. - entry)
    assert guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.) == baseline
    rows[1].liquidation["margin_balance"] = 179000.
    assert guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.) == baseline


@pytest.mark.parametrize("side,stop", [("long", 101.), ("short", 99.), ("long", 100.)])
def test_already_crossed_stop_blocks(side, stop):
    rows = lanes()
    rows[1] = replace(rows[1], qty=1., side=side, entry=80., stop=stop)
    with pytest.raises(ValueError, match="existing_stop_buffer"):
        guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.)


@pytest.mark.parametrize("field,value", [("available", 1.), ("margin_balance", 5.), ("current_mm", 9999.)])
def test_real_wallet_cannot_be_rescued_by_shared_capital(field, value):
    rows = lanes()
    rows[1].liquidation[field] = value
    with pytest.raises(ValueError, match="account_margin_exhausted"):
        guard.validate(10000., rows, (), "swing", "long", 1., 100., 95.)


def test_main_planned_leverage_unknown_blocks():
    with pytest.raises(ValueError, match="data_unknown"):
        guard.validate(10000., lanes(), (), "main", "long", 1., 100., 95.)


def test_main_uses_official_tier_not_only_old_fixed_maintenance():
    rows = lanes()
    rows[0].liquidation["tiers"] = ((300000., .05, 20.),)
    with pytest.raises(ValueError, match="planned_stop_buffer"):
        guard.validate(10000., rows, (), "main", "long", 1., 100., 95., 10.)


def test_pending_opening_fee_counted_without_recharging_reserved_im():
    rows = lanes()
    pending = SimpleNamespace(lane="main", remaining_qty=1., side="long", price_bound=100., stop=95.)
    value = guard.validate(10000., rows, (pending,), "swing", "long", 1., 100., 95.)
    assert value == pytest.approx(2 * (6 + 100 * (.0033 + 2 * guard.CLOSE_FEE)))
    rows[0].liquidation["available"] = .01  # Old order IM is already reserved.
    assert guard.validate(10000., rows, (pending,), "swing", "long", 1., 100., 95.) == value


def test_fx_and_short_stress_tier():
    rows = lanes()
    baseline = guard.validate(10000., rows, (), "swing", "short", 1., 100., 105.)
    rows[1].liquidation["fx"] = 2.
    assert guard.validate(10000., rows, (), "swing", "short", 1., 100., 105.) == 2 * baseline
    rows[1].liquidation["tiers"] = ((105., .0033, 150.),)
    with pytest.raises(ValueError, match="risk_tier_unknown"):
        guard.validate(10000., rows, (), "swing", "short", 1., 100., 105.)


def test_swing_planned_leverage_buffer():
    with pytest.raises(ValueError, match="planned_stop_buffer"):
        guard.validate(10000., lanes(), (), "swing", "long", 1., 100., 80.)


@pytest.mark.parametrize("field,value", [("accountMMRate", "1"), ("accountIMRate", "1.1"),
                                       ("accountMMRate", "nan"), ("accountMMRate", None),
                                       ("totalMarginBalance", "0"), ("totalAvailableBalance", "inf"),
                                       ("coin", [])])
def test_bad_wallet_blocks_before_reservation_and_order(setup, field, value):
    conn, sessions, main, _ = setup
    wallet = sessions["swing"].get_wallet_balance()["result"]["list"][0]
    sessions["swing"].get_wallet_balance = lambda **_: reply([{**wallet, field: value}])
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert not sessions["main"].submissions
    assert EntryReservationStore(conn.execute("PRAGMA database_list").fetchone()[2]).active() == ()


@pytest.mark.parametrize("mode", ["ISOLATED_MARGIN", "PORTFOLIO_MARGIN", None])
def test_unknown_margin_mode_blocks(setup, mode):
    _, sessions, main, _ = setup
    sessions["main"].get_account_info = lambda: {"retCode": 0, "result": {"marginMode": mode}}
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert not sessions["main"].submissions


def test_incomplete_risk_tiers_block(setup):
    _, sessions, main, _ = setup
    response = sessions["main"].get_risk_limit()
    response["result"]["nextPageCursor"] = "unknown-more"
    sessions["main"].get_risk_limit = lambda **_: response
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert not sessions["main"].submissions


@pytest.mark.parametrize("changes,cap", [({"totalAvailableBalance": "100", "totalInitialMargin": "0"}, 100.),
                                       ({"totalMaintenanceMargin": "10", "accountMMRate": ".5"}, 20.)])
def test_reported_collateral_bound_uses_wallet_haircuts(setup, changes, cap):
    _, sessions, _, _ = setup
    session = sessions["main"]
    wallet = session.get_wallet_balance()["result"]["list"][0] | changes
    data = guard.capture(lambda method, **kw: getattr(session, method)(**kw), wallet, {}, 0.)
    assert data["margin_balance"] == cap


def test_new_gets_share_original_snapshot_deadline(setup, monkeypatch):
    from live import shared_entry_coordinator as coordinator
    _, sessions, main, _ = setup
    clock = [0.]
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    original = sessions["swing"].get_risk_limit
    def slow(**kwargs):
        clock[0] = 11.
        return original(**kwargs)
    sessions["swing"].get_risk_limit = slow
    assert main._place_limit_postonly("long", 1., 100., stop_price=95., pending_payload=payload()) is None
    assert not sessions["main"].submissions
