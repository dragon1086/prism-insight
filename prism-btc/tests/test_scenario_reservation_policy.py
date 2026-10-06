from live.scenario_llm import SYSTEM_PROMPT
from live.scenario_preview import response_contract
from live.scenario_notice import render_notice
from tests.test_scenario_runtime import setup as setup, proposal


def test_reservation_contract_exposes_slot_deadline_and_price_cap():
    ctx=dict(now=1001,input_id='input',revision=0,scenario_id=None,
             conditional_entry_version=1,pending_entries=[])
    c=response_contract(ctx)
    assert c['reservation_expires_at']==1200
    assert 'trigger_price' in c['entries'][0]
    text=' '.join(SYSTEM_PROMPT.split())
    assert 'conditional stop-LIMIT' in text
    assert 'not an exact exchange-side expiry' in text
    assert 'cost_positive_stop' in text
    assert 'not a fixed 10x +1%' in text
    assert 'one direction per account' in text


def test_conditional_plan_never_claims_fill_or_exact_expiry():
    event=dict(kind='PLAN',timestamp=1791252000,side='SHORT',price=84990,quantity=.01,
        plan_action='OPEN',hard_stop=85400,take_profits=[],
        conditional_entries=[dict(trigger_price=85000,price=84990,quantity=.01)],
        reservation_expires_at=1791252300)
    text=render_notice(event)
    assert '조건부 예약 계획' in text
    assert '미체결' in text
    assert 'MarkPrice' in text
    assert '취소 확인 전 체결 가능' in text


def test_runtime_keeps_conditional_reservation_and_blocks_opposite_open(setup):
    runtime, broker, now, _ = setup
    broker.ctx['conditional_entry_version']=1
    broker.ctx['price_tick']=.1
    runtime.propose=lambda s,c:dict(proposal(s,c),
        entries=[dict(id='e1',price=102,quantity=1,trigger_price=101)],
        chase=dict(max_bps=0,max_reprices=0))
    notices=[]
    runtime._notice=lambda key,event:notices.append(event) or True
    assert runtime.tick()['status']=='intent_pending'
    assert next(e for e in notices if e['kind']=='PLAN')['conditional_entries'][0]['trigger_price']==101
    pending=dict(id='e1',price=102.,quantity=1.,trigger_price=101.,
                 order_status='Untriggered',expires_at=now[0]+90)
    broker.ctx['pending_entries']=[pending]
    broker.evidence=dict(intents=[dict(intent_id='action-1',terminal=False,
        protection_ok=True,orders_reconciled=True,executions_complete=True,
        execution_ids=[],filled_quantity=0,exchange_order_ids=['order-1'],open_entries=[pending])])
    state=runtime.state();runtime._reconcile(state)
    assert runtime.conn.execute("SELECT status FROM llm_scenario_intents").fetchone()[0]=='LIVE_RECONCILED'
    assert runtime._context(state)['pending_entries']==[pending]
    now[0]+=300
    runtime.propose=lambda s,c:dict(proposal(s,c),side='SHORT',action_id='opposite',hard_stop=110)
    assert runtime.tick()['status']=='blocked'
    assert broker.executed==['action-1']


def test_pending_receipt_rejects_partial_or_invalid_conditional_shape():
    from live.scenario_runtime import _valid_open_entry
    base=dict(id='e',price=100,quantity=1,trigger_price=101,order_status='Untriggered',expires_at=1200)
    assert _valid_open_entry(base)
    assert not _valid_open_entry({k:v for k,v in base.items() if k!='expires_at'})
    assert not _valid_open_entry(dict(base,order_status='Unknown'))
    assert not _valid_open_entry(dict(base,trigger_price=-1))
