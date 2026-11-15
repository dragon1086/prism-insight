import pytest

from live.scenario_fee_projection import fee_only_projection, observed_fee_evidence
from tests.test_scenario_review_guard import model_receipt_hash


def evidence(rate=.00055):
    return dict(status='AVAILABLE', basis='HISTORICAL_VERIFIED_EXECUTIONS',
        rates={'TAKER': dict(count=1, minimum=rate, maximum=rate, earliest_at=1000, latest_at=1000)},
        source_sha256='a'*64)


@pytest.mark.parametrize('side,exit_price', [('LONG',81100),('LONG',81200),('SHORT',80900),('SHORT',80800)])
def test_near_exit_uses_two_fees_not_leverage(side, exit_price):
    r=fee_only_projection(side=side, entry_price=81000, exit_price=exit_price,
        quantity=1, evidence=evidence(), now=1001, new_entry=True)
    assert r['status']=='AVAILABLE'
    assert r['estimated_net_before_unmodelled_costs']==pytest.approx(abs(exit_price-81000)-(81000+exit_price)*.00055)


def test_cost_can_exceed_gross():
    r=fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=evidence(.001),now=1001,new_entry=True)
    assert r['estimated_net_before_unmodelled_costs']<0


def test_holding_no_double_entry_fee_and_partial_is_not_scenario():
    r=fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=.5,
        evidence=evidence(),now=1001,new_entry=False)
    assert r['scope']=='LEG_ONLY'
    assert r['assumed_entry_fee']==0
    whole=fee_only_projection(side='LONG',entry_price=81000,exit_price=80800,quantity=.5,
        evidence=evidence(),now=1001,new_entry=False,recorded_net=20)
    assert whole['scope']=='WHOLE_SCENARIO'
    assert whole['estimated_net_before_unmodelled_costs']<0


@pytest.mark.parametrize('now', [999,1000+86401])
def test_stale_future_unknown(now):
    assert fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=evidence(),now=now,new_entry=True)['status']=='UNKNOWN'


def test_unverified_accounting_no_fee_evidence():
    assert observed_fee_evidence({}, {'status':'pending'}, now=1000)['status']=='UNKNOWN'


def test_accounting_unchanged_optional_metadata_is_verified_and_compact():
    from tests.test_scenario_accounting import fixture, run
    parts=fixture()
    before=run(parts)
    for trade in parts[0]['trades']:
        trade['raw_execution']['isMaker']=False
    after=run(parts)
    assert before==after
    result=observed_fee_evidence(parts[0],after,now=.1)
    assert result['rates']['TAKER']['count']==3
    assert len(result['source_sha256'])==64
    assert 'MAKER' not in result['rates']
    assert 'trades' not in result


@pytest.mark.parametrize('field,value', [('isMaker','false'),('isMaker',0),('execFee',0),
    ('execFee',-1),('execFee','NaN'),('execTime',2000000),('symbol','ETHUSDT'),('execType','Funding')])
def test_bad_fee_metadata_unknown_without_touching_accounting(field,value):
    from tests.test_scenario_accounting import fixture, run
    parts=fixture()
    accounting=run(parts)
    parts[0]['trades']=parts[0]['trades'][:1]
    parts[0]['trades'][0]['raw_execution'].update(isMaker=False)
    parts[0]['trades'][0]['raw_execution'][field]=value
    assert observed_fee_evidence(parts[0],accounting,now=.1)['status']=='UNKNOWN'
    assert accounting['net_pnl']==9.7


def test_maker_only_not_assumed_taker():
    e=evidence()
    e['rates']['MAKER']=e['rates'].pop('TAKER')
    assert fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=e,now=1001,new_entry=True)['status']=='UNKNOWN'


def test_candidate_projections_keep_gross_and_risk_unchanged():
    from tests.test_scenario_calculated_review import context, wire
    from live.scenario_calculated_review import calculate_candidate
    ctx=context()
    plan=wire(ctx,'LONG')
    before=calculate_candidate(plan,ctx,candidate_id='c')
    ctx['observed_execution_fee_evidence']=evidence()
    after=calculate_candidate(plan,ctx,candidate_id='c')
    assert before['raw_risk']==after['raw_risk']
    assert before['eligible']==after['eligible']
    target=after['projection']['targets'][0]
    assert target['gross_pnl']==before['projection']['targets'][0]['gross_pnl']
    assert target['fee_only_projection']['status']=='AVAILABLE'
    assert target['fee_only_projection']['scope']=='LEG_ONLY'
    assert target['fee_only_projection']['assumed_entry_fee']>0


def test_runner_keeps_stress_allowance_and_recorded_net_once():
    from tests.test_scenario_runner_economics import evidence as accounting_evidence, view
    a=accounting_evidence()
    old=view(accounting=a)
    a['observed_execution_fee_evidence']=evidence()
    new=view(accounting=a)
    for key,value in old.items():
        if key!='fee_only_projection':
            assert new[key]==value
    mark=new['fee_only_projection']['at_mark']
    assert mark['assumed_entry_fee']==0
    assert mark['estimated_net_before_unmodelled_costs']==pytest.approx(a['net_pnl']+new['remaining_gross_at_mark']-mark['assumed_exit_fee'])


def test_holding_candidate_stop_exit_and_pending_scopes():
    from tests.test_scenario_calculated_review import context, wire
    from live.scenario_calculated_review import _holding_projection
    ctx=context()
    ctx.update(scenario_id='s',side='LONG',accounting_status='confirmed',
        positions=[dict(price=83410,quantity=.01)],recorded_scenario_net=-.46,
        observed_execution_fee_evidence=evidence())
    plan=wire(ctx,'LONG')
    plan.update(action='ADJUST',entries=[])
    r=_holding_projection(plan,ctx)
    full=r['confirmed_remaining']['fee_only_projection_at_full_stop']
    assert full['scope']=='WHOLE_SCENARIO'
    assert full['assumed_entry_fee']==0
    assert full['estimated_net_before_unmodelled_costs']==pytest.approx(-.46-5.1-.01*82900*.00055)
    assert r['hypothetical_all_fills']['fee_only_projection_at_full_stop']['status']=='UNKNOWN'
    plan['action']='EXIT'
    exit_=_holding_projection(plan,ctx)['fee_only_projection']
    assert exit_['scope']=='WHOLE_SCENARIO'
    assert exit_['estimated_net_before_unmodelled_costs']==pytest.approx(-.46-.01*83410*.00055)


@pytest.mark.parametrize('target_count', [3,20])
def test_full_holding_two_candidate_select_fee_views_fit_and_restore(target_count, monkeypatch):
    """Exercise real assembly with complete seven-frame facts and legal exit max."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from tests.test_scenario_calculated_review import holding, holding_wire, wire
    from tests.test_scenario_snapshot import snapshot
    from live import scenario_calculated_review as review
    from live.scenario_model_input import project_model_input, restore_model_input
    from live.scenario_position_flow import project_position_flow_input
    from live.scenario_preview import response_contract
    from live.scenario_recovery import numeric_market
    from live.scenario_runner_economics import runner_economics
    market=snapshot()
    now=market['as_of_ms']/1000
    ctx=holding()
    ctx.update(now=now,input_captured_at=now,account_captured_at=now,
        observed_execution_fee_evidence=evidence(),recorded_scenario_net=-2.5)
    ctx['observed_execution_fee_evidence']['rates']['TAKER'].update(earliest_at=now,latest_at=now)
    ctx['recovery']=dict(first_observation=False,eligible=True,current=numeric_market(market),
        baseline=numeric_market(market),changed_evidence=[],risk_fraction=.005)
    accounting=dict(status='confirmed',accounting_complete=True,positions=ctx['positions'],
        gross_pnl=0,fees=2,funding_net=-.5,net_pnl=-2.5,
        observed_execution_fee_evidence=ctx['observed_execution_fee_evidence'])
    ctx['runner_economics']=runner_economics(accounting=accounting,side='LONG',mark_price=83400,
        hard_stop=82500,observed_at=now,now=now,pending_entries=[],initial_equity=10000,
        estimated_cost_rate=.002,slippage_bps=20,price_tick=.1)
    candidates=[]
    for i in range(2):
        p=holding_wire(ctx)
        p['expires_at']=now+300
        p['take_profits']=[dict(id=f't{i}_{j}',price=84000+i*100+j*10+.1,fraction=.02) for j in range(target_count)]
        p['partial_stops']=[dict(id=f's{i}_{j}',price=82600+i*100+j*10+.1,fraction=.02) for j in range(target_count)]
        candidates.append(p)
    wait=wire(ctx)
    wait['expires_at']=now+300
    originals=[]
    sizes=[]
    if target_count==20:
        # Keep this overflow fixture above the cap after retiring lower-frame input.
        ctx['capacity_fixture_padding']='x'*64
    def without_fee_additions(value):
        if isinstance(value,list):
            return [without_fee_additions(item) for item in value]
        if isinstance(value,dict):
            return {key:without_fee_additions(item) for key,item in value.items()
                if not key.startswith('fee_only_projection') and key not in
                    ('observed_execution_fee_evidence','recorded_scenario_net')}
        return value
    def project(value):
        originals.append(deepcopy(value))
        result=project_position_flow_input(value)
        if 'calculated_candidates' in value:
            baseline=project_model_input(without_fee_additions(value))[1]['projected_utf8_bytes']
            sizes.append((baseline,result[1]['projected_utf8_bytes']))
            assert restore_model_input(result[0])==value
        return result
    monkeypatch.setattr(review,'project_position_flow_input',project)
    calls=[]
    def generate(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:
            body=dict(wait_proposal=wait,candidates=candidates,no_candidate_reason='')
        else:
            sent=json.loads(kwargs['user_prompt'])
            restored=restore_model_input(sent)
            view=restored['contract_context'].pop('holding_exit_paths',None)
            if view is not None:
                assert len(view)==2
                assert all(row[2]['version']==3 for row in view)
            assert restored==originals[-1]
            assert len(kwargs['user_prompt'].encode())<=100_000
            assert restored['contract_context']['runner_economics']['fee_only_projection']['at_mark']['status']=='AVAILABLE'
            for r in restored['calculated_candidates']['candidates']:
                assert r['eligible']
                targets=r['projection']['confirmed_remaining']['targets']
                assert len(targets)==target_count*2
                assert all(t['fee_only_projection']['status']=='AVAILABLE' for t in targets)
            choice=restored['calculated_candidates']['wait']
            body=dict(selection_token=f"{choice['id']}:{model_receipt_hash(choice)}",rationale='cost review')
        return SimpleNamespace(text=json.dumps(body))
    if target_count==20:
        # This padded legal-array fixture exceeds the existing input cap.
        # Retain the cap; do not truncate financial facts or call SELECT anyway.
        with pytest.raises(ValueError,match='research_input_size'):
            review.research_review(market,ctx,response_contract(ctx),generate=generate,record=lambda event:None,clock=lambda:now)
        assert len(calls)==1
        assert sizes[-1][0]>100_000
        assert sizes[-1][1]>sizes[-1][0]
    else:
        review.research_review(market,ctx,response_contract(ctx),generate=generate,record=lambda event:None,clock=lambda:now)
        assert len(calls)==2
        assert sizes[-1][1]<100_000
