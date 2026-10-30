"""Restore accounting audit inputs referenced from durable broker evidence."""
import copy
import hashlib
import json
import math
import sys

if 'btc_financial_refs_embedded' in sys.modules:
    from btc_financial_refs_embedded import restore as restore_financial
else:
    from utils.btc_financial_references import restore as restore_financial

TAG = '$btc_accounting_refs_v1'
ORIGINAL_HASH = '$btc_accounting_original_sha256'
CHILDREN_REF = '$btc_accounting_children_ref_v1'
MAX_BYTES = 512 * 1024
SOURCE_LIMIT = 8


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def compact(raw, conn, observed_at):
    """Reference only exact earlier broker evidence; otherwise preserve the original."""
    if (not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES or conn is None
            or type(observed_at) not in (int, float) or not math.isfinite(observed_at) or observed_at <= 0):
        return raw
    try:
        event = json.loads(raw)
        if not isinstance(event, dict) or TAG in event or ORIGINAL_HASH in event or canonical(event) != raw:
            return raw
        projected = copy.deepcopy(event)
        for field, kind in (('observation', 'account'), ('financial_evidence', 'financial')):
            if not isinstance(event.get(field), dict):
                return raw
            expected = canonical(event[field])
            match = None
            rows = conn.execute('SELECT rowid,captured_at,body FROM llm_scenario_broker_evidence '
                                'WHERE kind=? AND captured_at<=? ORDER BY rowid DESC LIMIT ?',
                                (kind, observed_at, SOURCE_LIMIT))
            for rowid, captured_at, body in rows:
                if (type(captured_at) not in (int, float) or not math.isfinite(captured_at) or captured_at <= 0
                        or not isinstance(body, str) or len(body.encode()) > MAX_BYTES):
                    continue
                try:
                    value = json.loads(restore_financial(body) if kind == 'financial' else body)
                    if isinstance(value, dict) and canonical(value) == expected:
                        match = (rowid, captured_at)
                        break
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
            if match is None or type(match[0]) is not int or match[0] <= 0:
                return raw
            projected[field] = {'rowid': match[0], 'captured_at': match[1], 'sha256': sha(expected)}
        projected[TAG] = 1
        projected[ORIGINAL_HASH] = sha(raw)
        candidate = canonical(projected)
        return candidate if len(candidate.encode()) < len(raw.encode()) else raw
    except (ValueError, TypeError, KeyError, OverflowError):
        return raw


def read_accounting(raw, conn):
    if not isinstance(raw, str):
        raise ValueError('accounting_audit_invalid')
    if TAG not in raw and CHILDREN_REF not in raw:
        return raw
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('accounting_audit_too_large')
    event = json.loads(raw)
    if not isinstance(event, dict) or (TAG not in event and CHILDREN_REF not in event):
        return raw
    if type(event.get(TAG)) is not int or event[TAG] != 1 or not isinstance(event.get(ORIGINAL_HASH), str) or conn is None:
        raise ValueError('accounting_audit_reference_invalid')
    result = copy.deepcopy(event)
    expected = result.pop(ORIGINAL_HASH)
    result.pop(TAG)
    if CHILDREN_REF in result:
        marker = result.pop(CHILDREN_REF)
        if ('children' in result or not isinstance(marker, dict)
                or set(marker) != {'event_id', 'sha256'}
                or not isinstance(marker['event_id'], str) or not marker['event_id']
                or not isinstance(marker['sha256'], str)):
            raise ValueError('accounting_children_reference_invalid')
        row = conn.execute('SELECT kind,body FROM llm_scenario_audit_events WHERE event_id=?',
                           (marker['event_id'],)).fetchone()
        if not row or row[0] != 'accounting_observation' or not isinstance(row[1], str):
            raise ValueError('accounting_children_source_missing')
        if len(row[1].encode()) > MAX_BYTES:
            raise ValueError('accounting_children_source_invalid')
        source = json.loads(row[1])
        if (not isinstance(source, dict) or CHILDREN_REF in source
                or not isinstance(source.get('children'), list)
                or sha(canonical(source['children'])) != marker['sha256']):
            raise ValueError('accounting_children_source_changed')
        result['children'] = source['children']
    for field, kind in (('observation', 'account'), ('financial_evidence', 'financial')):
        marker = result.get(field)
        if (not isinstance(marker, dict) or set(marker) != {'rowid', 'sha256', 'captured_at'}
                or type(marker['rowid']) is not int or marker['rowid'] <= 0
                or type(marker['captured_at']) not in (int, float) or not math.isfinite(marker['captured_at'])
                or marker['captured_at'] <= 0
                or not isinstance(marker['sha256'], str)):
            raise ValueError('accounting_audit_reference_invalid')
        row = conn.execute('SELECT kind,captured_at,body FROM llm_scenario_broker_evidence WHERE rowid=?',
                           (marker['rowid'],)).fetchone()
        if not row or row[0] != kind or row[1] != marker['captured_at'] or not isinstance(row[2], str):
            raise ValueError('accounting_audit_source_missing')
        value = json.loads(restore_financial(row[2]) if kind == 'financial' else row[2])
        if not isinstance(value, dict) or sha(canonical(value)) != marker['sha256']:
            raise ValueError('accounting_audit_source_changed')
        result[field] = value
    restored = canonical(result)
    if len(restored.encode()) > MAX_BYTES or sha(restored) != expected:
        raise ValueError('accounting_audit_reference_changed')
    return restored
