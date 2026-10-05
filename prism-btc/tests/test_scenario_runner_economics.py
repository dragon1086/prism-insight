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
                   estimated_cost_rate=.002, slippage_bps=20)
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
