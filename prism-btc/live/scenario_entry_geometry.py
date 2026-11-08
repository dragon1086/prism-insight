"""Bounded pre-DRAFT arithmetic, never a classifier, plan or order authority."""
from copy import deepcopy
from decimal import Decimal
import json
import math

from core.llm_scenario import risk_snapshot
from core.scenario_limit_prices import possible_entry_price_anchor
from live.scenario_fee_projection import fee_only_projection
from live.scenario_entry_economics import assess
from live.scenario_explanation import source_from_structure

MAX_BYTES = 4096
MAX_ROWS = 12
INPUT_LIMIT = 100_000


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('missing_or_nonfinite')
    return Decimal(str(value))


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
        confirmed=snapshot.get('timeframes',{}).get('30m',{}).get('confirmed',{})
        atr = confirmed.get('atr14_risk_only')
        opened = confirmed.get('open_time_ms')
        atr=(_number(atr) if confirmed.get('is_confirmed') is True and type(opened) is int
             and opened+1800000==int(asof)//1800000*1800000
             and type(atr) in (int,float) and math.isfinite(atr) and atr>0 else None)
        rows=[]
        for side,entry in [('LONG',ask),('SHORT',bid)]:
            sign=Decimal(1 if side=='LONG' else -1)
            adverse=sorted((p for p in levels if sign*(entry-p)>0),key=lambda p:abs(entry-p))
            favorable=sorted((p for p in levels if sign*(p-entry)>0),key=lambda p:abs(entry-p))
            outer_stop=sorted((p for p in extremes if sign*(entry-p)>0),key=lambda p:abs(entry-p))
            outer_target=sorted((p for p in extremes if sign*(p-entry)>0),key=lambda p:abs(entry-p))
            stops=list(dict.fromkeys(adverse[:1]+outer_stop[-1:]))
            targets=list(dict.fromkeys(favorable[:1]+outer_target[-1:]))
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
                        rows.append([side,kind,float(raw_entry),float(e),levels[stop],float(stop),levels[target],float(target),float(abs(e-stop)/atr) if atr else None,result['ratio'],result['conditional_reward'],result['full_stop_loss'],basis])
        view={'version':1,'status':'AVAILABLE' if rows else 'UNAVAILABLE','snapshot_as_of_ms':int(asof),'quote_captured_at':float(stamp),
              'columns':['side','entry_kind','raw_entry_reference','worst_entry_bound','stop_ref','stop_level','target_ref','target_level','stop_distance_atr30','single_target_ratio','reward_per_btc','loss_per_btc','cost_basis'],
              'rows':rows[:MAX_ROWS],
              'meaning':'Hypothetical full-size single-target geometry in USDT/BTC, ratio dimensionless. Not orders, ladder payoff, current risk, support strength or win probability. Nearest levels and outer observed primary bar extremes, not strength rankings or exhaustive. Raw levels precede tick buffers. Worst entry uses the existing static pricing anchor, not promised repricing/fill. LEVEL_LIMIT requires evidence and fill; no expiry extension. FEE=historical taker assumption excluding future funding/slippage; RESERVE=existing host cost stress, not observed fees. Final host validates actual plan.'}
        return view if len(json.dumps(view,ensure_ascii=False).encode())<=MAX_BYTES else unavailable
    except (KeyError,TypeError,ValueError,ArithmeticError,OverflowError):
        return unavailable


def project(projected, snapshot, context):
    """No eviction of original facts, history, schema or metadata to add this view."""
    from live.scenario_model_input import restore_model_input,project_model_input
    try:
        logical = restore_model_input(projected)
        target = logical['contract_context']
        if ('entry_geometry' in target or target.get('scenario_id',True) is not None
                or any(target.get(k)!=context.get(k) for k in ['positions','pending_entries','mark_price','limit_price_quotes','entry_economics','now','initial_equity','estimated_cost_rate','slippage_bps','observed_execution_fee_evidence'])):
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
