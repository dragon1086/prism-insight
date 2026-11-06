"""Explicit administrative WAIT/EXIT authority, never exchange/settlement evidence."""
from copy import deepcopy
import hashlib
import json

PURPOSE = 'ADMIN_AUTHORIZATION_NOT_EXCHANGE_EVIDENCE'
KIND = 'management_quarantine_v1'
EXIT_KIND = 'management_quarantine_exit_v1'
ENDPOINT = 'https://api-demo.bybit.com'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _require(ok, reason):
    if not ok:
        raise ValueError('management_quarantine_' + reason)


def account_observation_fingerprint(observed):
    fields = ('captured_at', 'account_version', 'mark_price', 'position', 'open_orders', 'legacy_fenced')
    _require(isinstance(observed, dict) and all(key in observed for key in fields), 'observation_untrusted')
    return digest({key: observed[key] for key in fields})


def inspect_unsettled_entry_evidence(*, child, order, observed, active, children, pending,
                                    legacy_local, now, order_captured_at, maximum_quantity,
                                    identity_verified, endpoint):
    """Classify one aggregate match; this does not authorize actions or prove fills."""
    from decimal import Decimal, InvalidOperation

    def number(value):
        try:
            if isinstance(value, bool):
                raise InvalidOperation
            value = Decimal(str(value))
            if not value.is_finite():
                raise InvalidOperation
            return value
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError('management_quarantine_invalid_number') from None

    _require(all(isinstance(x, dict) for x in (child, order, observed, active)), 'evidence_shape')
    _require(identity_verified is True and endpoint == ENDPOINT, 'identity_untrusted')
    _require(isinstance(children, list) and children == [child] and isinstance(pending, list)
             and len(pending) == 1 and legacy_local is False, 'other_exposure_unknown')
    _require(child.get('kind') == 'entry' and child.get('status') == 'UNKNOWN'
             and child.get('evidence') is None and isinstance(child.get('order_id'), str)
             and bool(child['order_id']) and isinstance(child.get('link_id'), str)
             and bool(child['link_id']) and isinstance(child.get('intent_id'), str)
             and bool(child['intent_id']) and isinstance(child.get('scenario_id'), str)
             and bool(child['scenario_id']) and isinstance(child.get('request'), dict), 'child_untrusted')
    intent, request = pending[0], child['request']
    _require(isinstance(intent, dict) and intent.get('id') == child.get('intent_id')
             and intent.get('scenario_id') == child.get('scenario_id') and intent.get('status') == 'PENDING'
             and intent.get('action') == 'OPEN' and active.get('scenario_id') == child.get('scenario_id'), 'intent_changed')
    side = active.get('side')
    _require(side in {'LONG', 'SHORT'}, 'side_unknown')
    entry_side, exit_side = ('Buy', 'Sell') if side == 'LONG' else ('Sell', 'Buy')
    _require(request.get('symbol') == order.get('symbol') == 'BTCUSDT'
             and request.get('category') == 'linear' and request.get('triggerPrice') is None
             and type(request.get('positionIdx')) is int and request['positionIdx'] == 0
             and type(order.get('positionIdx')) is int and order['positionIdx'] == 0
             and request.get('side') == order.get('side') == entry_side
             and request.get('orderType') == order.get('orderType') == 'Limit'
             and request.get('timeInForce') == order.get('timeInForce') == 'GTC'
             and request.get('tpslMode') == 'Full' and request.get('slOrderType') == 'Market'
             and request.get('reduceOnly') is False and order.get('reduceOnly') is False
             and request.get('slTriggerBy') == order.get('slTriggerBy') == 'MarkPrice'
             and order.get('orderStatus') == 'Filled'
             and order.get('orderId') == child['order_id']
             and order.get('orderLinkId') == child['link_id'], 'order_changed')
    at, now_value = number(order_captured_at), number(now)
    _require(0 <= now_value-at <= 10 and 0 <= now_value-number(observed.get('captured_at')) <= 10, 'stale_evidence')
    qty, avg, stop = number(order.get('qty')), number(order.get('avgPrice')), number(active.get('hard_stop'))
    mark = number(observed.get('mark_price'))
    _require(qty > 0 and avg > 0 and stop > 0 and qty <= number(maximum_quantity)
             and qty == number(request.get('qty')) == number(order.get('cumExecQty'))
             and number(order.get('leavesQty')) == 0 and number(order.get('price')) > 0
             and number(order.get('price')) == number(request.get('price'))
             and stop == number(order.get('stopLoss')) == number(request.get('stopLoss'))
             and (stop < min(avg, mark) if side == 'LONG' else stop > max(avg, mark)), 'aggregate_changed')
    position, orders = observed.get('position'), observed.get('open_orders')
    _require(isinstance(position, dict) and isinstance(orders, list) and len(orders) == 1, 'position_unknown')
    _require(position.get('symbol') == 'BTCUSDT' and type(position.get('positionIdx')) is int
             and position['positionIdx'] == 0 and position.get('side') == entry_side
             and number(position.get('size')) == qty and number(position.get('avgPrice')) == avg
             and number(position.get('stopLoss')) == stop, 'position_changed')
    native = orders[0]
    _require(isinstance(native, dict) and isinstance(native.get('orderId'), str)
             and bool(native['orderId']) and native['orderId'] != child['order_id']
             and native.get('symbol') == 'BTCUSDT' and type(native.get('positionIdx')) is int
             and native['positionIdx'] == 0 and native.get('side') == exit_side
             and native.get('reduceOnly') is True and native.get('parentOrderLinkId') == child['link_id']
             and native.get('stopOrderType') == 'StopLoss' and native.get('triggerBy') == 'MarkPrice'
             and native.get('orderType') == 'Market' and native.get('orderStatus') == 'Untriggered'
             and number(native.get('triggerPrice')) == stop and number(native.get('qty')) == qty
             and number(native.get('leavesQty')) == qty, 'protection_changed')
    return dict(status='AGGREGATE_MATCH', accounting='UNKNOWN', execution_evidence='UNKNOWN', authorization=False)


def _entry_unresolved_rows(conn):
    from live import scenario_incident_epoch
    result = []
    for ident in scenario_incident_epoch.unresolved(conn):
        row = conn.execute('SELECT scenario_id,payload,status FROM llm_scenario_intents WHERE id=?',
                           (ident,)).fetchone()
        _require(row is not None, 'pending_disappeared')
        payload = json.loads(row[1])
        _require(isinstance(payload, dict), 'pending_payload_invalid')
        result.append(dict(id=ident, scenario_id=row[0], status=row[2], action=payload.get('action'),
                           payload_sha256=digest(payload)))
    return result


def probe_unsettled_entry(broker, *, maximum_quantity, self_exit=None, _observed=None):
    """Gather exact demo-account evidence; never grant authority or create an order."""
    from live.exchange_snapshot import read_complete
    from live.scenario_broker import read_evidence_pages
    from live.scenario_control import read_control
    from live.scenario_entry_contract import registered_contract
    from live.shared_entry_coordinator import mutation_lock

    def check_contract(active):
        _require(registered_contract(broker.conn, active), 'contract_changed')

    with mutation_lock(broker.conn):
        control = read_control(broker.conn)
        _require(broker.environment == 'demo' and broker.session.endpoint == ENDPOINT
                 and control and control.get('state') == 'active'
                 and control.get('main_uid') == broker.expected_main_uid, 'identity_untrusted')
        broker._identity()
        active, children, unresolved = broker._active(), broker.children(), _entry_unresolved_rows(broker.conn)
        pending = unresolved
        if self_exit is not None:
            event = _approval(broker)
            _require(event and event['body'].get('version') == 2, 'entry_exit_authority_missing')
            _pending_matches(broker.conn, event['body'], self_exit)
            pending = [row for row in unresolved if row['id'] != self_exit]
        _require(isinstance(active, dict) and isinstance(children, list) and len(children) == 1,
                 'other_exposure_unknown')
        check_contract(active)
        child = children[0]
        _require(isinstance(child, dict) and child.get('kind') == 'entry' and child.get('link_id'), 'child_untrusted')
        result = read_complete(broker._call, 'get_open_orders', max_pages=3, category='linear', symbol='BTCUSDT',
                               orderLinkId=child['link_id'])
        _require(result is not None, 'order_query_unknown')
        orders = result['result']['list']
        if not orders:
            orders = read_evidence_pages(broker._call, 'get_order_history', max_pages=3, category='linear', symbol='BTCUSDT',
                                        orderLinkId=child['link_id'])
        _require(len(orders) == 1, 'order_query_ambiguous')
        order_captured_at = broker.clock()
        if _observed is None:
            observed = broker.capture_account()
        else:
            observed = _observed
            _require(getattr(broker, '_last_account_observation_fingerprint', None)
                     == account_observation_fingerprint(observed), 'observation_not_attested')
        _require(active == broker._active() and children == broker.children()
                 and unresolved == _entry_unresolved_rows(broker.conn),
                 'state_changed_during_probe')
        check_contract(active)
        proof = inspect_unsettled_entry_evidence(child=child, order=orders[0], observed=observed,
            active=active, children=children, pending=pending, legacy_local=broker._legacy_local(),
            now=broker.clock(), order_captured_at=order_captured_at, maximum_quantity=maximum_quantity,
            identity_verified=True, endpoint=broker.session.endpoint)
        _require(isinstance(observed.get('account_version'), str) and bool(observed['account_version']),
                 'account_version_missing')
        return dict(proof, account_version=observed['account_version'])


def _entry_active_anchor(active):
    return {key: active.get(key) for key in ('scenario_id', 'side', 'hard_stop', 'entry_contract')}


def prepare_unsettled_entry_authority(broker, *, approval_id, reason, expires_at, maximum_quantity):
    """Build a reviewable proposal only; it is not registered or authorized."""
    import math
    from live.scenario_control import read_control
    from live.shared_entry_coordinator import mutation_lock

    with mutation_lock(broker.conn):
        now = broker.clock()
        _require(isinstance(approval_id, str) and 0 < len(approval_id.strip()) <= 128
                 and isinstance(reason, str) and 0 < len(reason.strip()) <= 240, 'approval_reason_missing')
        _require(type(now) in (int, float) and math.isfinite(now) and now > 0
                 and type(expires_at) in (int, float) and math.isfinite(expires_at)
                 and now < expires_at <= now + 86400, 'approval_window')
        before = dict(active=broker._active(), children=broker.children(),
                      pending=_entry_unresolved_rows(broker.conn), control=read_control(broker.conn))
        proof = probe_unsettled_entry(broker, maximum_quantity=maximum_quantity)
        after = dict(active=broker._active(), children=broker.children(),
                     pending=_entry_unresolved_rows(broker.conn), control=read_control(broker.conn))
        _require(before == after and proof.get('status') == 'AGGREGATE_MATCH'
                 and proof.get('authorization') is False, 'state_changed_during_preview')
        active, child = before['active'], before['children'][0]
        body = dict(version=2, operation='PROPOSE', approval_id=approval_id, reason=reason,
                    purpose=PURPOSE, issued_at=now, expires_at=expires_at,
                    main_uid=broker.expected_main_uid, endpoint=broker.session.endpoint,
                    scenario_id=active['scenario_id'], contract_id=active['entry_contract']['contract_id'],
                    maximum_quantity=str(maximum_quantity), child_sha256=digest(child),
                    unknown_link_id=child['link_id'], unknown_order_id=child['order_id'],
                    unknown_request_sha256=digest(child['request']),
                    pending_sha256=digest(before['pending']), active_sha256=digest(_entry_active_anchor(active)),
                    account_version=proof['account_version'],
                    allowed_actions=['WAIT', 'EXIT'])
        return dict(status='PROPOSED_NOT_AUTHORIZED', authorization=False, body=body, sha256=digest(body))


def authorize_unsettled_entry_management(broker, preview):
    """Register a revalidated administrative scope; never submit an order."""
    from live.shared_entry_coordinator import mutation_lock

    with mutation_lock(broker.conn):
        _require(isinstance(preview, dict) and preview.get('status') == 'PROPOSED_NOT_AUTHORIZED'
                 and preview.get('authorization') is False and isinstance(preview.get('body'), dict)
                 and preview.get('sha256') == digest(preview['body']), 'preview_invalid')
        body = preview['body']
        _require(body.get('version') == 2 and body.get('operation') == 'PROPOSE'
                 and body.get('purpose') == PURPOSE and body.get('allowed_actions') == ['WAIT', 'EXIT'], 'preview_scope')
        events = _events(broker.conn, KIND)
        for event in events:
            _require(isinstance(event, dict) and isinstance(event.get('body'), dict)
                     and event.get('sha256') == digest(event['body']), 'approval_corrupt')
        _require(not any(isinstance(e, dict) and isinstance(e.get('body'), dict)
                         and e['body'].get('approval_id') == body.get('approval_id') for e in events), 'approval_id_reused')
        latest = _latest(broker)
        _require(not latest or latest['body'].get('operation') == 'REVOKE', 'approval_already_active')
        fresh = prepare_unsettled_entry_authority(broker, approval_id=body.get('approval_id'),
            reason=body.get('reason'), expires_at=body.get('expires_at'), maximum_quantity=body.get('maximum_quantity'))
        stable = lambda value: {k: v for k, v in value.items() if k != 'issued_at'}
        _require(stable(fresh['body']) == stable(body), 'preview_stale')
        approved = dict(body, operation='AUTHORIZE', issued_at=broker.clock(), preview_sha256=preview['sha256'])
        event = dict(body=approved, sha256=digest(approved))
        _append(broker.conn, KIND, event, approved['issued_at'])
        broker.conn.commit()
        return _receipt(event)


def _events(conn, kind):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_broker_evidence'").fetchone():
        return []
    return [json.loads(r[0]) for r in conn.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=? ORDER BY rowid', (kind,))]


def _append(conn, kind, body, at):
    conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                 (kind, at, json.dumps(body, sort_keys=True, allow_nan=False)))


def _latest(broker):
    events = _events(broker.conn, KIND)
    if not events:
        return None
    event = events[-1]
    _require(event.get('sha256') == digest(event.get('body')), 'approval_corrupt')
    return event


def unsettled_entry_reconciliation_scope(broker, active):
    """Keep an entry quarantine read-only until its explicit revocation."""
    event = _latest(broker)
    if not event:
        return None
    body = event['body']
    if (body.get('operation') != 'AUTHORIZE' or body.get('version') != 2
            or not active or body.get('scenario_id') != active.get('scenario_id')):
        return None
    _require(body.get('main_uid') == broker.expected_main_uid and body.get('endpoint') == ENDPOINT
             and isinstance(body.get('unknown_link_id'), str) and bool(body['unknown_link_id']), 'identity_changed')
    return body['unknown_link_id']


def _approval(broker):
    event = _latest(broker)
    if not event:
        return None
    body = event['body']
    if body.get('operation') == 'REVOKE':
        return None
    _require(body.get('operation') == 'AUTHORIZE' and body.get('version') in {1, 2}
             and body.get('purpose') == PURPOSE and body.get('allowed_actions') == ['WAIT', 'EXIT'], 'approval_shape')
    if body['version'] == 2:
        _require(isinstance(body.get('preview_sha256'), str) and len(body['preview_sha256']) == 64,
                 'preview_binding_invalid')
    _require(body['issued_at'] < body['expires_at'] <= body['issued_at'] + 86400, 'approval_window')
    if not body['issued_at'] <= broker.clock() < body['expires_at']:
        return None
    active = broker._active()
    if not active or active['scenario_id'] != body['scenario_id']:
        return None
    return event


def _receipt(event):
    body = event['body']
    receipt = dict(status='AUTHORIZED', approval_id=body['approval_id'], approval_sha256=event['sha256'],
        scenario_id=body['scenario_id'], contract_id=body['contract_id'], expires_at=body['expires_at'],
        allowed_actions=['WAIT', 'EXIT'], purpose=PURPOSE)
    if body.get('version') == 2:
        receipt.update(scope='ENTRY_AGGREGATE_ONLY', execution_evidence='UNKNOWN', new_risk='BLOCKED',
                       account_version=body['account_version'])
    return receipt


def _pending(conn):
    return [dict(id=i, scenario_id=s, payload=json.loads(p), status=t) for i,s,p,t in conn.execute(
        "SELECT id,scenario_id,payload,status FROM llm_scenario_intents WHERE status='PENDING' OR status IS NULL")]


def _pending_matches(conn, body, self_exit=None):
    if body.get('version') == 2:
        rows = _entry_unresolved_rows(conn)
        if self_exit is not None:
            records = [r for r in _events(conn, EXIT_KIND) if r.get('intent_id') == self_exit]
            _require(len(records) == 1, 'self_exit_receipt_missing')
            receipt = records[0]
            own = [r for r in rows if r['id'] == self_exit]
            _require(receipt.get('approval_id') == body.get('approval_id')
                     and receipt.get('approval_sha256') == digest(body)
                     and len(own) == 1 and own[0]['status'] == 'PENDING'
                     and own[0]['scenario_id'] == body.get('scenario_id') and own[0]['action'] == 'EXIT'
                     and own[0]['payload_sha256'] == receipt.get('payload_sha256'), 'self_exit_changed')
            rows = [r for r in rows if r['id'] != self_exit]
        _require(digest(rows) == body.get('pending_sha256'), 'pending_set_changed')
        return
    expected = {r['id']: r for r in body['pending_intents']}
    rows = _pending(conn)
    if self_exit:
        records = [r for r in _events(conn, EXIT_KIND) if r.get('intent_id') == self_exit]
        _require(len(records) == 1, 'self_exit_receipt_missing')
        receipt = records[0]
        _require(receipt['approval_id'] == body['approval_id'] and receipt['approval_sha256'] == digest(body), 'self_exit_approval_changed')
        own = [r for r in rows if r['id'] == self_exit]
        _require(len(own) == 1 and own[0]['status'] == 'PENDING' and own[0]['payload']['action'] == 'EXIT'
                 and digest(own[0]['payload']) == receipt['payload_sha256'], 'self_exit_changed')
        rows = [r for r in rows if r['id'] != self_exit]
    _require(len(rows) == len(expected) and {r['id'] for r in rows} == set(expected), 'pending_set_changed')
    for row in rows:
        spec = expected[row['id']]
        _require(row['status'] == 'PENDING' and row['scenario_id'] == body['scenario_id']
                 and row['payload'].get('action') == spec['action']
                 and row['payload'].get('revision') == spec['revision']
                 and digest(row['payload']) == spec['payload_sha256'], 'pending_payload_changed')


def _inspect(broker, event, observed, active, children, self_exit=None):
    from live.scenario_control import read_control
    from live.scenario_entry_contract import valid_record
    from live.scenario_execution import decimal
    body = event['body']
    if body.get('version') == 2:
        _require(broker.environment == 'demo' and broker.session.endpoint == body.get('endpoint') == ENDPOINT
                 and broker.expected_main_uid == body.get('main_uid'), 'identity_changed')
        _require(isinstance(active, dict) and active.get('scenario_id') == body.get('scenario_id')
                 and digest(_entry_active_anchor(active)) == body.get('active_sha256')
                 and isinstance(children, list) and len(children) == 1
                 and digest(children[0]) == body.get('child_sha256'), 'entry_scope_changed')
        _pending_matches(broker.conn, body, self_exit)
        _require(isinstance(observed, dict) and isinstance(observed.get('account_version'), str)
                 and bool(observed['account_version']) and 0 <= broker.clock()-observed['captured_at'] <= 10,
                 'observation_untrusted')
        proof = probe_unsettled_entry(broker, maximum_quantity=body['maximum_quantity'], self_exit=self_exit,
                                      _observed=observed)
        _require(proof.get('status') == 'AGGREGATE_MATCH' and proof.get('authorization') is False
                 and proof.get('account_version') == observed['account_version'] == body.get('account_version')
                 and _approval(broker) == event, 'entry_proof_changed')
        return _receipt(event)
    _require(broker.environment == 'demo' and broker.session.endpoint == ENDPOINT
             and body['endpoint'] == ENDPOINT and body['main_uid'] == broker.expected_main_uid, 'identity_changed')
    control = read_control(broker.conn)
    _require(control and control['main_uid'] == body['main_uid'], 'control_changed')
    _require(active and active['scenario_id'] == body['scenario_id'] and active['side'] == body['side'], 'scenario_changed')
    contract = active.get('entry_contract')
    registered = broker.conn.execute('SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?', (body['scenario_id'],)).fetchone()
    _require(valid_record(contract, body['scenario_id']) and contract['contract_id'] == body['contract_id']
             and registered and json.loads(registered[0]) == contract, 'contract_changed')
    _pending_matches(broker.conn, body, self_exit)
    _require(not broker.conn.execute("SELECT 1 FROM llm_scenario_children WHERE (scenario_id IS NULL OR scenario_id!=?) AND (status IS NULL OR status!='TERMINAL') LIMIT 1",
                                    (body['scenario_id'],)).fetchone(), 'foreign_unresolved_child')
    _require(not broker.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE (scenario_id IS NULL OR scenario_id!=?) AND (status IS NULL OR status!='TERMINAL') LIMIT 1",
                                    (body['scenario_id'],)).fetchone(), 'foreign_unresolved_intent')
    _require(not observed['legacy_fenced'] and 0 <= broker.clock() - observed['captured_at'] <= 10, 'observation_untrusted')
    size = decimal(observed['position']['size'])
    _require(0 < size <= decimal(body['maximum_quantity'])
             and observed['position']['side'] == ('Buy' if body['side'] == 'LONG' else 'Sell')
             and broker._verify_protection(observed, active['hard_stop'], active['side']), 'position_or_protection_changed')
    target = [c for c in children if c['link_id'] == body['unknown_link_id']]
    _require(len(target) == 1, 'unknown_child_missing')
    child = target[0]
    identity = {k: child[k] for k in ('intent_id', 'local_id', 'created_at')}
    reduce_ids = [r['id'] for r in body['pending_intents'] if r['action'] == 'REDUCE']
    _require(child['status'] == 'UNKNOWN' and child['order_id'] is None and child['evidence'] is None
             and child['scenario_id'] == body['scenario_id'] and child['kind'] == 'partial_sl'
             and identity == body['unknown_child_identity'] and reduce_ids == [child['intent_id']]
             and child['local_id'].startswith('reduce:')
             and digest(child['request']) == body['unknown_request_sha256'], 'unknown_child_changed')
    _require(child['request'].get('reduceOnly') is True and child['request'].get('closeOnTrigger') is True
             and child['request'].get('orderType') == 'Market' and child['request'].get('triggerBy') == 'MarkPrice'
             and child['request'].get('symbol') == 'BTCUSDT' and child['request'].get('positionIdx') == 0
             and child['request'].get('side') == ('Sell' if body['side'] == 'LONG' else 'Buy'), 'unknown_request_unsafe')
    natives = []
    for c in children:
        if c['link_id'] == child['link_id']:
            continue
        _require(c['scenario_id'] == body['scenario_id'], 'foreign_child')
        if self_exit and c['intent_id'] == self_exit and c['kind'] == 'exit':
            continue  # Existing self-EXIT is query-only; never authority for another POST.
        _require(c['evidence'] and c['status'] in {'TERMINAL', 'LIVE'}, 'other_child_unknown')
        if c['kind'] == 'native_sl' and c['status'] == 'LIVE':
            natives.append(c)
        else:
            _require(c['status'] == 'TERMINAL', 'other_live_child')
    _require(len(natives) == 1, 'native_full_stop_unconfirmed')
    return _receipt(event)


def refresh_known(broker, self_exit=None):
    """Reuse same-instance exact terminal observations; live protection stays fresh."""
    from live.scenario_execution import encoded
    event = _approval(broker)
    if not event:
        return
    if event['body'].get('version') == 2:
        return  # Version 2's proof rechecks the sole unresolved child without mutating it.
    for c in broker.children(event['body']['scenario_id']):
        if c['link_id'] != event['body']['unknown_link_id']:
            if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl':
                fingerprint = hashlib.sha256(encoded(dict(main_uid=broker.expected_main_uid,
                    endpoint=broker.session.endpoint, child=c)).encode()).hexdigest()
                if getattr(broker, '_quarantine_terminal_observations', {}).get(c['link_id']) == fingerprint:
                    continue
            broker._query_child(c)


def project(broker, observed, active, children, self_exit=None):
    event = _approval(broker)
    return _inspect(broker, event, observed, active, children, self_exit) if event else None


def projection_failure(broker, observed=None):
    """Resolved source needs explicit revocation, never implicit normal authority."""
    event = _approval(broker)
    if not event:
        return None
    body = event['body']
    rows = [c for c in broker.children(body['scenario_id']) if c['link_id'] == body['unknown_link_id']]
    if len(rows) != 1:
        return 'INVALID'
    child = rows[0]
    if body.get('version') == 2:
        if (child.get('order_id') != body.get('unknown_order_id')
                or digest(child.get('request')) != body.get('unknown_request_sha256')):
            return 'INVALID'
        if child.get('status') == 'TERMINAL' and child.get('evidence'):
            exact = broker._query_child(child)
            if (exact.get('status') == 'TERMINAL' and exact.get('order_id') == body['unknown_order_id']
                    and exact.get('evidence')):
                return 'RELEASE_REQUIRED'
        if child.get('status') == 'UNKNOWN' and child.get('evidence') is None:
            from live.scenario_execution import decimal
            if (observed and observed.get('exchange_flat') is True and observed.get('legacy_fenced') is False
                    and observed.get('open_orders') == [] and decimal(observed['position']['size']) == 0
                    and 0 <= broker.clock()-observed['captured_at'] <= 10):
                return 'FLAT_UNSETTLED'
        return 'INVALID'
    if (digest(child['request']) != body['unknown_request_sha256']
            or {k: child[k] for k in ('intent_id', 'local_id', 'created_at')} != body['unknown_child_identity']):
        return 'INVALID'
    if child['status'] == 'TERMINAL' and child['order_id'] and child['evidence']:
        exact = broker._query_child(child)
        if exact['status'] == 'TERMINAL' and exact['order_id'] == child['order_id'] and exact['evidence']:
            return 'RELEASE_REQUIRED'
    if child['status'] == 'UNKNOWN' and child['order_id'] is None and child['evidence'] is None:
        from live.scenario_execution import decimal
        if (observed and observed.get('exchange_flat') is True and observed.get('legacy_fenced') is False
                and decimal(observed['position']['size']) == 0
                and observed['position'].get('side') in {'', 'Buy' if body['side'] == 'LONG' else 'Sell'}
                and 0 <= broker.clock() - observed['captured_at'] <= 10):
            return 'FLAT_UNSETTLED'  # No authority and no settlement assertion.
        exits = [r for r in _events(broker.conn, EXIT_KIND) if r.get('approval_id') == body['approval_id']]
        if len(exits) == 1:
            try:
                _pending_matches(broker.conn, body, self_exit=exits[0]['intent_id'])
                return None  # Preserve the ordinary pending-EXIT gate, never a new model call.
            except (ValueError, KeyError, TypeError):
                return 'INVALID'
    return 'INVALID'


def active_receipt(broker, self_exit=None):
    refresh_known(broker, self_exit)
    event = _approval(broker)
    if not event:
        return None
    observed = broker.capture_account()
    return project(broker, observed, broker._active(), broker.children(event['body']['scenario_id']), self_exit)


def pending_allowed(broker, context, self_exit=None):
    receipt = context.get('management_quarantine')
    if not receipt:
        return False
    event = _approval(broker)
    _require(event and receipt == _receipt(event), 'authority_changed')
    if event['body'].get('version') == 2:
        _require(context.get('legacy_fenced') is True and context.get('protection_ok') is True
                 and context.get('new_risk_blocked') is True and context.get('accounting_status') == 'pending'
                 and context.get('account_version') == event['body'].get('account_version')
                 and type(context.get('account_captured_at')) in (int, float)
                 and 0 <= broker.clock()-context['account_captured_at'] <= 10, 'entry_context_untrusted')
    _pending_matches(broker.conn, event['body'], self_exit)
    return True


def fenced_entry_management_allowed(broker, context):
    """Only a fresh, scoped receipt can narrow an entry-exposure fence."""
    import sqlite3
    try:
        receipt = context.get('management_quarantine') if isinstance(context, dict) else None
        if (not isinstance(receipt, dict) or receipt.get('scope') != 'ENTRY_AGGREGATE_ONLY'
                or receipt.get('allowed_actions') != ['WAIT', 'EXIT']):
            return False
        return pending_allowed(broker, context) is True
    except (ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        return False


def entry_aggregate_context(context, action):
    """Classify a restricted receipt for non-authorizing review calculations."""
    if not isinstance(context, dict) or action not in {'WAIT', 'EXIT'}:
        return False
    receipt = context.get('management_quarantine')
    if not isinstance(receipt, dict):
        return False
    approval_sha = receipt.get('approval_sha256')
    return (receipt.get('status') == 'AUTHORIZED' and receipt.get('scope') == 'ENTRY_AGGREGATE_ONLY'
            and receipt.get('purpose') == PURPOSE and receipt.get('allowed_actions') == ['WAIT', 'EXIT']
            and receipt.get('execution_evidence') == 'UNKNOWN' and receipt.get('new_risk') == 'BLOCKED'
            and isinstance(receipt.get('approval_id'), str) and bool(receipt['approval_id'])
            and isinstance(approval_sha, str) and len(approval_sha) == 64
            and all(c in '0123456789abcdef' for c in approval_sha)
            and isinstance(context.get('scenario_id'), str) and bool(context['scenario_id'])
            and receipt.get('scenario_id') == context['scenario_id']
            and isinstance(context.get('account_version'), str) and bool(context['account_version'])
            and receipt.get('account_version') == context['account_version']
            and context.get('legacy_fenced') is True and context.get('protection_ok') is True
            and context.get('accounting_status') == 'pending' and context.get('new_risk_blocked') is True)


def validate_action(payload, context):
    if context.get('management_quarantine'):
        _require(payload.get('action') in {'WAIT', 'EXIT'} and not payload.get('pending_renewal')
                 and not payload.get('cancel_entry_ids') and not payload.get('entries')
                 and not payload.get('take_profits') and not payload.get('partial_stops')
                 and payload.get('reduce_fraction') is None, 'action_forbidden')


def bind_exit(broker, payload, receipt):
    """Append inside the caller's atomic intent transaction; no commit/order."""
    _require(payload['action'] == 'EXIT', 'not_exit')
    validate_action(payload, {'management_quarantine': receipt})
    event = _approval(broker)
    _require(event and receipt == _receipt(event), 'authority_changed')
    _require(not any(r.get('approval_id') == receipt['approval_id'] for r in _events(broker.conn, EXIT_KIND)), 'exit_already_bound')
    _append(broker.conn, EXIT_KIND, dict(intent_id=payload['action_id'], payload_sha256=digest(payload),
        approval_id=receipt['approval_id'], approval_sha256=receipt['approval_sha256']), broker.clock())
    _pending_matches(broker.conn, event['body'], self_exit=payload['action_id'])


def register(broker, *, approval_id, reason, expires_at, scenario_id, contract_id,
             unknown_link_id, unknown_request_sha256, pending_intents, maximum_quantity):
    from live.shared_entry_coordinator import mutation_lock
    with mutation_lock(broker.conn):
        now = broker.clock()
        _require(isinstance(approval_id, str) and approval_id and isinstance(reason, str) and reason, 'approval_reason_missing')
        _require(now < expires_at <= now + 86400 and len(pending_intents) == 3
                 and {r['action'] for r in pending_intents} == {'OPEN', 'ADJUST', 'REDUCE'}, 'approval_scope')
        _require(not any(e['body']['approval_id'] == approval_id for e in _events(broker.conn, KIND)), 'approval_id_reused')
        active = broker._active()
        for child in broker.children(scenario_id):
            if child['link_id'] != unknown_link_id:
                broker._query_child(child)
        observed = broker.capture_account()
        unknown = [c for c in broker.children(scenario_id) if c['link_id'] == unknown_link_id]
        _require(len(unknown) == 1, 'unknown_child_missing')
        body = dict(version=1, operation='AUTHORIZE', approval_id=approval_id, reason=reason, purpose=PURPOSE,
            issued_at=now, expires_at=expires_at, main_uid=broker.expected_main_uid, endpoint=ENDPOINT,
            scenario_id=scenario_id, contract_id=contract_id, side=active['side'], maximum_quantity=maximum_quantity,
            unknown_link_id=unknown_link_id, unknown_request_sha256=unknown_request_sha256,
            unknown_child_identity={k: unknown[0][k] for k in ('intent_id', 'local_id', 'created_at')},
            pending_intents=deepcopy(pending_intents), allowed_actions=['WAIT', 'EXIT'])
        event = dict(body=body, sha256=digest(body))
        receipt = _inspect(broker, event, observed, active, broker.children(scenario_id))
        _append(broker.conn, KIND, event, now)
        broker.conn.commit()
        return receipt


def revoke(broker, *, approval_id, reason):
    from live.shared_entry_coordinator import mutation_lock
    with mutation_lock(broker.conn):
        broker._identity()
        event = _latest(broker)
        _require(event and event['body'].get('operation') == 'AUTHORIZE'
                 and event['body']['approval_id'] == approval_id and reason
                 and event['body']['main_uid'] == broker.expected_main_uid
                 and event['body']['endpoint'] == broker.session.endpoint == ENDPOINT, 'revocation_scope')
        body = dict(operation='REVOKE', approval_id=approval_id, reason=reason,
                    at=broker.clock(), approval_sha256=event['sha256'])
        _append(broker.conn, KIND, dict(body=body, sha256=digest(body)), broker.clock())
        broker.conn.commit()


def bound_exit(broker, ident):
    rows = [r for r in _events(broker.conn, EXIT_KIND) if r.get('intent_id') == ident]
    _require(len(rows) <= 1, 'duplicate_exit_receipt')
    return rows[0] if rows else None


def execute_exit(broker, payload, ident):
    """One fixed reduce-only order, retaining the unresolved conditional request."""
    from live.scenario_execution import decimal, text_number
    receipt = bound_exit(broker, ident)
    _require(receipt and receipt['payload_sha256'] == digest(payload) and payload['action'] == 'EXIT', 'exit_binding_changed')
    existing = [c for c in broker.children(payload['scenario_id']) if c['intent_id'] == ident]
    if existing:
        _require(len(existing) == 1 and existing[0]['kind'] == 'exit' and existing[0]['local_id'] == 'full', 'exit_children_changed')
        return broker._query_child(existing[0])  # Even expiry/revocation permits exact reads, never another POST.
    authority = active_receipt(broker, self_exit=ident)
    _require(authority and authority['approval_id'] == receipt['approval_id']
             and authority['approval_sha256'] == receipt['approval_sha256'], 'exit_authority_changed')
    validate_action(payload, {'management_quarantine': authority})
    instrument = broker._instrument()
    observed = broker.capture_account()
    active = broker._active()
    _require(project(broker, observed, active, broker.children(payload['scenario_id']), ident) == authority, 'exit_fresh_authority_changed')
    size = decimal(observed['position']['size'])
    event = _approval(broker)
    if authority.get('scope') == 'ENTRY_AGGREGATE_ONLY':
        _require(event and event['body'].get('version') == 2 and event['sha256'] == authority['approval_sha256'],
                 'exit_authority_changed')
    _require(size <= instrument['market_maximum'], 'exit_quantity_requires_split')
    guard = None
    if event and event['body'].get('version') == 2:
        def guard():
            from live.scenario_control import read_control
            from live.scenario_entry_contract import registered_contract
            current = _approval(broker)
            _require(current == event and current['sha256'] == authority['approval_sha256'], 'exit_authority_changed')
            body = current['body']
            _pending_matches(broker.conn, body, ident)
            state, children, control = broker._active(), broker.children(), read_control(broker.conn)
            _require(isinstance(state, dict) and digest(_entry_active_anchor(state)) == body['active_sha256']
                     and isinstance(children, list) and len(children) == 1
                     and digest(children[0]) == body['child_sha256'] and broker._legacy_local() is False,
                     'exit_state_changed')
            _require(control and control.get('state') == 'active' and control.get('main_uid') == body['main_uid']
                     and broker.expected_main_uid == body['main_uid'] and broker.session.endpoint == ENDPOINT,
                     'exit_identity_changed')
            _require(registered_contract(broker.conn, state), 'exit_contract_changed')
            _require(0 <= broker.clock()-observed['captured_at'] <= 10, 'exit_observation_stale')
    return broker._submit(payload, ident, 'exit', 'full', dict(
        side='Sell' if active['side'] == 'LONG' else 'Buy', orderType='Market',
        qty=text_number(size), reduceOnly=True, closeOnTrigger=True), single_attempt=True,
        pretransport_guard=guard)
