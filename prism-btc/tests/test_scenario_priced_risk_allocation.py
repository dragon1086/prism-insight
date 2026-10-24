"""One initial quantity covers raw and possible policy-price risk anchors."""
from copy import deepcopy

import pytest

from core.llm_scenario import ScenarioValidationError
from live.scenario_risk_allocation import allocate_entry_risk,validate_allocation_prices
from tests.test_scenario_risk_allocation import context,proposal


@pytest.mark.parametrize('side,stop,adjusted',[('LONG',82410,83107.3),('SHORT',83790,83092.7)])
@pytest.mark.parametrize('chase',[0,50])
def test_initial_size_covers_possible_policy_anchor_and_its_chase(side,stop,adjusted,chase):
    ctx=context()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},quantity_step=.000001,minimum_quantity=.000001)
    raw=proposal()
    raw.update(side=side,hard_stop=stop,chase=dict(max_bps=chase,max_reprices=1 if chase else 0))
    before=deepcopy((raw,ctx))
    plan,receipt=allocate_entry_risk(raw,ctx)
    effective=deepcopy(plan)
    effective['entries'][0]['price']=adjusted
    validate_allocation_prices(effective,receipt,ctx)
    assert effective['entries'][0]['quantity']==plan['entries'][0]['quantity']
    assert (raw,ctx)==before
    assert receipt['entries'][0]['risk_anchor_price']==adjusted
    assert receipt['entries'][0]['price_policy_anchor_is_execution_price'] is False


def test_fixed_5310_budget_crossing_is_prevented_before_pricing():
    # Numeric slice of preserved 5975310 v1r1; full-source reproduction is separate.
    ctx=context()
    ctx.update(initial_equity=9407.64006774,execution_price_policy={'version':'round-limit-v1'})
    raw=proposal()
    raw.update(confidence=.58,hard_stop=83520,chase=dict(max_bps=0,max_reprices=0))
    raw['entries']=[dict(id='long_initial',price=83890,trigger_price=None,allocation_fraction=.65)]
    plan,receipt=allocate_entry_risk(raw,ctx)
    assert receipt['entries'][0]['allocation_budget']==pytest.approx(17.7334015276899)
    assert plan['entries'][0]['price']==83890
    assert plan['entries'][0]['quantity']==.024
    effective=deepcopy(plan)
    effective['entries'][0]['price']=83897.3
    validate_allocation_prices(effective,receipt,ctx)


@pytest.mark.parametrize('kind',['absent','conditional','nonround'])
def test_no_static_price_policy_candidate_preserves_old_size(kind):
    raw,ctx=proposal(),context()
    original,_=allocate_entry_risk(raw,ctx)
    if kind!='absent':
        ctx['execution_price_policy']={'version':'round-limit-v1'}
    if kind=='conditional':
        raw['entries'][0]['trigger_price']=83101
    if kind=='nonround':
        raw['entries'][0]['price']=83100.1
        baseline=deepcopy(ctx)
        baseline.pop('execution_price_policy')
        original,_=allocate_entry_risk(raw,baseline)
    plan,_=allocate_entry_risk(raw,ctx)
    assert plan['entries'][0]['quantity']==original['entries'][0]['quantity']


def test_short_high_cost_uses_raw_anchor_when_offset_reduces_risk():
    raw,ctx=proposal(),context()
    raw.update(side='SHORT',hard_stop=90000,chase=dict(max_bps=50,max_reprices=1))
    ctx.update(estimated_cost_rate=1.01,quantity_step=.000001,minimum_quantity=.000001)
    from live.scenario_risk_allocation import _worst_unit_risk
    assert _worst_unit_risk(raw,ctx,83100)>_worst_unit_risk(raw,ctx,83092.7)
    ctx['execution_price_policy']={'version':'round-limit-v1'}
    # The real validator already forbids cost > 0.1; do not relax it for this branch.
    with pytest.raises(ScenarioValidationError,match='invalid cost assumptions'):
        allocate_entry_risk(raw,ctx)


def test_beyond_reserved_price_or_model_allocation_changes_still_rejected():
    raw,ctx=proposal(),context()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},quantity_step=.000001,minimum_quantity=.000001)
    plan,receipt=allocate_entry_risk(raw,ctx)
    plan['entries'][0]['price']=83200
    with pytest.raises(ScenarioValidationError,match='allocation_budget_exceeded'):
        validate_allocation_prices(plan,receipt,ctx)


@pytest.mark.parametrize('side',['LONG','SHORT'])
@pytest.mark.parametrize('tick',[.01,.1,.5,1,5,10,20])
def test_shared_static_anchor_matches_real_policy_when_applied(side,tick):
    from core.scenario_limit_prices import possible_entry_price_anchor,validate_execution_prices
    from tests.test_llm_scenario import sample
    raw,ctx=sample()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},price_tick=tick,
        quantity_step=.001,minimum_quantity=.001,minimum_notional=5,
        limit_price_quotes=dict(bid=100100 if side=='LONG' else 99890,
                               ask=100110 if side=='LONG' else 99900,captured_at=999))
    if side=='SHORT':
        raw.update(side=side,hard_stop=101000)
        raw['take_profits'][0]['price']=98000
    raw['entries'][0].pop('quantity')
    raw['entries'][0]['allocation_fraction']=1
    plan,receipt=allocate_entry_risk(raw,ctx)
    adjusted=validate_execution_prices(plan,ctx)
    anchor=possible_entry_price_anchor(raw['entries'][0],ctx,side)
    assert adjusted['entries'][0]['price']==(float(anchor) if anchor is not None else 100000)
    assert adjusted['entries'][0]['quantity']==plan['entries'][0]['quantity']
    validate_allocation_prices(adjusted,receipt,ctx)


@pytest.mark.parametrize('quotes',[None,dict(bid=100100,ask=100110,captured_at=0),
                                  dict(bid=100001,ask=100005,captured_at=999)])
def test_quote_skipped_policy_keeps_conservative_reserve_not_fictional_execution(quotes):
    from core.scenario_limit_prices import validate_execution_prices
    from tests.test_llm_scenario import sample
    raw,ctx=sample()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},price_tick=.1,
               quantity_step=.001,minimum_quantity=.001,minimum_notional=5)
    if quotes is not None:
        ctx['limit_price_quotes']=quotes
    raw['entries'][0].pop('quantity')
    raw['entries'][0]['allocation_fraction']=1
    plan,receipt=allocate_entry_risk(raw,ctx)
    effective=validate_execution_prices(plan,ctx)
    assert effective['entries'][0]['price']==100000
    assert receipt['entries'][0]['possible_policy_price']==100007.3
    assert receipt['entries'][0]['price_policy_anchor_is_execution_price'] is False
    validate_allocation_prices(effective,receipt,ctx)


def test_multiple_entries_keep_pending_and_consumed_reserves_and_individual_caps():
    raw,ctx=proposal(),context()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},quantity_step=.000001,minimum_quantity=.000001,
        positions=[dict(price=83000,quantity=.01)],pending_entries=[dict(id='old',price=83100,quantity=.01)],
        realized_loss=20,fees_paid=3,funding_paid=1)
    raw.update(action='ADJUST',hard_stop=82500,cancel_entry_ids=['old'],
        entries=[dict(id='a',price=83100,allocation_fraction=.4),dict(id='b',price=83200,allocation_fraction=.3)])
    plan,receipt=allocate_entry_risk(raw,ctx)
    assert receipt['reserved_risk_at_proposed_stop']==pytest.approx(41.622)
    assert receipt['new_risk_ceiling']==pytest.approx(8.378)
    effective=deepcopy(plan)
    for row in effective['entries']:
        row['price']+=7.3
    validate_allocation_prices(effective,receipt,ctx)
    assert receipt['cancellation_requests_release_risk'] is False


def test_initial_bounded_quantity_below_minimum_is_rejected_not_rounded_up():
    raw,ctx=proposal(),context()
    ctx.update(execution_price_policy={'version':'round-limit-v1'},minimum_quantity=.0367,quantity_step=.0001)
    with pytest.raises(ScenarioValidationError,match='allocation_below_minimum'):
        allocate_entry_risk(raw,ctx)


def test_invalid_raw_stop_is_not_repaired_by_price_anchor():
    raw,ctx=proposal(),context()
    raw['hard_stop']=83100
    ctx['execution_price_policy']={'version':'round-limit-v1'}
    with pytest.raises(ScenarioValidationError,match='allocation_stop_direction'):
        allocate_entry_risk(raw,ctx)
