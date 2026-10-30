"""Compact backed BTC decision snapshots without changing their logical content."""
import hashlib
import json
import os
import sqlite3
import sys

from live.scenario_review_storage import MAGIC, MAX_BYTES, review_text, snapshot_storage
from tools.btc_review_event_compaction import SOURCE, resource_guard, source_identity


def valid_identity(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def plan_backed_snapshots(conn, identity, *, after_rowid=0):
    if (not valid_identity(identity) or type(after_rowid) is not int or not 0 <= after_rowid < 2**63
            or conn.execute('PRAGMA query_only').fetchone()[0] != 1):
        raise ValueError('invalid_manifest')
    rows, scanned, last, unreadable = [], 0, after_rowid, 0
    for rowid, slot, value in conn.execute(
            'SELECT rowid,slot,snapshot FROM llm_scenario_decisions WHERE rowid>? ORDER BY rowid LIMIT 200',
            (after_rowid,)):
        last, scanned = rowid, scanned + 1
        if type(slot) is not int or slot < 0 or not isinstance(value, str):
            unreadable += 1
            continue
        raw = value.encode('utf-8')
        if len(raw) > MAX_BYTES:
            unreadable += 1
            continue
        packed = snapshot_storage(value)
        if not isinstance(packed, bytes) or len(packed) >= len(raw):
            continue
        rows.append({'rowid':rowid, 'slot':slot, 'expected_sha256':hashlib.sha256(raw).hexdigest()})
        if len(rows) == 20:
            break
    return {'manifest':{'source_identity':identity, 'rows':rows}, 'scanned':scanned,
            'unreadable':unreadable, 'last_scanned_rowid':last}


def compact_one(conn, row, guard):
    if guard() is not True:
        return 'RESOURCE_LIMIT'
    current = conn.execute('SELECT slot,snapshot FROM llm_scenario_decisions WHERE rowid=?', (row['rowid'],)).fetchone()
    if current is None or current[0] != row['slot']:
        return 'SOURCE_CHANGED'
    original = current[1]
    if isinstance(original, str):
        if hashlib.sha256(original.encode('utf-8')).hexdigest() != row['expected_sha256']:
            return 'SOURCE_CHANGED'
        candidate = snapshot_storage(original)
        if review_text(candidate) != original:
            raise ValueError('snapshot_roundtrip_mismatch')
    elif isinstance(original, bytes) and original.startswith(MAGIC):
        if hashlib.sha256(review_text(original).encode('utf-8')).hexdigest() != row['expected_sha256']:
            return 'SOURCE_CHANGED'
        candidate = original
    else:
        return 'SOURCE_CHANGED'
    try:
        conn.execute('BEGIN IMMEDIATE')
        check = conn.execute('SELECT slot,snapshot FROM llm_scenario_decisions WHERE rowid=?', (row['rowid'],)).fetchone()
        if check is None or tuple(check) != (row['slot'], original):
            conn.rollback()
            return 'SOURCE_CHANGED'
        if guard() is not True:
            conn.rollback()
            return 'RESOURCE_LIMIT'
        if candidate == original:
            conn.rollback()
            return 'UNCHANGED'
        result = conn.execute('UPDATE llm_scenario_decisions SET snapshot=? WHERE rowid=? AND snapshot=?',
                              (candidate, row['rowid'], original))
        if result.rowcount != 1:
            raise ValueError('snapshot_source_changed')
        conn.commit()
        return 'UPDATED'
    except BaseException:
        conn.rollback()
        raise


def compact_batch(conn, manifest, guard):
    if (not isinstance(manifest, dict) or set(manifest) != {'source_identity', 'rows'}
            or not valid_identity(manifest['source_identity']) or not isinstance(manifest['rows'], list)
            or not 1 <= len(manifest['rows']) <= 20 or not callable(guard) or conn.in_transaction):
        raise ValueError('invalid_manifest')
    rows = manifest['rows']
    if not all(isinstance(row, dict) and set(row) == {'rowid', 'slot', 'expected_sha256'}
               and type(row['rowid']) is int and 0 < row['rowid'] < 2**63
               and type(row['slot']) is int and 0 <= row['slot'] < 2**63
               and valid_identity(row['expected_sha256']) for row in rows):
        raise ValueError('invalid_manifest')
    ids = [row['rowid'] for row in rows]
    if ids != sorted(set(ids)):
        raise ValueError('invalid_manifest')
    counts = dict.fromkeys(('UPDATED', 'UNCHANGED', 'SOURCE_CHANGED', 'RESOURCE_LIMIT', 'ERROR'), 0)
    last = None
    for row in rows:
        try:
            status = compact_one(conn, row, guard)
        except Exception:
            counts['ERROR'] += 1
            break
        counts[status] += 1
        if status in ('SOURCE_CHANGED', 'RESOURCE_LIMIT'):
            break
        last = row['rowid']
    return {'counts':counts, 'last_rowid':last}


def main():
    if os.environ.get('PRISM_BTC_STORAGE_APPROVED') != '1':
        raise RuntimeError('explicit_storage_approval_required')
    data = sys.stdin.buffer.read(32769)
    if len(data) > 32768:
        raise ValueError('manifest_too_large')
    manifest = json.loads(data)
    identity = manifest.get('source_identity') if isinstance(manifest, dict) else None
    if not valid_identity(identity) or source_identity(SOURCE) != identity:
        raise ValueError('source_identity_changed')
    from utils.btc_review_retention import _backup_idle
    guard = lambda: resource_guard(SOURCE, identity) and _backup_idle(SOURCE)
    if guard() is not True:
        print(json.dumps({'status':'RESOURCE_LIMIT', 'counts':{}, 'last_rowid':None}))
        return
    with sqlite3.connect(SOURCE, timeout=0, isolation_level=None) as conn:
        from live.shared_entry_coordinator import mutation_lock
        with mutation_lock(conn):
            result = compact_batch(conn, manifest, guard)
    status = 'STOPPED' if any(result['counts'][k] for k in ('SOURCE_CHANGED', 'RESOURCE_LIMIT', 'ERROR')) else 'COMPLETE'
    print(json.dumps({'status':status, **result}))


if __name__ == '__main__':
    main()
