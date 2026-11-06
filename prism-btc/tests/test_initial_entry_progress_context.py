from copy import deepcopy

import pytest

from live.scenario_calculated_review import _initial_entry_progress_units, _project_initial_entry_progress
from live.scenario_model_input import project_model_input, restore_model_input
from tests.test_scenario_llm import context


def active(side='LONG', mark=98):
    c=context(True)
    c.update(side=side, mark_price=mark, accounting_status='confirmed', account_captured_at=999)
    c['entry_thesis']=dict(status='VERIFIED', side=side, initial_fill_average=100,
        initial_hard_stop=90 if side=='LONG' else 110, current_mark=mark,
        first_fill_at=950, observed_at=999,
        favorable_price_change_from_initial_average=(mark-100)*(1 if side=='LONG' else -1))
    return c


@pytest.mark.parametrize('side,mark,expected', [('LONG',98,-.2),('SHORT',102,-.2),
    ('LONG',100,0),('SHORT',95,.5),('LONG',89,-1.1)])
def test_entry_progress_is_directional_endpoint_not_pnl(side, mark, expected):
    c=active(side,mark);before=deepcopy(c)
    value=_initial_entry_progress_units(c)
    assert value['value']==pytest.approx(expected)
    assert value['meaning']=='SIGNED_PRICE_PROGRESS_OVER_INITIAL_STOP_DISTANCE_NOT_PNL_OR_CURRENT_RISK'
    assert c==before


@pytest.mark.parametrize('update', [
    {'accounting_status':'pending'}, {'account_captured_at':879}, {'account_captured_at':949},
    {'account_captured_at':998},
    {'positions':[]}, {'positions':[{'quantity':0}]}, {'mark_price':99},
    {'entry_thesis':{'status':'MISSING'}},
])
def test_uncertain_position_never_produces_progress(update):
    c=active();c.update(update)
    assert _initial_entry_progress_units(c) is None


def test_missing_context_never_produces_progress():
    assert _initial_entry_progress_units(None) is None


@pytest.mark.parametrize('change', [
    {'initial_hard_stop':100}, {'initial_hard_stop':101}, {'current_mark':float('nan')},
    {'side':'SHORT'}, {'observed_at':879}, {'first_fill_at':1001},
    {'favorable_price_change_from_initial_average':3},
])
def test_inconsistent_thesis_never_produces_progress(change):
    c=active();c['entry_thesis'].update(change)
    assert _initial_entry_progress_units(c) is None


def test_projection_preserves_all_existing_input_and_history():
    c=active();c['position_flow']={'status':'PARTIAL','history':[{'reason':'keep this'}]}
    base,_=project_model_input({'market_snapshot':{'mandatory':'keep'},'contract_context':c})
    before=deepcopy(base);result=_project_initial_entry_progress(base,c)
    restored=restore_model_input(result);value=restored['contract_context'].pop('initial_entry_progress_units')
    assert value['value']==pytest.approx(-.2)
    assert restored==restore_model_input(base)
    assert base==before


def test_projection_at_budget_falls_back_without_evicting_history(monkeypatch):
    import live.scenario_calculated_review as module
    c=active();c['position_flow']={'status':'PARTIAL','history':[{'reason':'keep this'}]}
    base,_=project_model_input({'market_snapshot':{'mandatory':'x'*300},'contract_context':c})
    before=deepcopy(base);monkeypatch.setattr(module,'_ENTRY_PROGRESS_INPUT_LIMIT',len(module._encoded(base).encode())+10)
    assert _project_initial_entry_progress(base,c)==base
    assert base==before


def test_projection_refuses_to_trade_away_existing_history(monkeypatch):
    import live.scenario_model_input as model_input
    c=active();c['position_flow']={'status':'PARTIAL','history':[{'reason':'keep this'}]}
    base,_=project_model_input({'market_snapshot':{},'contract_context':c})
    original=model_input.project_model_input

    def corrupt(value, **kwargs):
        altered=deepcopy(value)
        altered['contract_context']['position_flow']['history']=[]
        return original(altered, **kwargs)

    monkeypatch.setattr(model_input,'project_model_input',corrupt)
    assert _project_initial_entry_progress(base,c)==base
