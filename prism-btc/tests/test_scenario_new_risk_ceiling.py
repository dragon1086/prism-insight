from copy import deepcopy

import pytest

from core.llm_scenario import EconomicProposalRejection
from live.scenario_risk_allocation import allocate_entry_risk, validate_allocation_prices
from tests.test_scenario_risk_allocation import context, proposal


@pytest.mark.parametrize('action', ['OPEN', 'ADJUST'])
@pytest.mark.parametrize('side,stop', [('LONG', 82410), ('SHORT', 83790)])
@pytest.mark.parametrize('confidence', [.5, .75, 1.])
@pytest.mark.parametrize('fractions', [[1.], [.5,.5], [.2,.3,.3]])
def test_all_new_entries_share_one_batch_ceiling(action,side,stop,confidence,fractions):
    p,c=proposal(),context()
    p.update(action=action,side=side,hard_stop=stop,confidence=confidence)
    p['entries']=[dict(id=str(i),price=83100,allocation_fraction=v)for i,v in enumerate(fractions)]
    before=deepcopy((p,c))
    plan,receipt=allocate_entry_risk(p,c)
    assert (p,c)==before
    assert sum(r['worst_chase_risk']for r in receipt['entries'])<=receipt['budget']*.30
    assert sum(r['allocation_budget']for r in receipt['entries'])<=receipt['budget']*.30+1e-10
    validate_allocation_prices(plan,receipt,c)


def test_small_requested_allocation_is_not_discounted_twice():
    p,c=proposal(),context()
    p['confidence']=.5
    p['entries'][0]['allocation_fraction']=.2
    _,receipt=allocate_entry_risk(p,c)
    assert receipt['new_risk_ceiling']==25
    assert receipt['entries'][0]['allocation_budget']==5


def test_forged_entry_budgets_cannot_bypass_the_batch_limit():
    p,c=proposal(),context()
    plan,receipt=allocate_entry_risk(p,c)
    plan['entries'][0]['quantity']=.03
    receipt['entries'][0].update(quantity=.03,allocation_budget=50.)
    with pytest.raises(EconomicProposalRejection,match='allocation_batch_risk_limit_exceeded'):
        validate_allocation_prices(plan,receipt,c)


def test_minimum_quantity_does_not_raise_a_capped_order():
    p,c=proposal(),context()
    c['minimum_quantity']=.02
    with pytest.raises(EconomicProposalRejection,match='allocation_below_minimum'):
        allocate_entry_risk(p,c)
