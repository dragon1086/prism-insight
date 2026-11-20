"""Versioned holding explanation adapter. Never grants execution authority."""
from copy import deepcopy
import hashlib
import json
import math

MAX_INTERPRETATION = 160
MAX_EVIDENCE_REFS = 3
MAX_COMPARISONS = 3


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def enabled(context):
    return (type(context.get('explanation_contract_version')) is int
            and context['explanation_contract_version'] == 1
            and context.get('scenario_id') is not None and bool(context.get('positions')))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def rationale_schema():
    def obj(properties):
        return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)
    ref = dict(type='string', maxLength=180)
    return obj(dict(interpretation=dict(type='string', maxLength=MAX_INTERPRETATION),
        evidence_refs=dict(type='array', maxItems=MAX_EVIDENCE_REFS, items=ref),
        comparisons=dict(type='array', maxItems=MAX_COMPARISONS, items=obj(dict(level_ref=ref, target_ref=ref)))))


def model_matches(value, schema, context):
    """Explanation defects are advisory; every economic field stays strict."""
    from live.scenario_contract import _matches
    if not enabled(context):
        return _matches(value, schema)
    if 'anyOf' in schema:
        return any(model_matches(value, branch, context) for branch in schema['anyOf'])
    if schema.get('type') == 'object' and isinstance(value, dict):
        properties = schema['properties']
        if set(value) - set(properties):
            return False
        for key, child in properties.items():
            if key in {'rationale', 'no_candidate_reason'} and child == rationale_schema():
                continue
            if key not in value or not model_matches(value[key], child, context):
                return False
        return True
    if schema.get('type') == 'array' and isinstance(value, list):
        return (schema.get('minItems', 0) <= len(value) <= min(20, schema.get('maxItems', 20))
                and all(model_matches(item, schema['items'], context) for item in value))
    return _matches(value, schema)


def bind_receipt(receipt, context, *, snapshot=None):
    if not enabled(context):
        return receipt
    raw = receipt.get('raw_proposal') or receipt.get('raw_allocation_proposal') or {}
    effective = receipt.get('effective_plan') or raw
    if raw.get('action') == 'ADJUST' and not receipt.get('effective_plan'):
        effective = dict(action='ADJUST', explanation_plan_unavailable=True)
    explanation = compile_explanation(raw.get('rationale'), context, effective, scope=receipt['id'], snapshot=snapshot)
    receipt['explanation'] = explanation
    if receipt.get('effective_plan') is not None:
        receipt['effective_plan'] = dict(effective, rationale=explanation['text'])
        receipt['effective_sha256'] = digest(receipt['effective_plan'])
    return receipt


def source_from_structure(structure, context):
    points = []
    if _number(context.get('mark_price')) and _number(context.get('account_captured_at')):
        points.append(['MARK', context['mark_price'], 'MARK', context['account_captured_at'] * 1000])
    for frame in ('1h', '30m'):
        for index, point in enumerate((structure.get('primary', {}).get(frame, {}).get('points') or [])[:4]):
            phase = point.get('phase')
            if (phase not in {'CONFIRMED', 'FORMING'} or not _number(point.get('as_of_ms'))
                    or point.get('is_confirmed') is not (phase == 'CONFIRMED')):
                continue
            for field in ('high', 'low', 'close', 'ma10', 'ma35'):
                if _number(point.get(field)):
                    points.append([f'P:{frame}:{index}:{field}', point[field], phase, point['as_of_ms']])
    for ref, level in sorted(structure.get('levels', {}).items()):
        frame = ref.split('.')[0]
        if (frame not in {'4h', '1d', '1w'} or not _number(level.get('price'))
                or not _number(level.get('as_of_ms')) or type(level.get('is_confirmed')) is not bool):
            continue
        points.append(['MA:' + ref, level['price'], 'CONFIRMED' if level['is_confirmed'] else 'FORMING', level['as_of_ms']])
    body = dict(version=1, points=points[:77])
    return dict(body, sha256=digest(body))


def prepare_source(snapshot, context):
    if enabled(context):
        from engine.scenario_ma_context import build_ma_structure_context
        context['explanation_source'] = source_from_structure(build_ma_structure_context(snapshot, context), context)


def _verified_source(snapshot, context):
    from engine.scenario_ma_context import build_ma_structure_context
    source = context.get('explanation_source')
    try:
        return (isinstance(snapshot, dict) and isinstance(source, dict)
                and source.get('version') == 1 and isinstance(source.get('points'), list)
                and source.get('sha256') == digest({k: v for k, v in source.items() if k != 'sha256'})
                and source == source_from_structure(build_ma_structure_context(snapshot, context), context))
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


def project_input(projected, context, *, snapshot=None):
    if not enabled(context):
        return projected
    from live.scenario_model_input import restore_model_input, project_model_input
    logical = restore_model_input(projected)
    offered = context.get('explanation_source') if _verified_source(snapshot, context) else {'status': 'UNKNOWN_SOURCE_MISMATCH'}
    for source in (offered, {'status': 'OMITTED_INPUT_BUDGET'}):
        logical['contract_context']['explanation_source'] = source
        candidate, _ = project_model_input(logical, allow_gap_rescue=True)
        if len(json.dumps(candidate, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()) <= 100_000:
            return candidate
    return projected


def interpretation_text(raw):
    value = raw.get('interpretation') if isinstance(raw, dict) else raw
    if not isinstance(value, str) or not value:
        return '모델 해석(미검증): 설명 미제공.'
    return '모델 해석(미검증): ' + value[:MAX_INTERPRETATION] + (' [해석 표시 생략]' if len(value) > MAX_INTERPRETATION else '')


def compile_explanation(raw, context, plan, *, scope, snapshot=None):
    from live.scenario_contract import _matches
    source = context.get('explanation_source')
    source = source if isinstance(source, dict) else {}
    valid = _verified_source(snapshot, context)
    levels = {}
    if valid:
        for row in source['points']:
            if (isinstance(row, list) and len(row) == 4 and isinstance(row[0], str)
                    and (row[0] == 'MARK' or row[0].startswith(('P:30m:', 'P:1h:', 'MA:4h.', 'MA:1d.', 'MA:1w.')))
                    and _number(row[1]) and row[2] in {'MARK', 'CONFIRMED', 'FORMING'} and _number(row[3])):
                levels[row[0]] = dict(ref=row[0], price=row[1], phase=row[2], as_of_ms=row[3])
    data = raw if isinstance(raw, dict) else {}
    requested = data.get('comparisons') if isinstance(data.get('comparisons'), list) else []
    evidence = data.get('evidence_refs') if isinstance(data.get('evidence_refs'), list) else []
    action = plan.get('action')
    basis = plan if action == 'ADJUST' else context.get('current_plan')
    basis = basis if isinstance(basis, dict) else {}
    prefix = scope if action == 'ADJUST' else 'CURRENT'
    targets = {}
    for index, target in enumerate(basis.get('take_profits') or []):
        if isinstance(target, dict) and isinstance(target.get('id'), str) and _number(target.get('price')):
            ref = prefix + ':TP:' + target['id']
            targets[ref] = dict(ref=ref, price=target['price'], label=f'TP{index + 1}') if ref not in targets else None
    comparisons = []
    for request in requested[:MAX_COMPARISONS]:
        request = request if isinstance(request, dict) else {}
        level = levels.get(request.get('level_ref')) if isinstance(request.get('level_ref'), str) else None
        target = targets.get(request.get('target_ref')) if isinstance(request.get('target_ref'), str) else None
        relation = 'UNKNOWN'
        if level and target and context.get('side') in {'LONG', 'SHORT'}:
            difference = level['price'] - target['price']
            relation = 'EQUAL' if difference == 0 else ('BEFORE' if (difference < 0) == (context['side'] == 'LONG') else 'AFTER')
        comparisons.append(dict(level_ref=request.get('level_ref'), target_ref=request.get('target_ref'),
                                level=level, target=target, relation=relation))
    facts = [levels.get(ref) if isinstance(ref, str) else None for ref in evidence[:MAX_EVIDENCE_REFS]]
    omitted = (len(evidence) > MAX_EVIDENCE_REFS or len(requested) > MAX_COMPARISONS
               or len(str(data.get('interpretation', ''))) > MAX_INTERPRETATION)
    text = interpretation_text(raw) + '\n공식 수치 대조(표시계획 기준·실제 주문 상태 아님, 도달·지지저항 증명 아님):'
    for fact in facts:
        line = ('\nUNKNOWN: 근거 참조 미확인.' if not fact else
                f"\n{fact['ref']}={fact['price']} ({fact['phase']}, {fact['as_of_ms']}ms)")
        if len(text) + len(line) > 1900:
            omitted = True
            break
        text += line
    for item in comparisons:
        if item['relation'] == 'UNKNOWN':
            line = '\nUNKNOWN: 비교 참조 또는 방향 미확인.'
        else:
            level, target = item['level'], item['target']
            word = {'BEFORE': '앞', 'AFTER': '뒤', 'EQUAL': '동가'}[item['relation']]
            line = (f"\n{level['ref']}={level['price']} ({level['phase']}, {level['as_of_ms']}ms), "
                    f"{target['label']}={target['price']}: {context['side']} 방향 수치순서 {word}.")
        if len(text) + len(line) > 1900:
            omitted = True
            break
        text += line
    if not facts and not comparisons:
        text += '\nUNKNOWN: 요청된 공식 대조 없음.'
    if omitted:
        text += '\n[표시 일부 생략; 구조화 원문·대조 결과는 영수증에 보존]'
    receipt = dict(version=1, scope=scope, raw=deepcopy(raw), raw_sha256=digest(raw),
        model_explanation_status='STRUCTURED' if _matches(raw, rationale_schema()) else 'MISSING_OR_MALFORMED',
        interpretation_truth='UNVERIFIED', validation_scope='SOURCE_BOUND_NUMERIC_RELATIONS_ONLY',
        source_sha256=source.get('sha256'), source_status='VERIFIED' if valid else 'UNKNOWN',
        plan_sha256=digest({k: v for k, v in basis.items()
                           if k not in {'rationale', 'risk', 'execution_pricing'}}),
        basis=('UNAVAILABLE_EFFECTIVE_PLAN' if plan.get('explanation_plan_unavailable') else
               'EFFECTIVE_ADJUST_PLAN' if action == 'ADJUST' else 'CURRENT_PLAN_NOT_LIVE_ORDER_PROOF'),
        evidence=facts, comparisons=comparisons, text=text, omitted=omitted)
    return dict(receipt, sha256=digest(receipt))


PROMPT = """Holding explanations follow the structured rationale schema. interpretation is
unverified qualitative opinion; evidence_refs and comparisons identify frozen facts,
not newly calculated prices. Never put literal values/relations in comparison objects.
Use explanation_source references: MARK, P:30m|1h:index:field, or MA:level_id.
Respect each point's phase/time; no analysis below 30m or on 12h. If the catalog is factored, point
IDs refer to the supplied ma_structure primary points/levels and account Mark.
WAIT/REDUCE/EXIT targets use CURRENT:TP:<current target id>; ADJUST uses its own
c0/c1:TP:<target id>. SELECT uses only the selected candidate's target references.
The host checks effective prices and reference bindings; do not compute hashes.
Missing references stay UNKNOWN, not a new entry veto or reason to delay protection.
A displayed comparison proves neither support, active orders nor future fills.
"""
