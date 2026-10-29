"""Backup-matched, lossless compaction of a single BTC review tape."""
import hashlib
import json
import os
import sqlite3
import sys

from live.scenario_review_storage import MAGIC as GZIP_MAGIC, review_text, tape_storage
from tools.btc_review_event_compaction import SOURCE, resource_guard, source_identity
from utils.btc_review_references import MAGIC as REFS_MAGIC, read_tape


def _sha(value):
    if isinstance(value, str):
        value = value.encode('utf-8')
    if not isinstance(value, bytes):
        raise ValueError('invalid_tape_body')
    return hashlib.sha256(value).hexdigest()


def _valid_row(row):
    return (isinstance(row, dict) and set(row) == {'slot', 'expected_type', 'expected_sha256', 'expected_raw_sha256'}
            and type(row['slot']) is int and 0 < row['slot'] <= 9223372036854775807
            and row['expected_type'] in ('text', 'blob')
            and all(isinstance(row[k], str) and len(row[k]) == 64
                    and all(c in '0123456789abcdef' for c in row[k])
                    for k in ('expected_sha256', 'expected_raw_sha256')))


def compact_one(conn, *, slot, expected_type, expected_sha256, expected_raw_sha256, guard):
    if (type(slot) is not int or not 0 < slot <= 9223372036854775807
            or expected_type not in ('text', 'blob') or not callable(guard) or conn.in_transaction
            or any(not isinstance(x, str) or len(x) != 64 or any(c not in '0123456789abcdef' for c in x)
                   for x in (expected_sha256, expected_raw_sha256))):
        raise ValueError('invalid_compaction_context')
    if guard() is not True:
        return 'RESOURCE_LIMIT'
    try:
        conn.execute('BEGIN')
        row = conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()
        if row is None:
            return 'SOURCE_CHANGED'
        original = row[0]
        raw = read_tape(original, conn, slot, decode_legacy=review_text)
        if _sha(raw) != expected_raw_sha256:
            return 'SOURCE_CHANGED'
        actual_type = 'text' if isinstance(original, str) else 'blob' if isinstance(original, bytes) else None
        if (actual_type, _sha(original)) != (expected_type, expected_sha256):
            if not isinstance(original, bytes) or not original.startswith((GZIP_MAGIC, REFS_MAGIC)):
                return 'SOURCE_CHANGED'
            candidate = original
        else:
            candidate = tape_storage(conn, slot, raw)
            if _sha(read_tape(candidate, conn, slot, decode_legacy=review_text)) != expected_raw_sha256:
                raise ValueError('tape_roundtrip_mismatch')
            original_bytes = original.encode('utf-8') if isinstance(original, str) else original
            candidate_bytes = candidate.encode('utf-8') if isinstance(candidate, str) else candidate
            if len(candidate_bytes) >= len(original_bytes):
                candidate = original
    finally:
        conn.rollback()
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()
        if current is None or current[0] != original:
            conn.rollback()
            return 'SOURCE_CHANGED'
        try:
            if (_sha(read_tape(original, conn, slot, decode_legacy=review_text)) != expected_raw_sha256
                    or _sha(read_tape(candidate, conn, slot, decode_legacy=review_text)) != expected_raw_sha256):
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
        changed = conn.execute('UPDATE llm_scenario_review_tapes SET body=? WHERE slot=? AND body=?',
                               (candidate, slot, original))
        if changed.rowcount != 1:
            raise ValueError('tape_source_changed')
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
            or not all(_valid_row(row) for row in manifest['rows']) or not callable(guard)):
        raise ValueError('invalid_manifest')
    slots = [row['slot'] for row in manifest['rows']]
    if slots != sorted(set(slots)):
        raise ValueError('invalid_manifest')
    counts = {'UPDATED': 0, 'UNCHANGED': 0, 'SOURCE_CHANGED': 0, 'RESOURCE_LIMIT': 0, 'ERROR': 0}
    last = None
    for row in manifest['rows']:
        try:
            status = compact_one(conn, **row, guard=guard)
        except Exception:
            counts['ERROR'] += 1
            break
        counts[status] += 1
        if status in ('SOURCE_CHANGED', 'RESOURCE_LIMIT'):
            break
        last = row['slot']
    return {'counts': counts, 'last_slot': last}


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
        print(json.dumps({'status': 'RESOURCE_LIMIT', 'counts': {}, 'last_slot': None}))
        return
    with sqlite3.connect(SOURCE, timeout=0, isolation_level=None) as conn:
        result = compact_batch(conn, manifest, guard)
    print(json.dumps({'status': 'STOPPED' if any(result['counts'][k] for k in ('SOURCE_CHANGED', 'RESOURCE_LIMIT', 'ERROR'))
                      else 'COMPLETE', **result}))


if __name__ == '__main__':
    main()
