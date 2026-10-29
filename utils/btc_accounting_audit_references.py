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
MAX_BYTES = 512 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_accounting(raw, conn):
    if not isinstance(raw, str):
        raise ValueError('accounting_audit_invalid')
    if TAG not in raw:
        return raw
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError('accounting_audit_too_large')
    event = json.loads(raw)
    if not isinstance(event, dict) or TAG not in event:
        return raw
    if type(event[TAG]) is not int or event[TAG] != 1 or not isinstance(event.get(ORIGINAL_HASH), str) or conn is None:
        raise ValueError('accounting_audit_reference_invalid')
    result = copy.deepcopy(event)
    expected = result.pop(ORIGINAL_HASH)
    result.pop(TAG)
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
