"""Read-only progression evidence must not invent settlement or fill deltas."""
import json

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


def pending_entry_notice(broker, *, side="LONG", parent_changes=None, stale=False):
    active, children, observed = notice_fixture()
    active.update(side=side, initial_equity=9519.87522273)
    observed["position"]["side"] = "Buy" if side == "LONG" else "Sell"
    plan = dict(action="OPEN", action_id="entry-parent", scenario_id=active["scenario_id"],
                side=side, entries=[dict(id="entry", price=86100, quantity=.043)],
                take_profits=[dict(id="tp", price=86892.7 if side == "LONG" else 85307.3, fraction=1)])
    plan.update(parent_changes or {})
    broker.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                        ("entry-parent", active["scenario_id"], json.dumps(plan)))
    child = dict(kind="entry", status="TERMINAL", intent_id="entry-parent", local_id="entry",
                 evidence=dict(order={}, executions=[dict(execId="tp-omission", execQty="0.043",
                     execPrice="86100", execTime=str(int((observed["captured_at"]-(300 if stale else 0))*1000)))]))
    children.append(child)
    return active, children, observed


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pending_entry_keeps_exact_parent_tp_plan_without_claiming_live_targets(live, side):
    from live.scenario_notice import render_notice
    broker, exchange = live
    active, children, observed = pending_entry_notice(broker, side=side)
    events = broker._notices(active, children, observed, True, None, pending=True)
    event = next(e for e in events if e["kind"] == "FILLED")
    assert "position_after" not in event
    assert event["take_profits_scope"] == "entry_intent_plan"
    assert event["plan_action_id"] == "entry-parent"
    assert event["take_profits"][0]["fraction"] == 1
    assert event["scenario_initial_equity"] == active["initial_equity"]
    text = render_notice(event)
    assert ("86,892.70" if side == "LONG" else "85,307.30") in text
    assert "진입 당시 TP 계획" in text
    assert "계획 물량의 100%" in text
    assert "TP 설정 미확인 · 위 가격은 계획" in text
    assert "전체 포지션 기준 수익률 미확인" in text
    assert "+34.09" not in text  # No whole-position PnL from one fill.
    assert exchange.writes == []
    # Late complete observations do not resend or mutate the old fill event.
    again = broker._notices(active, children, observed, True, None)
    assert next(e for e in again if e["kind"] == "FILLED") == event


@pytest.mark.parametrize("changes", [dict(scenario_id="different"), dict(action_id="different"),
    dict(side="SHORT"), dict(action="EXIT"), dict(entries=[dict(id="other")]),
    dict(take_profits=None), dict(take_profits=[dict(price=0, fraction=1)]),
    dict(take_profits=[dict(price=86892.7, fraction=2)])])
def test_invalid_parent_plan_does_not_borrow_active_plan_or_drop_fill(live, changes):
    from live.scenario_notice import render_notice
    broker, exchange = live
    active, children, observed = pending_entry_notice(broker, parent_changes=changes)
    active["current_plan"] = dict(take_profits=[dict(price=99999, fraction=1)])
    event = next(e for e in broker._notices(active, children, observed, True, None, pending=True)
                 if e["kind"] == "FILLED")
    assert "take_profits" not in event
    text = render_notice(event)
    assert "🎯 TP 계획: 자료 미확인" in text and "99,999" not in text
    assert "고정 TP 없는 계획" not in text
    assert exchange.writes == []


def test_stale_fill_can_reference_original_plan_but_not_current_position(live):
    broker, _ = live
    active, children, observed = pending_entry_notice(broker, stale=True)
    event = next(e for e in broker._notices(active, children, observed, True, None, pending=True)
                 if e["kind"] == "FILLED")
    assert event["take_profits_scope"] == "entry_intent_plan"
    assert "position_after" not in event and "account_snapshot" not in event


def test_explicit_empty_parent_targets_are_not_missing(live):
    from live.scenario_notice import render_notice
    broker, _ = live
    active, children, observed = pending_entry_notice(broker, parent_changes=dict(take_profits=[]))
    event = next(e for e in broker._notices(active, children, observed, False, None, pending=True)
                 if e["kind"] == "FILLED")
    text = render_notice(event)
    assert "고정 TP 없는 계획" in text
    assert "TP 계획: 자료 미확인" not in text
    assert "SL 보호 유지" not in text


@pytest.mark.parametrize("damage", ["missing", "bad_json", "missing_table"])
def test_unavailable_entry_plan_does_not_suppress_fill(live, damage):
    from live.scenario_notice import render_notice
    broker, exchange = live
    active, children, observed = pending_entry_notice(broker)
    if damage == "missing":
        broker.conn.execute("DELETE FROM llm_scenario_intents")
    elif damage == "bad_json":
        broker.conn.execute("UPDATE llm_scenario_intents SET payload='not JSON'")
    else:
        broker.conn.execute("DROP TABLE llm_scenario_intents")
    event = next(e for e in broker._notices(active, children, observed, True, None, pending=True)
                 if e["kind"] == "FILLED")
    assert event["fill_confirmed"] is True
    assert "🎯 TP 계획: 자료 미확인" in render_notice(event)
    assert exchange.writes == []


def test_confirmed_position_targets_override_original_entry_plan(live):
    from live.scenario_notice import render_notice
    broker, _ = live
    active, children, observed = pending_entry_notice(broker)
    event = next(e for e in broker._notices(active, children, observed, True, None)
                 if e["kind"] == "FILLED")
    text = render_notice(event)
    assert "🎯 익절 TP 105.00" in text
    assert "86,892.70" not in text and "진입 당시 TP 계획" not in text


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


@pytest.mark.parametrize('equity', [9998.9, 0.])
def test_closed_notice_preserves_verified_flat_equity_and_time(live, equity):
    from live.scenario_notice import render_notice
    broker, exchange = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed['equity'] = equity
    events = broker._notices(active, children, observed, True, settlement)
    event = events[0]
    assert event['position_after']['quantity'] == 0
    assert event['position_after']['account_snapshot']['equity'] == equity
    rendered = render_notice(event)
    assert f'💰 종료 후 순자산: {equity:,.2f} USDT' in rendered
    assert '조회' in rendered
    observed['equity'] = 12345.
    assert broker._notices(active, children, observed, True, settlement)[0] == event
    assert exchange.writes == []


@pytest.mark.parametrize('offset', [-1, 121])
def test_closed_notice_does_not_attach_pre_exit_or_stale_equity(live, offset):
    from live.scenario_notice import render_notice
    broker, _ = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed['captured_at'] = 1799999999 + offset
    event = broker._notices(active, children, observed, True, settlement)[0]
    assert 'position_after' not in event
    assert '종료 후 순자산: 미확인' in render_notice(event)


@pytest.mark.parametrize('offset', [0, 120])
def test_closed_capture_time_inclusive_boundaries(live, offset):
    broker, _ = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed['captured_at'] = 1799999999 + offset
    event = broker._notices(active, children, observed, True, settlement)[0]
    assert event['position_after']['timestamp'] == observed['captured_at']


@pytest.mark.parametrize('equity', [None, -1., float('nan'), float('inf'), True])
def test_closed_invalid_optional_equity_never_blocks_valid_settlement(live, equity):
    from live.scenario_notice import render_notice
    broker, exchange = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed['equity'] = equity
    event = broker._notices(active, children, observed, True, settlement)[0]
    assert 'position_after' not in event
    assert '확정 순손익: -1.10 USDT' in render_notice(event)
    assert '종료 후 순자산: 미확인' in render_notice(event)
    assert exchange.writes == []


@pytest.mark.parametrize('invalid', ['wrong_account', 'wrong_event', 'unverified', 'missing', 'nonflat'])
def test_closed_producer_refuses_ineligible_optional_snapshot(live, monkeypatch, invalid):
    from live import scenario_notice_evidence as evidence
    from live.scenario_notice import render_notice
    broker, _ = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    candidate = evidence.position_snapshot(active, children, observed, True, False, None)
    if invalid == 'missing':
        candidate = None
    elif invalid == 'unverified':
        candidate['verified'] = False
    elif invalid == 'nonflat':
        candidate.update(quantity=.1, average_entry_price=100., hard_stop=99.)
    else:
        candidate['account_snapshot']['same_account' if invalid == 'wrong_account' else 'same_event'] = False
    monkeypatch.setattr(evidence, 'position_snapshot', lambda *args, **kwargs: candidate)
    event = broker._notices(active, children, observed, True, settlement)[0]
    assert 'position_after' not in event
    assert '종료 후 순자산: 미확인' in render_notice(event)


def test_missing_equity_on_first_close_is_never_backfilled_on_retry(live):
    broker, _ = live
    active, children, observed, settlement = flat_notice_fixture(broker)
    observed['equity'] = None
    first = broker._notices(active, children, observed, True, settlement)[0]
    observed['equity'] = 12345.
    second = broker._notices(active, children, observed, True, settlement)[0]
    assert second == first
    assert 'position_after' not in second


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
