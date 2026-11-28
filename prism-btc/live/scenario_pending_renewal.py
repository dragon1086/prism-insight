"""Immutable OPEN anchors and atomic local waiting leases, never order renewal."""
from copy import deepcopy
import json

from core.scenario_pending_renewal import digest, finite, lease_deadline


def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_pending_leases(scenario_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_pending_renewals(intent_id TEXT PRIMARY KEY, body TEXT NOT NULL)')


def create_anchor(conn, active, payload, context, created_at):
    entries = payload.get('entries', [])
    if (context.get('pending_wait_policy_version') != 1 or payload.get('action') != 'OPEN'
            or not entries or any('trigger_price' not in row for row in entries)
            or not active.get('entry_contract') or payload.get('chase') != {'max_bps': 0, 'max_reprices': 0}):
        return
    anchor = dict(version=1, scenario_id=active['scenario_id'], original_action_id=payload['action_id'],
        created_at=created_at, input_slot=int(context['input_captured_at'] // 300),
        hard_deadline=created_at + 3600, side=active['side'], hard_stop=active['hard_stop'],
        risk_budget=payload['risk']['budget'],
        contract_id=active['entry_contract']['contract_id'], entries=deepcopy(entries),
        original_payload_sha256=digest(payload))
    until = min((anchor['input_slot'] + 1) * 300 + 120, anchor['hard_deadline'])
    body = dict(anchor=anchor, revision=0, until=until, last_slot=anchor['input_slot'], last_intent_id=None)
    conn.execute('INSERT INTO llm_scenario_pending_leases VALUES(?,?)', (active['scenario_id'], json.dumps(body)))
    active['pending_wait_anchor'] = digest(anchor)


def load(conn, active):
    try:
        return _load(conn, active)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _load(conn, active):
    if not active or not isinstance(active.get('pending_wait_anchor'), str):
        return None
    row = conn.execute('SELECT body FROM llm_scenario_pending_leases WHERE scenario_id=?', (active['scenario_id'],)).fetchone()
    if not row:
        return None
    body = json.loads(row[0])
    anchor = body.get('anchor', {})
    if (digest(anchor) != active['pending_wait_anchor'] or anchor.get('version') != 1
            or anchor.get('scenario_id') != active['scenario_id']
            or anchor.get('side') != active.get('side') or anchor.get('hard_stop') != active.get('hard_stop')
            or anchor.get('contract_id') != (active.get('entry_contract') or {}).get('contract_id')
            or not finite(anchor.get('created_at')) or anchor.get('hard_deadline') != anchor['created_at'] + 3600
            or type(body.get('revision')) is not int or body['revision'] < 0
            or not finite(body.get('until')) or not anchor['created_at'] < body['until'] <= anchor['hard_deadline']):
        return None
    original = conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?', (anchor['original_action_id'],)).fetchone()
    if not original or digest(json.loads(original[0])) != anchor['original_payload_sha256']:
        return None
    if body['revision'] == 0:
        if (body['until'] != min((anchor['input_slot'] + 1) * 300 + 120, anchor['hard_deadline'])
                or body['last_slot'] != anchor['input_slot'] or body['last_intent_id'] is not None):
            return None
    else:
        applied = receipt(conn, body.get('last_intent_id'))
        if (not applied or applied.get('status') != 'APPLIED'
                or applied.get('lease_revision') != body['revision']
                or applied.get('to_expires_at') != body['until'] or applied.get('slot') != body['last_slot']
                or applied.get('anchor_sha256') != active['pending_wait_anchor']
                or not finite(applied.get('checked_at')) or not finite(applied.get('from_expires_at'))
                or not applied['checked_at'] < applied['from_expires_at'] < body['until']
                or int(applied['checked_at'] // 300) != body['last_slot']):
            return None
        intent = conn.execute('SELECT payload FROM llm_scenario_intents WHERE id=?',
                              (body['last_intent_id'],)).fetchone()
        plan = json.loads(intent[0]) if intent else {}
        grant = plan.get('pending_renewal') or {}
        if (plan.get('action') != 'WAIT' or plan.get('scenario_id') != active['scenario_id']
                or grant.get('offer_id') != applied.get('offer_id')
                or grant.get('lease_revision') != body['revision'] - 1
                or any(grant.get(key) != applied.get(key) for key in ('from_expires_at', 'to_expires_at', 'slot'))):
            return None
    return body


def receipt(conn, intent_id):
    row = conn.execute('SELECT body FROM llm_scenario_pending_renewals WHERE intent_id=?', (intent_id,)).fetchone()
    return json.loads(row[0]) if row else None


def effective_deadline(conn, active, parent, child=None):
    body = load(conn, active)
    if (body and body['anchor']['original_action_id'] == parent.get('action_id')
            and (child is None or matching_child(child, body['anchor']))):
        return body['until']
    return parent['expires_at']


def matching_child(child, anchor):
    from live.scenario_execution import decimal, stable_link
    expected = next((row for row in anchor['entries'] if row['id'] == child.get('local_id')), None)
    request = child.get('request') or {}
    return bool(expected and child.get('intent_id') == anchor['original_action_id']
        and child.get('scenario_id') == anchor['scenario_id'] and child.get('order_id')
        and child.get('link_id') == stable_link(anchor['original_action_id'], 'entry', expected['id'])
        and request.get('side') == ('Buy' if anchor['side'] == 'LONG' else 'Sell')
        and request.get('orderType') == 'Limit'
        and decimal(request.get('price', -1)) == decimal(expected['price'])
        and decimal(request.get('qty', -1)) == decimal(expected['quantity'])
        and decimal(request.get('triggerPrice', -1)) == decimal(expected['trigger_price'])
        and decimal(request.get('stopLoss', -1)) == decimal(anchor['hard_stop']))


def fingerprint(children, anchor):
    """Only the entire original, fully submitted, zero-fill Untriggered batch."""
    from live.scenario_execution import decimal, stable_link
    entries = {row['id']: row for row in anchor['entries']}
    own = [child for child in children if child['kind'] == 'entry']
    if len(own) != len(entries) or {c['local_id'] for c in own} != set(entries):
        return None
    bindings = []
    for child in own:
        expected = entries[child['local_id']]
        request, evidence = child['request'], child.get('evidence') or {}
        order = evidence.get('order') or {}
        if (child['status'] != 'LIVE' or not child.get('order_id')
                or child['intent_id'] != anchor['original_action_id']
                or child['scenario_id'] != anchor['scenario_id']
                or child['link_id'] != stable_link(child['intent_id'], 'entry', child['local_id'])
                or order.get('orderStatus') != 'Untriggered' or evidence.get('executions') != []
                or decimal(order.get('cumExecQty', -1)) != 0
                or decimal(order.get('leavesQty', -1)) != decimal(expected['quantity'])
                or request.get('side') != ('Buy' if anchor['side'] == 'LONG' else 'Sell')
                or request.get('orderType') != 'Limit'
                or decimal(request.get('price', -1)) != decimal(expected['price'])
                or decimal(request.get('qty', -1)) != decimal(expected['quantity'])
                or decimal(request.get('triggerPrice', -1)) != decimal(expected['trigger_price'])
                or decimal(request.get('stopLoss', -1)) != decimal(anchor['hard_stop'])):
            return None
        bindings.append(dict(link_id=child['link_id'], order_id=child['order_id'], request=request))
    return digest(sorted(bindings, key=lambda row: row['link_id']))


def retention_allowed(state, active, context):
    from live.scenario_recovery import hard_guards, managed_permit
    if (context.get('legacy_fenced') is not False or context.get('protection_ok') is not True
            or context.get('accounting_status') != 'confirmed'
            or type(context.get('new_risk_blocked')) is not bool
            or context.get('recovery_hard_blocked', context.get('new_risk_blocked')) is not False):
        return False
    if state.get('breaker', {}).get('blocked'):
        return bool(managed_permit(state, active) and hard_guards(state, context))
    return context.get('new_risk_blocked') is False


def offer(conn, state, context, children, now):
    active = state.get('active')
    body = load(conn, active)
    if (not body or not finite(now) or not now < body['until']
            or int(now // 300) <= body['last_slot'] or context.get('positions') != []
            or not retention_allowed(state, active, context)
            or context.get('entry_contract_status') != 'REGISTERED'
            or context.get('entry_goal_untrusted') is True
            or (context.get('entry_contract_observation') or {}).get('status') == 'FAILED'
            or active.get('entry_contract_model_failure')):
        return None
    identity = fingerprint(children, body['anchor'])
    for child in children:
        if child['kind'] == 'entry' and conn.execute(
                "SELECT 1 FROM llm_scenario_broker_evidence WHERE kind='cancel_ack_pending' "
                "AND json_extract(body,'$.order_id')=? LIMIT 1", (child.get('order_id'),)).fetchone():
            return None
    pending_actions = conn.execute("SELECT payload FROM llm_scenario_intents WHERE scenario_id=? AND status!='TERMINAL'",
                                   (active['scenario_id'],)).fetchall()
    if any(json.loads(row[0]).get('cancel_entry_ids') or json.loads(row[0]).get('action') == 'EXIT'
           for row in pending_actions):
        return None
    until = lease_deadline(now, body['anchor']['created_at'])
    if not identity or until <= body['until']:
        return None
    result = dict(original_action_id=body['anchor']['original_action_id'], lease_revision=body['revision'],
        from_expires_at=body['until'], to_expires_at=until, hard_deadline=body['anchor']['hard_deadline'],
        slot=int(now // 300), contract_id=body['anchor']['contract_id'], risk_budget=body['anchor']['risk_budget'])
    result['offer_id'] = digest(dict(offer=result, children_sha256=identity, anchor_sha256=digest(body['anchor'])))
    return result


def reject(conn, intent_id, reason, now, *, failure_stage=None):
    result = dict(status='REJECTED', reason=reason, checked_at=now)
    if failure_stage is not None:
        result['failure_stage'] = failure_stage
    conn.execute('INSERT OR IGNORE INTO llm_scenario_pending_renewals VALUES(?,?)',
                 (intent_id, json.dumps(result)))
    conn.commit()


def apply(conn, active, grant, intent_id, now, *, clock, approval_deadline):
    """Caller has freshly validated exact children/risk under the mutation lock."""
    previous = receipt(conn, intent_id)
    if previous:
        return previous
    body = load(conn, active)
    if (not body or body['revision'] != grant['lease_revision'] or body['until'] != grant['from_expires_at']
            or not now < body['until'] or int(now // 300) != grant['slot']
            or grant['to_expires_at'] > lease_deadline(now, body['anchor']['created_at'])):
        raise ValueError('pending_renewal_apply_stale')
    result = dict(status='APPLIED', checked_at=now, anchor_sha256=active['pending_wait_anchor'],
        offer_id=grant['offer_id'], from_expires_at=body['until'], to_expires_at=grant['to_expires_at'],
        lease_revision=body['revision'] + 1, slot=grant['slot'])
    original = conn.execute('SELECT status,evidence FROM llm_scenario_intents WHERE id=?',
                            (body['anchor']['original_action_id'],)).fetchone()
    evidence = json.loads(original[1]) if original and original[1] else {}
    rows = evidence.get('open_entries', [])
    if (not original or original[0] != 'LIVE_RECONCILED' or evidence.get('filled_quantity') != 0
            or {row['id'] for row in rows} != {row['id'] for row in body['anchor']['entries']}
            or any(row.get('expires_at') != body['until'] for row in rows)):
        raise ValueError('pending_renewal_latest_evidence_missing')
    for row in rows:
        row['expires_at'] = grant['to_expires_at']
    body.update(revision=result['lease_revision'], until=result['to_expires_at'],
                last_slot=grant['slot'], last_intent_id=intent_id)
    try:
        changed = conn.execute("UPDATE llm_scenario_pending_leases SET body=? WHERE scenario_id=? "
            "AND json_extract(body,'$.revision')=? AND json_extract(body,'$.until')=?",
            (json.dumps(body), active['scenario_id'], grant['lease_revision'], grant['from_expires_at']))
        if changed.rowcount != 1:
            raise ValueError('pending_renewal_lease_race')
        conn.execute('INSERT INTO llm_scenario_pending_renewals VALUES(?,?)', (intent_id, json.dumps(result)))
        changed = conn.execute("UPDATE llm_scenario_intents SET evidence=? WHERE id=? "
            "AND status='LIVE_RECONCILED' AND evidence=?",
            (json.dumps(evidence), body['anchor']['original_action_id'], original[1]))
        if changed.rowcount != 1:
            raise ValueError('pending_renewal_evidence_race')
        conn.execute('SAVEPOINT pending_renewal_notice')
        try:
            from live.scenario_outbox import enqueue
            enqueue(conn, 'reservation-renewed:' + intent_id, dict(kind='RESERVATION_RENEWED',
                timestamp=now, renewal_applied=True, reservation_expires_at=body['until'],
                absolute_expires_at=body['anchor']['hard_deadline']))
            conn.execute('RELEASE pending_renewal_notice')
        except Exception:
            conn.execute('ROLLBACK TO pending_renewal_notice')
            conn.execute('RELEASE pending_renewal_notice')
        commit_check = clock()
        if (not finite(commit_check) or commit_check < now
                or not commit_check < min(grant['from_expires_at'], approval_deadline)
                or int(commit_check // 300) != grant['slot']):
            raise ValueError('pending_renewal_commit_stale')
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result
