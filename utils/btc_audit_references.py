"""Lossless decision-input audit references against the same SQLite snapshot."""
import copy
import hashlib
import json

TAG = '$btc_audit_input_refs_v1'
ORIGINAL_HASH = '$btc_audit_original_sha256'
MAX_BYTES = 512 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _sources(snapshot, context):
    if not isinstance(snapshot, str) or not isinstance(context, str):
        raise ValueError('audit_reference_source_missing')
    if len(snapshot.encode()) + len(context.encode()) > MAX_BYTES:
        raise ValueError('audit_reference_source_too_large')
    values = [json.loads(snapshot), json.loads(context)]
    if not all(isinstance(value, dict) for value in values):
        raise ValueError('audit_reference_source_invalid')
    return values


def compact(raw, snapshot, context):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
        return raw
    try:
        event = json.loads(raw)
        if not isinstance(event, dict) or TAG in event or ORIGINAL_HASH in event or canonical(event) != raw:
            return raw
        values = _sources(snapshot, context)
        for name, value in zip(('snapshot', 'context'), values):
            if event.get(name) != value or event.get(name + '_hash') != sha(canonical(value)):
                return raw
        projected = copy.deepcopy(event)
        projected['snapshot'] = {'$decision_field': 'snapshot'}
        projected['context'] = {'$decision_field': 'context'}
        projected[TAG] = 1
        projected[ORIGINAL_HASH] = sha(raw)
        candidate = canonical(projected)
        return candidate if len(candidate.encode()) < len(raw.encode()) else raw
    except (ValueError, TypeError, KeyError, OverflowError):
        return raw


def read_audit(raw, conn, slot):
    if not isinstance(raw, str):
        raise ValueError('audit_body_invalid')
    if TAG not in raw:
        return raw
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('audit_reference_too_large')
    event = json.loads(raw)
    if not isinstance(event, dict) or TAG not in event:
        return raw
    if type(event[TAG]) is not int or event[TAG] != 1 or not isinstance(event.get(ORIGINAL_HASH), str):
        raise ValueError('audit_reference_invalid')
    if type(slot) is not int or slot < 0 or conn is None:
        raise ValueError('audit_reference_context_missing')
    rows = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=? LIMIT 2', (slot,)).fetchall()
    if len(rows) != 1:
        raise ValueError('audit_reference_source_missing')
    values = _sources(rows[0][0], rows[0][1])
    result = copy.deepcopy(event)
    expected = result.pop(ORIGINAL_HASH)
    result.pop(TAG)
    for name, value in zip(('snapshot', 'context'), values):
        if result.get(name) != {'$decision_field': name} or result.get(name + '_hash') != sha(canonical(value)):
            raise ValueError('audit_reference_changed')
        result[name] = value
    restored = canonical(result)
    if len(restored.encode()) > MAX_BYTES or sha(restored) != expected:
        raise ValueError('audit_reference_changed')
    return restored
