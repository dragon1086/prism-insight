"""Last closed trade is bounded historical evidence, never entry permission."""
from copy import deepcopy
import json
import sqlite3

import pytest

from live.scenario_entry_contract import bind_contract
from live.scenario_position_flow import build_closed_continuity


@pytest.fixture(params=['LONG','SHORT'])
def closed(request):
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE llm_scenario_settlements(scenario_id TEXT,evidence TEXT);
        CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT,status TEXT);
        CREATE TABLE llm_scenario_entry_contracts(scenario_id TEXT,body TEXT);
        CREATE TABLE llm_scenario_audit_events(event_id TEXT,kind TEXT,observed_at REAL,scenario_id TEXT,body TEXT);
        CREATE TABLE llm_scenario_decisions(slot INTEGER,proposal TEXT,outcome TEXT);
    ''')
    long=request.param=='LONG'
    plan = dict(action='OPEN', action_id='open', scenario_id='s', input_id='i', side=request.param,
                entries=[dict(id='e',price=80000,quantity=.01)],hard_stop=78000 if long else 82000)
    definition = dict(version=1, regime='TREND', premise='Independent bearish continuation',
        expected_progress='Fresh lower lows', explicit_failure=dict(
            source='MARK_PRICE', operator='le' if long else 'ge', price=79000 if long else 81000, minimum_observations=2))
    contract = bind_contract(definition, plan, 300)
    settlement = dict(scenario_id='s', flat_confirmed=True, orders_terminal=True,
        executions_complete=True, fees_complete=True, funding_complete=True,
        execution_ids=['entry-fill','exit-fill'], gross_pnl=-1.25, fees=.4, funding_net=.1, net_pnl=-1.55)
    exit_plan = dict(action='EXIT', action_id='exit', scenario_id='s', input_id='exit-input',
        rationale='Independent exit opinion', thesis_assessment=dict(
            contract_id=contract['contract_id'], status='WEAKENING', reason='Failed progress'))
    for p in (plan, exit_plan):
        conn.execute('INSERT INTO llm_scenario_intents VALUES(?,?,?,?)',
                     (p['action_id'], 's', json.dumps({k:v for k,v in p.items() if k!='thesis_assessment'}), 'TERMINAL'))
    conn.execute('INSERT INTO llm_scenario_entry_contracts VALUES(?,?)', ('s',json.dumps(contract,sort_keys=True)))
    conn.execute('INSERT INTO llm_scenario_settlements VALUES(?,?)',('s',json.dumps(settlement)))
    conn.execute('INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?)',
                 ('audit','settlement_recorded',950,'s',json.dumps(dict(settlement=settlement))))
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?)',
                 (3,json.dumps(exit_plan),json.dumps(dict(status='intent_pending'))))
    state = dict(breaker=dict(completed_ids=['s']))
    yield conn,state,settlement,contract
    conn.close()


def test_closed_continuity_exact_accounting_separate_opinions_and_no_writes(closed):
    conn,state,settlement,contract = closed
    before, changes = deepcopy(state), conn.total_changes
    result = build_closed_continuity(conn,state,1200)
    assert result['status']=='AVAILABLE'
    assert result['scenario_id']=='s' and result['side'] in {'LONG','SHORT'}
    assert result['settlement']['values']=={k:settlement[k] for k in ('gross_pnl','fees','funding_net','net_pnl')}
    assert result['original_contract']==contract
    assert result['exit_opinion']['assessment']['status']=='WEAKENING'
    assert result['exit_opinion']['source']['decision_slot']==3
    assert result['times']['settlement_observed_at']==950
    assert result['times']['last_exit_fill_at']=='UNKNOWN'
    assert conn.total_changes==changes and state==before
    assert len(json.dumps(result).encode())<=3000


@pytest.mark.parametrize('fault', ['missing','future','wrong_settlement','bad_cost','unfilled',
                                  'duplicate_audit','contract_tamper','wrong_original'])
def test_ambiguous_latest_is_unavailable_not_replaced_by_older(closed,fault):
    conn,state,settlement,_=closed
    if fault=='missing':
        state['breaker']['completed_ids'].append('newest_missing')
    elif fault=='future':
        conn.execute('UPDATE llm_scenario_audit_events SET observed_at=1300')
    elif fault=='duplicate_audit':
        conn.execute('INSERT INTO llm_scenario_audit_events SELECT * FROM llm_scenario_audit_events')
    elif fault=='contract_tamper':
        conn.execute("UPDATE llm_scenario_entry_contracts SET body='{}'")
    elif fault=='wrong_original':
        conn.execute("UPDATE llm_scenario_intents SET payload='{}' WHERE id='open'")
    else:
        if fault=='wrong_settlement':
            settlement['scenario_id']='other'
        if fault=='bad_cost':
            settlement['fees']=5
        if fault=='unfilled':
            settlement['execution_ids']=[]
        conn.execute('UPDATE llm_scenario_settlements SET evidence=?',(json.dumps(settlement),))
    assert build_closed_continuity(conn,state,1200)['status']=='UNAVAILABLE'


def test_no_closed_trade_is_explicit_missing(closed):
    conn,_,_,_=closed
    assert build_closed_continuity(conn,dict(breaker=dict(completed_ids=[])),1200)['status']=='MISSING'


def test_unlinked_exit_opinion_is_unknown_without_discarding_accounting(closed):
    conn,state,_,_=closed
    conn.execute("DELETE FROM llm_scenario_intents WHERE id='exit'")
    result=build_closed_continuity(conn,state,1200)
    assert result['status']=='AVAILABLE'
    assert result['exit_opinion']['status']=='UNAVAILABLE'


def test_exit_scan_is_bounded_and_does_not_reuse_stale_opinion(closed):
    from live.scenario_position_flow import SCAN_LIMIT
    conn,state,_,_=closed
    conn.executemany('INSERT INTO llm_scenario_decisions VALUES(?,?,?)',
                     [(slot,'{}','{}') for slot in range(4,4+SCAN_LIMIT)])
    assert build_closed_continuity(conn,state,1200)['exit_opinion']['status']=='UNAVAILABLE'


@pytest.mark.parametrize('frame', ['5m', '15m'])
def test_retired_opinion_is_omitted_but_original_contract_stays_historical(closed, frame):
    from live.scenario_analysis_frames import analysis_model_view
    conn,state,_,_=closed
    history=build_closed_continuity(conn,state,1200)
    history['exit_opinion']['rationale']=frame+' crossed with 2.99x volume'
    original=deepcopy(history)
    view=analysis_model_view(dict(contract_context=dict(last_closed_trade=history)))
    delivered=view['contract_context']['last_closed_trade']
    expected = 'OMITTED_RETIRED_TIMEFRAME_OPINION' if frame == '5m' else history['exit_opinion']['rationale']
    assert delivered['exit_opinion']['rationale'] == expected
    assert delivered['original_contract']==history['original_contract']
    assert history==original


@pytest.mark.parametrize('active', [False,True])
@pytest.mark.parametrize('pending', [False,True])
def test_both_model_paths_keep_bounded_history_and_original_inputs(closed,active,pending):
    from types import SimpleNamespace
    from live.scenario_llm import propose
    from live.scenario_calculated_review import _model_payload
    from live.scenario_preview import response_contract
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_llm import context,wire
    conn,state,_,_=closed
    ctx=context(active)
    ctx['last_closed_trade']=build_closed_continuity(conn,state,1200)
    if pending:
        ctx['pending_entries']=[dict(id='e',price=60000,quantity=.001)]
    original=deepcopy(ctx)
    snapshot=dict(valid=True,as_of_ms=1000000)
    calls=[]
    def generate(**request):
        calls.append(request)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    propose(snapshot,ctx,response_contract(ctx),generate=generate,clock=lambda:1000)
    payloads=[json.loads(calls[0]['user_prompt']),_model_payload(snapshot,ctx,response_contract(ctx))]
    for payload in payloads:
        assert restore_model_input(payload)['contract_context']['last_closed_trade']==ctx['last_closed_trade']
    assert ctx==original


def test_optional_continuity_budget_does_not_displace_existing_facts(closed):
    from live.scenario_position_flow import project_position_flow_input,_size
    from live.scenario_model_input import restore_model_input
    conn,state,_,_=closed
    payload=dict(contract_context={},market_snapshot={},response_contract={},padding='x'*99910)
    baseline,_=project_position_flow_input(payload)
    assert _size(baseline)<=100000
    payload['contract_context']['last_closed_trade']=build_closed_continuity(conn,state,1200)
    actual,_=project_position_flow_input(payload)
    assert _size(actual)<=100000
    logical=restore_model_input(actual)
    omitted=logical['contract_context'].pop('last_closed_trade',None)
    assert omitted in (None,dict(status='OMITTED_INPUT_BUDGET'))
    assert logical==restore_model_input(baseline)


def test_runtime_constructs_missing_continuity_in_frozen_context(tmp_path):
    from tests.test_scenario_review_runtime import runtime
    from live.scenario_model_input import restore_model_input
    r,_,calls=runtime(tmp_path,empty=True)
    r.tick()
    assert calls
    assert restore_model_input(json.loads(calls[0]['user_prompt']))['contract_context']['last_closed_trade']['status']=='MISSING'
    stored=json.loads(r.conn.execute('SELECT context FROM llm_scenario_decisions').fetchone()[0])
    assert stored['last_closed_trade']['status']=='MISSING'


def test_stage_a_reasoning_preserves_fast_breaks_and_failed_exit():
    from live.scenario_llm import system_instructions
    text=' '.join(system_instructions(dict(scenario_id=None,positions=[],pending_entries=[],entry_contract_version=2)).split())
    for phrase in ('A prior loss, elapsed time or changing numbers alone creates no edge',
                   'Already supported progress needs no habitual extra close',
                   'FAILED requires the allowed full EXIT','never repair a contradiction in prose'):
        assert phrase in text


@pytest.mark.parametrize('fault',['future_exit','other_contract','wrong_intent_input'])
def test_exit_opinion_tamper_does_not_become_a_linked_reason(closed,fault):
    conn,state,_,_=closed
    if fault=='future_exit':
        conn.execute('UPDATE llm_scenario_decisions SET slot=99')
    elif fault=='other_contract':
        proposal=json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
        proposal['thesis_assessment']['contract_id']='other'
        conn.execute('UPDATE llm_scenario_decisions SET proposal=?',(json.dumps(proposal),))
    else:
        plan=json.loads(conn.execute("SELECT payload FROM llm_scenario_intents WHERE id='exit'").fetchone()[0])
        plan['input_id']='other'
        conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='exit'",(json.dumps(plan),))
    result=build_closed_continuity(conn,state,1200)
    assert result['status']=='AVAILABLE'
    assert result['exit_opinion']['status']=='UNAVAILABLE'


def test_actual_exit_metadata_shape_matches_terminal_execution_payload(closed):
    # Same optional keys as the three retained 2026-10-24 EXIT decisions.
    conn,state,_,_=closed
    proposal=json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
    proposal.update(entry_contract=None,recovery=None,
                    review=dict(acknowledgements=[],conditions=[]),stop_revision=None)
    conn.execute('UPDATE llm_scenario_decisions SET proposal=?',(json.dumps(proposal),))
    assert build_closed_continuity(conn,state,1200)['exit_opinion']['status']=='AVAILABLE'


def test_shared_execution_field_mismatch_cannot_supply_exit_opinion(closed):
    conn,state,_,_=closed
    proposal=json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
    proposal['cancel_entry_ids']=['unrelated-order']
    conn.execute('UPDATE llm_scenario_decisions SET proposal=?',(json.dumps(proposal),))
    assert build_closed_continuity(conn,state,1200)['exit_opinion']['status']=='UNAVAILABLE'


def test_duplicate_exit_rationale_does_not_discard_verified_history(closed):
    conn,state,_,contract=closed
    proposal=json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
    proposal['rationale']='반등을 확인했습니다. '*80
    payload={k:v for k,v in proposal.items() if k!='thesis_assessment'}
    conn.execute('UPDATE llm_scenario_decisions SET proposal=?',(json.dumps(proposal),))
    conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='exit'",(json.dumps(payload),))
    before=conn.total_changes
    result=build_closed_continuity(conn,state,1200)
    assert result['status']=='AVAILABLE'
    assert result['original_contract']==contract
    assert result['exit_opinion']['assessment']==proposal['thesis_assessment']
    assert 'rationale' not in result['exit_opinion']
    assert len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode())<=3000
    assert conn.total_changes==before
    assert json.loads(conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])==proposal
