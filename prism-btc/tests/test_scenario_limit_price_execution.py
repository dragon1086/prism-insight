"""New-price policy reaches durable real executor; fake exchange only."""
import json
import sqlite3

from live.scenario_broker import ScenarioDemoBroker
from live.scenario_contract import identity_fields, validate_wire_proposal
from live.scenario_control import _write
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_execution import Exchange, reply


class BitcoinExchange(Exchange):
    def __init__(self):
        super().__init__()
        self.stop = '84000'
        self.average = '85000'

    def get_tickers(self, **kwargs):
        return reply([dict(symbol='BTCUSDT', markPrice='85100', lastPrice='85100',
                           bid1Price='85099.9', ask1Price='85100.1', nextFundingTime='1800003600000')])

    def get_positions(self, **kwargs):
        result = super().get_positions(**kwargs)
        result['result']['list'][0]['avgPrice'] = self.average
        return result

    def fill(self, link, amount):
        super().fill(link, amount)
        self.fills[-1]['execPrice'] = self.orders[link]['price']
        if not self.orders[link].get('reduceOnly'):
            self.average = self.orders[link]['price']


def setup_runtime(tmp_path):
    conn = sqlite3.connect(tmp_path/'price-runtime.sqlite')
    exchange = BitcoinExchange()
    broker = ScenarioDemoBroker(conn, session=exchange, expected_main_uid='123',
                                execution_enabled=True, clock=lambda: exchange.now)
    _write(conn, dict(version=1, state='active', main_uid='123'))
    def propose(snapshot, context):
        value = dict(**identity_fields(context), action='OPEN', side='LONG', confidence=.8,
                     expires_at=exchange.now+1800, hard_stop=84000,
                     entries=[dict(id='entry', price=85000, quantity=.01)],
                     take_profits=[dict(id='tp', price=87000, fraction=.5)],
                     partial_stops=[], cancel_entry_ids=[], leverage=10,
                     chase=dict(max_bps=0, max_reprices=0), rationale='isolated structural price')
        return validate_wire_proposal(value, context)
    runtime = ScenarioRuntime(conn, broker, propose,
        lambda: dict(valid=True, as_of_ms=exchange.now*1000), clock=lambda: exchange.now)
    return runtime, broker, exchange


def test_raw_final_plan_and_actual_request_prices_agree(tmp_path):
    runtime, broker, exchange = setup_runtime(tmp_path)
    try:
        result = runtime.tick()
        assert result['status'] == 'intent_pending', result
        raw = json.loads(runtime.conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
        payload = json.loads(runtime.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0])
        child = next(c for c in broker.children() if c['kind'] == 'entry')
        assert raw['entries'][0]['price'] == 85000
        assert raw['take_profits'][0]['price'] == 87000
        assert payload['entries'][0]['price'] == 85007.3
        assert payload['take_profits'][0]['price'] == 86992.7
        assert float(child['request']['price']) == 85007.3
        assert child['request']['stopLoss'] == '84000' and child['request']['qty'] == '0.01'
        plan = runtime.conn.execute("SELECT body FROM llm_scenario_outbox WHERE kind='PLAN'").fetchone()[0]
        assert '85,007.30' in plan and '86,992.70' in plan
        assert runtime.tick()['status'] == 'duplicate_slot'
        assert len([w for w in exchange.writes if w[0] == 'place']) == 1
    finally:
        runtime.conn.close()


def test_partial_fills_and_broker_restart_never_rebuffer_targets(tmp_path):
    runtime, broker, exchange = setup_runtime(tmp_path)
    try:
        assert runtime.tick()['status'] == 'intent_pending'
        entry = next(c for c in broker.children() if c['kind'] == 'entry')
        exchange.fill(entry['link_id'], .005)
        exchange.now += 60
        broker.reconcile()
        targets = [c for c in broker.children() if c['kind'] == 'tp' and c['status'] == 'LIVE']
        assert targets and all(float(c['request']['price']) == 86992.7 for c in targets)
        exchange.fill(entry['link_id'], .005)
        exchange.now += 60
        restarted = ScenarioDemoBroker(runtime.conn, session=exchange, expected_main_uid='123',
                                       execution_enabled=True, clock=lambda: exchange.now)
        restarted.reconcile()
        targets = [c for c in restarted.children() if c['kind'] == 'tp' and c['status'] == 'LIVE']
        assert targets and all(float(c['request']['price']) == 86992.7 for c in targets)
        assert sum(float(c['request']['qty']) for c in targets) == .005
        assert exchange.stop == '84000'
        entry_requests = [kw for method, kw in exchange.writes if method == 'place' and not kw.get('reduceOnly')]
        assert len(entry_requests) == 1 and float(entry_requests[0]['price']) == 85007.3
    finally:
        runtime.conn.close()


def test_predeployment_persisted_plan_is_not_rewritten_on_reconcile(tmp_path):
    runtime, broker, exchange = setup_runtime(tmp_path)
    try:
        original = broker.context
        def old_context():
            result = original()
            for key in tuple(result):
                if 'price_policy' in key or 'execution_pricing' in key:
                    del result[key]
            return result
        broker.context = old_context
        assert runtime.tick()['status'] == 'intent_pending'
        payload = json.loads(runtime.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0])
        assert payload['entries'][0]['price'] == 85000
        assert payload['take_profits'][0]['price'] == 87000
        entry = next(c for c in broker.children() if c['kind'] == 'entry')
        exchange.fill(entry['link_id'], .01)
        exchange.now += 60
        broker.context = original
        broker.reconcile()
        targets = [c for c in broker.children() if c['kind'] == 'tp' and c['status'] == 'LIVE']
        assert targets and all(float(c['request']['price']) == 87000 for c in targets)
        assert json.loads(runtime.conn.execute('SELECT payload FROM llm_scenario_intents').fetchone()[0]) == payload
        assert exchange.stop == '84000'
    finally:
        runtime.conn.close()
