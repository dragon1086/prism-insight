"""Structured goal invariants; no model, broker or profitability assertions."""
from copy import deepcopy
import json
import hashlib
from functools import partial
from types import SimpleNamespace

import pytest

from live.scenario_review_storage import review_text
from live.scenario_entry_goal import (
    GoalConsistencyRejection, goal_schema, prepare_goal_source, resolve_goal, validate_goal_plan,
)
from tests.test_scenario_ma_context import NOW, point, snapshot


def context():
    ctx = dict(now=NOW / 1000, input_id='frozen-input', entry_contract_version=2,
               entry_goal_decision_slot=6666666)
    snap = snapshot('4h', before=point('4h', 2, slow=100),
                    after=point('4h', 1, slow=101, confirmed=False))
    snap['valid'] = True
    prepare_goal_source(snap, ctx)
    return ctx


def goal(mode='BOUNDED_BEFORE', phase='confirmed'):
    return dict(mode=mode, reference=None if mode == 'NO_MA_BOUND'
                else dict(frame='4h', phase=phase, ma='ma35'))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('offset', [-.00000001, 0, .00000001])
def test_original_unrounded_bound_is_strict_for_every_target(side, offset):
    resolved = resolve_goal(goal(), context())
    price = 100 + offset
    plan = dict(action='OPEN', side=side, entries=[dict(price=90 if side == 'LONG' else 110)],
                take_profits=[dict(price=95 if side == 'LONG' else 105, fraction=.5),
                              dict(price=price, fraction=.5)])
    if (side == 'LONG' and offset < 0) or (side == 'SHORT' and offset > 0):
        validate_goal_plan(plan, resolved)
    else:
        with pytest.raises(GoalConsistencyRejection):
            validate_goal_plan(plan, resolved)


def test_reference_and_snapshot_identity_are_bound_without_model_price():
    ctx = context()
    original = deepcopy(ctx)
    a, b = resolve_goal(goal(), ctx), resolve_goal(goal(phase='forming'), ctx)
    assert a['price'] == 100 and b['price'] == 101
    assert a['input_id'] == 'frozen-input' and a['snapshot_sha256'] == b['snapshot_sha256']
    assert a['observed_at_ms'] != b['observed_at_ms']
    assert ctx == original
    assert all('price' not in branch['properties'] for branch in goal_schema()['anyOf'])


@pytest.mark.parametrize('mode', ['REFERENCE_EXTENSION', 'NO_MA_BOUND'])
def test_nonbounded_goal_does_not_cap_supported_strategy(mode):
    resolved = resolve_goal(goal(mode), context())
    validate_goal_plan(dict(action='OPEN', side='LONG', entries=[dict(price=90)],
                            take_profits=[dict(price=110, fraction=.5)]), resolved)


def test_missing_reference_is_not_invented():
    ctx = context()
    chosen = goal()
    chosen['reference']['frame'] = '1w'
    with pytest.raises(GoalConsistencyRejection):
        resolve_goal(chosen, ctx)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_real_runtime_registers_original_source_and_renews_same_order(tmp_path, side):
    from live.scenario_pending_renewal import load
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, e, calls, contexts = renewal_runtime(tmp_path, side=side, goal_enabled=True)
    outcome = r.tick()
    assert outcome['status'] == 'intent_pending', outcome
    active = r.state()['active']
    record = deepcopy(active['entry_contract'])
    assert record['version'] == 2 and 'expected_progress' not in record['definition']
    assert r._entry_contract_registered(active)
    original = deepcopy(b.children()[0]['request'])
    writes = deepcopy(e.writes)
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    assert r.state()['active']['entry_contract'] == record

    assert load(r.conn, r.state()['active'])['revision'] == 1
    assert b.children()[0]['request'] == original and e.writes == writes
    from live.scenario_runtime import ScenarioRuntime
    r = ScenarioRuntime(r.conn, b, r.propose, r.snapshot, clock=r.clock,
        calculated_review_enabled=True, calculated_review_propose=r.calculated_review_propose,
        research_review_mode=r.research_review_mode, entry_contract_enabled=True,
        pending_renewal_enabled=True, entry_goal_enabled=False)
    e.now += 301
    assert r.tick()['status'] == 'intent_pending'
    assert contexts[-1]['entry_contract_version'] == 2
    assert r.state()['active']['entry_contract'] == record
    lease = deepcopy(load(r.conn, r.state()['active']))
    r = ScenarioRuntime(r.conn, b, r.propose, r.snapshot, clock=r.clock,
        calculated_review_enabled=True, calculated_review_propose=r.calculated_review_propose,
        research_review_mode=r.research_review_mode, entry_contract_enabled=False,
        pending_renewal_enabled=False, entry_goal_enabled=False)
    e.now += 301
    assert r.tick()['status'] == 'wait'
    assert contexts[-1]['entry_contract_version'] == 2
    assert load(r.conn, r.state()['active']) == lease
    assert r._entry_contract_registered(r.state()['active'])


@pytest.mark.parametrize('mutation', ['bound', 'snapshot', 'definition', 'input'])
def test_rehashed_record_cannot_replace_original_frozen_source(tmp_path, mutation):
    from live.scenario_entry_contract import _digest
    from live.scenario_entry_goal import canonical_goal
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, _, _, _, _ = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    active = r.state()['active']
    record = active['entry_contract']
    if mutation == 'bound':
        record['resolved_goal']['price'] = 120
    elif mutation == 'snapshot':
        record['resolved_goal']['snapshot_sha256'] = 'a' * 64
    elif mutation == 'definition':
        record['definition']['goal']['reference']['ma'] = 'ma10'
        record['resolved_goal']['goal']['reference']['ma'] = 'ma10'
    else:
        record['origin']['input_id'] = record['resolved_goal']['input_id'] = 'forged'
    record['resolved_goal']['canonical'] = canonical_goal(record['definition']['goal'], record['resolved_goal']['price'])
    record['contract_id'] = _digest({k: v for k, v in record.items() if k != 'contract_id'})
    r.conn.execute('UPDATE llm_scenario_entry_contracts SET body=?', (json.dumps(record, sort_keys=True, separators=(',', ':')),))
    assert not r._entry_contract_registered(active)


def test_goal_rejected_candidate_keeps_taped_select_wait_and_original_prose():
    from analysis.scenario_review_tape import record_review
    from live.scenario_calculated_review import research_review
    from live.scenario_model_input import restore_model_input
    from live.scenario_preview import response_contract
    from live.scenario_review_guard import guard_review
    from tests.test_scenario_allocation_review import allocation_wire
    from tests.test_scenario_review_guard import context as base_context, wire
    from tests.test_scenario_entry_contract_wire import definition
    ctx = base_context()
    ctx.update(entry_contract_version=2, now=NOW / 1000, input_captured_at=NOW / 1000,
               account_captured_at=NOW / 1000, entry_goal_decision_slot=6666666)
    snap = snapshot('4h', before=point('4h', 2, slow=83800))
    snap['valid'] = True
    prepare_goal_source(snap, ctx)
    raw, wait = allocation_wire(ctx), wire(ctx, 'WAIT')
    premise = definition()
    premise.pop('expected_progress')
    premise.update(version=2, goal=goal())
    raw.update(entry_contract=premise, thesis_assessment=None,
               rationale='Keep immutable original explanatory text')
    wait.update(entry_contract=None, thesis_assessment=None)
    raw['expires_at'] = wait['expires_at'] = ctx['now'] + 100
    raw['take_profits'] = [dict(id='bad', price=84000, fraction=1)]
    calls = []

    def generate(**request):
        calls.append(request)
        payload = restore_model_input(json.loads(request['user_prompt']))
        assert 'entry_goal_source' not in payload['contract_context']
        if len(calls) == 1:
            result = dict(wait_proposal=wait, candidates=[raw], no_candidate_reason='')
        else:
            bundle = payload['calculated_candidates']
            rejected = bundle['candidates'][0]
            assert rejected['rejection_type'] == 'PREAUTHORITY_PLAN_CONSISTENCY'
            assert rejected['raw_allocation_proposal'] == raw
            chosen = bundle['wait']
            result = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale='원래 목표와 불일치')
        return SimpleNamespace(text=json.dumps(result))

    mode = 'economic-reject-calculated-v1'
    contract = response_contract(ctx)
    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx,
        contract, generate=generate, record=lambda _: None, clock=lambda: ctx['now'])
    assert envelope['outcome']['status'] == 'COMPLETE', envelope['outcome']
    assert envelope['outcome']['result']['selected']['id'] == 'WAIT' and len(calls) == 2
    checked = guard_review(snap, ctx, contract, envelope,
        dict(ctx, now=ctx['now'] + 1, account_captured_at=ctx['now'] + 1), research_mode=mode)
    assert checked['receipt']['orders_submitted'] == 0


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('phase', ['raw', 'effective'])
def test_price_adjustments_cannot_repair_or_create_bound_violation(side, phase):
    from live.scenario_entry_goal import validate_goal_pair
    ctx = context()
    valid, invalid = (99.9, 100.0) if side == 'LONG' else (100.1, 100.0)
    raw = dict(action='OPEN', side=side,
        entries=[dict(price=90 if side == 'LONG' else 110, quantity=.1)],
        take_profits=[dict(price=invalid if phase == 'raw' else valid, fraction=1)],
        entry_contract=dict(goal=goal()))
    effective = deepcopy(raw)
    effective['take_profits'][0]['price'] = valid if phase == 'raw' else invalid
    with pytest.raises(GoalConsistencyRejection) as caught:
        validate_goal_pair(raw, effective, ctx)
    assert caught.value.evidence['phase'] == phase
    assert caught.value.evidence['raw_price'] == raw['take_profits'][0]['price']
    assert caught.value.evidence['effective_price'] == effective['take_profits'][0]['price']
    assert caught.value.evidence['comparison_scope'] == 'FIRST_VIOLATION_ONLY'


@pytest.mark.parametrize('fraction,quantity,target,accepted', [
    (1, 1, 99, False), (1, 1, 100, False), (1, 1, 101, True),
    (.5, 1, 99, True), (.999, .1, 99, False), (0, 1, 99, True),
])
def test_extension_requires_intended_tradeable_runner_or_beyond_target(fraction, quantity, target, accepted):
    from live.scenario_entry_goal import validate_goal_pair
    ctx = context()
    ctx.update(quantity_step=.001, minimum_quantity=.001, minimum_notional=5)
    plan = dict(action='OPEN', side='LONG', entries=[dict(price=90, quantity=quantity)],
                take_profits=[dict(price=target, fraction=fraction)] if fraction else [],
                entry_contract=dict(goal=goal('REFERENCE_EXTENSION')))
    if accepted:
        validate_goal_pair(plan, deepcopy(plan), ctx)
    else:
        with pytest.raises(GoalConsistencyRejection, match='extension_without_target'):
            validate_goal_pair(plan, deepcopy(plan), ctx)


def test_zero_quantity_beyond_tp_and_rounding_dust_are_not_extension_witness():
    from live.scenario_entry_goal import validate_goal_pair
    ctx = context()
    ctx.update(quantity_step=.001, minimum_quantity=.001)
    plan = dict(action='OPEN', side='LONG', entries=[dict(price=90, quantity=.01)],
        take_profits=[dict(price=99, fraction=.999999), dict(price=101, fraction=.000001)],
        entry_contract=dict(goal=goal('REFERENCE_EXTENSION')))
    with pytest.raises(GoalConsistencyRejection, match='extension_without_target'):
        validate_goal_pair(plan, deepcopy(plan), ctx)


@pytest.mark.parametrize('action', ['ADJUST', 'EXIT', 'WAIT'])
def test_bound_arrival_is_not_failure_and_protective_only_paths_survive(action):
    from live.scenario_entry_goal import validate_goal_pair
    resolved = resolve_goal(goal(), context())
    ctx = dict(registered_entry_contract=dict(version=2, resolved_goal=resolved), mark_price=110)
    plan = dict(action=action, side='LONG', entries=[], take_profits=[],
                hard_stop=105, partial_stops=[dict(price=107, fraction=.5)], cancel_entry_ids=['old'])
    validate_goal_pair(plan, deepcopy(plan), ctx)


@pytest.mark.parametrize('status', ['unavailable', 'error', 'incomplete', 'stale'])
def test_flagged_frame_with_numbers_cannot_supply_reference(status):
    ctx = dict(now=NOW / 1000, input_id='i', entry_contract_version=2, entry_goal_decision_slot=6666666)
    snap = snapshot('4h')
    snap['valid'] = True
    snap['timeframes']['4h']['status'] = status
    prepare_goal_source(snap, ctx)
    with pytest.raises(GoalConsistencyRejection, match='reference_unavailable'):
        resolve_goal(goal(), ctx)


def test_bounded_notice_does_not_invent_unbounded_trend_runner():
    from live.scenario_notice import render_notice
    event = dict(kind='PLAN', timestamp=1800000000, side='LONG', price=90, quantity=1,
                 hard_stop=80, take_profits=[], entry_goal=resolve_goal(goal(), context()))
    out = render_notice(event)
    assert '남은 전량 보호' in out and '원래 목표:' in out and '추세 추종' not in out
    event.pop('entry_goal')
    assert '추세 추종' in render_notice(event)


@pytest.mark.parametrize('fault', ['fraction', 'cancel', 'risk'])
def test_goal_rejection_never_softens_other_invalid_authority(fault):
    from core.llm_scenario import ScenarioValidationError
    from live.scenario_calculated_review import calculate_economic_candidate
    from tests.test_scenario_allocation_review import allocation_wire
    from tests.test_scenario_review_guard import context as base_context
    from tests.test_scenario_entry_contract_wire import definition
    ctx = base_context()
    ctx.update(entry_contract_version=2)
    # Independently frozen source for the calculator; no model source fields.
    source = context()['entry_goal_source']
    source['input_id'] = ctx['input_id']
    source['levels']['4h.confirmed.ma35']['price'] = 83800
    ctx['entry_goal_source'] = source
    raw = allocation_wire(ctx)
    premise = definition()
    premise.pop('expected_progress')
    premise.update(version=2, goal=goal())
    raw.update(entry_contract=premise, thesis_assessment=None)
    raw['take_profits'] = [dict(id='bad', price=84000, fraction=1)]
    if fault == 'fraction':
        raw['take_profits'][0]['fraction'] = 1.1
    elif fault == 'cancel':
        raw['cancel_entry_ids'] = ['unknown']
    else:
        ctx['new_risk_blocked'] = True
    with pytest.raises((ValueError, ScenarioValidationError)) as caught:
        calculate_economic_candidate(raw, ctx, candidate_id='c0')
    assert not isinstance(caught.value, GoalConsistencyRejection)


@pytest.mark.parametrize('status', ['unavailable', 'error', 'incomplete', 'stale'])
def test_flagged_path_cannot_supply_reference(status):
    ctx = dict(now=NOW / 1000, input_id='i', entry_contract_version=2, entry_goal_decision_slot=6666666)
    snap = snapshot('4h')
    snap['valid'] = True
    snap['timeframes']['4h']['ma_path']['status'] = status
    prepare_goal_source(snap, ctx)
    with pytest.raises(GoalConsistencyRejection):
        resolve_goal(goal(), ctx)


@pytest.mark.parametrize('action', ['EXIT', 'WAIT', 'ADJUST'])
def test_executor_missing_v2_source_does_not_block_existing_protective_actions(tmp_path, action):
    from tests.test_scenario_execution import live, persist
    b, exchange = live.__wrapped__(tmp_path)
    b.execute(persist(b), 'a1')
    link = b.children()[0]['link_id']
    if action != 'WAIT':
        exchange.fill(link, .1)
        assert b.reconcile()['protection_confirmed'] is True
    state = json.loads(b.conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
    state['active']['entry_contract'] = dict(version=2, resolved_goal={'corrupt': True})
    b.conn.execute('UPDATE llm_scenario_state SET body=?', (json.dumps(state),))
    payload = persist(b, action=action, entries=[], take_profits=[], partial_stops=[],
                      action_id='protect', hard_stop=99, cancel_entry_ids=['e1'] if action == 'WAIT' else [],
                      chase=dict(max_bps=0, max_reprices=0))
    # No valid source/registration exists. These paths must retain normal behavior,
    # not fail specifically because of goal metadata.
    b.execute(payload, payload['action_id'])
    assert b.conn.execute('SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?',
                          (payload['action_id'],)).fetchone()[0] == 'COMPLETE'
    if action == 'WAIT':
        assert exchange.orders[link]['orderStatus'] == 'Cancelled'
    elif action == 'EXIT':
        assert any(c['kind'] == 'exit' and c['request']['reduceOnly'] for c in b.children())
    else:
        assert float(exchange.stop) == 99


def test_v2_schema_factoring_preserves_order_numeric_types_and_legacy_wire():
    from live.scenario_model_input import _json, project_model_input, restore_model_input
    shared = dict(type='object', properties={f'field{i}': dict(type='number', minimum=1.0,
                   maximum=i + 1, description='정확한 공통 필드') for i in range(12)})
    original = dict(contract_context=dict(entry_contract_version=2),
                    response_contract=dict(anyOf=[deepcopy(shared), deepcopy(shared)]))
    projected, audit = project_model_input(original)
    assert projected['model_input_projection']['version'] == 6
    assert _json(restore_model_input(projected)) == _json(original)
    assert _json(restore_model_input(json.loads(json.dumps(projected, sort_keys=True)))) == _json(original)
    assert audit['projected_utf8_bytes'] < audit['original_utf8_bytes']
    legacy = deepcopy(original)
    legacy['contract_context']['entry_contract_version'] = 1
    assert project_model_input(legacy)[0] == legacy
    forged = deepcopy(projected)
    key = next(iter(forged['model_input_projection']['schema_value_definitions']))
    forged['model_input_projection']['schema_value_definitions'][key]['$schema_object'][1][0] = 'array'
    with pytest.raises(ValueError, match='schema_value_hash'):
        restore_model_input(forged)
    forged = deepcopy(projected)
    forged['response_contract']['$schema_object'][1][0][0]['$schema_value'] = 'missing'
    with pytest.raises(ValueError, match='schema_value_reference'):
        restore_model_input(forged)


@pytest.mark.parametrize('contract_enabled', [False, True])
def test_broker_cannot_enable_v2_or_inject_goal_authority(tmp_path, contract_enabled):
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, e, calls, contexts = renewal_runtime(tmp_path, goal_enabled=True)
    r.entry_contract_enabled = contract_enabled
    r.entry_goal_enabled = r.pending_renewal_enabled = False
    original = b.context
    def forged():
        return dict(original(), entry_contract_version=2, entry_goal_source={'forged': True},
                    entry_goal_untrusted=True, registered_entry_contract={'version': 2})
    b.context = forged
    r.tick()
    assert contexts[-1].get('entry_contract_version') == (1 if contract_enabled else None)
    assert 'entry_goal_source' not in contexts[-1] and 'entry_goal_untrusted' not in contexts[-1]
    active = r.state()['active']
    assert active is None or (active.get('entry_contract') or {}).get('version') != 2
    if not contract_enabled:
        assert not e.writes


@pytest.mark.parametrize('case', ['raw_tp_repaired_inside', 'effective_entry_outside'])
def test_actual_price_buffer_pipeline_cannot_cross_or_repair_original_goal(case):
    from live.scenario_calculated_review import calculate_candidate
    from tests.test_scenario_calculated_review import context as base_context, wire
    from tests.test_scenario_entry_contract_wire import definition
    ctx = base_context()
    ctx.update(entry_contract_version=2, execution_price_policy=dict(version='round-limit-v1'),
               limit_price_quotes=dict(bid=83400, ask=83401, captured_at=1000))
    source = context()['entry_goal_source']
    source['input_id'] = ctx['input_id']
    source['levels']['4h.confirmed.ma35']['price'] = 83995 if case == 'raw_tp_repaired_inside' else 83415
    ctx['entry_goal_source'] = source
    raw = wire(ctx, 'LONG')
    premise = definition()
    premise.pop('expected_progress')
    premise.update(version=2, goal=goal())
    raw.update(entry_contract=premise, thesis_assessment=None)
    if case == 'effective_entry_outside':
        raw['take_profits'] = []
    receipt = calculate_candidate(raw, ctx, candidate_id='c0')
    assert receipt['eligible'] is False
    assert receipt['rejection_type'] == 'PREAUTHORITY_PLAN_CONSISTENCY'
    assert receipt['goal_evidence']['phase'] == ('raw' if case == 'raw_tp_repaired_inside' else 'effective')
    assert receipt['raw_proposal'] == raw


@pytest.mark.parametrize('bad', [None, [], 'broken', {'goal': {'mode': []}}])
def test_optional_bad_goal_does_not_drop_notice_or_invent_runner(bad):
    from live.scenario_notice import render_notice
    event = dict(kind='PLAN', timestamp=1800000000, side='LONG', price=90, quantity=1,
                 hard_stop=80, take_profits=[], entry_goal=bad)
    text = render_notice(event)
    assert '자료 미확인' in text and '추세 추종' not in text


def test_goal_version_rejects_single_review_before_model():
    from live.scenario_calculated_review import research_review
    with pytest.raises(ValueError, match='two_phase'):
        research_review({}, dict(entry_contract_version=2), {}, generate=lambda **_: pytest.fail('model'),
                        record=lambda _: None, research_mode='economic-reject-single-v1')


def test_weekly_confirmed_reference_uses_bar_phase_not_120_second_candle_age():
    ctx = dict(now=NOW / 1000, input_id='weekly', entry_contract_version=2,
               entry_goal_decision_slot=6666666)
    snap = snapshot('1w')
    snap['valid'] = True
    prepare_goal_source(snap, ctx)
    chosen = goal()
    chosen['reference']['frame'] = '1w'
    resolved = resolve_goal(chosen, ctx)
    assert NOW - resolved['observed_at_ms'] > 120000
    assert resolved['price'] == 95


def test_later_adjust_uses_registered_bound_not_new_ma_or_new_thesis(tmp_path):
    from live.scenario_calculated_review import calculate_candidate
    from live.scenario_entry_contract import contract_context
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    from tests.test_scenario_review_runtime import wire
    r, _, e, _, contexts = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    active = r.state()['active']
    original = deepcopy(active['entry_contract'])
    ctx = deepcopy(contexts[0])
    ctx.update(contract_context(active, True))
    ctx.update(input_id='later', revision=2, scenario_id=active['scenario_id'], side='LONG',
               positions=[dict(price=102, quantity=.01)], pending_entries=[], previous_hard_stop=98,
               mark_price=103, now=e.now + 60, input_captured_at=e.now + 60, account_captured_at=e.now + 60)
    ctx['ma_structure'] = {'later_ma35': 110}  # Not a replacement for original 106.
    raw = wire(ctx, 'ADJUST')
    raw.update(side='LONG', hard_stop=98, chase=dict(max_bps=0, max_reprices=0),
        entry_contract=None, thesis_assessment=dict(contract_id=original['contract_id'], status='INTACT', reason='Still original'))
    for price, expected in [(105, True), (107, False)]:
        raw['take_profits'] = [dict(id='tp', price=price, fraction=.5)]
        receipt = calculate_candidate(raw, ctx, candidate_id='c0')
        assert receipt['eligible'] is expected, receipt
        if not expected:
            assert receipt['rejection_type'] == 'PREAUTHORITY_PLAN_CONSISTENCY'
    raw['entry_contract'] = deepcopy(original['definition'])
    with pytest.raises(ValueError):
        calculate_candidate(raw, ctx, candidate_id='c0')
    assert r.state()['active']['entry_contract'] == original


def test_v1_schema_and_registered_record_match_deployed_9d4a_golden():
    from live.scenario_entry_contract import bind_contract, entry_contract_schema
    from tests.test_scenario_entry_contract_wire import definition
    from tests.test_scenario_calculated_review import context as base_context, wire
    # Computed by executing the unmodified 9d4a1466 entry-contract module.
    record = bind_contract(definition(), wire(base_context(), 'LONG'), 1000)
    assert record['contract_id'] == '6f666405864db7e14f4442d16c3d9759e71138467d691d0d57fac188fae699cb'
    encoded = json.dumps(entry_contract_schema(), sort_keys=True, separators=(',', ':'))
    assert hashlib.sha256(encoded.encode()).hexdigest() == 'eb797b3fabdae729684b8a17331ddab48683ea30693f5fbfcda854f608f79dea'


def test_v2_prompt_is_reviewed_and_v1_prompt_is_unchanged():
    from live.scenario_llm import ENTRY_CONTRACT_PROMPT, entry_contract_prompt
    assert entry_contract_prompt({'entry_contract_version': 1}) == ENTRY_CONTRACT_PROMPT
    assert hashlib.sha256(entry_contract_prompt({'entry_contract_version': 2}).encode()).hexdigest() == (
        '33d1c257facbc6711586c5cc78800de6df5e6f6c45fc8f322c8c3d92584d7381')


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('outside', [False, True])
@pytest.mark.parametrize('automatic', [False, True])
def test_ordinary_chase_respects_original_goal_before_cancel_and_submit(tmp_path, side, outside, automatic):
    from engine.scenario_snapshot import TIMEFRAME_MS, candle_start
    from live.scenario_entry_contract import bind_contract
    from tests.test_scenario_execution import live, persist
    b, e = live.__wrapped__(tmp_path)
    bound = 100.15 if side == 'LONG' else 99.85
    state = json.loads(b.conn.execute('SELECT body FROM llm_scenario_state').fetchone()[0])
    state['active'].update(side=side, hard_stop=98 if side == 'LONG' else 102)
    p = persist(b, side=side, hard_stop=state['active']['hard_stop'], input_id='origin-input')
    ctx = dict(now=1800000000, input_id='origin-input', scenario_id=None,
               entry_contract_version=2, entry_goal_decision_slot=6000000)
    start = candle_start(1800000000000, TIMEFRAME_MS['4h'])
    snap = dict(valid=True, as_of_ms=1800000000000, timeframes={'4h': {'ma_path': dict(version=1,
        confirmed_points=[dict(open_time_ms=start-TIMEFRAME_MS['4h'], open=100, high=101,
                               low=99, close=100, ma10=100, ma35=bound)], forming_point=None)}})
    prepare_goal_source(snap, ctx)
    definition = dict(version=2, regime='TREND', premise='Original ordinary limit',
        goal=goal(), explicit_failure=dict(source='MARK_PRICE', operator='le' if side == 'LONG' else 'ge',
                                          price=99 if side == 'LONG' else 101, minimum_observations=2))
    record = bind_contract(definition, p, 1800000000, resolved_goal=resolve_goal(goal(), ctx))
    p['host_entry_contract_id'] = record['contract_id']
    b.conn.execute('UPDATE llm_scenario_intents SET payload=? WHERE id=?', (json.dumps(p), 'a1'))
    state['active']['entry_contract'] = record
    b.conn.execute('UPDATE llm_scenario_state SET body=?', (json.dumps(state),))
    b.conn.execute('CREATE TABLE llm_scenario_entry_contracts(scenario_id TEXT,body TEXT)')
    b.conn.execute('INSERT INTO llm_scenario_entry_contracts VALUES(?,?)', ('s1', json.dumps(record, sort_keys=True)))
    b.conn.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT)')
    b.conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)', (6000000, json.dumps(snap),
        json.dumps(ctx), json.dumps(dict({k: v for k, v in p.items() if k != 'host_entry_contract_id'},
                                        entry_contract=definition, thesis_assessment=None))))
    b.conn.commit()
    b.execute(p, 'a1')
    old = b.children()[0]['link_id']
    writes = deepcopy(e.writes)
    price = 100 + (1 if side == 'LONG' else -1) * (.2 if outside else .1)
    def perform():
        if automatic:
            e.now += 61
            b.clock = lambda: e.now
            old_ticker = e.get_tickers
            def quoted(**kwargs):
                result = old_ticker(**kwargs)
                result['result']['list'][0]['ask1Price' if side == 'LONG' else 'bid1Price'] = str(price)
                return result
            e.get_tickers = quoted
            return b.reconcile()
        return b.chase('a1', 'e1', price)
    if outside:
        if automatic:
            result = perform()
            assert result['protection_confirmed'] is True
            assert all(item['orders_reconciled'] for item in result['intents'])
        else:
            with pytest.raises(GoalConsistencyRejection):
                perform()
        assert e.writes == writes and e.orders[old]['orderStatus'] == 'New'
        if automatic:
            snap['valid'] = False
            b.conn.execute('UPDATE llm_scenario_decisions SET snapshot=?', (json.dumps(snap),))
            b.conn.commit()
            uncertain = b.reconcile()
            assert any(not item['orders_reconciled'] for item in uncertain['intents'])
            assert e.writes == writes
    else:
        perform()
        assert e.orders[old]['orderStatus'] == 'Cancelled'
        assert float(b.children()[-1]['request']['price']) == price


@pytest.mark.parametrize('damage', ['snapshot_missing', 'snapshot_changed', 'decision_missing'])
@pytest.mark.parametrize('before_samples', [False, True])
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_goal_source_loss_does_not_disable_registered_numeric_failure_exit(tmp_path, damage, before_samples, side):
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, e, calls, _ = renewal_runtime(tmp_path, goal_enabled=True, side=side)
    assert r.tick()['status'] == 'intent_pending'
    original = r.state()['active']['entry_contract']
    slot = original['resolved_goal']['decision_slot']
    original_intent = r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
                                    (original['origin']['action_id'],)).fetchone()[0]
    assert json.loads(original_intent)['host_entry_contract_id'] == original['contract_id']
    child = b.children()[0]
    e.now += 1
    e.fill(child['link_id'], float(child['request']['qty']))
    if side == 'SHORT':
        # The shared minimal Exchange fixture is long-only; model Bybit's positive
        # size + Sell side and Buy native SL explicitly for this short fill.
        e.size, e.stop = str(abs(float(e.size))), '102'
        e.orders['native'] = dict(orderId='native-id', orderLinkId='', symbol='BTCUSDT',
            positionIdx=0, qty=e.size, parentOrderLinkId=child['link_id'], createdTime=str(int(e.now * 1000)),
            side='Buy', reduceOnly=True, stopOrderType='StopLoss', orderType='Market',
            triggerBy='MarkPrice', triggerPrice=e.stop, orderStatus='Untriggered', cumExecQty='0', leavesQty=e.size)
        positions = e.get_positions
        def short_position(**kwargs):
            result = positions(**kwargs)
            result['result']['list'][0]['side'] = 'Sell'
            return result
        e.get_positions = short_position
    original_ticker = e.get_tickers
    def below_failure(**kwargs):
        result = original_ticker(**kwargs)
        mark = '98.5' if side == 'LONG' else '101.5'
        result['result']['list'][0].update(markPrice=mark, lastPrice=mark)
        return result
    e.get_tickers = below_failure
    def lose_source():
        if damage == 'snapshot_missing':
            r.conn.execute('UPDATE llm_scenario_decisions SET snapshot=NULL WHERE slot=?', (slot,))
        elif damage == 'decision_missing':
            r.conn.execute('DELETE FROM llm_scenario_decisions WHERE slot=?', (slot,))
        else:
            snap = json.loads(r.conn.execute('SELECT snapshot FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()[0])
            snap['timeframes']['4h']['ma_path']['confirmed_points'][0]['ma35'] += 1
            r.conn.execute('UPDATE llm_scenario_decisions SET snapshot=? WHERE slot=?', (json.dumps(snap), slot))
        r.conn.commit()
    if before_samples:
        lose_source()
    for at in (1800000301, 1800000601):
        e.now = at
        state = r.state()
        r._reconcile(state)
        ctx = r._context(state)
        r._save(state)
    assert state['active']['entry_contract_observation']['status'] == 'FAILED'
    if not before_samples:
        lose_source()
    ctx = r._context(state)
    assert ctx['entry_contract_status'] == 'REGISTERED' and ctx['entry_goal_untrusted'] is True
    assert r._entry_contract_registered(state['active']) and not r._entry_goal_registered(state['active'])
    outcome = r._entry_contract_exit(state, ctx, int(e.now // 300))
    assert outcome['status'] == 'intent_pending', outcome
    assert any(c['kind'] == 'exit' and c['request']['reduceOnly'] for c in b.children())
    assert len(calls) == 2  # Only the initial OPEN review, no new model to protect.
    assert r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
        (original['origin']['action_id'],)).fetchone()[0] == original_intent
    assert r.state()['active']['entry_contract'] == original


@pytest.mark.parametrize('version', [None, 1, 2])
def test_host_registration_id_cannot_be_supplied_by_model(version):
    from live.scenario_contract import validate_wire_proposal
    from tests.test_scenario_calculated_review import context as base_context, wire
    ctx = base_context()
    if version:
        ctx['entry_contract_version'] = version
    raw = wire(ctx)
    if version:
        raw.update(entry_contract=None, thesis_assessment=None)
    raw['host_entry_contract_id'] = 'forged'
    with pytest.raises(ValueError, match='unknown_fields'):
        validate_wire_proposal(raw, ctx)


@pytest.mark.parametrize('change', ['id_missing', 'id_wrong', 'price', 'quantity', 'risk', 'rationale'])
def test_v2_original_intent_values_are_pinned_independently_of_goal_source(tmp_path, change):
    from live.scenario_entry_contract import _digest
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, _, _, _, _ = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    active = r.state()['active']
    record = active['entry_contract']
    plan = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
                                    (record['origin']['action_id'],)).fetchone()[0])
    assert plan['host_entry_contract_id'] == record['contract_id']
    assert _digest({k: v for k, v in plan.items() if k != 'host_entry_contract_id'}) == record['origin']['plan_sha256']
    r.conn.execute('DELETE FROM llm_scenario_decisions')
    assert r._entry_contract_registered(active) and not r._entry_goal_registered(active)
    if change == 'id_missing':
        plan.pop('host_entry_contract_id')
    elif change == 'id_wrong':
        plan['host_entry_contract_id'] = 'f' * 64
    elif change == 'price':
        plan['entries'][0]['price'] += .1  # Still within original bound.
    elif change == 'quantity':
        plan['entries'][0]['quantity'] /= 2
    elif change == 'risk':
        plan['risk']['total_risk'] += .1
    else:
        plan['rationale'] += ' changed'
    r.conn.execute('UPDATE llm_scenario_intents SET payload=? WHERE id=?',
                   (json.dumps(plan), record['origin']['action_id']))
    assert not r._entry_contract_registered(active)


def test_host_link_preserves_fresh_economic_receipt_and_final_lease_hash(tmp_path):
    from live.scenario_entry_contract import _digest
    from live.scenario_calculated_review import receipt_hash
    from live.scenario_pending_renewal import load, digest
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, _, _, _, _ = renewal_runtime(tmp_path, goal_enabled=True, selection_reason='명시적 원래 계획 승인')
    assert r.tick()['status'] == 'intent_pending'
    active = r.state()['active']
    record = active['entry_contract']
    payload = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
        (record['origin']['action_id'],)).fetchone()[0])
    economic = {k: v for k, v in payload.items() if k != 'host_entry_contract_id'}
    events = (json.loads(review_text(row[0])) for row in r.conn.execute('SELECT body FROM llm_scenario_review_events'))
    event = next(item for item in events if item['kind'] == 'FINAL_FRESH_REVIEW_RECEIPT')
    assert receipt_hash(economic) == event['receipt']['validated_sha256']
    assert _digest(economic) == record['origin']['plan_sha256']
    assert receipt_hash(economic) != _digest(economic)  # Unicode vs ASCII JSON encoding is intentional.
    assert load(r.conn, active)['anchor']['original_payload_sha256'] == digest(payload)


@pytest.mark.parametrize('damage', ['version_only', 'valid_v1_replacement'])
def test_original_open_host_pin_prevents_goal_version_downgrade(tmp_path, damage):
    from live.scenario_entry_contract import bind_contract, has_goal_contract, valid_record
    from tests.test_scenario_execution import persist
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, e, _, _ = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    child = b.children()[0]
    e.now += 1
    e.fill(child['link_id'], float(child['request']['qty']))
    state = r.state()
    r._reconcile(state)
    record = deepcopy(state['active']['entry_contract'])
    original = json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
        (record['origin']['action_id'],)).fetchone()[0])
    if damage == 'version_only':
        record['version'] = 1
    else:
        definition = deepcopy(record['definition'])
        definition.pop('goal')
        definition.update(version=1, expected_progress='forged unbounded legacy progress')
        plan = {k: v for k, v in original.items() if k != 'host_entry_contract_id'}
        plan['entry_contract'] = definition
        record = bind_contract(definition, plan, record['origin']['registered_at'])
        assert valid_record(record, state['active']['scenario_id'])
        assert 'resolved_goal' not in record and 'plan_sha256' not in record['origin']
    state['active']['entry_contract'] = record
    r.conn.execute('UPDATE llm_scenario_entry_contracts SET body=? WHERE scenario_id=?',
                   (json.dumps(record, sort_keys=True, allow_nan=False), state['active']['scenario_id']))
    r._save(state)
    assert has_goal_contract(r.conn, state['active'])
    ctx = r._context(state)
    assert ctx['entry_contract_status'] == 'CORRUPT' and ctx['entry_goal_untrusted'] is True
    assert not r._entry_contract_registered(state['active'])
    payload = persist(b, scenario_id=state['active']['scenario_id'], action='ADJUST',
        action_id='downgrade-target', entries=[], take_profits=[dict(id='bad', price=130, fraction=1)],
        chase=dict(max_bps=0, max_reprices=0))
    writes = deepcopy(e.writes)
    with pytest.raises(ValueError, match='entry_goal_registered_record_mismatch'):
        b._execute(payload, payload['action_id'])
    assert e.writes == writes
    assert not any(c['kind'] == 'tp' and float(c['request']['price']) == 130 for c in b.children())
    assert json.loads(r.conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
        (original['action_id'],)).fetchone()[0]) == original


def test_corrupt_legacy_without_positive_goal_evidence_keeps_existing_scope(tmp_path):
    from live.scenario_entry_contract import has_goal_contract
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, _, _, _ = renewal_runtime(tmp_path)
    assert r.tick()['status'] == 'intent_pending'
    state = r.state()
    ident = state['active']['entry_contract']['origin']['action_id']
    r.conn.execute('UPDATE llm_scenario_intents SET payload=? WHERE id=?', ('broken JSON', ident))
    assert not has_goal_contract(r.conn, state['active'])
    # Existing malformed-intent handling remains strict; this is not a new v2
    # classification and is not repaired or silently accepted by goal helpers.
    with pytest.raises(json.JSONDecodeError):
        r._context(state)
    assert not r._entry_contract_registered(state['active'])
    assert b._registered_goal(dict(scenario_id=state['active']['scenario_id'])) is None


@pytest.mark.parametrize('when', ['before_offer', 'after_commit'])
def test_goal_source_loss_cannot_renew_but_keeps_original_lease(tmp_path, when):
    from live.scenario_pending_renewal import load, receipt
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    r, b, e, _, contexts = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    old = deepcopy(load(r.conn, r.state()['active']))
    slot = r.state()['active']['entry_contract']['resolved_goal']['decision_slot']
    def damage():
        r.conn.execute('UPDATE llm_scenario_decisions SET snapshot=NULL WHERE slot=?', (slot,))
        r.conn.commit()
    if when == 'before_offer':
        damage()
    else:
        execute = b.execute
        def after_commit(payload, ident):
            if payload.get('pending_renewal'):
                damage()
            return execute(payload, ident)
        b.execute = after_commit
    e.now += 301
    outcome = r.tick()
    assert load(r.conn, r.state()['active']) == old
    if when == 'before_offer':
        assert outcome['status'] == 'wait'
        assert contexts[-1]['entry_contract_status'] == 'REGISTERED'
        assert contexts[-1]['entry_goal_untrusted'] is True
        assert 'pending_renewal_offer' not in contexts[-1]
    else:
        ident = r.conn.execute("SELECT id FROM llm_scenario_intents WHERE json_extract(payload,'$.action')='WAIT'").fetchone()[0]
        assert receipt(r.conn, ident)['status'] == 'REJECTED'


@pytest.mark.parametrize('action', ['EXIT', 'SL_ONLY', 'CANCEL_WAIT', 'TP_ADJUST'])
def test_source_loss_after_selection_does_not_reject_goal_independent_protection(tmp_path, action):
    from analysis.scenario_review_tape import record_review
    from live.scenario_calculated_review import research_review
    from live.scenario_model_input import restore_model_input
    from live.scenario_preview import response_contract
    from live.scenario_review_guard import guard_review
    from tests.test_scenario_pending_renewal_runtime import renewal_runtime
    from tests.test_scenario_review_runtime import wire
    r, b, e, _, _ = renewal_runtime(tmp_path, goal_enabled=True)
    assert r.tick()['status'] == 'intent_pending'
    source_slot = r.state()['active']['entry_contract']['resolved_goal']['decision_slot']
    if action != 'CANCEL_WAIT':
        e.now += 1
        child = b.children()[0]
        e.fill(child['link_id'], float(child['request']['qty']))
    e.now = 1800000301
    state = r.state()
    r._reconcile(state)
    ctx = r._context(state)
    r._save(state)
    ctx.update(input_id='protect-selection', input_captured_at=e.now, now=e.now, max_input_age_seconds=120)
    snap = dict(valid=True, as_of_ms=e.now * 1000)
    calls = []
    def proposal(kind):
        raw = wire(ctx, kind)
        raw.update(entry_contract=None, thesis_assessment=dict(
            contract_id=ctx['registered_entry_contract']['contract_id'], status='INTACT', reason='original'))
        if ctx.get('pending_renewal_version') == 1:
            raw['pending_renewal'] = None
        if kind == 'ADJUST':
            raw.update(side='LONG', hard_stop=99, chase=dict(max_bps=0, max_reprices=0),
                take_profits=[dict(id='tp', price=105, fraction=.5)] if action == 'TP_ADJUST' else [])
        if action == 'CANCEL_WAIT':
            raw['cancel_entry_ids'] = [ctx['pending_entries'][0]['id']]
        return raw
    def generate(**request):
        calls.append(request)
        if 'wait_proposal' in request['response_schema']['properties']:
            answer = dict(wait_proposal=proposal('WAIT'), candidates=[] if action == 'CANCEL_WAIT' else [
                proposal('EXIT' if action == 'EXIT' else 'ADJUST')], no_candidate_reason='protect')
        else:
            bundle = restore_model_input(json.loads(request['user_prompt']))['calculated_candidates']
            chosen = bundle['candidates'][0]
            assert chosen['eligible'], chosen
            answer = dict(selection_token=f"{chosen['id']}:{chosen['receipt_sha256']}", rationale='protect')
        return SimpleNamespace(text=json.dumps(answer))
    mode = 'economic-reject-calculated-v1'
    contract = response_contract(ctx)
    envelope = record_review(partial(research_review, research_mode=mode), snap, ctx, contract,
        generate=generate, record=lambda _: None, clock=lambda: e.now)
    assert envelope['outcome']['status'] == 'COMPLETE', envelope['outcome']
    r.conn.execute('UPDATE llm_scenario_decisions SET snapshot=NULL WHERE slot=?', (source_slot,))
    r.conn.commit()
    e.now += 1
    fresh = r._context(r.state())
    fresh.update(input_id=ctx['input_id'], input_captured_at=ctx['input_captured_at'],
                 max_input_age_seconds=120, now=e.now)
    assert fresh['entry_contract_status'] == 'REGISTERED' and fresh['entry_goal_untrusted'] is True
    if action == 'TP_ADJUST':
        with pytest.raises(ValueError, match='entry_goal_untrusted'):
            guard_review(snap, ctx, contract, envelope, fresh, research_mode=mode)
    else:
        checked = guard_review(snap, ctx, contract, envelope, fresh, research_mode=mode)
        assert checked['validated']['action'] == ('WAIT' if action == 'CANCEL_WAIT' else 'ADJUST' if action == 'SL_ONLY' else 'EXIT')
        assert not checked['validated'].get('pending_renewal')
        if action == 'CANCEL_WAIT':
            assert 'pending_renewal_version' not in fresh and 'pending_renewal_offer' not in fresh
            assert checked['validated']['cancel_entry_ids'] == [ctx['pending_entries'][0]['id']]
