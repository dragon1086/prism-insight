import pytest

from live.scenario_runner_economics import runner_economics


def evidence():
    return dict(status='confirmed', accounting_complete=True, gross_pnl=12.038,
                fees=7.24746703, funding_net=.35815114,
                realized_loss=0, fees_paid=7.24746703, funding_paid=0,
                net_pnl=5.14868411, positions=[dict(price=85242.5, quantity=.066)])


def view(**kwargs):
    options = dict(accounting=evidence(), side='SHORT', mark_price=84910,
                   hard_stop=85650, observed_at=1000, now=1001,
                   pending_entries=[], initial_equity=9539.78,
                   estimated_cost_rate=.002, slippage_bps=20, price_tick='.1')
    options.update(kwargs)
    return runner_economics(**options)


def test_partial_profit_does_not_mean_scenario_profit_at_stop():
    r = view()
    assert r['status'] == 'available'
    assert r['realized_net_including_recorded_costs'] == pytest.approx(5.14868411)
    assert r['remaining_gross_at_mark'] == pytest.approx(21.945)
    assert r['remaining_gross_at_stop'] == pytest.approx(-26.895)
    assert r['scenario_net_at_stop_before_future_costs'] < 0
    assert r['scenario_net_at_stop_after_estimated_costs'] < r['scenario_net_at_stop_before_future_costs']
    assert r['budget_replenishment_allowed'] is False


@pytest.mark.parametrize('side,mark,stop', [('LONG', 105, 98), ('SHORT', 95, 102)])
def test_symmetric_signs_and_conservative_allowance(side, mark, stop):
    a=evidence();a.update(gross_pnl=3, fees=1, funding_net=-.2, net_pnl=1.8,
                          positions=[dict(price=100,quantity=2)])
    r=view(accounting=a, side=side, mark_price=mark, hard_stop=stop)
    assert r['remaining_gross_at_mark'] == 10
    assert r['remaining_gross_at_stop'] == -4
    assert r['giveback_from_mark_to_stop_before_future_costs'] == 14


@pytest.mark.parametrize('field,value', [('gross_pnl',None),('fees',float('nan')),
                                      ('funding_net',None),('net_pnl',999),
                                      ('accounting_complete',False)])
def test_incomplete_accounting_never_becomes_zero(field,value):
    a=evidence();a[field]=value
    assert view(accounting=a)['status']=='unavailable'


@pytest.mark.parametrize('changes', [dict(observed_at=800),dict(observed_at=1002),
    dict(pending_entries=[dict(quantity=.1)]),dict(hard_stop=None),dict(side=''),
    dict(mark_price=float('inf')),dict(accounting={}),dict(initial_equity=0)])
def test_missing_stale_or_pending_is_unknown(changes):
    assert view(**changes)['status']=='unavailable'


def test_input_not_mutated_and_fee_rebate_is_signed():
    a=evidence();a.update(fees=-1,net_pnl=13.39615114)
    original=repr(a)
    r=view(accounting=a)
    assert r['status']=='available'
    assert r['recorded_fees']==-1
    assert repr(a)==original


def test_real_broker_context_passes_confirmed_finance_without_extra_collection():
    from live.scenario_broker import ScenarioDemoBroker
    broker=object.__new__(ScenarioDemoBroker)
    calls=[]
    def capture():
        calls.append('capture')
        return dict(captured_at=1000, mark_price=84910, equity=9539.78,
                    account_version='verified', legacy_fenced=False,
                    position=dict(size='.066',avgPrice='85242.5'),
                    ticker=dict(bid1Price='84909.9',ask1Price='84910'))
    broker.capture_account=capture
    broker.clock=lambda:1001
    broker._active=lambda:dict(scenario_id='owned',side='SHORT',hard_stop=85650,initial_equity=9539.78)
    broker.children=lambda sid:[dict(kind='native_sl',status='LIVE',evidence={})]
    broker._daily=lambda observed:dict(new_risk_allowed=True,day='2026-10-06',day_start_equity=9600,daily_net_pnl=-60)
    broker._instrument=lambda:dict(tick='.1',step='.001',minimum='.001',notional='5')
    broker._risk_accounting=lambda *args:evidence()
    broker._verify_protection=lambda *args:True
    broker._new_risk_enabled=lambda:True
    ctx=broker.context()
    assert calls==['capture']
    assert ctx['runner_economics']==view()
    assert ctx['account_captured_at']==1000
    assert ctx['previous_hard_stop']==85650


@pytest.mark.parametrize('side,mark,stop,expected', [
    ('LONG', 110, 90, 101.1), ('SHORT', 90, 110, 98.9)])
def test_positive_stop_is_strictly_profitable_tick_not_breakeven(side,mark,stop,expected):
    a=evidence();a.update(gross_pnl=0,fees=2,funding_net=0,net_pnl=-2,
                          positions=[dict(price=100,quantity=2)])
    r=view(accounting=a,side=side,mark_price=mark,hard_stop=stop,
           estimated_cost_rate=0,slippage_bps=0)['cost_positive_stop']
    assert r['minimum_positive_stop_price']==expected
    assert r['estimated_scenario_net_at_minimum_positive_stop']==pytest.approx(.2)
    assert r['feasible_without_widening'] is True


@pytest.mark.parametrize('side', ['LONG','SHORT'])
def test_positive_stop_cost_equation_with_partial_realization_and_signed_costs(side):
    from decimal import Decimal
    a=evidence();a.update(gross_pnl=3,fees=-.2,funding_net=-.7,net_pnl=2.5,
                          positions=[dict(price=100,quantity=1),dict(price=102,quantity=1)])
    r=view(accounting=a,side=side,mark_price=120 if side=='LONG' else 80,
           hard_stop=90 if side=='LONG' else 110)['cost_positive_stop']
    p=Decimal(str(r['minimum_positive_stop_price']))
    sign=1 if side=='LONG' else -1
    def net(price):
        return Decimal('2.5')+sign*(2*price-202)-2*price*Decimal('.004')
    assert net(p)>0
    assert net(p-sign*Decimal('.1'))<=0
    assert r['estimated_scenario_net_at_minimum_positive_stop']==pytest.approx(float(net(p)))


@pytest.mark.parametrize('side,mark,stop', [('LONG',100,90),('SHORT',100,110)])
def test_positive_stop_never_recommends_crossed_mark(side,mark,stop):
    r=view(side=side,mark_price=mark,hard_stop=stop,
           accounting=dict(evidence(),positions=[dict(price=100,quantity=2)],
                           gross_pnl=0,fees=2,funding_net=0,net_pnl=-2))['cost_positive_stop']
    assert r['feasible_without_widening'] is False
    assert r['feasible_stop_price'] is None


@pytest.mark.parametrize('side,mark,stop', [('LONG',120,110),('SHORT',80,90)])
def test_already_tighter_stop_is_not_relaxed(side,mark,stop):
    a=dict(evidence(),positions=[dict(price=100,quantity=2)])
    r=view(accounting=a,side=side,mark_price=mark,hard_stop=stop)['cost_positive_stop']
    assert r['feasible_stop_price']==stop


@pytest.mark.parametrize('tick', [None,0,-.1,True,'nan','Infinity'])
def test_unknown_tick_does_not_invent_positive_stop(tick):
    assert view(price_tick=tick)['cost_positive_stop']['status']=='unavailable'


def test_equality_to_mark_is_not_feasible():
    a=dict(evidence(),gross_pnl=0,fees=0,funding_net=0,net_pnl=0,
           positions=[dict(price=100,quantity=1)])
    r=view(accounting=a,side='LONG',mark_price=100.1,hard_stop=90,
           estimated_cost_rate=0,slippage_bps=0)['cost_positive_stop']
    assert r['minimum_positive_stop_price']==100.1
    assert r['feasible_stop_price'] is None


def test_non_power_of_ten_tick_is_respected():
    a=dict(evidence(),gross_pnl=0,fees=2.2,funding_net=0,net_pnl=-2.2,
           positions=[dict(price=100,quantity=2)])
    r=view(accounting=a,side='LONG',mark_price=110,hard_stop=90,
           estimated_cost_rate=0,slippage_bps=0,price_tick='.25')['cost_positive_stop']
    assert r['minimum_positive_stop_price']==101.25


def test_large_realized_profit_still_preserves_existing_long_stop():
    a=dict(evidence(),gross_pnl=200,fees=0,funding_net=0,net_pnl=200,
           positions=[dict(price=100,quantity=1)])
    r=view(accounting=a,side='LONG',mark_price=110,hard_stop=90)['cost_positive_stop']
    assert r['minimum_positive_stop_price']==.1
    assert r['feasible_stop_price']==90


@pytest.mark.parametrize('pending', [None,[dict(quantity=1)]])
def test_positive_stop_not_projected_with_unknown_or_pending_entries(pending):
    r=view(pending_entries=pending)
    assert r['status']=='unavailable'
    assert 'cost_positive_stop' not in r
