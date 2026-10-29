"""Bounded, backup-matched compaction primitives for BTC review events."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

from live.scenario_review_storage import MAGIC, review_storage, review_text

SOURCE = Path('/root/prism-insight/stock_tracking_db.sqlite')
MIN_FREE = 4 * 1024**3
MAX_WAL = 128 * 1024**2


def _bytes(value):
    if isinstance(value, str):
        return value.encode('utf-8')
    if isinstance(value, bytes):
        return value
    raise ValueError('invalid_review_body')


def _valid_row(rowid, slot, sequence, expected_type, expected_sha256):
    return (all(type(v) is int and 0 <= v <= 9223372036854775807 for v in (rowid, slot, sequence))
            and rowid > 0 and expected_type in ('text', 'blob') and isinstance(expected_sha256, str)
            and len(expected_sha256) == 64 and all(c in '0123456789abcdef' for c in expected_sha256))


def compact_one(conn, *, rowid, slot, sequence, expected_type, expected_sha256, guard):
    """Change one unchanged backed-up row, preserving its original logical text."""
    if (not _valid_row(rowid, slot, sequence, expected_type, expected_sha256)
            or not callable(guard) or conn.in_transaction):
        raise ValueError('invalid_compaction_context')
    if guard() is not True:
        return 'RESOURCE_LIMIT'
    row = conn.execute('SELECT slot,sequence,body FROM llm_scenario_review_events WHERE rowid=?', (rowid,)).fetchone()
    if row is None or row[0] != slot or row[1] != sequence:
        return 'SOURCE_CHANGED'
    original = row[2]
    current_type = 'text' if isinstance(original, str) else 'blob' if isinstance(original, bytes) else 'invalid'
    current_sha = hashlib.sha256(_bytes(original)).hexdigest()
    if current_type != expected_type or current_sha != expected_sha256:
        if (expected_type == 'text' and isinstance(original, bytes) and original.startswith(MAGIC)
                and hashlib.sha256(review_text(original).encode('utf-8')).hexdigest() == expected_sha256):
            candidate = original
        else:
            return 'SOURCE_CHANGED'
    elif not isinstance(original, str):
        review_text(original)
        candidate = original
    else:
        candidate = review_storage(original)
    if isinstance(original, str) and review_text(candidate) != original:
        raise ValueError('review_roundtrip_mismatch')
    try:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT slot,sequence,body FROM llm_scenario_review_events WHERE rowid=?', (rowid,)).fetchone()
        if current is None or tuple(current) != (slot, sequence, original):
            conn.rollback()
            return 'SOURCE_CHANGED'
        if guard() is not True:
            conn.rollback()
            return 'RESOURCE_LIMIT'
        if candidate == original:
            conn.rollback()
            return 'UNCHANGED'
        updated = conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE rowid=? AND body=?',
                               (candidate, rowid, original))
        if updated.rowcount != 1:
            raise ValueError('review_source_changed')
        conn.commit()
        return 'UPDATED'
    except BaseException:
        conn.rollback()
        raise


def source_identity(path):
    stat = path.stat()
    return hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()


def resource_guard(path, identity):
    """Fail closed outside an idle decision window or if storage is constrained."""
    try:
        if not 25 <= int(time.strftime('%S')) <= 49 or source_identity(path) != identity:
            return False
        stats = os.statvfs(path)
        wal = Path(str(path) + '-wal')
        if stats.f_bavail * stats.f_frsize < MIN_FREE or (wal.exists() and wal.stat().st_size >= MAX_WAL):
            return False
        process = subprocess.run(['pgrep', '-f', '[p]ython.*-m live.scenario_runner'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
        return process.returncode == 1
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def compact_batch(conn, manifest, guard):
    if not isinstance(manifest, dict) or set(manifest) != {'source_identity', 'rows'}:
        raise ValueError('invalid_manifest')
    identity, rows = manifest['source_identity'], manifest['rows']
    if (not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
            or not isinstance(rows, list) or not 1 <= len(rows) <= 20
            or not all(isinstance(row, dict) and set(row) == {'rowid','slot','sequence','expected_type','expected_sha256'}
                       and _valid_row(**row) for row in rows)):
        raise ValueError('invalid_manifest')
    ids = [row['rowid'] for row in rows]
    if any(type(i) is not int or i < 1 for i in ids) or ids != sorted(set(ids)):
        raise ValueError('invalid_manifest')
    counts = {'UPDATED':0, 'UNCHANGED':0, 'SOURCE_CHANGED':0, 'RESOURCE_LIMIT':0, 'ERROR':0}
    last = None
    for row in rows:
        try:
            status = compact_one(conn, **row, guard=guard)
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
    if not isinstance(identity, str) or source_identity(SOURCE) != identity:
        raise ValueError('source_identity_changed')
    guard = lambda: resource_guard(SOURCE, identity)
    if guard() is not True:
        print(json.dumps({'status':'RESOURCE_LIMIT','counts':{},'last_rowid':None}))
        return
    with sqlite3.connect(SOURCE, timeout=0, isolation_level=None) as conn:
        result = compact_batch(conn, manifest, guard)
    print(json.dumps({'status':'STOPPED' if any(result['counts'][key] for key in ('SOURCE_CHANGED','RESOURCE_LIMIT','ERROR'))
                      else 'COMPLETE', **result}))


if __name__ == '__main__':
    main()
