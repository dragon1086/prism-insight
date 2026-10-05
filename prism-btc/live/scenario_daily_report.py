"""Read-only daily view of the MAIN demo scenario ledger; never sends/orders."""
from datetime import datetime
from collections import Counter
from decimal import Decimal
import json
import re
import time
import unicodedata
from zoneinfo import ZoneInfo

from live.scenario_control import read_control, existing_account_bindings

KST = ZoneInfo('Asia/Seoul')


def number(value):
    if isinstance(value, bool):
        raise ValueError('invalid_number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('invalid_number')
    return result


def stamp(value):
    return datetime.fromtimestamp(float(number(value)), KST).strftime('%m/%d %H:%M KST')


def unavailable(now=None):
    return f'📊 BTC 데모 시나리오 요약\n🕐 {stamp(time.time() if now is None else now)}\n\n⚠️ 운영 자료 확인 필요'


def _account(conn, now, activated):
    row = conn.execute("SELECT captured_at,body FROM llm_scenario_broker_evidence WHERE kind='account' ORDER BY rowid DESC LIMIT 1").fetchone()
    if not row:
        raise ValueError('account_missing')
    observed = json.loads(row[1])
    captured = number(observed['captured_at'])
    stored = number(row[0])
    if not (activated <= captured <= now and 0 <= now-captured <= 180
            and activated <= stored <= now and 0 <= now-stored <= 180 and abs(stored-captured) <= 10):
        raise ValueError('account_stale')
    equity, position = number(observed['equity']), observed['position']
    size = number(position['size'])
    if (equity <= 0 or size < 0 or position.get('symbol') != 'BTCUSDT'
            or position.get('positionIdx') != 0 or number(position['leverage']) != 10
            or observed.get('exchange_flat') is not (size == 0)):
        raise ValueError('account_shape')
    orders = observed['open_orders']
    if not isinstance(orders, list):
        raise ValueError('orders_unknown')
    lines = [f'💰 순자산 {equity:,.2f} USDT', f'계좌 기준 {stamp(captured)}']
    if not size:
        lines.append('📦 보유 없음')
    else:
        if position.get('side') not in {'Buy', 'Sell'}:
            raise ValueError('position_side')
        average = number(position['avgPrice'])
        if average <= 0:
            raise ValueError('average_missing')
        lines.append(f'📦 {"롱" if position["side"] == "Buy" else "숏"} {size.normalize():f} BTC · 평단 {average:,.2f}')
    valid_orders = all(isinstance(order,dict) and type(order.get('reduceOnly')) is bool
        and order.get('symbol') == 'BTCUSDT' and type(order.get('positionIdx')) is int and order['positionIdx'] == 0
        and isinstance(order.get('orderId'),str) and bool(order['orderId'])
        and order.get('side') in {'Buy','Sell'}
        and order.get('orderStatus') in {'New','PartiallyFilled','Untriggered'} for order in orders)
    if not valid_orders or observed.get('legacy_fenced') is not False:
        lines.append('⚠️ 진입 대기·보호주문 확인 필요')
        return lines
    pending = [order for order in orders if order['reduceOnly'] is False]
    lines.append(f'진입 대기 {len(pending)}건')
    if size:
        opposite = 'Sell' if position['side'] == 'Buy' else 'Buy'
        exits = [order for order in orders if order.get('reduceOnly') is True
                 and order.get('symbol') == 'BTCUSDT' and order.get('positionIdx') == 0
                 and order.get('side') == opposite]
        stop = number(position.get('stopLoss') or 0)
        protected = any(order.get('stopOrderType') == 'StopLoss' and order.get('triggerBy') == 'MarkPrice'
                        and order.get('orderStatus') == 'Untriggered' and order.get('orderType') == 'Market'
                        and number(order.get('triggerPrice') or 0) == stop > 0
                        and number(order.get('cumExecQty',0)) == 0
                        and size <= number(order.get('leavesQty',order.get('qty',0)))
                        <= number(order.get('qty') or 0) for order in exits)
        lines.append(f'🛡 SL {stop:,.2f} · 남은 전량' if protected else '⚠️ 보호주문 확인 필요')
        targets = [order for order in exits if order.get('orderType') == 'Limit'
                   and order.get('orderStatus') in {'New','PartiallyFilled'}
                   and not order.get('stopOrderType') and number(order.get('leavesQty') or 0) > 0
                   and number(order.get('price') or 0) > 0]
        targets.sort(key=lambda order: number(order['price']), reverse=position['side'] == 'Sell')
        if targets:
            lines.append('🎯 TP ' + ' / '.join(f'{number(order["price"]):,.2f}' for order in targets[:2])
                         + (f' 외 {len(targets)-2}건' if len(targets)>2 else ''))
        else:
            lines.append('🎯 고정 TP 없음')
    return lines


def _settlements(conn):
    rows = conn.execute('SELECT scenario_id,evidence FROM llm_scenario_settlements ORDER BY rowid DESC LIMIT 10001').fetchall()
    if len(rows) > 10000:
        raise ValueError('settlement_bound')
    parsed, owners, bad, values = [], Counter(), 0, []
    for sid, raw in rows:
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = None
        parsed.append((sid,value))
        if isinstance(value,dict) and isinstance(value.get('execution_ids'),list):
            owners.update(item for item in value['execution_ids'] if isinstance(item,str) and item)
    for sid, value in parsed:
        try:
            if not isinstance(sid,str) or not sid or value['scenario_id'] != sid:
                raise ValueError('settlement_identity')
            if any(value.get(flag) is not True for flag in ('flat_confirmed','orders_terminal','executions_complete','fees_complete','funding_complete')):
                raise ValueError('settlement_unconfirmed')
            ids = value['execution_ids']
            if (not isinstance(ids,list) or any(not isinstance(item,str) or not item for item in ids)
                or len(set(ids)) != len(ids) or any(owners[item] != 1 for item in ids)):
                raise ValueError('execution_identity')
            gross, fees, funding, net = (number(value[key]) for key in ('gross_pnl','fees','funding_net','net_pnl'))
            if fees < 0 or abs(net-(gross-fees+funding)) > Decimal('.000001'):
                raise ValueError('settlement_math')
            if not ids:
                if value.get('no_fills_confirmed') is not True or any((gross,fees,funding,net)):
                    raise ValueError('unfilled_not_proven')
                continue
            if value.get('no_fills_confirmed') is True:
                raise ValueError('contradictory_fills')
            values.append(net)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            bad += 1
    if not values and bad:
        return ['💵 정산 자료 미확인']
    lines = [f'💵 확인된 종료 {len(values)}건 · 합계 {sum(values,Decimal(0)):+,.2f} USDT']
    if values:
        lines.append('최근 완료: ' + ' / '.join(f'{net:+,.2f}' for net in values[:3]) + ' USDT')
    lines.append('시나리오 전체 누적 · 진행 중 손익 제외')
    if bad:
        lines.append('⚠️ 정산 자료 일부 미확인')
    return lines


def _safe_reason(value):
    if not isinstance(value,str):
        return ''
    value = re.sub(r'https?://\S+', '', value)
    value = ''.join(char for char in value if not unicodedata.category(char).startswith('C')
                    and char not in '*_[]()`~<>#\\')
    value = ' '.join(value.split())
    if len(value) <= 100:
        return value
    prefix = value[:99]
    if ' ' in prefix:
        prefix = prefix.rsplit(' ', 1)[0]
    return prefix.rstrip() + '…'


def _decision(conn, now):
    row = conn.execute('SELECT slot,proposal,outcome FROM llm_scenario_decisions ORDER BY slot DESC LIMIT 1').fetchone()
    if not row:
        return ['🧠 판단 자료 없음']
    when = number(row[0])*300
    if when > now:
        raise ValueError('future_decision')
    label = '최근 판단' if now-when <= 900 else '지난 판단 · 오래된 자료'
    outcome = json.loads(row[2]) if row[2] else {}
    status = outcome.get('status')
    names = dict(wait='대기',intent_pending='당시 주문 확인 대기 · 당시 체결 미확정',blocked='판단 보류 · 확인 필요',
                 stale_proposal='판단 보류 · 입력 변경',lock_busy='판단 보류 · 보호 점검 중',
                 fenced='판단 보류 · 계좌 확인 필요',execution_disabled='신규 실행 비활성')
    lines = [f'🧠 {label} ({stamp(when)})', names.get(status,'판단 결과 미확인')]
    proposal = json.loads(row[1]) if row[1] else {}
    if ((status == 'wait' and proposal.get('action') == 'WAIT') or
            (status == 'intent_pending' and proposal.get('action') in {'OPEN','ADJUST','EXIT','WAIT'})):
        reason = _safe_reason(proposal.get('rationale'))
        if reason:
            lines.append(reason)
    return lines


def build_report(conn, *, now=None):
    now = number(time.time() if now is None else now)
    try:
        control = read_control(conn)
        main, _ = existing_account_bindings(conn)
        if not control or control['main_uid'] != main:
            raise ValueError('binding_unconfirmed')
    except Exception:
        return unavailable(now)
    state = {'active':'운영 중','paused':'일시 중지','transition':'전환 점검 중'}[control['state']]
    lines = [f'📊 BTC 데모 시나리오 요약 · {state}', f'🕐 {stamp(now)}', '']
    try:
        activated = number(control['started_at'] if control['state']=='transition' else control['activated_at'])
        lines.extend(_account(conn,now,activated))
    except Exception:
        lines.append('💰 계좌 자료 미확인 · 현재 잔량/보호 확인 필요')
    lines.append('')
    try:
        lines.extend(_settlements(conn))
    except Exception:
        lines.append('💵 정산 자료 미확인')
    lines.append('')
    try:
        lines.extend(_decision(conn,now))
    except Exception:
        lines.append('🧠 판단 자료 미확인')
    lines.extend(['', '※ 데모 기록이며 새 정책의 수익성 입증은 아닙니다.'])
    return '\n'.join(lines)
