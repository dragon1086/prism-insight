"""Coupled pre-DRAFT prices and executable-size illustrations, never permission."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json

from core.scenario_limit_prices import preview_execution_prices
from live.scenario_contract import identity_fields
from live.scenario_entry_geometry import build, _number
from live.scenario_entry_economics import assess
from live.scenario_explanation import source_from_structure
from live.scenario_risk_allocation import allocate_entry_risk

MAX_PLANS = 6
MAX_BYTES = 12000


def _preview(raw, context):
    from live.scenario_calculated_review import _gross_projection, _supplied_instrument_filters
    values = []
    for confidence, fraction in ((.5, .5), (.5, 1.), (1., 1.)):
        proposal = dict(**identity_fields(context), **deepcopy(raw), confidence=confidence,
                        expires_at=min(context['now']+60,(context['input_captured_at']//300+1)*300), rationale='Host arithmetic illustration; not a trade thesis.',
                        leverage=10, cancel_entry_ids=[], chase=dict(max_bps=0,max_reprices=0))
        proposal['entries'][0]['allocation_fraction'] = fraction
        try:
            wire, _ = allocate_entry_risk(proposal, context)
            effective = preview_execution_prices(wire, context, original_input_now=context['now'])
            _supplied_instrument_filters(effective, context)
            projection = _gross_projection(effective, context)
            economics = assess(effective, context, projection)
            if not economics or economics['status'] != 'PASS':
                continue
            # A clipped-away tier is not an executable split or a meaningful runner.
            if any(v['quantity'] < context['minimum_quantity'] for v in projection['targets']):
                continue
            values.append(dict(confidence=confidence, allocation_fraction=fraction,
                quantity=projection['quantity'], reserved_risk=effective['risk']['total_risk'],
                ratio=economics['ratio'], net_reward=economics['conditional_reward'],
                full_stop_loss=economics['full_stop_loss'], cost_basis=economics['basis'],
                target_quantities=[v['quantity'] for v in projection['targets']],
                residual_quantity=round(projection['quantity']-sum(v['quantity'] for v in projection['targets']),12)))
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            continue
    return values


def prepare(snapshot, context, structure):
    unavailable = dict(version=1,status='UNAVAILABLE',layouts=[],reason='missing_verified_flat_inputs')
    # Reuse quote/as-of/flat validation; a valid view may have no favourable observed levels.
    view = build(snapshot, context, structure)
    if view.get('version') != 2 or context.get('protection_ok') is not True or context.get('new_risk_blocked') is not False:
        return unavailable
    try:
        tick = _number(context['price_tick'])
        if tick <= 0:return unavailable
        points = [(ref,_number(price),phase) for ref,price,phase,at in source_from_structure(structure,context)['points']
                  if phase in {'CONFIRMED','FORMING'} and 0<_number(at)<=_number(snapshot['as_of_ms'])]
        def rounded(value, upward):
            return (value/tick).to_integral_value(rounding=ROUND_CEILING if upward else ROUND_FLOOR)*tick
        layouts=[]
        for side in ('LONG','SHORT'):
            sign=Decimal(1 if side=='LONG'else -1)
            quote=_number(context['limit_price_quotes']['ask'if side=='LONG'else'bid'])
            adverse='low'if side=='LONG'else'high'
            favourable='high'if side=='LONG'else'low'
            stops=sorted([(ref,p,phase)for ref,p,phase in points if ref.startswith('P:')and ref.endswith(':'+adverse)and sign*(quote-p)>0],
                         key=lambda row:(abs(quote-row[1]),row[2]!='CONFIRMED',row[0]))
            if not stops:continue
            anchors=[stops[0]]
            confirmed=next((v for v in stops if v[2]=='CONFIRMED'and v[1]!=stops[0][1]),None)
            if confirmed is not None:anchors.append(confirmed)
            offers=[(anchor,dict(kind='CURRENT_LIMIT',price=quote))for anchor in anchors]
            breaks=sorted({p for ref,p,phase in points if ref.startswith('P:')and ref.endswith(':'+favourable)and sign*(p-quote)>tick},key=lambda p:abs(p-quote))
            for anchor in anchors:
                if breaks and context.get('conditional_entry_version')==1:
                    trigger=rounded(breaks[0]+sign*tick,side=='LONG')
                    offers.append((anchor,dict(kind='BREAKOUT_TRIGGER',price=trigger+sign*tick,trigger_price=trigger)))
            for anchor in anchors:
                stop=rounded(anchor[1]-sign*tick,side=='SHORT')
                pullbacks=sorted({p for _,p,_ in points if sign*(p-stop)>tick and sign*(quote-p)>tick},key=lambda p:abs(quote-p))
                if pullbacks:offers.append((anchor,dict(kind='PULLBACK_LIMIT',price=pullbacks[0])))
            side_start=len(layouts)
            observed=sorted({p for _,p,_ in points if sign*(p-quote)>0},key=lambda p:sign*(p-quote))
            for anchor,entry in offers:
                if len(layouts)-side_start>=3:break
                stop=rounded(anchor[1]-sign*tick,side=='SHORT')
                price=rounded(entry['price'],side=='LONG');distance=sign*(price-stop)
                if distance<=0:continue
                level_targets=[p-sign*tick for p in observed if sign*(p-price)>tick]
                families=[]
                if level_targets:
                    targets=[level_targets[0],level_targets[-1]] if len(level_targets)>1 else level_targets
                    families.append(('OBSERVED_LEVELS_NOT_STRENGTH',targets,[.3,.7]if len(targets)==2 else[1.]))
                families.append(('R_PROJECTION_NOT_SUPPORT',[price+sign*distance,price+sign*Decimal('3')*distance],[.3,.7]))
                for basis,targets,fractions in families:
                    identity='plan-'+str(len(layouts))
                    row=dict(id=identity,price=float(price))
                    if 'trigger_price'in entry:row['trigger_price']=float(entry['trigger_price'])
                    tps=[dict(id=identity+'-tp'+str(i),price=float(rounded(p,side=='SHORT')),fraction=f)
                         for i,(p,f)in enumerate(zip(targets,fractions))]
                    if len({p['price']for p in tps})!=len(tps)or any(sign*(_number(p['price'])-price)<=0 for p in tps):continue
                    raw=dict(action='OPEN',side=side,hard_stop=float(stop),entries=[row],take_profits=tps,partial_stops=[])
                    examples=_preview(raw,context)
                    if not examples:continue
                    atr=snapshot.get('timeframes',{}).get('30m',{}).get('confirmed',{}).get('atr14_risk_only')
                    try:stop_atr=float(distance/_number(atr)) if _number(atr)>0 else None
                    except (TypeError,ValueError,ArithmeticError):stop_atr=None
                    layouts.append(dict(id=identity,side=side,entry_kind=entry['kind'],entry_price=float(price),
                        stop_ref=anchor[0],stop_phase=anchor[2],price_risk_per_btc=float(distance),stop_distance_atr30=stop_atr,
                        raw_target_R=[float(sign*(_number(t['price'])-price)/distance)for t in tps],
                        trigger_price=row.get('trigger_price'),hard_stop=float(stop),take_profits=tps,
                        target_basis=basis,unpriced_runner_fraction=0.,sizing_examples=examples,
                        support_strength='NOT_CERTIFIED',fill_probability='UNKNOWN'))
                    break  # Preserve structural/entry-style choices, not a ranking of returns.
        result=dict(version=1,status='AVAILABLE'if layouts else'UNAVAILABLE',layouts=layouts[:MAX_PLANS],
            meaning='Coupled numerical proposals, not permission or win probabilities. Sizing examples are illustrations, not suggested confidence. Host rechecks chosen confidence/allocation, lot rounding including dust at SL, goal, risk and fresh execution. Partial stops keep their original qualitative contract; no risk credit. R projections are not observed support.')
        return result if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())<=MAX_BYTES else unavailable
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return unavailable


def constrain(schema, menu):
    """Adapt the existing coupled-schema prototype without changing wire fields."""
    result=deepcopy(schema);branches=[]
    for branch in result['anyOf']:
        if branch['properties']['action']['enum']!=['OPEN']:
            branches.append(branch);continue
        for side in ('LONG','SHORT'):
            plans=[p for p in menu['layouts']if p['side']==side]
            if not plans:
                original=deepcopy(branch);original['properties']['side']['enum']=[side];branches.append(original)
            for plan in plans:
                offered=deepcopy(branch);props=offered['properties']
                props['side']['enum']=[side];props['hard_stop']['enum']=[plan['hard_stop']]
                entry=props['entries'];entry.update(minItems=1,maxItems=1)
                for key,value in [('id',plan['id']),('price',plan['entry_price'])]:entry['items']['properties'][key]['enum']=[value]
                if 'trigger_price'in entry['items']['properties']:
                    entry['items']['properties']['trigger_price']=({'type':'null'}if plan['trigger_price']is None else{'type':'number','enum':[plan['trigger_price']]})
                template=props['take_profits']['items'];targets=[]
                for target in plan['take_profits']:
                    item=deepcopy(template)
                    for key,value in target.items():item['properties'][key]['enum']=[value]
                    targets.append(item)
                props['take_profits'].update(minItems=len(targets),maxItems=len(targets),items={'anyOf':targets})
                for key in ('max_bps','max_reprices'):props['chase']['properties'][key]['enum']=[0]
                branches.append(offered)
    for branch in branches:
        if branch['properties']['action']['enum']==['OPEN']:
            branch['properties']['entry_readiness']={
                'type':'object','additionalProperties':False,
                'properties':{'status':{'type':'string','enum':['SUPPORTED_NOW','WAIT_FOR_EVIDENCE']},
                              'reason':{'type':'string'}},'required':['status','reason']}
            branch['required'].append('entry_readiness')
    result['anyOf']=branches
    return result
