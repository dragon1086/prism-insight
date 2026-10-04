"""Read-only progression evidence must not invent settlement or fill deltas."""
import pytest

from live.scenario_notice_evidence import position_snapshot
from tests import test_scenario_execution as execution_tests
from tests.test_scenario_execution import flat_notice_fixture, notice_fixture


@pytest.fixture(name="live")
def notice_broker(tmp_path):
    return execution_tests.live.__wrapped__(tmp_path)


def accounting(**changes):
    result = dict(status="confirmed", accounting_complete=True, gross_pnl=20.,
                  fees=3., funding_net=-1., net_pnl=16.)
    result.update(changes)
    return result


def snapshot(account):
    active, children, observed = notice_fixture()
    return position_snapshot(active, children, observed, True, False, account)


def test_confirmed_progress_reuses_net_including_all_recorded_costs():
    result = snapshot(accounting())
    assert result["scenario_realized_net_pnl"] == 16
    assert result["scenario_accounting_confirmed"] is True
    assert result["scenario_initial_equity"] == 10000


@pytest.mark.parametrize("changes", [dict(status="unknown"), dict(accounting_complete=False),
    dict(accounting_complete=None), dict(net_pnl=None), dict(net_pnl=float("nan")),
    dict(net_pnl=True), dict(net_pnl=0), dict(funding_net=None)])
def test_unknown_or_inconsistent_progress_never_becomes_zero(changes):
    result = snapshot(accounting(**changes))
    assert "scenario_realized_net_pnl" not in result
    assert "scenario_accounting_confirmed" not in result


def test_confirmed_zero_is_distinct_from_missing():
    assert snapshot(accounting(gross_pnl=4., net_pnl=0.))["scenario_realized_net_pnl"] == 0
    assert "scenario_realized_net_pnl" not in snapshot(None)


def test_closed_notice_uses_original_equity_not_current_account(live):
    broker, exchange = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed["equity"] = 9998.9
    events = broker._notices(active, children, observed, True, settlement)
    assert events[0]["scenario_initial_equity"] == 10000
    assert exchange.writes == []


def test_multiple_fills_share_observation_total_without_per_fill_before(live):
    broker, exchange = live
    active, children, observed = notice_fixture()
    assert broker._notices(active, children, observed, True, None) == []
    observed["captured_at"] += 60
    observed["position"]["size"] = "2"
    children.append(dict(kind="entry", status="TERMINAL", evidence=dict(order={}, executions=[
        dict(execId="a", execQty="0.4", execPrice="100", execTime="1800000040000"),
        dict(execId="b", execQty="0.6", execPrice="101", execTime="1800000050000")])))
    events = broker._notices(active, children, observed, True, None, accounting=accounting())
    assert [event["kind"] for event in events] == ["FILLED", "FILLED"]
    for event in events:
        assert "position_before" not in event
        assert event["position_snapshot_scope"] == "post_observation_total"
        assert event["position_after"]["quantity"] == 2
        assert event["position_after"]["scenario_realized_net_pnl"] == 16
    assert broker._notices(active, children, observed, True, None, accounting=accounting()) == events
    assert exchange.writes == []


def test_stale_fill_does_not_borrow_current_realized_total(live):
    broker, _ = live
    active, children, observed = notice_fixture()
    children.append(dict(kind="tp", status="TERMINAL", evidence=dict(order={}, executions=[
        dict(execId="old", execQty="0.5", execPrice="105", execTime="1799999000000")])))
    event = broker._notices(active, children, observed, True, None, accounting=accounting())[0]
    assert "position_after" not in event
    assert "net_pnl" not in event
    assert event["settlement_confirmed"] is False


def test_accounting_only_change_does_not_generate_repeat_notice(live):
    broker, _ = live
    active, children, observed = notice_fixture()
    assert broker._notices(active, children, observed, True, None, accounting=accounting()) == []
    observed["captured_at"] += 60
    assert broker._notices(active, children, observed, True, None,
        accounting=accounting(funding_net=-2., net_pnl=15.)) == []


@pytest.mark.parametrize("changes,expected", [
    (dict(execution_gross_pnl={"exit": -3.5}), -3.5),
    (dict(execution_gross_pnl={"exit": 0.}), 0.),
    (dict(execution_gross_pnl={"other": 12.}), None),
    (dict(execution_gross_pnl={"exit": float("inf")}), None),
    (dict(execution_gross_pnl={"exit": True}), None),
    (dict(execution_gross_pnl=None), None),
    (dict(execution_gross_pnl={"exit": 12.}, accounting_complete=False), None),
    (dict(execution_gross_pnl={"exit": 12.}, status="unknown"), None),
])
def test_partial_price_pnl_requires_exact_confirmed_execution(live, changes, expected):
    broker, _ = live
    active, children, observed = notice_fixture()
    children.append(dict(kind="tp", status="TERMINAL", evidence=dict(order={}, executions=[
        dict(execId="exit", execQty="0.5", execPrice="105", execTime="1800000000000")])))
    event = broker._notices(active, children, observed, True, None, accounting=accounting(**changes))[0]
    assert event.get("fill_gross_pnl") == expected
    assert event.get("fill_gross_pnl_confirmed") is (True if expected is not None else None)
    assert event["settlement_confirmed"] is False
    assert "net_pnl" not in event
    assert event["entry_price"] is None


def test_reduction_does_not_use_all_entries_average_as_exit_cost(live):
    broker, _ = live
    active, children, observed = notice_fixture()
    for kind, eid, price, qty, when in [
        ("entry", "first", "100", "1", "1799999500000"),
        ("tp", "old-exit", "110", "0.5", "1799999600000"),
        ("entry", "add", "120", "1", "1799999700000"),
        ("tp", "last-exit", "130", "0.5", "1800000000000"),
    ]:
        children.append(dict(kind=kind, status="TERMINAL", evidence=dict(order={}, executions=[
            dict(execId=eid, execQty=qty, execPrice=price, execTime=when)])))
    events = broker._notices(active, children, observed, True, None,
        accounting=accounting(execution_gross_pnl={"last-exit": 8.25}))
    partial = next(event for event in events if event["event_id"] == "scenario-fill-last-exit")
    assert partial["fill_gross_pnl"] == 8.25
    assert partial["entry_price"] is None
