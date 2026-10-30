"""Bounded, backup-matched compaction primitives for BTC review events."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
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
        from utils.btc_review_event_references import read_event
        read_event(original, conn, slot, sequence, decode_legacy=review_text)
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


def compact_backed_model_pair(conn, raw_row, source_row, *, guard):
    """Compact one pair only after the caller verifies both rows against a backup."""
    fields = {'rowid', 'slot', 'sequence', 'expected_type', 'expected_sha256'}
    if (conn.in_transaction or not callable(guard) or not isinstance(raw_row, dict)
            or not isinstance(source_row, dict) or set(raw_row) != fields or set(source_row) != fields
            or not _valid_row(**raw_row) or not _valid_row(**source_row)
            or raw_row['slot'] != source_row['slot'] or raw_row['sequence'] >= 128
            or source_row['sequence'] >= raw_row['sequence']):
        raise ValueError('invalid_compaction_context')
    if guard() is not True:
        return 'RESOURCE_LIMIT'
    from utils.btc_review_event_references import MAX_BYTES, MAGIC as REF_MAGIC, _unpack, compact, read_event
    try:
        conn.execute('BEGIN IMMEDIATE')
        if guard() is not True:
            conn.rollback()
            return 'RESOURCE_LIMIT'
        indexes = [row for row in conn.execute('PRAGMA index_list(llm_scenario_review_events)')
                   if row[1] == 'sqlite_autoindex_llm_scenario_review_events_1']
        columns = [row[2] for row in conn.execute('PRAGMA index_info(sqlite_autoindex_llm_scenario_review_events_1)')]
        if len(indexes) != 1 or indexes[0][2] != 1 or indexes[0][4] != 0 or columns != ['slot', 'sequence']:
            conn.rollback()
            return 'SOURCE_CHANGED'
        def load(proof):
            return conn.execute('SELECT slot,sequence,body FROM llm_scenario_review_events WHERE rowid=?',
                                (proof['rowid'],)).fetchone()
        def matches(proof, row):
            return (row is not None and row[0] == proof['slot'] and row[1] == proof['sequence']
                    and ('text' if isinstance(row[2], str) else 'blob' if isinstance(row[2], bytes) else 'invalid') == proof['expected_type']
                    and hashlib.sha256(_bytes(row[2])).hexdigest() == proof['expected_sha256'])
        source = load(source_row)
        raw = load(raw_row)
        if not matches(source_row, source) or raw is None or raw[0] != raw_row['slot'] or raw[1] != raw_row['sequence']:
            conn.rollback()
            return 'SOURCE_CHANGED'
        slot, sequence = raw_row['slot'], raw_row['sequence']
        if not matches(raw_row, raw):
            value = raw[2]
            if not isinstance(value, bytes) or not value.startswith(REF_MAGIC) or _unpack(value)['source_sequence'] != source_row['sequence']:
                conn.rollback()
                return 'SOURCE_CHANGED'
            original = read_event(value, conn, slot, sequence, decode_legacy=review_text)
            prior = original if raw_row['expected_type'] == 'text' else review_storage(original)
            if hashlib.sha256(_bytes(prior)).hexdigest() != raw_row['expected_sha256']:
                conn.rollback()
                return 'SOURCE_CHANGED'
            conn.rollback()
            return 'UNCHANGED'
        original = read_event(raw[2], conn, slot, sequence, decode_legacy=review_text)
        sources, source_bytes = [], 0
        for number, value in conn.execute('SELECT sequence,body FROM llm_scenario_review_events '
                                          'WHERE slot=? AND sequence<? ORDER BY sequence LIMIT 128', (slot, sequence)):
            text = read_event(value, conn, slot, number, decode_legacy=review_text)
            source_bytes += len(text.encode('utf-8'))
            if source_bytes > MAX_BYTES:
                conn.rollback()
                return 'UNCHANGED'
            sources.append((number, text))
        candidate = compact(original, sources, slot, sequence)
        if (not isinstance(candidate, bytes) or not candidate.startswith(REF_MAGIC)
                or _unpack(candidate)['source_sequence'] != source_row['sequence']
                or len(candidate) >= len(_bytes(raw[2]))):
            conn.rollback()
            return 'UNCHANGED'
        if read_event(candidate, conn, slot, sequence, decode_legacy=review_text) != original:
            raise ValueError('review_roundtrip_mismatch')
        if guard() is not True:
            conn.rollback()
            return 'RESOURCE_LIMIT'
        changed = conn.execute('UPDATE llm_scenario_review_events SET body=? WHERE rowid=? AND body=?',
                               (candidate, raw_row['rowid'], raw[2]))
        if changed.rowcount != 1:
            raise ValueError('review_source_changed')
        conn.commit()
        return 'UPDATED'
    except BaseException:
        conn.rollback()
        raise


def source_identity(path):
    stat = path.stat()
    return hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()


def scenario_runner_idle(proc_root=Path('/proc')):
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                cmdline = (entry / 'cmdline').read_bytes().replace(b'\x00', b' ')
            except FileNotFoundError:
                continue
            runner = re.search(rb'-m\s+live\.scenario_runner(?:\s|$)', cmdline)
            if runner and b'python' in cmdline[:runner.start()]:
                return False
        return True
    except OSError:
        return False


def resource_guard(path, identity):
    """Fail closed outside an idle decision window or if storage is constrained."""
    try:
        if not 25 <= int(time.strftime('%S')) <= 49 or source_identity(path) != identity:
            return False
        stats = os.statvfs(path)
        wal = Path(str(path) + '-wal')
        if stats.f_bavail * stats.f_frsize < MIN_FREE or (wal.exists() and wal.stat().st_size >= MAX_WAL):
            return False
        return scenario_runner_idle()
    except (OSError, ValueError):
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


def compact_backed_pairs(conn, manifest, guard):
    if not isinstance(manifest, dict) or set(manifest) != {'source_identity', 'pairs'}:
        raise ValueError('invalid_manifest')
    identity, pairs = manifest['source_identity'], manifest['pairs']
    fields = {'rowid','slot','sequence','expected_type','expected_sha256'}
    if (not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity)
            or not isinstance(pairs, list) or not 1 <= len(pairs) <= 20
            or not all(isinstance(pair, dict) and set(pair) == {'raw','source'}
                       and all(isinstance(pair[k], dict) and set(pair[k]) == fields and _valid_row(**pair[k])
                               for k in ('raw','source'))
                       and pair['raw']['slot'] == pair['source']['slot']
                       and pair['raw']['sequence'] < 128 and pair['source']['sequence'] < pair['raw']['sequence']
                       for pair in pairs)):
        raise ValueError('invalid_manifest')
    ids = [pair['raw']['rowid'] for pair in pairs]
    if ids != sorted(set(ids)):
        raise ValueError('invalid_manifest')
    counts = {'UPDATED':0, 'UNCHANGED':0, 'SOURCE_CHANGED':0, 'RESOURCE_LIMIT':0, 'ERROR':0}
    last = None
    for pair in pairs:
        try:
            status = compact_backed_model_pair(conn, pair['raw'], pair['source'], guard=guard)
        except Exception:
            counts['ERROR'] += 1
            break
        counts[status] += 1
        if status in ('SOURCE_CHANGED', 'RESOURCE_LIMIT'):
            break
        last = pair['raw']['rowid']
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
    from utils.btc_review_retention import _backup_idle
    guard = lambda: resource_guard(SOURCE, identity) and _backup_idle(SOURCE)
    if guard() is not True:
        print(json.dumps({'status':'RESOURCE_LIMIT','counts':{},'last_rowid':None}))
        return
    with sqlite3.connect(SOURCE, timeout=0, isolation_level=None) as conn:
        from live.shared_entry_coordinator import mutation_lock
        with mutation_lock(conn):
            result = (compact_backed_pairs(conn, manifest, guard) if isinstance(manifest, dict) and 'pairs' in manifest
                      else compact_batch(conn, manifest, guard))
    print(json.dumps({'status':'STOPPED' if any(result['counts'][key] for key in ('SOURCE_CHANGED','RESOURCE_LIMIT','ERROR'))
                      else 'COMPLETE', **result}))


if __name__ == '__main__':
    main()
