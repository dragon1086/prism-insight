"""Losslessly deduplicate backed BTC recovery request and response records."""
import json
import os
import sqlite3
import sys

from live.scenario_review_storage import review_text
from tools.btc_review_event_compaction import SOURCE, resource_guard, source_identity
from tools.btc_review_tape_compaction import _sha
from utils.btc_recovery_references import MAGIC, compact, pack, read_recovery
from utils.btc_review_references import MAX_BYTES


def _valid(row):
    return (isinstance(row, dict) and set(row) == {'rowid', 'slot', 'kind', 'expected_type', 'expected_sha256', 'expected_raw_sha256'}
            and type(row['rowid']) is int and 0 < row['rowid'] <= 9223372036854775807
            and type(row['slot']) is int and 0 <= row['slot'] <= 9223372036854775807
            and row['kind'] in ('MODEL_REQUEST', 'MODEL_RAW') and row['expected_type'] in ('text', 'blob')
            and all(isinstance(row[k], str) and len(row[k]) == 64
                    and all(c in '0123456789abcdef' for c in row[k])
                    for k in ('expected_sha256', 'expected_raw_sha256')))


def compact_one(conn, *, rowid, slot, kind, expected_type, expected_sha256, expected_raw_sha256, guard):
    args = dict(rowid=rowid, slot=slot, kind=kind, expected_type=expected_type,
                expected_sha256=expected_sha256, expected_raw_sha256=expected_raw_sha256)
    if not _valid(args) or not callable(guard) or conn.in_transaction:
        raise ValueError('invalid_compaction_context')
    if guard() is not True:
        return 'RESOURCE_LIMIT'
    try:
        conn.execute('BEGIN')
        row = conn.execute('SELECT slot,kind,body FROM llm_scenario_recovery_events WHERE rowid=?', (rowid,)).fetchone()
        if row is None or tuple(row[:2]) != (slot, kind):
            return 'SOURCE_CHANGED'
        original = row[2]
        raw = read_recovery(original, conn, slot, decode_legacy=review_text, expected_kind=kind)
        if _sha(raw) != expected_raw_sha256:
            return 'SOURCE_CHANGED'
        actual_type = 'text' if isinstance(original, str) else 'blob' if isinstance(original, bytes) else None
        if (actual_type, _sha(original)) != (expected_type, expected_sha256):
            if not isinstance(original, bytes) or not original.startswith(MAGIC):
                return 'SOURCE_CHANGED'
            candidate = original
        else:
            options = []
            total = 0
            events = conn.execute('SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence LIMIT 129', (slot,))
            for index, (sequence, body) in enumerate(events):
                if index >= 128:
                    return 'INELIGIBLE'
                text = review_text(body)
                total += len(text.encode('utf-8'))
                if total > MAX_BYTES:
                    return 'INELIGIBLE'
                value = compact(text, raw, slot, sequence)
                if value != raw:
                    options.append(pack(value))
            if len(options) > 1:
                return 'AMBIGUOUS'
            candidate = options[0] if options else original
            if _sha(read_recovery(candidate, conn, slot, decode_legacy=review_text, expected_kind=kind)) != expected_raw_sha256:
                raise ValueError('recovery_roundtrip_mismatch')
            before = original.encode('utf-8') if isinstance(original, str) else original
            after = candidate.encode('utf-8') if isinstance(candidate, str) else candidate
            if len(after) >= len(before):
                candidate = original
    finally:
        conn.rollback()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT slot,kind,body FROM llm_scenario_recovery_events WHERE rowid=?', (rowid,)).fetchone()
        if current is None or tuple(current) != (slot, kind, original):
            conn.rollback()
            return 'SOURCE_CHANGED'
        try:
            if (_sha(read_recovery(original, conn, slot, decode_legacy=review_text, expected_kind=kind)) != expected_raw_sha256
                    or _sha(read_recovery(candidate, conn, slot, decode_legacy=review_text, expected_kind=kind)) != expected_raw_sha256):
                conn.rollback()
                return 'SOURCE_CHANGED'
        except ValueError:
            conn.rollback()
            return 'SOURCE_CHANGED'
        if guard() is not True:
            conn.rollback()
            return 'RESOURCE_LIMIT'
        if candidate == original:
            conn.rollback()
            return 'UNCHANGED'
        updated = conn.execute('UPDATE llm_scenario_recovery_events SET body=? WHERE rowid=? AND body=?',
                               (candidate, rowid, original))
        if updated.rowcount != 1:
            raise ValueError('recovery_source_changed')
        conn.commit()
        return 'UPDATED'
    except BaseException:
        conn.rollback()
        raise


def compact_batch(conn, manifest, guard):
    if (not isinstance(manifest, dict) or set(manifest) != {'source_identity', 'rows'}
            or not isinstance(manifest['source_identity'], str) or len(manifest['source_identity']) != 64
            or any(c not in '0123456789abcdef' for c in manifest['source_identity'])
            or not isinstance(manifest['rows'], list) or not 1 <= len(manifest['rows']) <= 20
            or not all(_valid(row) for row in manifest['rows']) or not callable(guard)):
        raise ValueError('invalid_manifest')
    ids = [row['rowid'] for row in manifest['rows']]
    if ids != sorted(set(ids)):
        raise ValueError('invalid_manifest')
    counts = {k: 0 for k in ('UPDATED', 'UNCHANGED', 'SOURCE_CHANGED', 'RESOURCE_LIMIT', 'AMBIGUOUS', 'INELIGIBLE', 'ERROR')}
    last = None
    for row in manifest['rows']:
        try:
            status = compact_one(conn, **row, guard=guard)
        except Exception:
            counts['ERROR'] += 1
            break
        counts[status] += 1
        if status in ('SOURCE_CHANGED', 'RESOURCE_LIMIT', 'AMBIGUOUS', 'INELIGIBLE'):
            break
        last = row['rowid']
    return {'counts': counts, 'last_rowid': last}


def main():
    if os.environ.get('PRISM_BTC_STORAGE_APPROVED') != '1':
        raise RuntimeError('explicit_storage_approval_required')
    data = sys.stdin.buffer.read(32769)
    if len(data) > 32768:
        raise ValueError('manifest_too_large')
    manifest = json.loads(data)
    identity = manifest.get('source_identity') if isinstance(manifest, dict) else None
    if not isinstance(identity, str) or source_identity(SOURCE) != identity:
        raise ValueError('source_identity_changed')
    guard = lambda: resource_guard(SOURCE, identity)
    if guard() is not True:
        print(json.dumps({'status': 'RESOURCE_LIMIT', 'counts': {}, 'last_rowid': None}))
        return
    with sqlite3.connect(SOURCE, timeout=0, isolation_level=None) as conn:
        result = compact_batch(conn, manifest, guard)
    print(json.dumps({'status': 'STOPPED' if any(result['counts'][k] for k in ('SOURCE_CHANGED', 'RESOURCE_LIMIT', 'AMBIGUOUS', 'INELIGIBLE', 'ERROR'))
                      else 'COMPLETE', **result}))


if __name__ == '__main__':
    main()
