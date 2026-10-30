"""Host-bound original goals, not a verifier of unrestricted model prose."""
from copy import deepcopy
import hashlib
import json
import math
from decimal import Decimal, ROUND_DOWN

from engine.scenario_ma_context import _frame_points
from engine.scenario_snapshot import TIMEFRAME_MS
from live.scenario_analysis_frames import ANALYSIS_FRAMES


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


class GoalConsistencyRejection(ValueError):
    def __init__(self, code, evidence):
        super().__init__(code)
        self.evidence = deepcopy(evidence)


def goal_schema():
    def obj(properties):
        return dict(type='object', properties=properties, required=list(properties),
                    additionalProperties=False)
    ref = obj(dict(frame=dict(type='string', enum=list(ANALYSIS_FRAMES)),
                   phase=dict(type='string', enum=['confirmed', 'forming']),
                   ma=dict(type='string', enum=['ma10', 'ma35'])))
    return dict(anyOf=[
        obj(dict(mode=dict(type='string', enum=['BOUNDED_BEFORE', 'REFERENCE_EXTENSION']), reference=ref)),
        obj(dict(mode=dict(type='string', enum=['NO_MA_BOUND']), reference=dict(type='null'))),
    ])


def valid_goal(goal):
    if not isinstance(goal, dict) or set(goal) != {'mode', 'reference'} or not isinstance(goal['mode'], str):
        return False
    if goal['mode'] == 'NO_MA_BOUND':
        return goal['reference'] is None
    ref = goal['reference']
    return (goal['mode'] in {'BOUNDED_BEFORE', 'REFERENCE_EXTENSION'}
        and isinstance(ref, dict) and set(ref) == {'frame', 'phase', 'ma'}
        and all(isinstance(v, str) for v in ref.values())
        and ref['frame'] in TIMEFRAME_MS and ref['phase'] in {'confirmed', 'forming'}
        and ref['ma'] in {'ma10', 'ma35'})


def canonical_goal(goal, price):
    if goal['mode'] == 'NO_MA_BOUND':
        return 'MA 경계로 제한하지 않은 원래 목표'
    ref = goal['reference']
    relation = '도달 전까지' if goal['mode'] == 'BOUNDED_BEFORE' else '너머 진행'
    phase = '확정' if ref['phase'] == 'confirmed' else '형성'
    return f"최초 판단 입력의 {ref['frame']} {phase} {ref['ma'].upper()} {price} {relation}"


def valid_resolved(value):
    keys = {'goal', 'input_id', 'decision_slot', 'snapshot_sha256', 'snapshot_as_of_ms', 'price',
            'open_time_ms', 'observed_at_ms', 'canonical'}
    if (not isinstance(value, dict) or set(value) != keys or not valid_goal(value['goal'])
            or not isinstance(value['input_id'], str) or not value['input_id']
            or type(value['decision_slot']) is not int or value['decision_slot'] < 0
            or not isinstance(value['snapshot_sha256'], str) or len(value['snapshot_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in value['snapshot_sha256'])
            or not number(value['snapshot_as_of_ms'])):
        return False
    if value['goal']['mode'] == 'NO_MA_BOUND':
        if any(value[k] is not None for k in ('price', 'open_time_ms', 'observed_at_ms')):
            return False
    elif (not all(number(value[k]) for k in ('price', 'open_time_ms', 'observed_at_ms'))
          or not value['open_time_ms'] <= value['observed_at_ms'] <= value['snapshot_as_of_ms']):
        return False
    return value['canonical'] == canonical_goal(value['goal'], value['price'])


def prepare_goal_source(snapshot, context):
    """Private host context, excluded from model payload; no extra wire catalog."""
    if context.get('entry_contract_version') != 2 or context.get('scenario_id'):
        return
    at, now = snapshot.get('as_of_ms'), context.get('now')
    slot = context.get('entry_goal_decision_slot')
    if type(slot) is not int or slot < 0:
        raise ValueError('entry_goal_decision_slot_missing')
    if snapshot.get('valid') is not True or not number(at) or not number(now) or not 0 <= now * 1000 - at <= 120000:
        raise ValueError('entry_goal_snapshot_stale')
    levels = {}
    frames = snapshot.get('timeframes', {})
    for frame, duration in TIMEFRAME_MS.items():
        item = frames.get(frame)
        if (not isinstance(item, dict) or item.get('status', 'ok') not in {'ok', 'available'}
                or item.get('valid') is False):
            continue
        path = item.get('ma_path')
        if isinstance(path, dict) and (path.get('status', 'available') != 'available' or path.get('valid') is False):
            continue
        confirmed, forming, _, _ = _frame_points(item, duration, at)
        for phase, point in [('confirmed', confirmed[-1] if confirmed else None), ('forming', forming)]:
            if point:
                for ma in ('ma10', 'ma35'):
                    levels[f'{frame}.{phase}.{ma}'] = dict(price=point[ma],
                        open_time_ms=point['open_time_ms'], observed_at_ms=point['as_of_ms'])
    source = dict(input_id=context['input_id'], decision_slot=slot,
        snapshot_sha256=digest(snapshot), as_of_ms=at, levels=levels)
    if 'entry_goal_source' in context and context['entry_goal_source'] != source:
        raise ValueError('entry_goal_source_changed')
    context['entry_goal_source'] = source


def resolve_goal(goal, context):
    if not valid_goal(goal):
        raise ValueError('entry_goal_invalid_definition')
    source = context.get('entry_goal_source')
    if (not isinstance(source, dict) or source.get('input_id') != context.get('input_id')
            or not isinstance(source.get('snapshot_sha256'), str)
            or len(source['snapshot_sha256']) != 64):
        raise ValueError('entry_goal_source_missing')
    result = dict(goal=deepcopy(goal), input_id=source['input_id'],
                  decision_slot=source['decision_slot'],
                  snapshot_sha256=source['snapshot_sha256'], snapshot_as_of_ms=source['as_of_ms'])
    if goal['mode'] == 'NO_MA_BOUND':
        return dict(result, price=None, open_time_ms=None, observed_at_ms=None,
                    canonical=canonical_goal(goal, None))
    ref = goal['reference']
    point = source['levels'].get(f"{ref['frame']}.{ref['phase']}.{ref['ma']}")
    if not point:
        raise GoalConsistencyRejection('entry_goal_reference_unavailable', dict(goal=goal))
    canonical = canonical_goal(goal, point['price'])
    return dict(result, **deepcopy(point), canonical=canonical)


def validate_goal_plan(plan, resolved):
    if not resolved or resolved['goal']['mode'] != 'BOUNDED_BEFORE' or plan.get('action') in {'WAIT', 'EXIT', 'REDUCE'}:
        return
    bound, side = resolved['price'], plan.get('side')
    if not number(bound) or side not in {'LONG', 'SHORT'}:
        raise ValueError('entry_goal_bound_invalid')
    for field in ('entries', 'take_profits'):
        for index, row in enumerate(plan.get(field, [])):
            price = row.get('price')
            if not number(price):
                raise ValueError('entry_goal_plan_price_invalid')
            if not (price < bound if side == 'LONG' else price > bound):
                raise GoalConsistencyRejection('entry_goal_bound_exceeded',
                    dict(field=field, index=index, price=price, bound=bound, side=side,
                         goal=resolved['goal']))


def plan_goal(payload, context):
    if payload.get('action') == 'OPEN' and context.get('entry_contract_version') == 2:
        reference = payload['entry_contract']['goal'].get('reference')
        if isinstance(reference, dict) and reference.get('frame') not in ANALYSIS_FRAMES:
            raise ValueError('entry_goal_frame_not_allowed_for_new_open')
        return resolve_goal(payload['entry_contract']['goal'], context)
    record = context.get('registered_entry_contract')
    return deepcopy(record.get('resolved_goal')) if isinstance(record, dict) and record.get('version') == 2 else None


def validate_goal_pair(raw, effective, context):
    resolved = plan_goal(raw, context)
    for phase, plan in [('raw', raw)] + ([('effective', effective)] if effective is not None else []):
        try:
            validate_goal_plan(plan, resolved)
            if resolved and resolved['goal']['mode'] == 'REFERENCE_EXTENSION' and plan.get('action') == 'OPEN':
                bound = resolved['price']
                remainder = Decimal(1) - sum((Decimal(str(t['fraction'])) for t in raw['take_profits']), Decimal(0))
                step = context.get('quantity_step')
                minimum = context.get('minimum_quantity')
                if not all(number(v) for v in (step, minimum)):
                    raise ValueError('entry_goal_instrument_missing')
                quantity = sum((Decimal(str(e['quantity'])) for e in plan['entries']), Decimal(0))
                # Same sequential clipping/step/minimum as host TP projection.
                allocated, beyond = Decimal(0), False
                for target in plan['take_profits']:
                    qty = (max(Decimal(0), min(quantity - allocated,
                        quantity * Decimal(str(target['fraction'])))) / Decimal(str(step))).to_integral_value(
                            rounding=ROUND_DOWN) * Decimal(str(step))
                    allocated += qty
                    if qty >= Decimal(str(minimum)) and (
                            target['price'] > bound if plan['side'] == 'LONG' else target['price'] < bound):
                        beyond = True
                runner = ((quantity * max(remainder, Decimal(0)) / Decimal(str(step)))
                          .to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step)))
                usable_runner = runner >= Decimal(str(minimum))
                if not beyond and not usable_runner:
                    raise GoalConsistencyRejection('entry_goal_extension_without_target',
                        dict(bound=bound, intended_remainder=str(remainder), runner_quantity=str(runner)))
        except GoalConsistencyRejection as exc:
            field, index = exc.evidence.get('field'), exc.evidence.get('index')
            exc.evidence.update(phase=phase, comparison_scope='FIRST_VIOLATION_ONLY',
                                raw_reference='receipt.raw_proposal; replay remaining comparisons')
            if field is not None:
                row = raw[field][index]
                exc.evidence.update(id=row.get('id'), raw_price=row['price'],
                    effective_price=effective[field][index]['price'] if effective is not None else None)
            raise
    return resolved


def stored_goal(conn, record):
    """Re-resolve only the original durable decision, never current market data."""
    origin = record['origin']
    rows = conn.execute("SELECT snapshot,context,proposal FROM llm_scenario_decisions "
        "WHERE slot=?", (record['resolved_goal']['decision_slot'],)).fetchall()
    if len(rows) != 1 or any(value is None for value in rows[0]):
        raise ValueError('entry_goal_original_decision_missing')
    from observability.review_storage import review_text
    snapshot = json.loads(review_text(rows[0][0]))
    context, proposal = map(json.loads, rows[0][1:])
    if (context.get('entry_contract_version') != 2 or context.get('scenario_id') is not None
            or context.get('input_id') != origin['input_id']
            or context.get('entry_goal_decision_slot') != record['resolved_goal']['decision_slot']
            or proposal.get('entry_contract') != record['definition']
            or proposal.get('action_id') != origin['action_id']
            or proposal.get('input_id') != origin['input_id']):
        raise ValueError('entry_goal_original_decision_mismatch')
    context.pop('entry_goal_source', None)
    prepare_goal_source(snapshot, context)
    try:
        resolved = resolve_goal(record['definition']['goal'], context)
        validate_goal_plan(proposal, resolved)
    except GoalConsistencyRejection as exc:
        raise ValueError('entry_goal_original_source_invalid') from exc
    if resolved != record['resolved_goal']:
        raise ValueError('entry_goal_original_source_mismatch')
    return resolved
