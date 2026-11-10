"""Bounded pre-DRAFT arithmetic, never a classifier, plan or order authority."""
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import json
import math

from core.llm_scenario import risk_snapshot
from core.scenario_limit_prices import possible_entry_price_anchor
from live.scenario_fee_projection import fee_only_projection, verified_taker_rate
from live.scenario_entry_economics import assess
from live.scenario_explanation import source_from_structure

MAX_BYTES = 4096
MAX_ROWS = 12
INPUT_LIMIT = 100_000


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('missing_or_nonfinite')
    return Decimal(str(value))


def _entry_bound(row, reward, context):
    """Invert the same unit payoff; this price is not an observed market level."""
    entry,stop,loss=map(_number,(row['worst_entry_bound'],row['stop_level'],row['loss_per_btc']))
    k=_number(context['entry_economics']['minimum_ratio'])
    rate=(verified_taker_rate(context.get('observed_execution_fee_evidence'),context['now'])
          if row['cost_basis']=='FEE' else _number(context['estimated_cost_rate']))
    sign=Decimal(1 if row['side']=='LONG'else -1)
    slope=-(1+k)*(sign+rate)
    price=entry-(_number(reward)-k*loss)/slope
    if price<=0 or sign*(price-stop)<=0:return None
    return float(price.quantize(Decimal('.000001'),rounding=ROUND_FLOOR if sign==1 else ROUND_CEILING))


def _plan_math(view,context):
    """Continuous, one-BTC comparisons; final rounding/TTL/structure still apply."""
    groups={};result=[]
    for i,values in enumerate(view['rows']):
        row=dict(zip(view['columns'],values))
        key=tuple(row[k]for k in ('side','entry_kind','worst_entry_bound','stop_level','cost_basis'))
        groups.setdefault(key,[]).append((i,row))
    for items in groups.values():
        items=sorted(items,key=lambda x:_number(x[1]['reward_per_btc']))
        (ni,near),(fi,far)=items[0],items[-1]
        try:
            if len(items)==1:
                reward,loss,k=map(_number,(far['reward_per_btc'],far['loss_per_btc'],context['entry_economics']['minimum_ratio']))
                fraction=(1+k)*loss/(reward+loss) if loss>0 and reward>=k*loss else None
                result.append([None,fi,None,None,None,_entry_bound(far,float(reward),context),
                    float(fraction.quantize(Decimal('.000001'),rounding=ROUND_CEILING))if fraction is not None else None])
                continue
            a,b,loss,k=map(_number,(near['reward_per_btc'],far['reward_per_btc'],far['loss_per_btc'],context['entry_economics']['minimum_ratio']))
            if b<=a or loss<=0 or loss!=_number(near['loss_per_btc']):continue
            maximum=min(Decimal(1),(b-k*loss)/(b-a)) if b>=k*loss else None
            minimum=loss/(a+loss) if a>=0 else None
            rounded=lambda v,mode:float(v.quantize(Decimal('.000001'),rounding=mode))if v is not None else None
            result.append([ni,fi,rounded(maximum,ROUND_FLOOR),rounded(minimum,ROUND_CEILING),
                _entry_bound(far,float((a+b)/2),context),_entry_bound(far,float(b),context),
                rounded((1+k)*loss/(b+loss),ROUND_CEILING)if b>=k*loss else None])
        except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
            continue
    by_side={side:[r for r in result if view['rows'][r[0]if r[0]is not None else r[1]][0]==side]for side in ['LONG','SHORT']}
    paired=[by_side[side][i]for i in range(max((len(v)for v in by_side.values()),default=0))for side in ['LONG','SHORT']if i<len(by_side[side])]
    return dict(minimum_ratio=context['entry_economics']['minimum_ratio'],columns=['near_row','far_row','near_tp_fraction_max','tp1_stop_breakeven_min_fraction','worst_entry_half_split','worst_entry_far_only','far_tp_fraction_min_with_runner'],rows=paired,
        meaning='Continuous 1BTC TP fractions, fixed SL/targets, not orders or observed levels. near=null means one target. Far+runner assumes near=0. LONG bounds ceilings; SHORT floors. Null=unavailable/infeasible. TP1 protection is optional, not a gate. Actual sizing/ticks/buffers/expiry/fills need validation.')


def build(snapshot, context, structure):
    """Numeric nearest levels are not proven support, invalidation or TP targets."""
    unavailable={'status':'UNAVAILABLE','reason':'missing_verified_flat_inputs'}
    try:
        if (context.get('scenario_id',True) is not None or context.get('positions')!=[]
                or context.get('pending_entries')!=[] or context.get('accounting_status')!='confirmed'
                or snapshot.get('valid') is not True or not isinstance(context.get('entry_economics'),dict)):
            return unavailable
        now = _number(context['now'])
        asof = _number(snapshot['as_of_ms'])
        quotes = context['limit_price_quotes']
        stamp = _number(quotes['captured_at'])
        bid = _number(quotes['bid'])
        ask = _number(quotes['ask'])
        if not 0<bid<ask or not 0<=now-stamp<=10 or not 0<=now*1000-asof<=120000:
            return unavailable
        levels = {}
        extremes = set()
        for ref,price,phase,observed in source_from_structure(structure,context)['points']:
            if phase!='CONFIRMED' or ref=='MARK' or not 0<_number(observed)<=asof:
                continue
            if ref.startswith('P:') and ref.rsplit(':',1)[-1] not in {'high','low','ma10','ma35'}:
                continue
            price=_number(price)
            if price>0:
                levels.setdefault(price,ref)
                if ref.startswith('P:') and ref.rsplit(':',1)[-1] in {'high','low'}:
                    extremes.add(price)
        atrs = {}
        for frame, duration in [('30m', 1800000), ('1h', 3600000)]:
            confirmed = snapshot.get('timeframes', {}).get(frame, {}).get('confirmed', {})
            value = confirmed.get('atr14_risk_only')
            opened = confirmed.get('open_time_ms')
            atrs[frame] = (_number(value) if confirmed.get('is_confirmed') is True
                and type(opened) is int and opened + duration == int(asof) // duration * duration
                and type(value) in (int, float) and math.isfinite(value) and value > 0 else None)
        atr = atrs['30m']
        captured = context.get('input_captured_at')
        deadline_remaining = None
        if (type(context.get('entry_contract_version')) is int and context['entry_contract_version'] == 2
                and type(captured) in (int, float) and math.isfinite(captured)
                and 0 <= now - _number(captured) <= 120):
            deadline_remaining = max(0., float((_number(captured) // 300 + 1) * 300 - now))
        rows=[]
        for side,entry in [('LONG',ask),('SHORT',bid)]:
            sign=Decimal(1 if side=='LONG' else -1)
            adverse=sorted((p for p in levels if sign*(entry-p)>0),key=lambda p:abs(entry-p))
            favorable=sorted((p for p in levels if sign*(p-entry)>0),key=lambda p:abs(entry-p))
            outer_stop=sorted((p for p in extremes if sign*(entry-p)>0),key=lambda p:abs(entry-p))
            outer_target=sorted((p for p in extremes if sign*(p-entry)>0),key=lambda p:abs(entry-p))
            stops=list(dict.fromkeys(adverse[:1]+outer_stop[-1:]))
            macro=[p for p in favorable if levels[p].startswith('MA:')][:1]
            extensions=outer_target[-1:]+macro
            farthest=sorted(extensions,key=lambda p:abs(p-entry))[-1:]
            targets=list(dict.fromkeys(favorable[:1]+farthest))
            choices=[('QUOTE_REFERENCE',entry,stops)]
            if len(outer_stop)>=2:
                choices.append(('LEVEL_LIMIT_IF_FILLED',outer_stop[0],outer_stop[-1:]))
            for kind,raw_entry,stops in choices:
                anchor=possible_entry_price_anchor({'price':float(raw_entry)},context,side)
                e=(max(raw_entry,anchor) if side=='LONG' else min(raw_entry,anchor)) if anchor is not None else raw_entry
                for stop in stops:
                    unit = risk_snapshot(
                        initial_equity=context['initial_equity'], side=side, hard_stop=float(stop),
                        positions=[], pending_entries=[], entries=[{'price':float(e),'quantity':1}],
                        realized_loss=0, fees_paid=0, funding_paid=0,
                        estimated_cost_rate=context['estimated_cost_rate'],
                        slippage_bps=context['slippage_bps'],
                        risk_fraction=context.get('scenario_risk_fraction',.02),
                    )['proposed_risk']
                    for target in targets:
                        def fees(exit_price):
                            return fee_only_projection(
                                side=side, entry_price=float(e), exit_price=float(exit_price), quantity=1,
                                evidence=context.get('observed_execution_fee_evidence'),
                                now=context['now'], new_entry=True,
                            )
                        gain,loss=fees(target),fees(stop)
                        payoff=({'status':'AVAILABLE','net_reward':gain['estimated_net_before_unmodelled_costs'],'full_stop_loss':-loss['estimated_net_before_unmodelled_costs']}
                                if gain['status']==loss['status']=='AVAILABLE' else {'status':'UNKNOWN'})
                        p={'quantity':1,'weighted_risk_per_btc':unit,'gross_at_full_stop':float(sign*(stop-e)),
                           'whole_plan_payoff':payoff,'joint_exit_paths':{'paths':[[['TP_STOP'],0,float(sign*(target-e)),None]]}}
                        result=assess({'action':'OPEN'},context,p)
                        if not result or result['status']=='UNKNOWN':
                            continue
                        basis='FEE' if result['basis'].startswith('VERIFIED_') else 'RESERVE'
                        price_risk = abs(e - stop)
                        fee_share = ((_number(loss['assumed_entry_fee']) + _number(loss['assumed_exit_fee'])) / price_risk
                                     if basis == 'FEE' else None)
                        reserve_share = ((_number(unit) - price_risk) / price_risk if basis == 'RESERVE' else None)
                        rounded = lambda value: round(float(value), 6) if value is not None else None
                        diagnostics = [rounded(price_risk / atrs['1h']) if atrs['1h'] else None,
                                       rounded(abs(raw_entry - entry) / atr) if atr else None,
                                       rounded(fee_share), rounded(reserve_share), rounded(sign * (target - e) / price_risk)]
                        rows.append([side,kind,float(raw_entry),float(e),levels[stop],float(stop),levels[target],float(target),float(abs(e-stop)/atr) if atr else None,result['ratio'],result['conditional_reward'],result['full_stop_loss'],basis] + diagnostics)
        view={'version':2,'status':'AVAILABLE' if rows else 'UNAVAILABLE','snapshot_as_of_ms':int(asof),'quote_captured_at':float(stamp),'entry_deadline_seconds_remaining':deadline_remaining,
              'columns':['side','entry_kind','raw_entry_reference','worst_entry_bound','stop_ref','stop_level','target_ref','target_level','stop_distance_atr30','single_target_ratio','reward_per_btc','loss_per_btc','cost_basis','stop_distance_atr1h','entry_distance_atr30','fee_to_price_stop_risk','reserve_to_price_stop_risk','target_distance_R'],
              'rows':rows[:MAX_ROWS],
              'meaning':'USDT/BTC single-target arithmetic, not orders, ladder payoff, support strength or win/fill probability. Raw levels before buffers; no fill promise or ranking. FEE=historical taker, future funding/slippage excluded; RESERVE=host cost stress, not known fees. R=target-distance/stop-distance before costs. Ratios rounded6dp; null=MISSING. V2 deadline entry-only; host validates actual ladder.'}
        if len(json.dumps(view,ensure_ascii=False).encode())>MAX_BYTES:return unavailable
        extra=_plan_math(view,context)
        if extra['rows']:
            samples=extra['rows'];extra['total_pairs']=len(samples);extra['rows']=[]
            view['plan_math']=extra
            for sample in samples:
                extra['rows'].append(sample)
                if len(json.dumps(view,ensure_ascii=False).encode())>MAX_BYTES:extra['rows'].pop()
            if not extra['rows']:view.pop('plan_math')
        return view
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return unavailable


def project(projected, snapshot, context):
    """No eviction of original facts, history, schema or metadata to add this view."""
    from live.scenario_model_input import restore_model_input,project_model_input
    try:
        logical = restore_model_input(projected)
        target = logical['contract_context']
        if ('entry_geometry' in target or target.get('scenario_id',True) is not None
                or any(target.get(k)!=context.get(k) for k in ['positions','pending_entries','input_captured_at','entry_contract_version','mark_price','limit_price_quotes','entry_economics','now','initial_equity','estimated_cost_rate','slippage_bps','observed_execution_fee_evidence'])):
            return projected
        view=build(logical.get('market_snapshot',{}),target,target.get('ma_structure',{}))
        if view['status']!='AVAILABLE':
            return projected
        original = deepcopy(logical)
        target['entry_geometry'] = view
        candidate,_=project_model_input(logical,allow_gap_rescue=True)
        if len(json.dumps(candidate,ensure_ascii=False,separators=(',',':')).encode())>INPUT_LIMIT:
            return projected
        restored = restore_model_input(candidate)
        restored['contract_context'].pop('entry_geometry',None)
        return candidate if restored==original else projected
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return projected
