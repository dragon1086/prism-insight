"""Durable REDUCE state is included in exact native replay, not ignored."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from analysis.scenario_replay import _runtime_event_hash, run_replay, network_boundary
from backtest.scenario_tape import digest
from live.scenario_immediate_reduce import initialize
from live.scenario_model_input import restore_model_input
from tests.test_scenario_replay import inputs
from tests.test_scenario_calculated_replay import wire
from tests.test_scenario_review_guard import model_receipt_hash


def test_empty_reduction_table_is_part_of_runtime_event_hash():
    with sqlite3.connect(':memory:') as conn:
        initialize(conn)
        assert _runtime_event_hash(conn)==digest({'llm_scenario_reductions':dict(
            columns=['intent_id','body','sha256'],rows=[])})


@pytest.mark.parametrize('field',['intent_id','body','sha256'])
def test_every_durable_reduction_column_changes_runtime_hash(field):
    with sqlite3.connect(':memory:') as conn:
        initialize(conn)
        conn.execute('INSERT INTO llm_scenario_reductions VALUES(?,?,?)',('r','{"status":"PREPARED"}','original'))
        original=_runtime_event_hash(conn)
        statements={
            'intent_id':"UPDATE llm_scenario_reductions SET intent_id='other'",
            'body':"UPDATE llm_scenario_reductions SET body='{}'",
            'sha256':"UPDATE llm_scenario_reductions SET sha256='tampered'",
        }
        conn.execute(statements[field])
        assert _runtime_event_hash(conn)!=original


@pytest.mark.parametrize('side',['LONG','SHORT'])
def test_native_reduce_fresh_frozen_includes_exact_durable_state(tmp_path,side):
    bundle,market=inputs(1200000)
    start=bundle['start_ms']/1000
    wall=[0.]
    def generate(**request):
        wall[0]+=1
        payload=restore_model_input(json.loads(request['user_prompt']))
        ctx=payload['contract_context']
        if 'selection_token' in request['response_schema']['properties']:
            candidates=payload['calculated_candidates']
            chosen=next((c for c in candidates['candidates'] if c['eligible']),candidates['wait'])
            value=dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}",rationale='Synthetic replay')
        else:
            action=('REDUCE' if ctx['revision']==1 else 'EXIT') if ctx.get('scenario_id') else ('OPEN' if ctx['now']<start+300 else 'WAIT')
            candidate=wire(ctx,'WAIT' if action=='REDUCE' else action)
            if action=='REDUCE':
                candidate.update(action='REDUCE',reduce_fraction=.5)
            if action=='OPEN':
                candidate.update(side=side,hard_stop=98 if side=='LONG' else 102,
                    take_profits=[dict(id='tp',price=120 if side=='LONG' else 80,fraction=.5)])
            value=dict(wait_proposal=wire(ctx,'WAIT'),candidates=[] if action=='WAIT' else [candidate],
                       no_candidate_reason='No new trade' if action=='WAIT' else '')
        return SimpleNamespace(text=json.dumps(value))
    profile='calculated-economic-v4'
    tape=tmp_path/'tape'
    with network_boundary('frozen') as blocked:
        fresh=run_replay(bundle,market,tmp_path/'fresh',mode='fixture',tape_path=tape,
            policy_profile=profile,model_generator=generate,clock_monotonic=lambda:wall[0])
        frozen=run_replay(bundle,market,tmp_path/'frozen',mode='frozen',tape_path=tape,policy_profile=profile)
    assert blocked==[]
    assert fresh['execution_status']==frozen['execution_status']=='COMPLETE',fresh.get('failure_evidence')
    assert fresh['runtime_event_hash']==frozen['runtime_event_hash']
    assert fresh['result_hash']==frozen['result_hash']
    assert fresh['exchange_hash']==frozen['exchange_hash']
    with sqlite3.connect(tmp_path/'fresh/replay.sqlite') as one, sqlite3.connect(tmp_path/'frozen/replay.sqlite') as two:
        a=one.execute('SELECT * FROM llm_scenario_reductions ORDER BY rowid').fetchall()
        assert a and a==two.execute('SELECT * FROM llm_scenario_reductions ORDER BY rowid').fetchall()
        assert all(json.loads(row[1])['status']=='COMPLETE' for row in a)
        assert one.execute("SELECT count(*) FROM llm_scenario_children WHERE kind='reduce'").fetchone()[0]==1
        original=_runtime_event_hash(two)
        two.execute("UPDATE llm_scenario_reductions SET body='{}'")
        assert _runtime_event_hash(two)!=original
