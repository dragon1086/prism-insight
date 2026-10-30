"""Bounded, backup-matched compaction of BTC account evidence."""
import hashlib
import json
import math
import re

from observability.btc_scenario_export import _anchor_hash
from observability.review_storage import MAGIC, review_text
from live.scenario_review_storage import snapshot_storage


def plan_old_accounts(conn, identity, cutoff, *, after_rowid=0, limit=1000):
    if (not conn.in_transaction or conn.execute('PRAGMA query_only').fetchone() != (1,)
            or not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity)
            or type(cutoff) not in (int, float) or not math.isfinite(cutoff) or cutoff <= 0
            or type(after_rowid) is not int or after_rowid < 0
            or type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError('invalid_plan_context')
    if [row[2] for row in conn.execute('PRAGMA index_info(llm_scenario_broker_evidence_kind)')] != ['kind']:
        raise ValueError('account_index_missing')
    selected = conn.execute("SELECT rowid,captured_at,body FROM llm_scenario_broker_evidence INDEXED BY llm_scenario_broker_evidence_kind WHERE kind='account' AND rowid>? AND captured_at<? ORDER BY rowid LIMIT ?", (after_rowid, cutoff, limit)).fetchall()
    rows = []
    for rowid, captured, body in selected:
        if (type(rowid) is not int or rowid <= after_rowid or type(captured) not in (int, float)
                or not math.isfinite(captured) or captured <= 0 or captured >= cutoff or not isinstance(body, str)):
            raise ValueError('invalid_backup_row')
        raw = body.encode()
        if len(raw) > 8_000_000 or not isinstance(json.loads(body), dict):
            raise ValueError('invalid_backup_body')
        rows.append({'rowid': rowid, 'captured_at': captured, 'sha256': hashlib.sha256(raw).hexdigest()})
    return {'source_identity': identity, 'cutoff': cutoff, 'after_rowid': after_rowid, 'rows': rows}


def compact_batch(conn, rows, guard):
    if conn.in_transaction or not callable(guard) or not 1 <= len(rows) <= 1000:
        raise ValueError('invalid_context')
    ids = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'rowid', 'captured_at', 'sha256'}
                or type(row['rowid']) is not int or row['rowid'] <= 0
                or type(row['captured_at']) not in (int, float) or not math.isfinite(row['captured_at']) or row['captured_at'] <= 0
                or not isinstance(row['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', row['sha256'])):
            raise ValueError('invalid_manifest')
        ids.append(row['rowid'])
    if ids != sorted(set(ids)):
        raise ValueError('invalid_manifest')
    if guard() is not True:
        return {'status': 'DEFERRED', 'updated': 0}
    try:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='llm_scenario_broker_evidence' LIMIT 1").fetchone():
            raise ValueError('unsupported_trigger')
        updated = unchanged = saved = 0
        for index, proof in enumerate(rows):
            if index % 25 == 0 and guard() is not True:
                raise ValueError('resource_guard')
            row = conn.execute('SELECT kind,captured_at,body FROM llm_scenario_broker_evidence WHERE rowid=?', (proof['rowid'],)).fetchone()
            if not row or row[0] != 'account' or row[1] != proof['captured_at']:
                raise ValueError('source_changed')
            current = row[2]
            if isinstance(current, str):
                raw = current
            elif isinstance(current, bytes) and current.startswith(MAGIC):
                raw = review_text(current)
            else:
                raise ValueError('source_type')
            if hashlib.sha256(raw.encode()).hexdigest() != proof['sha256']:
                raise ValueError('source_changed')
            if isinstance(current, bytes):
                unchanged += 1
                continue
            candidate = snapshot_storage(raw)
            if review_text(candidate) != raw:
                raise ValueError('roundtrip_failed')
            if candidate == current:
                unchanged += 1
                continue
            if _anchor_hash('llm_scenario_broker_evidence', {'kind':'account','body':current}) != _anchor_hash('llm_scenario_broker_evidence', {'kind':'account','body':candidate}):
                raise ValueError('anchor_changed')
            changed = conn.execute('UPDATE llm_scenario_broker_evidence SET body=? WHERE rowid=? AND kind=? AND captured_at=? AND body=?',
                                   (candidate, proof['rowid'], 'account', proof['captured_at'], current)).rowcount
            if changed != 1:
                raise ValueError('source_changed')
            updated += 1
            saved += len(raw.encode()) - len(candidate)
        if guard() is not True:
            raise ValueError('resource_guard')
        conn.commit()
        return {'status': 'COMMITTED', 'updated': updated, 'unchanged': unchanged, 'saved_bytes': saved}
    except BaseException:
        conn.rollback()
        raise


def compact_attested_batch(conn, manifest, source_identity, now, guard):
    fields = {'source_identity', 'cutoff', 'after_rowid', 'rows', 'backup_proof_sha256'}
    if (not isinstance(manifest, dict) or set(manifest) != fields
            or not isinstance(source_identity, str) or not re.fullmatch('[a-f0-9]{64}', source_identity)
            or manifest['source_identity'] != source_identity
            or not isinstance(manifest['backup_proof_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', manifest['backup_proof_sha256'])
            or type(now) not in (int, float) or not math.isfinite(now)
            or type(manifest['cutoff']) not in (int, float) or not math.isfinite(manifest['cutoff'])
            or not 0 < manifest['cutoff'] <= now - 7 * 86400
            or type(manifest['after_rowid']) is not int or manifest['after_rowid'] < 0
            or not isinstance(manifest['rows'], list) or not 1 <= len(manifest['rows']) <= 1000):
        raise ValueError('invalid_attested_manifest')
    if any(not isinstance(row, dict) or type(row.get('rowid')) is not int
           or row['rowid'] <= manifest['after_rowid']
           or type(row.get('captured_at')) not in (int, float)
           or not math.isfinite(row['captured_at']) or not 0 < row['captured_at'] < manifest['cutoff']
           for row in manifest['rows']):
        raise ValueError('invalid_attested_row')
    result = compact_batch(conn, manifest['rows'], guard)
    result['manifest_sha256'] = manifest_sha256(manifest)
    return result


def manifest_sha256(manifest):
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def verify_next_batch(previous, receipt, current):
    if (not isinstance(previous, dict) or not isinstance(current, dict) or not isinstance(receipt, dict)
            or receipt.get('status') != 'COMMITTED' or receipt.get('manifest_sha256') != manifest_sha256(previous)
            or not isinstance(previous.get('rows'), list)
            or not previous['rows'] or type(receipt.get('updated')) is not int
            or type(receipt.get('unchanged')) is not int
            or receipt['updated'] < 0 or receipt['unchanged'] < 0
            or receipt['updated'] + receipt['unchanged'] != len(previous['rows'])
            or any(previous.get(k) != current.get(k) for k in ('source_identity', 'cutoff', 'backup_proof_sha256'))
            or current.get('after_rowid') != previous['rows'][-1].get('rowid')):
        raise ValueError('batch_continuity_invalid')
    return True


def compact_file_batch(conn, manifest, now, guard):
    from pathlib import Path
    if conn.execute('PRAGMA busy_timeout').fetchone() != (0,):
        raise ValueError('nonblocking_connection_required')
    main = [row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main']
    if len(main) != 1 or not main[0]:
        raise ValueError('durable_database_required')
    path = Path(main[0])
    stat = path.stat()
    identity = hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}'.encode()).hexdigest()
    return compact_attested_batch(conn, manifest, identity, now, guard)


def guarded_operational_batch(conn, manifest, expected_manifest_sha, expected_backup_sha):
    """Apply only a manifest sealed by a separately verified backup planner."""
    if (not isinstance(expected_manifest_sha, str) or not re.fullmatch('[a-f0-9]{64}', expected_manifest_sha)
            or not isinstance(expected_backup_sha, str) or not re.fullmatch('[a-f0-9]{64}', expected_backup_sha)
            or manifest_sha256(manifest) != expected_manifest_sha
            or manifest.get('backup_proof_sha256') != expected_backup_sha):
        raise ValueError('backup_binding_invalid')
    import time
    from pathlib import Path
    from live.shared_entry_coordinator import mutation_lock
    from tools.btc_review_event_compaction import SOURCE, resource_guard, source_identity
    from utils.btc_review_retention import _backup_idle, _runtime_safe
    main = [row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main']
    source = Path(SOURCE)
    if len(main) != 1 or not main[0] or source.is_symlink() or Path(main[0]).resolve() != source.resolve():
        raise ValueError('source_path_invalid')
    identity = source_identity(source)
    if manifest.get('source_identity') != identity:
        raise ValueError('source_identity_changed')
    def guard():
        return resource_guard(source, identity) and _backup_idle(source) and _runtime_safe(conn)
    with mutation_lock(conn):
        return compact_file_batch(conn, manifest, time.time(), guard)


def plan_attested_accounts(proof_path, cutoff, *, after_rowid=0, limit=1000):
    from tools.btc_review_backup_manifest import plan_pairs_from_attestation
    def planner(conn, identity, *, after_rowid):
        return plan_old_accounts(conn, identity, cutoff, after_rowid=after_rowid, limit=limit)
    return plan_pairs_from_attestation(proof_path, after_rowid=after_rowid, planner=planner)
