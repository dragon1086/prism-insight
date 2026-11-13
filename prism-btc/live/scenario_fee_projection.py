"""Optional historical-fee illustrations, never risk or execution authority."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math

DEMO_REFERENCE_BASIS = 'PUBLISHED_NONVIP_TAKER_AND_5BPS_SLIPPAGE_NOT_ACCOUNT_RATE'


def demo_entry_fee_reference():
    """Explicit operator opt-in estimate, never a fresh or observed account rate."""
    return dict(version=1, environment='demo', taker_fee_rate=.00055, slippage_rate_per_fill=.0005,
        basis=DEMO_REFERENCE_BASIS, current_account_rate_verified=False,
        source='https://www.bybit.com/en/help-center/article/Trading-Fee-Structure')


def _reference_rate(reference):
    if (not isinstance(reference, dict) or type(reference.get('version')) is not int
            or reference.get('current_account_rate_verified') is not False
            or reference != demo_entry_fee_reference()):
        raise ValueError('invalid_demo_fee_reference')
    return _d(reference['taker_fee_rate'])


def _d(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_number')
    result=Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite_number')
    return result


def observed_fee_evidence(evidence, accounting, *, now):
    """Called only after complete owned-execution/transaction reconciliation.

    Metadata failure is optional: never invalidate settlement or protection.
    Missing maker identity is not inferred; rebates/zero rates are not estimates.
    """
    unknown=dict(status='UNKNOWN', reason='no_usable_verified_execution_fees')
    try:
        if accounting.get('status')!='confirmed' or accounting.get('accounting_complete') is not True:
            return unknown
        current=_d(now)
        groups={}
        sources=[]
        for trade in evidence.get('trades', []):
            raw,txn=trade['raw_execution'],trade['raw_transaction']
            maker=raw.get('isMaker')
            if type(maker) is not bool:
                continue
            qty,price,fee,stamp=map(_d,(raw.get('execQty'),raw.get('execPrice'),raw.get('execFee'),raw.get('execTime')))
            if (raw.get('symbol')!='BTCUSDT' or txn.get('symbol')!='BTCUSDT' or
                    raw.get('execType')!='Trade' or txn.get('type')!='TRADE' or
                    txn.get('currency')!='USDT' or not raw.get('execId') or
                    raw.get('execId')!=txn.get('tradeId') or raw.get('orderId')!=txn.get('orderId') or
                    fee!=_d(txn.get('fee')) or min(qty,price,fee,stamp)<=0 or
                    not 0<=current-stamp/1000<=86400):
                continue
            rate=fee/(qty*price)
            if not 0<rate<=Decimal('.01'):
                continue
            kind='MAKER' if maker else 'TAKER'
            groups.setdefault(kind,[]).append((rate,stamp/1000))
            sources.append((raw['execId'],raw['orderId'],str(qty),str(price),str(fee),str(stamp),maker))
        if not groups:
            return unknown
        return dict(status='AVAILABLE',basis='HISTORICAL_VERIFIED_EXECUTIONS',
            current_fee_tier_verified=False, future_rate_guaranteed=False,
            rates={kind:dict(count=len(rows),minimum=float(min(r for r,_ in rows)),
                maximum=float(max(r for r,_ in rows)),earliest_at=float(min(t for _,t in rows)),
                latest_at=float(max(t for _,t in rows)))
                for kind,rows in groups.items()},
            source_sha256=hashlib.sha256(json.dumps(sorted(sources),separators=(',',':')).encode()).hexdigest())
    except (KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
        return unknown


def verified_taker_rate(evidence, now):
    """Validate original execution timestamps, never refresh their age."""
    if not isinstance(evidence,dict) or evidence.get('status')!='AVAILABLE' or evidence.get('basis')!='HISTORICAL_VERIFIED_EXECUTIONS':
        raise ValueError('unverified_fee_evidence')
    source=evidence.get('source_sha256')
    if not isinstance(source,str) or len(source)!=64 or any(c not in '0123456789abcdef' for c in source):
        raise ValueError('invalid_fee_source')
    row=evidence.get('rates',{}).get('TAKER',{})
    rate,minimum,stamp,current=map(_d,(row.get('maximum'),row.get('minimum'),row.get('latest_at'),now))
    earliest=_d(row.get('earliest_at'))
    if (type(row.get('count')) is not int or row['count']<=0 or
            not 0<minimum<=rate<=Decimal('.01') or not 0<earliest<=stamp<=current or
            not 0<=current-earliest<=86400):
        raise ValueError('invalid_or_stale_taker_fee')
    return rate


def fee_only_projection(*, side, entry_price, exit_price, quantity, evidence, now,
                        new_entry, recorded_net=None, entry_fee_reference=None):
    """TAKER historical upper observed rate illustration, not net-profit guarantee.

    A new leg includes its attributable entry fee. Held lots have paid entry fees
    in recorded accounting already. Only a full remaining exit may pass recorded_net.
    Unknown future funding/slippage are excluded, never filled with zero.
    """
    unknown=dict(status='UNKNOWN',reason='missing_or_stale_taker_fee_evidence')
    try:
        basis='HISTORICAL_TAKER_MAX_ASSUMPTION_NOT_CURRENT_TIER'
        use_reference = new_entry is True and entry_fee_reference is not None
        reference_rate = _reference_rate(entry_fee_reference) if use_reference else None
        try:
            rate=verified_taker_rate(evidence, now)
        except (AttributeError,KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
            if new_entry is not True or recorded_net is not None:
                raise ValueError('fee_reference_not_for_holdings')
            if reference_rate is None:
                raise ValueError('missing_entry_fee_reference')
            rate=reference_rate
            basis='PUBLISHED_NONVIP_TAKER_ASSUMPTION'
        fee_basis=basis
        if use_reference:
            basis=DEMO_REFERENCE_BASIS
        entry,exit_,qty=map(_d,(entry_price,exit_price,quantity))
        if side not in ('LONG','SHORT') or min(entry,exit_,qty)<=0 or type(new_entry) is not bool:
            return dict(status='UNKNOWN',reason='invalid_projection_inputs')
        gross=(exit_-entry)*qty*(1 if side=='LONG' else -1)
        entry_fee=entry*qty*rate if new_entry else Decimal(0)
        exit_fee=exit_*qty*rate
        recorded=_d(recorded_net) if recorded_net is not None else Decimal(0)
        if new_entry and recorded_net is not None:
            return dict(status='UNKNOWN',reason='mixed_accounting_scope')
        values=dict(estimated_gross_pnl=gross,assumed_entry_fee=entry_fee,
            assumed_exit_fee=exit_fee,estimated_net_before_unmodelled_costs=recorded+gross-entry_fee-exit_fee)
        if use_reference:
            slip = _d(entry_fee_reference['slippage_rate_per_fill'])
            values['assumed_entry_slippage'] = entry * qty * slip
            values['assumed_exit_slippage'] = exit_ * qty * slip
            values['estimated_net_before_unmodelled_costs'] -= (entry + exit_) * qty * slip
        values={key:float(value) for key,value in values.items()}
        if not all(math.isfinite(value) for value in values.values()):
            return dict(status='UNKNOWN',reason='nonfinite_projection')
        return dict(status='AVAILABLE',scope='WHOLE_SCENARIO' if recorded_net is not None else 'LEG_ONLY',
            entry_fee_treatment='ATTRIBUTABLE_NEW_ENTRY' if new_entry else 'ALREADY_RECORDED_NOT_CHARGED_AGAIN',
            basis=basis,
            **(dict(fee_rate_basis=fee_basis, future_slippage='ASSUMED_5BPS_EACH_FILL') if use_reference else dict(future_slippage='UNKNOWN_EXCLUDED')),
            assumed_fee_rate=float(rate),future_funding='UNKNOWN_EXCLUDED',
            estimates_not_guaranteed=True,**values)
    except (AttributeError,KeyError,TypeError,ValueError,InvalidOperation,OverflowError):
        return unknown
