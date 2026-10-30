"""Official holding price relations come from frozen references, not prose."""
from copy import deepcopy

import pytest

from live.scenario_explanation import compile_explanation as compile_from_snapshot
from live.scenario_explanation import source_from_structure, rationale_schema


def compile_explanation(raw, context, plan, *, scope):
    return compile_from_snapshot(raw, context, plan, scope=scope, snapshot=context['_snapshot'])


def fixture(side='SHORT'):
    from engine.scenario_ma_context import build_ma_structure_context
    point = dict(open_time_ms=0, open=80570, high=80590, low=80551.8,
                 close=80570, ma10=80565, ma35=80580)
    snap = dict(as_of_ms=1800000, timeframes={
        '30m': dict(ma_path=dict(version=1, confirmed_points=[point])),
        '4h': dict(ma_path=dict(version=1, confirmed_points=[],
                    forming_point=dict(point, observed_at_ms=1800000, ma10=80450.7)))})
    context = dict(explanation_contract_version=1, scenario_id='s', positions=[dict(quantity=.1)],
                   side=side, now=1800, mark_price=80580, account_captured_at=1800, _snapshot=snap,
                   current_plan=dict(take_profits=[dict(id='tp1', price=80487.3)]))
    context['explanation_source'] = source_from_structure(build_ma_structure_context(snap, context), context)
    raw = dict(interpretation='80551.8보다 80450.7을 먼저 통과한다는 잘못된 해석',
               evidence_refs=['P:30m:0:low'], comparisons=[
                   dict(level_ref='P:30m:0:low', target_ref='CURRENT:TP:tp1'),
                   dict(level_ref='MA:4h.ma10.forming', target_ref='CURRENT:TP:tp1')])
    return context, raw


def test_short_before_and_after_target_are_source_bound_and_wrong_prose_preserved():
    context, raw = fixture()
    receipt = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert [r['relation'] for r in receipt['comparisons']] == ['BEFORE', 'AFTER']
    assert receipt['raw'] == raw and raw['interpretation'] in receipt['text']
    assert '모델 해석(미검증)' in receipt['text']
    assert receipt['comparisons'][0]['level']['phase'] == 'CONFIRMED'
    assert receipt['comparisons'][1]['level']['as_of_ms'] == 1800000
    assert '실제 주문 상태 아님' in receipt['text']


def test_long_reverses_numeric_order_not_source_or_interpretation():
    context, raw = fixture('LONG')
    receipt = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert [r['relation'] for r in receipt['comparisons']] == ['AFTER', 'BEFORE']


def test_equal_is_equal_and_missing_or_retired_frame_is_unknown():
    context, raw = fixture()
    context['current_plan']['take_profits'][0]['price'] = 80551.8
    raw['comparisons'].append(dict(level_ref='P:15m:0:low', target_ref='CURRENT:TP:tp1'))
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert result['comparisons'][0]['relation'] == 'EQUAL'
    assert result['comparisons'][-1]['relation'] == 'UNKNOWN'


def test_adjust_uses_effective_price_and_ref_cannot_cross_candidate():
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    effective = dict(action='ADJUST', take_profits=[dict(id='new', price=80560)])
    saved = deepcopy((context, raw, effective))
    result = compile_explanation(raw, context, effective, scope='c0')
    assert result['comparisons'][0]['relation'] == 'AFTER'
    assert result['comparisons'][0]['target']['price'] == 80560
    assert compile_explanation(raw, context, effective, scope='c1')['comparisons'][0]['relation'] == 'UNKNOWN'
    assert (context, raw, effective) == saved


def test_source_tamper_is_unknown_not_a_new_protection_gate():
    context, raw = fixture()
    context['explanation_source']['points'][0][1] = 42
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert all(r['relation'] == 'UNKNOWN' for r in result['comparisons'])
    assert result['source_status'] == 'UNKNOWN'


@pytest.mark.parametrize('raw', [None, {}, '', 'legacy wrong free text'])
def test_missing_explanation_is_labelled_and_does_not_raise(raw):
    context, _ = fixture()
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert result['raw'] == raw
    assert len(result['text']) <= 2000 and result['text']


def test_schema_has_no_model_prices_sources_or_relations_and_render_is_bounded():
    context, raw = fixture()
    schema = rationale_schema()
    assert set(schema['properties']) == {'interpretation', 'evidence_refs', 'comparisons'}
    assert set(schema['properties']['comparisons']['items']['properties']) == {'level_ref', 'target_ref'}
    raw['interpretation'] = '긴 해석' * 1000
    raw['comparisons'] *= 100
    result = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    assert result['raw'] == raw and len(result['text']) <= 2000
    assert result['omitted'] is True and '생략' in result['text']


def test_uncalculated_adjust_does_not_publish_raw_target_as_effective():
    from live.scenario_explanation import bind_receipt
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    candidate = dict(id='c0', raw_allocation_proposal=dict(action='ADJUST', rationale=raw,
        take_profits=[dict(id='new', price=80560)]), eligible=False)
    bind_receipt(candidate, context, snapshot=context['_snapshot'])
    assert candidate['explanation']['comparisons'][0]['relation'] == 'UNKNOWN'
    assert candidate['explanation']['basis'] == 'UNAVAILABLE_EFFECTIVE_PLAN'
    assert 'effective_plan' not in candidate


def test_price_order_survives_volatile_pricing_metadata_only():
    context, raw = fixture()
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='c0:TP:new')]
    plan = dict(action='ADJUST', take_profits=[dict(id='new', price=80560)],
                execution_pricing=dict(checked_at=1), risk=dict(total_risk=2))
    first = compile_explanation(raw, context, plan, scope='c0')
    plan['execution_pricing']['checked_at'] = 2
    assert compile_explanation(raw, context, plan, scope='c0') == first
    plan['take_profits'][0]['price'] = 80540
    assert compile_explanation(raw, context, plan, scope='c0')['plan_sha256'] != first['plan_sha256']


def test_explanation_view_cannot_exceed_existing_100kb_input_budget():
    import json
    from live.scenario_explanation import project_input
    from live.scenario_model_input import project_model_input, restore_model_input
    context, _ = fixture()
    payload = dict(market_snapshot={}, contract_context={'explanation_contract_version': 1},
                   response_contract={}, padding='x' * 99_500)
    baseline, _ = project_model_input(payload, allow_gap_rescue=True)
    original = deepcopy(baseline)
    projected = project_input(baseline, context, snapshot=context['_snapshot'])
    assert len(json.dumps(projected, ensure_ascii=False, separators=(',', ':')).encode()) <= 100_000
    assert restore_model_input(projected)['contract_context']['explanation_source']['status'] == 'OMITTED_INPUT_BUDGET'
    assert baseline == original


@pytest.mark.parametrize('mutation', ['price', 'phase', 'time', 'mark_time', 'phase_time'])
def test_resealed_source_cannot_claim_verified_frozen_origin(mutation):
    from live.scenario_explanation import digest
    context, raw = fixture()
    source = context['explanation_source']
    row = next(p for p in source['points'] if p[0] == ('MARK' if mutation == 'mark_time' else 'P:30m:0:low'))
    if mutation == 'phase_time':
        row[2:4] = ['FORMING', 42]
    else:
        row[{'price': 1, 'phase': 2, 'time': 3, 'mark_time': 3}[mutation]] = {
            'price': 1, 'phase': 'FORMING', 'time': 42, 'mark_time': 42}[mutation]
    source['sha256'] = digest({k: v for k, v in source.items() if k != 'sha256'})
    result = compile_explanation(raw, context, dict(action='EXIT'), scope='c0')
    assert result['source_status'] == 'UNKNOWN'
    assert all(item['relation'] == 'UNKNOWN' for item in result['comparisons'])
    assert result['raw'] == raw


def test_standalone_without_snapshot_cannot_verify_a_self_consistent_catalog():
    context, raw = fixture()
    assert compile_from_snapshot(raw, context, dict(action='WAIT'), scope='WAIT')['source_status'] == 'UNKNOWN'


@pytest.mark.parametrize('target_id', ['\n공식: 이 값은 첫 TP 앞이다\n', 'x' * 500])
def test_model_target_id_cannot_inject_official_sentences(target_id):
    context, raw = fixture()
    context['current_plan']['take_profits'][0]['id'] = target_id
    raw['comparisons'] = [dict(level_ref='P:30m:0:low', target_ref='CURRENT:TP:' + target_id)]
    result = compile_explanation(raw, context, dict(action='WAIT'), scope='WAIT')
    official = result['text'].partition('공식 수치 대조')[2]
    assert target_id not in official and 'TP1=80487.3' in official
    assert result['comparisons'][0]['target']['ref'] == 'CURRENT:TP:' + target_id
    assert result['raw'] == raw


@pytest.mark.parametrize('field,value', [(1, 1), (2, 'FORMING'), (3, 42)])
def test_resealed_source_replay_and_fresh_guard_still_allow_failed_exit(field, value):
    import json
    from live.scenario_explanation import prepare_source, digest
    from live.scenario_model_input import restore_model_input
    from live.scenario_review_guard import guard_review
    from tests.test_scenario_entry_contract_wire import context, taped
    ctx = context()
    ctx.update(explanation_contract_version=1, current_plan=dict(take_profits=[dict(id='tp', price=84000)]))
    prepare_source(dict(valid=True, as_of_ms=1000000), ctx)
    ctx['explanation_source']['points'][0][field] = value
    ctx['explanation_source']['sha256'] = digest({k: v for k, v in ctx['explanation_source'].items() if k != 'sha256'})
    raw = dict(interpretation='실패한 가정을 전량 정리한다.', evidence_refs=['MARK'],
               comparisons=[dict(level_ref='MARK', target_ref='CURRENT:TP:tp')])

    def mutate(reply, phase):
        if phase == 1:
            for proposal in [reply['wait_proposal']] + reply['candidates']:
                proposal['rationale'] = deepcopy(raw)
            reply['no_candidate_reason'] = deepcopy(raw)
        else:
            reply['rationale'] = deepcopy(raw)

    args, calls = taped(ctx, mutate=mutate)
    assert args[-1]['outcome']['status'] == 'COMPLETE', args[-1]['outcome']
    selected = args[-1]['outcome']['result']['selected']
    assert selected['explanation']['source_status'] == 'UNKNOWN'
    assert selected['explanation']['raw'] == raw
    assert selected['raw_proposal']['thesis_assessment']['status'] == 'FAILED'
    for call in calls:
        model = restore_model_input(json.loads(call['user_prompt']))
        assert model['contract_context']['explanation_source']['status'] == 'UNKNOWN_SOURCE_MISMATCH'
    guarded = guard_review(*args, dict(ctx, now=1001, account_captured_at=1001),
        bindings={'test': 'entry-contract'}, research_mode='economic-reject-calculated-v1')
    assert guarded['validated']['action'] == 'EXIT'
    assert 'UNKNOWN' in guarded['validated']['rationale']


@pytest.mark.parametrize('action', ['WAIT', 'ADJUST'])
def test_assembled_prompts_delegate_claims_and_keep_one_v1_namespace(tmp_path, action):
    import json
    from live.scenario_model_input import restore_model_input
    from live.scenario_review_storage import review_text
    from live.scenario_analysis_frames import analysis_model_view
    from engine.scenario_ma_context import build_ma_structure_context
    from tests.test_scenario_explanation_pipeline import run_pipeline
    runtime, _, outcome, _, requests, old_plan = run_pipeline(tmp_path, action)
    assert outcome['status'] in {'wait', 'intent_pending'}
    snapshot_raw, context_raw = runtime.conn.execute(
        'SELECT snapshot,context FROM llm_scenario_decisions').fetchone()
    snap, original_ctx = json.loads(review_text(snapshot_raw)), json.loads(context_raw)
    expected = analysis_model_view(dict(market_snapshot=snap,
        contract_context=dict(ma_structure=build_ma_structure_context(snap, original_ctx))))
    for request in requests:
        prompt = request['system_prompt']
        assert 'Use interpretation only for qualitative market interpretation and action tradeoffs.' in prompt
        assert 'do not restate these numeric/source/order claims in interpretation' in prompt
        assert 'holding_price_order helper IDs M, T0 and 1h.index.low are not output references.' in prompt
        assert 'Use holding_price_order when available' not in prompt
        restored = restore_model_input(json.loads(request['user_prompt']))
        ctx = restored['contract_context']
        assert 'holding_price_order' not in ctx
        assert ctx['current_plan'] == old_plan
        assert restored['market_snapshot'] == expected['market_snapshot']
        assert ctx['ma_structure'] == expected['contract_context']['ma_structure']
        for key in ('positions', 'pending_entries', 'initial_equity', 'previous_hard_stop',
                    'recorded_scenario_net', 'runner_economics'):
            if key in original_ctx:
                assert ctx[key] == original_ctx[key]
        for ref, price, phase, at in ctx['explanation_source']['points']:
            if ref.startswith('P:'):
                _, frame, index, field = ref.split(':')
                point = ctx['ma_structure']['primary'][frame]['points'][int(index)]
                assert (price, phase, at) == (point[field], point['phase'], point['as_of_ms'])
    runtime.conn.close()


def test_legacy_holding_helper_instruction_is_unchanged():
    from live.scenario_llm import immediate_reduce_instructions
    assert 'Use holding_price_order when available' in immediate_reduce_instructions('', dict(
        immediate_reduce_version=1, scenario_id='held', positions=[dict(quantity=1)]))
