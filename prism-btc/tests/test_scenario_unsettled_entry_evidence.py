from copy import deepcopy
import json
import sqlite3

import pytest

from live.scenario_management_quarantine import (
    authorize_unsettled_entry_management, digest, inspect_unsettled_entry_evidence,
    prepare_unsettled_entry_authority, probe_unsettled_entry,
)


def evidence(side='LONG'):
    from live.scenario_entry_contract import bind_contract
    from tests.test_scenario_entry_contract_runtime import definition

    entry, exit_side = ('Buy', 'Sell') if side == 'LONG' else ('Sell', 'Buy')
    stop_price = '75720' if side == 'LONG' else '76720'
    request = dict(symbol='BTCUSDT', positionIdx=0, side=entry, qty='0.019',
                   price='76220', stopLoss=stop_price, orderType='Limit', reduceOnly=False,
                   slTriggerBy='MarkPrice', category='linear', timeInForce='GTC',
                   slOrderType='Market', tpslMode='Full')
    child = dict(kind='entry', status='UNKNOWN', evidence=None, link_id='entry-link',
                 order_id='order-id', intent_id='intent-id', scenario_id='scenario-id', request=request)
    order = dict(symbol='BTCUSDT', positionIdx=0, orderLinkId='entry-link', orderId='order-id',
                 side=entry, qty='0.019', cumExecQty='0.019', leavesQty='0', avgPrice='76220',
                 price='76220', stopLoss=stop_price, orderType='Limit', reduceOnly=False,
                 orderStatus='Filled', slTriggerBy='MarkPrice', timeInForce='GTC')
    stop = dict(orderId='native-stop-id', symbol='BTCUSDT', positionIdx=0, side=exit_side, reduceOnly=True,
                parentOrderLinkId='entry-link', stopOrderType='StopLoss', triggerBy='MarkPrice',
                orderType='Market', orderStatus='Untriggered', qty='0.019', leavesQty='0.019',
                triggerPrice=stop_price)
    mark_price = '76100' if side == 'LONG' else '76300'
    observed = dict(captured_at=1000, account_version='account-v1', mark_price=mark_price, legacy_fenced=True,
                    position=dict(symbol='BTCUSDT', positionIdx=0, side=entry,
                    size='0.019', avgPrice='76220', stopLoss=stop_price), open_orders=[stop])
    premise=definition(side)
    premise['explicit_failure']['price']=75820 if side=='LONG' else 76620
    plan=dict(action='OPEN',scenario_id='scenario-id',action_id='intent-id',input_id='input-id',
              side=side,entries=[dict(price=76220)],hard_stop=int(stop_price))
    contract=bind_contract(premise,plan,900)
    active = dict(scenario_id='scenario-id', side=side, hard_stop=int(stop_price), entry_contract=contract,
                  initial_equity=10000, created_at=900, revision=1)
    pending = [dict(id='intent-id', scenario_id='scenario-id', status='PENDING', action='OPEN')]
    return dict(child=child, order=order, observed=observed, active=active, children=[child],
                pending=pending, legacy_local=False, now=1001, order_captured_at=1000,
                maximum_quantity='0.019', identity_verified=True,
                endpoint='https://api-demo.bybit.com')


def restricted_context(receipt):
    return dict(management_quarantine=receipt,legacy_fenced=True,protection_ok=True,
                new_risk_blocked=True,accounting_status='pending',account_version='account-v1',
                account_captured_at=1000)


def origin_plan(value):
    active=value['active']
    return dict(action='OPEN',scenario_id='scenario-id',action_id='intent-id',input_id='input-id',
                side=active['side'],entries=[dict(price=76220)],hard_stop=int(active['hard_stop']),revision=1)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_aggregate_match_is_not_trade_or_management_authority(side):
    value=evidence(side)
    result=inspect_unsettled_entry_evidence(**value)
    assert result == {'status': 'AGGREGATE_MATCH', 'accounting': 'UNKNOWN',
                      'execution_evidence': 'UNKNOWN', 'authorization': False}
    assert value == evidence(side)


@pytest.mark.parametrize('mutation', [
    lambda x: x['order'].update(orderStatus='PartiallyFilled'),
    lambda x: x['order'].update(cumExecQty='0.018'),
    lambda x: x['order'].update(avgPrice='76221'),
    lambda x: x['order'].update(orderId='different'),
    lambda x: x['order'].update(timeInForce='IOC'),
    lambda x: x['child']['request'].update(triggerPrice='76100'),
    lambda x: x['observed']['position'].update(size='0.018'),
    lambda x: x['observed'].update(captured_at=989),
    lambda x: x.update(order_captured_at=989),
    lambda x: x.update(maximum_quantity='0.018'),
    lambda x: x.update(identity_verified=False),
    lambda x: x.update(endpoint='https://api.bybit.com'),
    lambda x: x['observed'].update(mark_price='75600'),
    lambda x: x['observed'].update(mark_price=None),
    lambda x: x['observed']['open_orders'][0].update(parentOrderLinkId='other'),
    lambda x: x['observed']['open_orders'][0].update(leavesQty='0.018'),
    lambda x: x['observed']['open_orders'][0].update(triggerBy='LastPrice'),
    lambda x: x['observed']['open_orders'].append(deepcopy(x['observed']['open_orders'][0])),
    lambda x: x.update(legacy_local=True),
    lambda x: x['pending'].append(dict(id='other', scenario_id='other', status='PENDING', action='OPEN')),
    lambda x: x['children'].append(dict(kind='exit', status='UNKNOWN')),
    lambda x: x['child'].update(order_id=None),
    lambda x: x['child'].update(evidence={}),
])
def test_mismatch_and_unknown_never_authorize(mutation):
    value=evidence();mutation(value)
    with pytest.raises(ValueError, match='management_quarantine_'):
        inspect_unsettled_entry_evidence(**value)


class BrokerStub:
    environment = 'demo'
    expected_main_uid = '123'

    def __init__(self):
        self.value = evidence()
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute('CREATE TABLE llm_scenario_control(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
        self.conn.execute('INSERT INTO llm_scenario_control VALUES(1,?)',
                          (json.dumps(dict(version=1, state='active', main_uid='123')),))
        self.conn.execute('CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY,scenario_id TEXT,payload TEXT,status TEXT)')
        self.conn.execute('CREATE TABLE llm_scenario_entry_contracts(scenario_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        self.conn.execute('INSERT INTO llm_scenario_entry_contracts VALUES(?,?)',
                          ('scenario-id',json.dumps(self.value['active']['entry_contract'],sort_keys=True)))
        self.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                          ('intent-id', 'scenario-id', json.dumps(origin_plan(self.value)), 'PENDING'))
        self.conn.commit()
        self.session = type('Session', (), {'endpoint': 'https://api-demo.bybit.com'})()
        self.calls = []
        self.on_capture = None

    def _identity(self):
        self.calls.append('identity')

    def _active(self):
        return deepcopy(self.value['active'])

    def children(self, scenario_id=None):
        rows=self.value['children']
        return deepcopy([row for row in rows if scenario_id is None or row.get('scenario_id')==scenario_id])

    def _legacy_local(self):
        return self.value['legacy_local']

    def clock(self):
        return self.value['now']

    def capture_account(self):
        if self.on_capture:
            self.on_capture(self)
        from live.scenario_management_quarantine import account_observation_fingerprint
        observed=deepcopy(self.value['observed'])
        self._last_account_observation_fingerprint=account_observation_fingerprint(observed)
        return observed

    def _call(self, method, **kwargs):
        self.calls.append(method)
        assert method in {'get_open_orders', 'get_order_history'}
        rows = [] if method == 'get_open_orders' else [deepcopy(self.value['order'])]
        return dict(retCode=0, result=dict(list=rows, nextPageCursor=''))

    def _query_child(self, child):
        self.calls.append('query_child')
        return deepcopy(child)


def test_broker_probe_uses_exact_reads_without_authorizing_or_writing():
    broker = BrokerStub()
    before = broker.conn.total_changes
    result = probe_unsettled_entry(broker, maximum_quantity='0.019')
    assert result['status'] == 'AGGREGATE_MATCH' and result['authorization'] is False
    assert result['execution_evidence'] == result['accounting'] == 'UNKNOWN'
    assert broker.calls == ['identity', 'get_open_orders', 'get_order_history']
    assert broker.conn.total_changes == before


def test_authority_preview_binds_exact_state_without_activating_it():
    broker=BrokerStub()
    before=broker.conn.total_changes
    preview=prepare_unsettled_entry_authority(broker, approval_id='approval', reason='review',
                                              expires_at=1600, maximum_quantity='0.019')
    assert preview['status']=='PROPOSED_NOT_AUTHORIZED' and preview['authorization'] is False
    assert preview['body']['allowed_actions']==['WAIT','EXIT']
    assert preview['body']['purpose']=='ADMIN_AUTHORIZATION_NOT_EXCHANGE_EVIDENCE'
    assert preview['sha256']==digest(preview['body'])
    assert broker.conn.total_changes==before


@pytest.mark.parametrize('expiry', [1000, 1001, 1001+86401, float('nan'), True])
def test_authority_preview_refuses_expired_or_overlong_window(expiry):
    with pytest.raises(ValueError,match='management_quarantine_'):
        prepare_unsettled_entry_authority(BrokerStub(),approval_id='approval',reason='review',
                                          expires_at=expiry,maximum_quantity='0.019')


def test_authority_preview_refuses_unbounded_approval_identifier():
    with pytest.raises(ValueError,match='management_quarantine_'):
        prepare_unsettled_entry_authority(BrokerStub(),approval_id='x'*129,reason='review',
                                          expires_at=1600,maximum_quantity='0.019')


def test_authority_preview_refuses_state_change_after_probe(monkeypatch):
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    real=quarantine.probe_unsettled_entry
    def changed(*args, **kwargs):
        result=real(*args, **kwargs)
        broker.conn.execute("UPDATE llm_scenario_intents SET payload=?",
                            (json.dumps({'action':'OPEN','changed':True}),))
        return result
    monkeypatch.setattr(quarantine,'probe_unsettled_entry',changed)
    with pytest.raises(ValueError,match='management_quarantine_state_changed_during_preview'):
        prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                          expires_at=1600,maximum_quantity='0.019')


def test_authorization_binds_preview_but_does_not_modify_trade_state_or_submit():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    before=broker.conn.execute('SELECT * FROM llm_scenario_intents').fetchall()
    receipt=authorize_unsettled_entry_management(broker,preview)
    assert receipt['allowed_actions']==['WAIT','EXIT'] and receipt['purpose']==quarantine.PURPOSE
    assert receipt['scope']=='ENTRY_AGGREGATE_ONLY' and receipt['execution_evidence']=='UNKNOWN'
    assert receipt['new_risk']=='BLOCKED'
    assert receipt['account_version']=='account-v1'
    event=quarantine._approval(broker)
    assert event['body']['version']==2 and event['body']['preview_sha256']==preview['sha256']
    projected=quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'])
    assert projected==receipt
    assert quarantine.pending_allowed(broker,restricted_context(receipt)) is True
    assert broker.conn.execute('SELECT * FROM llm_scenario_intents').fetchall()==before
    assert broker.value['children'][0]['status']=='UNKNOWN' and broker.value['children'][0]['evidence'] is None
    assert set(broker.calls)<= {'identity','get_open_orders','get_order_history'}


@pytest.mark.parametrize('change', [
    lambda b: b.value['order'].update(avgPrice='76221'),
    lambda b: b.value['observed']['open_orders'][0].update(parentOrderLinkId='other'),
    lambda b: b.conn.execute("UPDATE llm_scenario_intents SET payload=?",(json.dumps({'action':'OPEN','changed':True}),)),
])
def test_authorization_refuses_changed_live_evidence_or_intent(change):
    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    change(broker)
    with pytest.raises(ValueError,match='management_quarantine_'):
        authorize_unsettled_entry_management(broker,preview)
    assert broker.conn.execute('SELECT count(*) FROM llm_scenario_broker_evidence').fetchone()==(0,)


def test_authorization_refuses_preview_tampering_and_reuse():
    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    altered=deepcopy(preview);altered['body']['maximum_quantity']='1'
    with pytest.raises(ValueError,match='management_quarantine_'):
        authorize_unsettled_entry_management(broker,altered)
    authorize_unsettled_entry_management(broker,preview)
    with pytest.raises(ValueError,match='management_quarantine_'):
        authorize_unsettled_entry_management(broker,preview)
    assert broker.conn.execute('SELECT count(*) FROM llm_scenario_broker_evidence').fetchone()==(1,)


def test_authorization_fails_closed_on_corrupt_existing_authority_event():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    broker.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                        (quarantine.KIND,1000,json.dumps({'body':{'operation':'REVOKE'},'sha256':'bad'})))
    good={'operation':'REVOKE','approval_id':'old'}
    broker.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                        (quarantine.KIND,1000,json.dumps({'body':good,'sha256':digest(good)})))
    broker.conn.commit()
    with pytest.raises(ValueError,match='management_quarantine_'):
        authorize_unsettled_entry_management(broker,preview)
    assert broker.conn.execute('SELECT count(*) FROM llm_scenario_broker_evidence').fetchone()==(2,)


def test_authorized_entry_rechecks_account_and_never_authorizes_exit_yet():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    receipt=quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'])
    from live.scenario_contract import response_schema
    from live.scenario_llm import immediate_reduce_instructions
    schema=response_schema(dict(input_id='input',revision=1,scenario_id='scenario-id',management_quarantine=receipt))
    assert schema['properties']['action']['enum']==['WAIT','EXIT']
    instructions=immediate_reduce_instructions('base',{'management_quarantine':receipt})
    assert 'only WAIT or full EXIT' in instructions
    changed=deepcopy(broker.value['observed']);changed['account_version']='different'
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.project(broker,changed,broker.value['active'],broker.value['children'])
    forged=deepcopy(broker.value['observed']);forged['position']['size']='0.001'
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.project(broker,forged,broker.value['active'],broker.value['children'])
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'],self_exit='exit')


def test_authorized_entry_rejects_new_nonterminal_intent():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    broker.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                        ('other','other-scenario',json.dumps({'action':'OPEN'}),'LIVE_RECONCILED'))
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.pending_allowed(broker,restricted_context(receipt))


def test_entry_authority_requires_explicit_release_if_exact_execution_returns():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    broker.value['children'][0].update(status='TERMINAL',evidence={'executions':[{'execId':'confirmed'}]})
    assert quarantine.projection_failure(broker,broker.value['observed'])=='RELEASE_REQUIRED'


def test_entry_authority_flat_unknown_remains_unsettled():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    flat=deepcopy(broker.value['observed']);flat.update(exchange_flat=True,legacy_fenced=False,open_orders=[])
    flat['position'].update(size='0',side='')
    assert quarantine.projection_failure(broker,flat)=='FLAT_UNSETTLED'


def test_entry_authority_allows_nonfinancial_review_state_but_rejects_stop_change():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    broker.value['active']['last_review']={'status':'INTACT'}
    assert quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'])
    broker.value['active']['hard_stop']='75600'
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'])


def test_entry_authority_revoked_during_fresh_probe_never_projects():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    original=broker._call
    def revoke_during_order_read(method, **kwargs):
        if method=='get_order_history':
            quarantine.revoke(broker,approval_id='approval',reason='revoked')
        return original(method,**kwargs)
    broker._call=revoke_during_order_read
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.project(broker,broker.value['observed'],broker.value['active'],broker.value['children'])


def test_entry_authority_binds_one_exit_without_changing_unknown_entry():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    assert quarantine.pending_allowed(broker,restricted_context(receipt),self_exit='exit')
    assert quarantine.active_receipt(broker,self_exit='exit')==receipt
    assert broker.value['children'][0]['status']=='UNKNOWN' and broker.value['children'][0]['evidence'] is None
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.bind_exit(broker,payload,receipt)


def test_entry_exit_binding_refuses_any_payload_change():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='exit'",
                        (json.dumps(dict(payload, changed=True)),))
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.pending_allowed(broker,restricted_context(receipt),self_exit='exit')


@pytest.mark.parametrize('change', [
    lambda c: c.update(account_version='different'),
    lambda c: c.update(account_captured_at=989),
    lambda c: c.update(protection_ok=False),
    lambda c: c.update(new_risk_blocked=False),
    lambda c: c.update(accounting_status='confirmed'),
])
def test_pending_authority_rejects_unbound_or_unsafe_context(change):
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    context=restricted_context(receipt);change(context)
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.pending_allowed(broker,context)


def test_fenced_exception_requires_current_bound_limited_context():
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    assert not quarantine.fenced_entry_management_allowed(broker,restricted_context({}))
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    context=restricted_context(receipt)
    assert quarantine.fenced_entry_management_allowed(broker,context)
    for key,value in [('account_version','other'),('account_captured_at',989),('protection_ok',False),
                      ('new_risk_blocked',False),('accounting_status','confirmed')]:
        changed=deepcopy(context);changed[key]=value
        assert not quarantine.fenced_entry_management_allowed(broker,changed)
    quarantine.revoke(broker,approval_id='approval',reason='revoked')
    assert not quarantine.fenced_entry_management_allowed(broker,context)


@pytest.mark.parametrize('change', [
    lambda c: c.update(protection_ok=False),
    lambda c: c.update(legacy_fenced=False),
    lambda c: c.update(new_risk_blocked=False),
    lambda c: c.update(accounting_status='confirmed'),
    lambda c: c.update(account_version='different'),
    lambda c: c.update(scenario_id='different'),
    lambda c: c['management_quarantine'].update(execution_evidence='VERIFIED'),
    lambda c: c['management_quarantine'].update(approval_sha256='bad'),
    lambda c: c['management_quarantine'].update(allowed_actions=['WAIT','OPEN']),
])
def test_non_authorizing_review_classification_rejects_untrusted_receipt(change):
    from live import scenario_management_quarantine as quarantine

    broker=BrokerStub()
    broker.conn.execute('CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT)')
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    context=restricted_context(receipt);context['scenario_id']='scenario-id'
    assert quarantine.entry_aggregate_context(context,'WAIT')
    assert quarantine.entry_aggregate_context(context,'EXIT')
    assert not quarantine.entry_aggregate_context(context,'OPEN')
    assert not quarantine.entry_aggregate_context(context,'ADJUST')
    change(context)
    assert not quarantine.entry_aggregate_context(context,'WAIT')
    assert not quarantine.entry_aggregate_context(context,'EXIT')


@pytest.mark.parametrize('status', ['PENDING', 'LIVE_RECONCILED', 'UNKNOWN', None])
def test_broker_probe_refuses_foreign_unfinished_intent_even_without_child(status):
    broker = BrokerStub()
    broker.conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                        ('other-intent', 'other-scenario', json.dumps({'action':'OPEN'}), status))
    broker.conn.commit()
    with pytest.raises(ValueError, match='management_quarantine_'):
        probe_unsettled_entry(broker, maximum_quantity='0.019')


@pytest.mark.parametrize('change', [
    lambda b: b.value['children'][0].update(status='TERMINAL'),
    lambda b: b.conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL'"),
    lambda b: b.conn.execute("UPDATE llm_scenario_intents SET payload=?", (json.dumps({'action':'OPEN','changed':True}),)),
    lambda b: b.conn.execute("UPDATE llm_scenario_entry_contracts SET body='{}'"),
    lambda b: b.value['active'].update(hard_stop='75000'),
])
def test_broker_probe_refuses_state_changes_during_capture(change):
    broker = BrokerStub()
    broker.on_capture = change
    with pytest.raises(ValueError, match='management_quarantine_'):
        probe_unsettled_entry(broker, maximum_quantity='0.019')


def real_entry_broker(tmp_path):
    from live.scenario_broker import ScenarioDemoBroker
    from live.scenario_control import _write
    from tests.test_scenario_execution import Exchange
    from tests.test_scenario_broker import reply

    fixture=evidence()
    exchange=Exchange()
    exchange.size='0.019'
    exchange.stop='75720'
    exchange.orders={'entry-link': deepcopy(fixture['order']),
                     'native': deepcopy(fixture['observed']['open_orders'][0])}
    exchange.get_positions=lambda **kw: reply([deepcopy(fixture['observed']['position']) | {'leverage':'10'}])
    exchange.get_tickers=lambda **kw: reply([dict(symbol='BTCUSDT',markPrice='76100',lastPrice='76100',
                                                  nextFundingTime='1800003600000')])
    conn=sqlite3.connect(tmp_path/'broker.db')
    broker=ScenarioDemoBroker(conn, session=exchange, expected_main_uid='123',
                              clock=lambda: 1001, execution_enabled=True)
    _write(conn, dict(version=1,state='active',main_uid='123'))
    conn.execute('CREATE TABLE llm_scenario_state(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
    conn.execute('INSERT INTO llm_scenario_state VALUES(1,?)',
                 (json.dumps({'active':fixture['active'],'version':1,'breaker':{}}),))
    conn.execute('CREATE TABLE llm_scenario_intents(id TEXT PRIMARY KEY,scenario_id TEXT,payload TEXT,status TEXT,evidence TEXT)')
    conn.execute('CREATE TABLE llm_scenario_entry_contracts(scenario_id TEXT PRIMARY KEY,body TEXT NOT NULL)')
    conn.execute('INSERT INTO llm_scenario_entry_contracts VALUES(?,?)',
                 ('scenario-id',json.dumps(fixture['active']['entry_contract'],sort_keys=True)))
    conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                 ('intent-id','scenario-id',json.dumps(origin_plan(fixture)),'PENDING'))
    child=fixture['child']
    conn.execute('INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (child['link_id'],child['intent_id'],child['scenario_id'],child['kind'],'entry-1',
                  json.dumps(child['request']),child['status'],child['order_id'],None,999))
    conn.commit()
    return broker,exchange,fixture


def test_real_broker_probe_never_reconciles_or_submits_orders(tmp_path):
    broker,exchange,fixture=real_entry_broker(tmp_path)
    conn=broker.conn
    original=conn.execute('SELECT * FROM llm_scenario_children').fetchall()
    result=probe_unsettled_entry(broker,maximum_quantity='0.019')
    assert result['status']=='AGGREGATE_MATCH' and result['authorization'] is False
    assert conn.execute('SELECT * FROM llm_scenario_children').fetchall()==original
    assert conn.execute('SELECT status FROM llm_scenario_intents').fetchone()==('PENDING',)
    assert exchange.writes==[]
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    context=broker.context()
    assert context['management_quarantine']==receipt
    assert context['legacy_fenced'] is True and context['accounting_status']=='pending'
    assert context['new_risk_blocked'] is True and exchange.writes==[]


def test_failed_account_refresh_invalidates_previous_observation(tmp_path):
    from live import scenario_management_quarantine as quarantine
    from live.scenario_broker import BrokerNotReady

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    old=broker.capture_account()
    def unavailable(**kwargs):
        raise TimeoutError('account read unavailable')
    exchange.get_positions=unavailable
    with pytest.raises(BrokerNotReady):
        broker.capture_account()
    assert getattr(broker,'_last_account_observation_fingerprint',None) is None
    with pytest.raises(ValueError,match='management_quarantine_observation_not_attested'):
        quarantine.project(broker,old,broker._active(),broker.children('scenario-id'))
    assert exchange.writes==[]


def test_runtime_can_evaluate_wait_only_with_fresh_entry_quarantine(tmp_path):
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import wait_proposal

    broker,exchange,_=real_entry_broker(tmp_path)
    now=[1001]
    broker.clock=lambda:now[0]
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    calls=[]
    def propose(snapshot,context):
        calls.append(deepcopy(context))
        return dict(wait_proposal(snapshot,context,action_id='wait-'+str(now[0])),entry_contract=None,
                    thesis_assessment=dict(contract_id=context['registered_entry_contract']['contract_id'],
                                           status='INTACT',reason='No new failure evidence.'))
    runtime=ScenarioRuntime(broker.conn,broker,propose,lambda: dict(valid=True,as_of_ms=now[0]*1000),
                            clock=lambda:now[0],entry_contract_enabled=True)
    result=runtime.tick()
    assert result['status']=='wait', (result, [(bool(c.get('registered_entry_contract')),c.get('entry_contract_status')) for c in calls])
    assert len(calls)==1 and calls[0]['management_quarantine']['scope']=='ENTRY_AGGREGATE_ONLY'
    assert calls[0]['accounting_status']=='pending' and calls[0]['new_risk_blocked'] is True
    assert broker.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='intent-id'").fetchone()==('PENDING',)
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='UNKNOWN'
    assert exchange.writes==[]
    now[0]=1301
    exchange.now=1301
    second=runtime.tick()
    assert second['status']=='wait', second
    assert len(calls)==2 and exchange.writes==[]


@pytest.mark.parametrize('field,value', [('size','0.018'),('stopLoss','75600')])
def test_runtime_detects_external_position_change_after_reconcile(tmp_path, field, value):
    from live.scenario_runtime import ScenarioRuntime

    broker,exchange,fixture=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    reconcile=broker.reconcile
    def change_after_reconcile():
        result=reconcile()
        fixture['observed']['position'][field]=value
        return result
    broker.reconcile=change_after_reconcile
    calls=[]
    runtime=ScenarioRuntime(broker.conn,broker,lambda *args:calls.append(args),
                            lambda:dict(valid=True,as_of_ms=1001000),clock=lambda:1001,
                            entry_contract_enabled=True)
    result=runtime.tick()
    assert result['status'] in {'blocked','fenced','stale_proposal'}, result
    assert calls==[] and exchange.writes==[]
    assert broker.conn.execute('SELECT id,status FROM llm_scenario_intents').fetchall()==[('intent-id','PENDING')]


@pytest.mark.parametrize('case', ['no_authority','revoked','changed_order','foreign_intent'])
def test_runtime_fence_never_opens_on_missing_or_changed_authority(tmp_path, case):
    from live import scenario_management_quarantine as quarantine
    from live.scenario_runtime import ScenarioRuntime

    broker,exchange,_=real_entry_broker(tmp_path)
    if case!='no_authority':
        preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                                  expires_at=1600,maximum_quantity='0.019')
        authorize_unsettled_entry_management(broker,preview)
    if case=='revoked':
        quarantine.revoke(broker,approval_id='approval',reason='revoked')
    if case=='changed_order':
        exchange.orders['entry-link']['avgPrice']='76221'
    if case=='foreign_intent':
        broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                            ('other','other-scenario',json.dumps({'action':'OPEN','revision':1}),'PENDING'))
        broker.conn.commit()
    calls=[]
    runtime=ScenarioRuntime(broker.conn,broker,lambda s,c: calls.append(c),
                            lambda: dict(valid=True,as_of_ms=1001000),clock=lambda:1001,
                            entry_contract_enabled=True)
    result=runtime.tick()
    assert result['status'] in {'fenced','blocked','intent_pending'}, result
    assert calls==[] and exchange.writes==[]
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='UNKNOWN'


def test_entry_quarantine_wait_get_call_count_is_bounded(tmp_path):
    from collections import Counter
    import inspect
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import wait_proposal

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    counts=Counter()
    captures=Counter()
    for name in dir(exchange):
        method=getattr(exchange,name)
        if not name.startswith('get_') or not callable(method):
            continue
        def wrapped(*args,_name=name,_method=method,**kwargs):
            counts[_name]+=1
            if _name=='get_positions':
                stack=inspect.stack()
                captures[' > '.join(f.function+':'+str(f.lineno) for f in stack if f.function in {'_tick','_reconcile','reconcile','_context','context','active_receipt','probe_unsettled_entry','_inspect'})]+=1
            return _method(*args,**kwargs)
        setattr(exchange,name,wrapped)
    def propose(snapshot,context):
        return dict(wait_proposal(snapshot,context,action_id='wait-count'),entry_contract=None,
                    thesis_assessment=dict(contract_id=context['registered_entry_contract']['contract_id'],
                                           status='INTACT',reason='No new failure evidence.'))
    runtime=ScenarioRuntime(broker.conn,broker,propose,lambda:dict(valid=True,as_of_ms=1001000),
                            clock=lambda:1001,entry_contract_enabled=True)
    assert runtime.tick()['status']=='wait'
    print('GET_COUNTS',dict(sorted(counts.items())))
    print('CAPTURE_PATHS',dict(sorted(captures.items())))
    assert counts and sum(counts.values())<100
    assert exchange.writes==[]


def test_entry_quarantine_does_not_cancel_unknown_or_sync_unverified_exits(tmp_path):
    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    calls=[]
    broker._cancel=lambda *a,**k: calls.append('cancel')
    broker._sync_exits=lambda *a,**k: calls.append('sync')
    broker.reconcile()
    assert calls==[] and exchange.writes==[]
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='UNKNOWN'


def test_entry_quarantine_never_syncs_after_evidence_returns_until_released(tmp_path):
    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    exchange.fills=[dict(execId='real-fill',orderId='order-id',symbol='BTCUSDT',side='Buy',
                         execType='Trade',execQty='0.019',execPrice='76220',execFee='0.8',execTime='1000000')]
    calls=[]
    broker._sync_exits=lambda *a,**k: calls.append('sync')
    broker.reconcile()
    assert calls==[] and exchange.writes==[]
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='TERMINAL'


def test_corrupt_authority_does_not_disable_safe_entry_cancellation(tmp_path):
    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    broker.conn.execute("UPDATE llm_scenario_broker_evidence SET body='{}' WHERE kind='management_quarantine_v1'")
    broker.conn.commit()
    calls=[]
    broker._cancel=lambda *a,**k: calls.append('cancel')
    broker._sync_exits=lambda *a,**k: calls.append('sync')
    broker.reconcile()
    assert 'cancel' in calls and 'sync' not in calls
    assert exchange.writes==[]


@pytest.mark.parametrize('action', ['OPEN','ADJUST','REDUCE'])
def test_entry_quarantine_rejects_new_or_partial_risk_actions(tmp_path, action):
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import proposal

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    calls=[]
    def propose(snapshot,context):
        calls.append(context)
        return dict(proposal(snapshot,context),scenario_id='scenario-id',action_id='forbidden-open',action=action)
    runtime=ScenarioRuntime(broker.conn,broker,propose,lambda:dict(valid=True,as_of_ms=1001000),
                            clock=lambda:1001,entry_contract_enabled=True)
    result=runtime.tick()
    assert result['status'] not in {'executed','intent_pending'} or result.get('reason')!='submission_unknown'
    assert len(calls)==1 and exchange.writes==[]
    assert not broker.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE id='forbidden-open'").fetchone()


@pytest.mark.parametrize('lose_ack', [False, True])
def test_runtime_entry_quarantine_exit_is_single_reduce_only_request(tmp_path, lose_ack):
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import wait_proposal

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    exchange.lose_ack=lose_ack
    calls=[]
    def propose(snapshot,context):
        calls.append(deepcopy(context))
        return dict(wait_proposal(snapshot,context,action_id='exit-once'),action='EXIT',entry_contract=None,
                    thesis_assessment=dict(contract_id=context['registered_entry_contract']['contract_id'],
                                           status='WEAKENING',reason='Risk increased.'))
    runtime=ScenarioRuntime(broker.conn,broker,propose,lambda: dict(valid=True,as_of_ms=1001000),
                            clock=lambda:1001,entry_contract_enabled=True)
    result=runtime.tick()
    assert result['status']=='intent_pending', result
    assert len(calls)==1 and len(exchange.writes)==1
    verb,request=exchange.writes[0]
    assert verb=='place' and request['reduceOnly'] is True and request['closeOnTrigger'] is True
    assert request['side']=='Sell' and request['qty']=='0.019'
    assert exchange.orders['native']['orderStatus']=='Untriggered'
    child=next(c for c in broker.children('scenario-id') if c['kind']=='entry')
    assert child['status']=='UNKNOWN' and child['evidence'] is None
    assert broker.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='intent-id'").fetchone()==('PENDING',)
    broker.clock=lambda:1301
    exchange.now=1301
    restarted_calls=[]
    restarted=ScenarioRuntime(broker.conn,broker,lambda s,c: restarted_calls.append(c),
                              lambda: dict(valid=True,as_of_ms=1301000),clock=lambda:1301,
                              entry_contract_enabled=True)
    restarted.tick()
    assert restarted_calls==[]
    assert len(exchange.writes)==1
    assert exchange.orders['native']['orderStatus']=='Untriggered'


@pytest.mark.parametrize('action,disrupt', [('WAIT',None),('EXIT',None),('OPEN',None),
                                             ('EXIT','revoked'),('EXIT','order_changed')])
def test_entry_quarantine_two_stage_production_profile(tmp_path, action, disrupt):
    from types import SimpleNamespace
    from live import scenario_management_quarantine as quarantine
    from live.scenario_review_adapter import make_calculated_review_proposer
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_review_runtime import wire

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    authorize_unsettled_entry_management(broker,preview)
    captured={};calls=[]
    rationale=dict(interpretation='기존 보유와 보호 상태를 검토합니다.',evidence_refs=[],comparisons=[])
    def plan(choice):
        return dict(wire(captured,choice),entry_contract=None,
                    thesis_assessment=dict(contract_id=captured['registered_entry_contract']['contract_id'],
                                           status='INTACT',reason='Original premise remains intact.'),rationale=rationale)
    def generate(**request):
        calls.append(request)
        if 'wait_proposal' in request['response_schema']['properties']:
            result=dict(wait_proposal=plan('WAIT'),candidates=[plan('OPEN' if action=='OPEN' else 'EXIT')],no_candidate_reason='')
        else:
            bundle=json.loads(request['user_prompt'])['calculated_candidates']
            selected=bundle['wait'] if action=='WAIT' else bundle['candidates'][0]
            result=dict(selection_token=f"{selected['id']}:{selected['receipt_sha256']}",rationale=rationale)
            if disrupt=='revoked':
                quarantine.revoke(broker,approval_id='approval',reason='revoked')
            elif disrupt=='order_changed':
                exchange.orders['entry-link']['avgPrice']='76001'
        return SimpleNamespace(text=json.dumps(result))
    adapter=make_calculated_review_proposer(generate=generate,clock=lambda:1001,
                                            research_mode='economic-reject-calculated-v1')
    def propose(snapshot,context,*,record):
        captured.update(deepcopy(context))
        return adapter(snapshot,context,record=record)
    runtime=ScenarioRuntime(broker.conn,broker,lambda *_:pytest.fail('legacy path'),
        lambda:dict(valid=True,as_of_ms=1001000),clock=lambda:1001,entry_contract_enabled=True,
        calculated_review_enabled=True,calculated_review_propose=propose,
        research_review_mode='economic-reject-calculated-v1')
    result=runtime.tick()
    tape=broker.conn.execute('SELECT slot,body FROM llm_scenario_review_tapes ORDER BY slot DESC LIMIT 1').fetchone()
    if tape:
        from live.scenario_review_storage import review_text
        from utils.btc_review_references import read_tape
        raw=read_tape(tape[1],broker.conn,tape[0],decode_legacy=review_text)
        failure=json.loads(raw).get('outcome')
    else:
        failure=None
    if disrupt:
        assert result['status']!='intent_pending', (result,failure)
        assert broker.conn.execute('SELECT id,status FROM llm_scenario_intents').fetchall()==[('intent-id','PENDING')]
    else:
        assert result['status']==('wait' if action=='WAIT' else 'intent_pending' if action=='EXIT' else 'blocked'), (result,failure)
    assert len(calls)==(1 if action=='OPEN' else 2)
    assert len(exchange.writes)==(1 if action=='EXIT' and not disrupt else 0)
    if action=='EXIT' and not disrupt:
        assert exchange.writes[0][1]['reduceOnly'] is True
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='UNKNOWN'


@pytest.mark.parametrize('lose_ack', [False, True])
def test_entry_quarantine_exit_is_reduce_only_and_never_reposts(tmp_path, lose_ack):
    from live import scenario_management_quarantine as quarantine
    from live.scenario_broker import BrokerNotReady

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.commit()
    exchange.lose_ack=lose_ack
    if lose_ack:
        with pytest.raises(BrokerNotReady,match='submission_unknown'):
            quarantine.execute_exit(broker,payload,'exit')
    else:
        quarantine.execute_exit(broker,payload,'exit')
    assert len(exchange.writes)==1
    verb,request=exchange.writes[0]
    assert verb=='place' and request['reduceOnly'] is True and request['closeOnTrigger'] is True
    assert request['side']=='Sell' and request['qty']=='0.019'
    assert exchange.orders['native']['orderStatus']=='Untriggered'
    quarantine.execute_exit(broker,payload,'exit')
    assert len(exchange.writes)==1
    entry=next(c for c in broker.children('scenario-id') if c['kind']=='entry')
    assert entry['status']=='UNKNOWN' and entry['evidence'] is None


@pytest.mark.parametrize('change', ['expired_observation', 'revoked_authority'])
def test_entry_quarantine_exit_rechecks_before_transport(tmp_path, change):
    from live import scenario_management_quarantine as quarantine

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.commit()
    original=broker._submit
    def changed(*args, **kwargs):
        if change=='expired_observation':
            broker.clock=lambda: 1020
        else:
            quarantine.revoke(broker,approval_id='approval',reason='revoked')
        return original(*args, **kwargs)
    broker._submit=changed
    with pytest.raises(Exception):
        quarantine.execute_exit(broker,payload,'exit')
    assert exchange.writes==[]
    assert not [c for c in broker.children('scenario-id') if c['kind']=='exit']
    assert exchange.orders['native']['orderStatus']=='Untriggered'


def test_entry_quarantine_exit_refuses_stop_changed_during_instrument_read(tmp_path):
    from live import scenario_management_quarantine as quarantine

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.commit()
    original=broker._instrument
    def changed():
        value=original()
        exchange.orders['native']['triggerPrice']='75600'
        return value
    broker._instrument=changed
    with pytest.raises(ValueError,match='management_quarantine_'):
        quarantine.execute_exit(broker,payload,'exit')
    assert exchange.writes==[]
    assert not [c for c in broker.children('scenario-id') if c['kind']=='exit']


@pytest.mark.parametrize('after', ['unchanged','revoked','foreign_intent','expired'])
def test_pretransport_failure_can_continue_only_after_fresh_proof(tmp_path, after):
    from live import scenario_management_quarantine as quarantine

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.commit()
    original=broker._submit
    def delayed(*args, **kwargs):
        broker.clock=lambda:1020
        return original(*args, **kwargs)
    broker._submit=delayed
    with pytest.raises(Exception):
        quarantine.execute_exit(broker,payload,'exit')
    assert exchange.writes==[] and not [c for c in broker.children('scenario-id') if c['kind']=='exit']
    broker._submit=original
    broker.clock=lambda:1601 if after=='expired' else 1301
    exchange.now=broker.clock()
    if after=='revoked':
        quarantine.revoke(broker,approval_id='approval',reason='revoked')
    if after=='foreign_intent':
        broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                            ('other','other-scenario',json.dumps({'action':'OPEN'}),'PENDING'))
        broker.conn.commit()
    broker.reconcile()
    assert len(exchange.writes)==(1 if after=='unchanged' else 0)
    if after=='unchanged':
        assert exchange.writes[0][1]['reduceOnly'] is True
    assert exchange.orders['native']['orderStatus']=='Untriggered'


@pytest.mark.parametrize('failure', ['timeout','retry_code','explicit_rejection'])
def test_entry_quarantine_real_sdk_exit_has_at_most_one_transport_attempt(tmp_path, failure):
    sdk_module=pytest.importorskip('pybit.unified_trading')
    import requests
    from live import scenario_management_quarantine as quarantine
    from live.scenario_broker import BrokerNotReady

    broker,exchange,_=real_entry_broker(tmp_path)
    preview=prepare_unsettled_entry_authority(broker,approval_id='approval',reason='review',
                                              expires_at=1600,maximum_quantity='0.019')
    receipt=authorize_unsettled_entry_management(broker,preview)
    payload=dict(action='EXIT',action_id='exit',scenario_id='scenario-id',entries=[],take_profits=[],
                 partial_stops=[],cancel_entry_ids=[],pending_renewal=None,reduce_fraction=None)
    broker.conn.execute('INSERT INTO llm_scenario_intents(id,scenario_id,payload,status) VALUES(?,?,?,?)',
                        ('exit','scenario-id',json.dumps(payload),'PENDING'))
    quarantine.bind_exit(broker,payload,receipt)
    broker.conn.commit()
    sdk=sdk_module.HTTP(demo=True,api_key='offline-placeholder',api_secret='offline-placeholder',
                        max_retries=3,force_retry=True,retry_delay=0)
    for name in dir(exchange):
        if name.startswith('get_'):
            setattr(sdk,name,getattr(exchange,name))
    attempts=[]
    def send(request,**kwargs):
        attempts.append(request)
        if failure=='timeout':
            raise requests.exceptions.ReadTimeout('offline test')
        response=requests.Response();response.status_code=200
        code=10002 if failure=='retry_code' and len(attempts)==1 else 110093
        response._content=('{"retCode":%s,"retMsg":"offline","result":{}}'%code).encode()
        return response
    sdk.client.send=send
    broker.session=sdk
    with pytest.raises(BrokerNotReady):
        quarantine.execute_exit(broker,payload,'exit')
    assert len(attempts)==1 and (sdk.max_retries,sdk.force_retry)==(3,True)
    assert exchange.writes==[] and exchange.orders['native']['orderStatus']=='Untriggered'
    assert next(c for c in broker.children('scenario-id') if c['kind']=='entry')['status']=='UNKNOWN'
