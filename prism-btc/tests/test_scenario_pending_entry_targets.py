"""Committed TP installation must not depend on delayed execution-history indexing."""
import json

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from tests.test_scenario_execution import live, persist, reply


def delayed_fill(live, monkeypatch):
    broker, exchange = live
    plan = persist(broker, take_profits=[dict(id='near', price=105, fraction=.3),
                                       dict(id='far', price=110, fraction=.7)],
                   chase=dict(max_bps=0, max_reprices=0))
    place = exchange.place_order

    def instant(**request):
        result = place(**request)
        if request.get('reduceOnly') is False:
            exchange.fill(request['orderLinkId'], .1)
            exchange.orders[request['orderLinkId']]['avgPrice'] = '100'
        return result

    monkeypatch.setattr(exchange, 'place_order', instant)
    monkeypatch.setattr(exchange, 'get_executions', lambda **kw: reply([]))
    with pytest.raises(BrokerNotReady, match='execution_quantity_unreconciled'):
        broker.execute(plan, 'a1')
    return broker, exchange, plan


def test_pending_entry_installs_original_targets_without_fabricating_fills(live, monkeypatch):
    broker, exchange, plan = delayed_fill(live, monkeypatch)
    broker.reconcile()
    targets = [c for c in broker.children('s1') if c['kind'] == 'tp']
    assert [(c['request']['price'], c['request']['qty']) for c in targets] == [('105', '0.03'), ('110', '0.07')]
    assert all(c['request']['reduceOnly'] is True for c in targets)
    entry = next(c for c in broker.children('s1') if c['kind'] == 'entry')
    assert entry['status'] == 'UNKNOWN' and entry['evidence'] is None
    assert broker.conn.execute('SELECT status FROM llm_scenario_intents WHERE id=?', ('a1',)).fetchone()[0] == 'PENDING'
    assert exchange.orders['native']['orderStatus'] == 'Untriggered'
    assert broker.capture_account()['legacy_fenced'] is True
    assert not broker.conn.execute("SELECT 1 FROM sqlite_master WHERE name='llm_scenario_settlements'").fetchone()
    before = list(exchange.writes)
    broker.reconcile()
    assert exchange.writes == before
    restarted = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid='123',
                                  clock=broker.clock, execution_enabled=True)
    restarted.reconcile()
    assert exchange.writes == before
    monkeypatch.setattr(exchange, 'get_executions', type(exchange).get_executions.__get__(exchange))
    restarted.reconcile()
    assert exchange.writes == before
    assert next(c for c in broker.children('s1') if c['kind'] == 'entry')['status'] == 'TERMINAL'


def test_partial_target_fill_does_not_cancel_or_recreate_targets_while_history_lags(live, monkeypatch):
    broker, exchange, plan = delayed_fill(live, monkeypatch)
    broker.reconcile()
    near = next(c for c in broker.children('s1') if c['kind'] == 'tp')
    exchange.fill(near['link_id'], .03)
    before = list(exchange.writes)
    broker.reconcile()
    assert exchange.writes == before


def test_ack_loss_target_is_queried_never_resubmitted_and_missing_peer_recovers(live, monkeypatch):
    broker, exchange, plan = delayed_fill(live, monkeypatch)
    exchange.lose_ack = True
    broker.reconcile()
    assert len([c for c in broker.children('s1') if c['kind'] == 'tp']) == 1
    exchange.lose_ack = False
    broker.reconcile()
    targets = [c for c in broker.children('s1') if c['kind'] == 'tp']
    assert len(targets) == 2
    assert len([r for kind, r in exchange.writes if kind == 'place' and r.get('reduceOnly')]) == 2


@pytest.mark.parametrize('damage', ['position', 'average', 'parent', 'stop', 'foreign_order', 'partial', 'plan'])
def test_aggregate_mismatch_never_installs_targets(live, monkeypatch, damage):
    broker, exchange, plan = delayed_fill(live, monkeypatch)
    entry = next(c for c in broker.children('s1') if c['kind'] == 'entry')
    if damage == 'position': exchange.size = '.09'
    if damage == 'average': exchange.orders[entry['link_id']]['avgPrice'] = '101'
    if damage == 'parent': exchange.orders['native']['parentOrderLinkId'] = 'other'
    if damage == 'stop': exchange.orders[entry['link_id']]['stopLoss'] = '97'
    if damage == 'foreign_order': exchange.orders['other'] = dict(exchange.orders['native'], orderId='foreign')
    if damage == 'partial': exchange.orders[entry['link_id']]['orderStatus'] = 'PartiallyFilled'
    if damage == 'plan':
        broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='a1'", (json.dumps(dict(plan, hard_stop=97)),))
        broker.conn.commit()
    broker.reconcile()
    assert not [c for c in broker.children('s1') if c['kind'] == 'tp']
