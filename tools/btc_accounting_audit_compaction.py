"""Losslessly compact old accounting audits proven by a verified backup."""
import hashlib
import json
import math
import re

from live.scenario_review_storage import review_storage
from observability.btc_scenario_export import _anchor_hash
from observability.review_storage import MAX_BYTES
from tools.btc_account_compaction import MAX_BATCH_SOURCE_BYTES, manifest_sha256
from utils.btc_accounting_audit_references import read_accounting


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plan_old_audits(conn, identity, cutoff, *, after_rowid=0, limit=1000):
    if (not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone() != (1,)
            or not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity)
            or type(cutoff) not in (int, float) or not math.isfinite(cutoff) or cutoff <= 0
            or type(after_rowid) is not int or after_rowid < 0
            or type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError('invalid_plan_context')
    query = "SELECT rowid,kind,observed_at,CASE WHEN kind='accounting_observation' THEN body END FROM llm_scenario_audit_events WHERE rowid>? ORDER BY rowid LIMIT ?"
    proofs, scanned, source_bytes = [], after_rowid, 0
    for rowid, kind, captured, body in conn.execute(query, (after_rowid, limit)):
        if kind != 'accounting_observation':
            scanned = rowid
            continue
        if type(captured) not in (int, float) or not math.isfinite(captured) or captured <= 0:
            raise ValueError('invalid_backup_time')
        if captured >= cutoff or isinstance(body, bytes):
            scanned = rowid
            continue
        if not isinstance(body, str):
            raise ValueError('invalid_backup_body')
        size = len(body.encode('utf-8'))
        if size > min(MAX_BATCH_SOURCE_BYTES, MAX_BYTES):
            raise ValueError('batch_size_limit')
        if source_bytes + size > MAX_BATCH_SOURCE_BYTES or len(proofs) == 100:
            break
        original = read_accounting(body, conn)
        if not isinstance(json.loads(body), dict) or not isinstance(json.loads(original), dict):
            raise ValueError('invalid_backup_body')
        source_bytes += size
        proofs.append({'rowid': rowid, 'observed_at': captured, 'stored_sha256': _sha(body),
                       'original_sha256': _sha(original)})
        scanned = rowid
    return {'source_identity': identity, 'cutoff': cutoff, 'after_rowid': after_rowid,
            'scanned_through': scanned, 'rows': proofs}


def compact_attested_batch(conn, manifest, identity, now, guard):
    """Caller must authenticate the backup proof and identity before invoking."""
    fields = {'source_identity', 'cutoff', 'after_rowid', 'scanned_through', 'rows', 'backup_proof_sha256'}
    if (not isinstance(manifest, dict) or set(manifest) != fields or not callable(guard) or conn.in_transaction
            or not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity)
            or manifest['source_identity'] != identity or not isinstance(manifest['backup_proof_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', manifest['backup_proof_sha256'])
            or type(now) not in (int, float) or not math.isfinite(now)
            or type(manifest['cutoff']) not in (int, float) or not math.isfinite(manifest['cutoff'])
            or not 0 < manifest['cutoff'] <= now-7*86400
            or type(manifest['after_rowid']) is not int or manifest['after_rowid'] < 0
            or type(manifest['scanned_through']) is not int or not manifest['after_rowid'] < manifest['scanned_through'] <= 9223372036854775807
            or not isinstance(manifest['rows'], list) or not 1 <= len(manifest['rows']) <= 100):
        raise ValueError('invalid_attested_manifest')
    ids = []
    for row in manifest['rows']:
        if (not isinstance(row, dict) or set(row) != {'rowid', 'observed_at', 'stored_sha256', 'original_sha256'}
                or type(row['rowid']) is not int or not manifest['after_rowid'] < row['rowid'] <= manifest['scanned_through']
                or type(row['observed_at']) not in (int, float) or not math.isfinite(row['observed_at'])
                or not 0 < row['observed_at'] < manifest['cutoff']
                or any(not isinstance(row[k], str) or not re.fullmatch('[a-f0-9]{64}', row[k])
                       for k in ('stored_sha256', 'original_sha256'))):
            raise ValueError('invalid_attested_row')
        ids.append(row['rowid'])
    if ids != sorted(set(ids)):
        raise ValueError('invalid_attested_row')
    if guard() is not True:
        return {'status': 'DEFERRED', 'updated': 0, 'manifest_sha256': manifest_sha256(manifest)}
    try:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='llm_scenario_audit_events' LIMIT 1").fetchone():
            raise ValueError('unsupported_trigger')
        updated = unchanged = saved = source_bytes = 0
        for index, proof in enumerate(manifest['rows']):
            if index % 25 == 0 and guard() is not True:
                raise ValueError('resource_guard')
            row = conn.execute('SELECT kind,observed_at,body FROM llm_scenario_audit_events WHERE rowid=?', (proof['rowid'],)).fetchone()
            if not row or row[0] != 'accounting_observation' or row[1] != proof['observed_at'] or not isinstance(row[2], str):
                raise ValueError('source_changed')
            raw = row[2]
            source_bytes += len(raw.encode('utf-8'))
            if source_bytes > MAX_BATCH_SOURCE_BYTES or _sha(raw) != proof['stored_sha256']:
                raise ValueError('source_changed')
            original = read_accounting(raw, conn)
            if _sha(original) != proof['original_sha256']:
                raise ValueError('referenced_source_changed')
            candidate = review_storage(raw)
            if read_accounting(candidate, conn) != original:
                raise ValueError('roundtrip_failed')
            if candidate == raw:
                unchanged += 1
                continue
            before = {'kind': 'accounting_observation', 'body': raw}
            after = {'kind': 'accounting_observation', 'body': candidate}
            if _anchor_hash('llm_scenario_audit_events', before, conn) != _anchor_hash('llm_scenario_audit_events', after, conn):
                raise ValueError('anchor_changed')
            changed = conn.execute('UPDATE llm_scenario_audit_events SET body=? WHERE rowid=? AND kind=? AND observed_at=? AND body=?',
                                   (candidate, proof['rowid'], 'accounting_observation', proof['observed_at'], raw)).rowcount
            if changed != 1:
                raise ValueError('source_changed')
            updated += 1
            saved += len(raw.encode('utf-8'))-len(candidate)
        if guard() is not True:
            raise ValueError('resource_guard')
        conn.commit()
        return {'status': 'COMMITTED', 'updated': updated, 'unchanged': unchanged,
                'saved_bytes': saved, 'manifest_sha256': manifest_sha256(manifest)}
    except BaseException:
        conn.rollback()
        raise


def guarded_operational_batch(conn, manifest, expected_manifest_sha, expected_backup_sha):
    """Run only after a separate trusted backup planner seals the manifest."""
    import time
    from pathlib import Path
    from live.shared_entry_coordinator import mutation_lock
    from tools.btc_account_compaction import _trusted_b3_readonly_fd
    from tools.btc_review_event_compaction import SOURCE, resource_guard, source_identity
    from utils.btc_review_retention import _backup_idle, _runtime_safe

    if (not isinstance(expected_manifest_sha, str) or not re.fullmatch('[a-f0-9]{64}', expected_manifest_sha)
            or not isinstance(expected_backup_sha, str) or not re.fullmatch('[a-f0-9]{64}', expected_backup_sha)
            or not isinstance(manifest, dict) or manifest_sha256(manifest) != expected_manifest_sha
            or manifest.get('backup_proof_sha256') != expected_backup_sha
            or conn.execute('PRAGMA busy_timeout').fetchone() != (0,)):
        raise ValueError('backup_binding_invalid')
    main = [row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main']
    source = Path(SOURCE)
    if len(main) != 1 or not main[0] or source.is_symlink() or Path(main[0]).resolve() != source.resolve():
        raise ValueError('source_path_invalid')
    identity = source_identity(source)
    if manifest.get('source_identity') != identity:
        raise ValueError('source_identity_changed')
    wal = conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal'
    def guard():
        return (300-time.time()%300 >= 120 and resource_guard(source, identity)
                and _backup_idle(source, allow_reader=lambda process, fd: wal and _trusted_b3_readonly_fd(process, fd))
                and _runtime_safe(conn))
    with mutation_lock(conn):
        return compact_attested_batch(conn, manifest, identity, time.time(), guard)


def plan_attested_audits(proof_path, cutoff, *, after_rowid=0, limit=1000):
    from functools import partial
    from tools.btc_review_backup_manifest import plan_pairs_from_attestation
    return plan_pairs_from_attestation(proof_path, after_rowid=after_rowid,
                                       planner=partial(plan_old_audits, cutoff=cutoff, limit=limit))


def run_bounded_batches(proof_path, cutoff, plan, apply, *, after_rowid=0, max_batches=10, max_scans=30, clock=None):
    """Advance over empty bounded scans without confusing them with end of data."""
    import time
    clock = time.monotonic if clock is None else clock
    if (type(after_rowid) is not int or after_rowid < 0 or type(max_batches) is not int
            or not 1 <= max_batches <= 10 or type(max_scans) is not int or not 1 <= max_scans <= 30
            or not callable(plan) or not callable(apply) or not callable(clock)):
        raise ValueError('invalid_run_limits')
    deadline = clock()+60
    totals = {'updated': 0, 'unchanged': 0, 'saved_bytes': 0, 'batches': 0}
    binding = None
    for _ in range(max_scans):
        if clock() >= deadline:
            return {'status':'DEADLINE_REACHED', 'after_rowid':after_rowid, **totals}
        try:
            manifest = plan(proof_path, cutoff, after_rowid=after_rowid)
            if (not isinstance(manifest, dict) or manifest.get('after_rowid') != after_rowid
                    or type(manifest.get('scanned_through')) is not int or manifest['scanned_through'] < after_rowid
                    or not isinstance(manifest.get('rows'), list)):
                raise ValueError('invalid_scan')
            current = (manifest.get('source_identity'), manifest.get('backup_proof_sha256'), manifest.get('cutoff'))
            if binding is None:
                binding = current
            if current != binding or current[2] != cutoff:
                raise ValueError('backup_binding_changed')
            if not manifest['rows']:
                if manifest['scanned_through'] == after_rowid:
                    return {'status':'COMPLETE', 'after_rowid':after_rowid, **totals}
                after_rowid = manifest['scanned_through']
                continue
            rowids = [row.get('rowid') if isinstance(row, dict) else None for row in manifest['rows']]
            if (len(rowids) > 100 or any(type(i) is not int or not after_rowid < i <= manifest['scanned_through'] for i in rowids)
                    or rowids != sorted(set(rowids))):
                raise ValueError('invalid_scan_rows')
            seal = manifest_sha256(manifest)
            receipt = apply(manifest, seal, manifest['backup_proof_sha256'])
            if not isinstance(receipt, dict) or receipt.get('manifest_sha256') != seal:
                raise ValueError('batch_receipt_invalid')
            if receipt.get('status') == 'DEFERRED':
                if any(receipt.get(k, 0) != 0 for k in ('updated', 'unchanged', 'saved_bytes')):
                    raise ValueError('batch_receipt_invalid')
                return {'status':'DEFERRED', 'after_rowid':after_rowid, **totals}
            if (receipt.get('status') != 'COMMITTED' or any(type(receipt.get(k)) is not int or receipt[k] < 0
                    for k in ('updated', 'unchanged', 'saved_bytes'))
                    or receipt['updated']+receipt['unchanged'] != len(manifest['rows'])):
                raise ValueError('batch_receipt_invalid')
            for k in ('updated', 'unchanged', 'saved_bytes'):
                totals[k] += receipt[k]
            totals['batches'] += 1
            after_rowid = manifest['scanned_through']
            if totals['batches'] == max_batches:
                return {'status':'LIMIT_REACHED', 'after_rowid':after_rowid, **totals}
        except Exception as exc:
            return {'status':'ERROR', 'error_type':type(exc).__name__, 'after_rowid':after_rowid, **totals}
    return {'status':'LIMIT_REACHED', 'after_rowid':after_rowid, **totals}


def main():
    """Apply one manifest sealed by a separately authenticated backup planner."""
    from contextlib import closing
    import sqlite3
    import sys
    from tools.btc_review_event_compaction import SOURCE
    if len(sys.argv) != 4 or sys.argv[1] != '--apply':
        raise ValueError('explicit_apply_required')
    raw = sys.stdin.buffer.read(512*1024+1)
    if len(raw) > 512*1024:
        raise ValueError('manifest_size_limit')
    manifest = json.loads(raw)
    with closing(sqlite3.connect(SOURCE.resolve().as_uri()+'?mode=rw', uri=True, timeout=0)) as conn:
        result = guarded_operational_batch(conn, manifest, sys.argv[2], sys.argv[3])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
