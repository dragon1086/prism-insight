"""Conditional entries remain bounded reservations, never market chases."""
import json

import pytest

from core.llm_scenario import ScenarioValidationError, validate_scenario
from tests.test_llm_scenario import sample
from tests.test_scenario_execution import live as execution_live, persist
from tests.test_scenario_exchange import session, order
from live.scenario_broker import BrokerNotReady
from live.scenario_contract import identity_fields, response_schema, validate_wire_proposal
from core.scenario_limit_prices import validate_execution_prices, POLICY_VERSION


@pytest.fixture
def live(tmp_path):
    return execution_live.__wrapped__(tmp_path)


def conditional():
    p, c = sample()
    c.update(conditional_entry_version=1, mark_price=99900, price_tick=.1)
    p['entries'][0]['trigger_price'] = 99950
    p['chase'] = dict(max_bps=0, max_reprices=0)
    return p, c


def test_conditional_core_reserves_full_limit_risk():
    p, c = conditional()
    assert validate_scenario(p, c)['risk']['total_risk'] == pytest.approx(60.95)


def test_core_short_and_opposite_pending_rejected():
    p, c = conditional()
    p.update(side='SHORT', hard_stop=101000, take_profits=[])
    c['mark_price'] = 100200
    p['entries'][0]['trigger_price'] = 100100
    assert validate_scenario(p, c)['side'] == 'SHORT'
    c.update(scenario_id='s1', side='LONG', previous_hard_stop=99000)
    p['action'] = 'ADJUST'
    with pytest.raises(ScenarioValidationError, match='opposite'):
        validate_scenario(p, c)


def test_wire_trigger_required_nullable_with_legacy_regular_normalization():
    p, c = conditional()
    p.update(identity_fields(c))
    p['cancel_entry_ids'] = []
    assert 'trigger_price' in response_schema(c)['properties']['entries']['items']['required']
    assert validate_wire_proposal(p, c)['entries'][0]['trigger_price'] == 99950
    p['entries'][0]['trigger_price'] = None
    assert 'trigger_price' not in validate_wire_proposal(p, c)['entries'][0]
    del p['entries'][0]['trigger_price']
    assert 'trigger_price' not in validate_wire_proposal(p, c)['entries'][0]


def test_explicit_limit_not_round_price_corrected():
    p, c = conditional()
    c.update(execution_price_policy=dict(version=POLICY_VERSION),
             limit_price_quotes=dict(bid=99899, ask=99900, captured_at=1000))
    result = validate_execution_prices(p, c)
    assert result['entries'][0]['price'] == 100000
    assert result['execution_pricing']['rows'][0]['reason'] == 'conditional_explicit_limit'


@pytest.mark.parametrize('change', ['disabled', 'past_trigger', 'bad_limit', 'expiry', 'chase', 'tick'])
def test_conditional_core_invalid(change):
    p, c = conditional()
    if change == 'disabled': c.pop('conditional_entry_version')
    if change == 'past_trigger': p['entries'][0]['trigger_price'] = 99800
    if change == 'bad_limit': p['entries'][0]['trigger_price'] = 100001
    if change == 'expiry': p['expires_at'] = 1201
    if change == 'chase': p['chase']['max_bps'] = 1
    if change == 'tick': p['entries'][0]['trigger_price'] = 99950.01
    with pytest.raises(ScenarioValidationError): validate_scenario(p, c)


def test_response_crossing_decision_boundary_cannot_renew_reservation():
    p, c = conditional()
    c.update(input_captured_at=1199, now=1201)
    p['expires_at'] = 1500
    with pytest.raises(ScenarioValidationError, match='next decision'):
        validate_scenario(p, c)


def test_native_wire_and_immutable_expiry(live):
    b, e = live
    p = persist(b, entries=[dict(id='e1', price=102, quantity=.1, trigger_price=101)],
                chase=dict(max_bps=0, max_reprices=0))
    b.execute(p, 'a1')
    child = b.children()[0]
    assert child['request']['triggerPrice'] == '101'
    assert child['request']['triggerDirection'] == 1
    assert child['request']['triggerBy'] == 'MarkPrice'
    assert child['request']['slOrderType'] == 'Market'
    assert b.context()['pending_entries'][0]['trigger_price'] == 101
    with pytest.raises(BrokerNotReady, match='conditional_entry_chase'):
        b.chase('a1', 'e1', 103)
    state = json.loads(b.conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
    state['active']['expires_at'] += 3600
    b.conn.execute('UPDATE llm_scenario_state SET body=?', (json.dumps(state),))
    b.conn.commit()
    b.clock = lambda: 1800000301
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'


def armed(live):
    b, e = live
    p = persist(b, entries=[dict(id='e1', price=102, quantity=.1, trigger_price=101)],
                chase=dict(max_bps=0, max_reprices=0))
    b.execute(p, 'a1')
    return b, e, p


@pytest.mark.parametrize('field,value', [('triggerPrice', '101.1'), ('triggerBy', 'LastPrice'),
                                       ('triggerDirection', 2), ('positionIdx', 1),
                                       ('timeInForce', 'IOC'), ('slOrderType', 'Limit')])
def test_exact_trigger_terms_cannot_change_after_trigger(live, field, value):
    b, e, _ = armed(live)
    child = b.children()[0]
    e.orders[child['link_id']].update(orderStatus='Triggered', **{field: value})
    with pytest.raises(BrokerNotReady, match='conditional_entry_terms_changed'):
        b._query_child(child)


def test_realistic_order_response_without_undocumented_sl_order_type(live):
    b, e, _ = armed(live)
    child = b.children()[0]
    del e.orders[child['link_id']]['slOrderType']
    assert b._query_child(child)['status'] == 'LIVE'
    # Request terms are not post-fill protection proof: verify the actual native order.
    e.fill(child['link_id'], .04)
    observed = b.capture_account()
    assert b._verify_protection(observed, 98, 'LONG') is True
    e.orders['native']['orderType'] = 'Limit'
    assert b._verify_protection(b.capture_account(), 98, 'LONG') is False
    del e.orders['native']['orderType']
    assert b._verify_protection(b.capture_account(), 98, 'LONG') is False


def test_unknown_submission_never_duplicates(live):
    b, e = live
    e.lose_ack = True
    p = persist(b, entries=[dict(id='e1', price=102, quantity=.1, trigger_price=101)],
                chase=dict(max_bps=0, max_reprices=0))
    with pytest.raises(BrokerNotReady): b.execute(p, 'a1')
    e.lose_ack = False
    b.execute(p, 'a1')
    assert len([write for write in e.writes if write[0] == 'place']) == 1


def test_conditional_fill_price_cannot_exceed_explicit_cap(live):
    b, e, _ = armed(live)
    child = b.children()[0]
    e.fill(child['link_id'], .04)
    e.fills[-1]['execPrice'] = '103'
    with pytest.raises(BrokerNotReady, match='fill_exceeded_limit'):
        b._query_child(child)


def test_unknown_cancel_keeps_pending_reservation(live, monkeypatch):
    b, e, _ = armed(live)
    monkeypatch.setattr(e, 'cancel_order', lambda **kw: dict(retCode=0, result={}))
    b.clock = lambda: 1800000301
    result = b.reconcile()
    assert b.children()[0]['status'] == 'LIVE'
    assert result['settlement'] is None
    assert result['intents'][0]['open_entries'][0]['quantity'] == .1
    assert result['intents'][0]['orders_reconciled'] is False


def test_expiry_cancel_partial_fill_race_is_protected(live, monkeypatch):
    b, e, _ = armed(live)
    cancel = e.cancel_order
    def race(**kw):
        e.fill(kw['orderLinkId'], .04)
        return cancel(**kw)
    monkeypatch.setattr(e, 'cancel_order', race)
    b.clock = lambda: 1800000301
    result = b.reconcile()
    assert float(e.size) == .04
    assert result['protection_confirmed'] is True
    assert result['settlement'] is None
    assert b.children()[0]['status'] == 'TERMINAL'


def test_hard_stop_crossed_before_trigger_cancels(live, monkeypatch):
    b, e, _ = armed(live)
    from tests.test_scenario_broker import reply
    monkeypatch.setattr(e, 'get_tickers', lambda **kw: reply([dict(symbol='BTCUSDT',
        markPrice='97', lastPrice='97', nextFundingTime='1800003600000')]))
    b.reconcile()
    assert b.children()[0]['status'] == 'TERMINAL'
    assert float(e.size) == 0


@pytest.mark.parametrize('case', ['trigger_crossed', 'opposite', 'chase', 'expiry', 'capacity'])
def test_fresh_preflight_blocks_invalid_reservation(live, case):
    b, e = live
    p = persist(b, entries=[dict(id='e1', price=102, quantity=.1, trigger_price=101)],
                chase=dict(max_bps=0, max_reprices=0))
    obs = b.capture_account()
    if case == 'trigger_crossed': obs['mark_price'] = 101
    if case == 'opposite': p.update(side='SHORT', hard_stop=105, entries=[])
    if case == 'chase': p['chase']['max_reprices'] = 1
    if case == 'expiry': p['expires_at'] += 1
    if case == 'capacity': obs['open_orders'] = [dict(orderStatus='Untriggered', triggerPrice='90')] * 10
    with pytest.raises(BrokerNotReady): b._preflight(p, obs, b._instrument(), [])
    assert e.writes == []


def test_flat_conditional_batch_reserves_native_stop_capacity(live):
    b, e = live
    p = persist(b, entries=[dict(id=f'e{i}', price=102, quantity=.001, trigger_price=101)
                           for i in range(10)], chase=dict(max_bps=0, max_reprices=0))
    with pytest.raises(BrokerNotReady, match='conditional_order_capacity'):
        b.execute(p, 'a1')
    assert e.writes == []


def test_native_short_trigger_direction(live):
    b, _ = live
    state = json.loads(b.conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
    state['active'].update(side='SHORT', hard_stop=102)
    b.conn.execute('UPDATE llm_scenario_state SET body=?', (json.dumps(state),))
    b.conn.commit()
    p = persist(b, side='SHORT', hard_stop=102,
                entries=[dict(id='e1', price=98, quantity=.1, trigger_price=99)],
                chase=dict(max_bps=0, max_reprices=0))
    b.execute(p, 'a1')
    request = b.children()[0]['request']
    assert request['side'] == 'Sell'
    assert request['triggerDirection'] == 2
    assert request['triggerPrice'] == '99'
    assert request['price'] == '98'


@pytest.mark.parametrize('side,trigger,limit,before,gap', [
    ('Buy', 101, 102, 100, 103), ('Sell', 99, 98, 100, 97)])
def test_offline_no_fill_before_trigger_or_beyond_limit(side, trigger, limit, before, gap):
    s = session()
    oid = order(s, side=side, price=str(limit), triggerPrice=str(trigger),
                triggerBy='MarkPrice', triggerDirection=1 if side == 'Buy' else 2)
    s.advance(100, before, before, 1000)
    assert s.position == 0
    s.advance(200, gap, gap, 1000)
    assert s.position == 0
    s.advance(300, limit, limit, 1000)
    assert s.orders[oid]['orderStatus'] == 'Filled'
