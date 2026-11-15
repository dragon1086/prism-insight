"""Joint paths are advisory hypotheticals, not fill/risk authorization."""
from copy import deepcopy

import pytest

from live.scenario_calculated_review import _gross_projection, calculate_candidate, receipt_hash
from tests.test_scenario_calculated_review import context, wire






@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('tp_fraction', [None, .5, 1])
def test_no_partial_stop_full_tp_and_unpriced_runner_remain_valid_without_risk_credit(side, tp_fraction):
    ctx = context()
    raw = wire(ctx, side)
    if tp_fraction is None:
        raw['take_profits'] = []
    else:
        raw['take_profits'][0]['fraction'] = tp_fraction
    before = deepcopy(raw)
    receipt = calculate_candidate(raw, ctx, candidate_id='c0')
    assert receipt['eligible']
    assert receipt['raw_proposal'] == raw == before
    projection = receipt['projection']
    expected_remainder = .01 * (1 - (tp_fraction or 0))
    path_name = 'TP_THEN_HARD_SL' if tp_fraction is not None else 'HARD_SL_ONLY'
    assert paths(projection)[path_name]['remaining_before_hard_stop'] == pytest.approx(expected_remainder)
    assert paths(projection)['HARD_SL_ONLY']['gross_pnl'] == projection['gross_at_full_stop']
    assert projection['actual_net_pnl'] is None
    baseline = calculate_candidate(wire(ctx, side), ctx, candidate_id='c0')
    assert receipt['raw_risk'] == baseline['raw_risk']
    assert receipt['effective_per_entry_risk'] == baseline['effective_per_entry_risk']


def test_comparison_labels_are_not_new_selection_fields_or_execution_gate():
    from tests.test_scenario_calculated_review import run
    result, _, calls = run()
    assert set(calls[1]['response_schema']['properties']) == {'selection_token', 'rationale'}
    assert result['selection']['selection'] == 'c0'
    assert result['selection']['rationale'] == '계산과 추세 검토 후 선택'
    assert set(result['selection']) == {'selection', 'receipt_sha256', 'rationale'}


def fixture(side='LONG'):
    ctx = context()
    ctx.update(quantity_step=.1, minimum_quantity=.1, now=1000)
    plan = dict(action='OPEN', side=side, entries=[dict(id='entry', price=100, quantity=1)],
        hard_stop=80 if side == 'LONG' else 120,
        take_profits=[dict(id='tp', price=120 if side == 'LONG' else 80, fraction=.6)],
        partial_stops=[dict(id='ps', price=90 if side == 'LONG' else 110, fraction=.6)])
    return plan, ctx


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('case,expected', [
    ('no_sl', {'TP_STOP', 'TP1_STOP', 'STOP'}),
    ('no_tp', {'SL_STOP', 'SL1_STOP', 'STOP'}),
    ('neither', {'STOP'}),
    ('full_tp', {'TP_STOP', 'TP1_STOP', 'SL_STOP', 'SL1_TP'}),
    ('full_sl', {'TP_STOP', 'SL_STOP', 'SL1_STOP', 'TP1_SL'}),
    ('subminimum_sl_after_tp', {'TP_STOP', 'TP1_STOP', 'SL_STOP', 'SL1_TP'}),
    ('subminimum_initial_sl', {'TP_STOP', 'TP1_STOP', 'STOP'}),
])
def test_only_actually_applied_leg_types_are_named(side, case, expected):
    plan, ctx = fixture(side)
    if case in {'no_sl', 'neither'}:
        plan['partial_stops'] = []
    if case in {'no_tp', 'neither'}:
        plan['take_profits'] = []
    if case == 'full_tp':
        plan['take_profits'][0]['fraction'] = 1
    if case == 'full_sl':
        plan['partial_stops'][0]['fraction'] = 1
    if case == 'subminimum_sl_after_tp':
        ctx['minimum_quantity'] = .2
        plan['take_profits'][0]['fraction'] = .9
    if case == 'subminimum_initial_sl':
        plan['partial_stops'][0]['fraction'] = .05
    joint = _gross_projection(plan, ctx)['joint_exit_paths']
    actual = [name for row in joint['paths'] for name in row[0]]
    assert set(actual) == expected
    assert len(actual) == len(set(actual))
    assert joint['version'] == 2


def paths(projection):
    result = projection['joint_exit_paths']
    names = dict(TP_STOP='TP_THEN_HARD_SL', SL_STOP='PARTIAL_SL_THEN_HARD_SL',
                 TP1_SL='FIRST_TP_THEN_PARTIAL_SL', SL1_TP='FIRST_PARTIAL_SL_THEN_TP',
                 TP1_STOP='FIRST_TP_THEN_HARD_SL', SL1_STOP='FIRST_PARTIAL_SL_THEN_HARD_SL',
                 STOP='HARD_SL_ONLY')
    expanded = {}
    for sequences, residual, gross, fee in result['paths']:
        path = dict(remaining_before_hard_stop=residual, gross_pnl=gross,
            fee_only=dict(status='UNKNOWN') if fee is None else dict(status='AVAILABLE',
                **dict(zip(('assumed_entry_fee', 'assumed_exit_fee', 'estimated_net_before_unmodelled_costs'), fee))))
        expanded.update({names[index]: path for index in sequences})
    return expanded


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_crossed_sixty_percent_exits_cap_original_quota_not_remaining_fraction(side):
    plan, ctx = fixture(side)
    before = deepcopy((plan, ctx))
    projection = _gross_projection(plan, ctx)
    p = paths(projection)
    assert p['FIRST_TP_THEN_PARTIAL_SL']['remaining_before_hard_stop'] == 0
    assert p['FIRST_TP_THEN_PARTIAL_SL']['gross_pnl'] == pytest.approx(8)
    assert p['FIRST_PARTIAL_SL_THEN_TP']['remaining_before_hard_stop'] == 0
    assert p['FIRST_PARTIAL_SL_THEN_TP']['gross_pnl'] == pytest.approx(2)
    assert p['TP_THEN_HARD_SL']['gross_pnl'] == pytest.approx(4)
    assert p['PARTIAL_SL_THEN_HARD_SL']['gross_pnl'] == pytest.approx(-14)
    assert projection['gross_at_full_stop'] == -20
    assert (plan, ctx) == before


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_allocation_original_order_then_price_path_sorting(side):
    plan, ctx = fixture(side)
    plan['take_profits'] = [dict(id='far', price=130 if side == 'LONG' else 70, fraction=.7),
                            dict(id='near', price=110 if side == 'LONG' else 90, fraction=.3)]
    projection = _gross_projection(plan, ctx)
    assert [t['quantity'] for t in projection['targets'][:2]] == [.7, .3]
    p = paths(projection)
    assert p['TP_THEN_HARD_SL']['gross_pnl'] == 24
    assert p['FIRST_TP_THEN_PARTIAL_SL']['gross_pnl'] == -5
    assert p['FIRST_TP_THEN_PARTIAL_SL']['remaining_before_hard_stop'] == .1


@pytest.mark.parametrize('empty', ['take_profits', 'partial_stops', 'both'])
def test_empty_legs_deduplicate_and_never_resurrect_lots(empty):
    plan, ctx = fixture()
    for name in ('take_profits', 'partial_stops'):
        if empty == name or empty == 'both':
            plan[name] = []
    result = _gross_projection(plan, ctx)['joint_exit_paths']
    assert len(result['paths']) < 4
    for path in result['paths']:
        assert 0 <= path[1] <= 1
    if empty == 'both':
        assert result['paths'][0][1:3] == [1, -20]


def test_minimum_dust_and_full_close_respect_existing_target_quantities():
    plan, ctx = fixture()
    ctx['minimum_quantity'] = .2
    plan['take_profits'] = [dict(id='dust', price=110, fraction=.15), dict(id='rest', price=120, fraction=.9)]
    p = paths(_gross_projection(plan, ctx))
    assert p['TP_THEN_HARD_SL']['gross_pnl'] == 16
    assert p['FIRST_TP_THEN_HARD_SL']['gross_pnl'] == 16
    plan['take_profits'] = [dict(id='all', price=120, fraction=1)]
    p = paths(_gross_projection(plan, ctx))
    assert p['FIRST_TP_THEN_HARD_SL']['gross_pnl'] == 20
    assert p['FIRST_TP_THEN_HARD_SL']['remaining_before_hard_stop'] == 0


@pytest.mark.parametrize('evidence', ['missing', 'valid', 'stale'])
def test_fee_all_entry_quantity_charged_once_or_entire_path_unknown(evidence):
    plan, ctx = fixture()
    if evidence != 'missing':
        ctx['observed_execution_fee_evidence'] = dict(status='AVAILABLE', basis='HISTORICAL_VERIFIED_EXECUTIONS',
            source_sha256='a'*64, rates=dict(TAKER=dict(count=1, minimum=.001, maximum=.001,
                earliest_at=999, latest_at=999)))
        if evidence == 'stale':
            ctx['now'] = 100000
    result = _gross_projection(plan, ctx)['joint_exit_paths']
    for path in paths(_gross_projection(plan, ctx)).values():
        fee = path['fee_only']
        if evidence != 'valid':
            assert fee['status'] == 'UNKNOWN'
            assert 'estimated_net_before_unmodelled_costs' not in fee
        else:
            assert fee['status'] == 'AVAILABLE'
            assert fee['assumed_entry_fee'] == pytest.approx(.1)
    if evidence == 'valid':
        path = paths(_gross_projection(plan, ctx))['FIRST_TP_THEN_PARTIAL_SL']
        assert path['fee_only']['assumed_exit_fee'] == pytest.approx(.108)
        assert path['fee_only']['estimated_net_before_unmodelled_costs'] == pytest.approx(7.792)
    from live.scenario_calculated_review import SELECT_PROMPT
    assert result['version'] == 2
    assert 'Funding/unknown execution remain unknown' in SELECT_PROMPT


def test_holding_and_adjust_projections_unchanged_scope():
    plan, ctx = fixture()
    ctx['scenario_id'] = 'existing'
    assert 'joint_exit_paths' not in _gross_projection(plan, ctx)
    ctx['scenario_id'] = None
    plan['action'] = 'ADJUST'
    assert 'joint_exit_paths' not in _gross_projection(plan, ctx)
    plan['action'] = 'OPEN'
    assert 'joint_exit_paths' not in _gross_projection(plan, ctx, lots=plan['entries'])


def test_joint_projection_bound_to_receipt_without_plan_risk_changes():
    ctx = context()
    raw = wire(ctx, 'LONG')
    raw['partial_stops'] = [dict(id='ps', price=83200, fraction=.5)]
    frozen = deepcopy(raw)
    receipt = calculate_candidate(raw, ctx, candidate_id='c0')
    assert receipt['eligible']
    assert receipt['raw_proposal'] == frozen and raw == frozen
    assert receipt['raw_sha256'] == receipt_hash(frozen)
    no_partial = calculate_candidate(dict(raw, partial_stops=[]), ctx, candidate_id='c0')
    assert receipt['raw_risk'] == no_partial['raw_risk']
    assert receipt['effective_per_entry_risk'] == no_partial['effective_per_entry_risk']
    assert receipt['projection']['gross_at_full_stop'] == no_partial['projection']['gross_at_full_stop']
    original = receipt['receipt_sha256']
    receipt['projection']['joint_exit_paths']['paths'][0][2] += 1
    assert receipt_hash({k: v for k, v in receipt.items() if k != 'receipt_sha256'}) != original


def test_one_unknown_fee_leg_makes_whole_path_unknown(monkeypatch):
    from live import scenario_fee_projection as module
    from tests.test_scenario_fee_projection import evidence
    plan, ctx = fixture()
    ctx['observed_execution_fee_evidence'] = evidence()
    original = module.fee_only_projection
    def uncertain(**kwargs):
        return dict(status='UNKNOWN') if kwargs['exit_price'] == 90 else original(**kwargs)
    monkeypatch.setattr(module, 'fee_only_projection', uncertain)
    p = paths(_gross_projection(plan, ctx))
    assert p['TP_THEN_HARD_SL']['fee_only']['status'] == 'AVAILABLE'
    assert p['FIRST_TP_THEN_PARTIAL_SL']['fee_only'] == dict(status='UNKNOWN')


def test_equal_price_stable_order_and_subminimum_crossed_residual():
    plan, ctx = fixture()
    ctx['minimum_quantity'] = .2
    plan['take_profits'] = [dict(id='$hard_stop', price=120, fraction=.9)]
    plan['partial_stops'] = [dict(id='first', price=90, fraction=.3), dict(id='second', price=90, fraction=.3)]
    p = paths(_gross_projection(plan, ctx))
    assert p['FIRST_TP_THEN_HARD_SL']['gross_pnl'] == 16
    assert p['FIRST_TP_THEN_HARD_SL']['remaining_before_hard_stop'] == .1
    assert p['PARTIAL_SL_THEN_HARD_SL']['gross_pnl'] == -14
    assert p['PARTIAL_SL_THEN_HARD_SL']['remaining_before_hard_stop'] == .4


def test_two_phase_shared_receipt_roundtrip_and_no_execution():
    import json
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_calculated_review import run
    result, _, calls = run()
    assert result['orders_submitted'] == 0 and result['executable'] is False
    restored = restore_model_input(json.loads(calls[1]['user_prompt']))
    candidate = restored['calculated_candidates']['candidates'][0]
    assert 'joint_exit_paths' in candidate['projection']
    assert candidate['host_receipt_sha256'] == result['selected']['receipt_sha256']
    assert result['selected']['receipt_sha256'] == receipt_hash({k:v for k,v in result['selected'].items() if k!='receipt_sha256'})
    assert 'Partial SL is optional' in calls[0]['system_prompt']
    assert 'joint_exit_paths' in calls[1]['system_prompt']
    assert len(calls[1]['user_prompt'].encode()) <= 100000


def test_two_candidate_multilevel_partial_sl_compact_input_stress():
    import json
    from live.scenario_model_input import restore_model_input
    from tests.test_scenario_calculated_review import run
    ctx = context()
    ctx['synthetic_padding_not_market_evidence'] = 'x' * 85000
    candidates = []
    for side in ('LONG', 'SHORT'):
        plan = wire(ctx, side)
        plan['take_profits'] = [dict(id=f'tp{i}', price=price, fraction=.3)
            for i, price in enumerate((84000, 84500) if side == 'LONG' else (82500, 82000))]
        plan['partial_stops'] = [dict(id=f'ps{i}', price=price, fraction=.3)
            for i, price in enumerate((83200, 83100) if side == 'LONG' else (83600, 83700))]
        candidates.append(plan)
    result, _, calls = run(ctx=ctx, candidates=candidates)
    assert result['calls'] == 2
    payload = restore_model_input(json.loads(calls[1]['user_prompt']))
    for candidate in payload['calculated_candidates']['candidates']:
        joint = candidate['projection']['joint_exit_paths']
        assert len(joint['paths']) == 4
        assert len(json.dumps(joint, separators=(',', ':')).encode()) < 1000
        assert candidate['effective_plan']['entries'] in [p['entries'] for p in candidates]
        assert candidate['draft_intent']['side'] in ('LONG','SHORT')
    assert len(calls[1]['user_prompt'].encode()) <= 100000
