"""Identify strictly flat WAIT tapes for later archive and delivery verification.

This module never deletes data. A candidate is not proof of safe deletion.
"""
import hashlib
import json
import math
from contextlib import contextmanager
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys

if 'btc_review_storage_embedded' in sys.modules:
    from btc_review_storage_embedded import review_text
else:
    from observability.review_storage import review_text
if 'btc_review_refs_embedded' in sys.modules:
    from btc_review_refs_embedded import read_tape
else:
    from utils.btc_review_references import read_tape


def candidates(conn, now, *, after_slot=-1, limit=100, guard=None):
    if (type(now) not in (int, float) or not math.isfinite(now) or now <= 7 * 86400
            or type(after_slot) is not int or after_slot < -1
            or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError('retention_input_invalid')
    cutoff_slot = int((now - 7 * 86400) // 300)
    rows = conn.execute(
        'SELECT t.rowid,t.slot,t.body,d.context,d.proposal,d.outcome '
        'FROM llm_scenario_review_tapes t LEFT JOIN llm_scenario_decisions d ON d.slot=t.slot '
        'WHERE t.slot>? AND t.slot<? ORDER BY t.slot LIMIT ?', (after_slot, cutoff_slot, limit))
    found, last, scanned = [], after_slot, 0
    for rowid, slot, tape, context, proposal, outcome in rows:
        if guard is not None:
            guard()
        if type(rowid) is not int or type(slot) is not int or slot < 0:
            raise ValueError('retention_source_invalid')
        last = slot
        scanned += 1
        try:
            ctx, plan, result = json.loads(context), json.loads(proposal), json.loads(outcome)
        except (TypeError, ValueError):
            continue
        if not all(isinstance(item, dict) for item in (ctx, plan, result)):
            continue
        if (ctx.get('scenario_id', True) is not None or ctx.get('positions') != []
                or ctx.get('pending_entries') != [] or ctx.get('unknown_intents') is not False
                or ctx.get('historical_unsettled') != [] or ctx.get('incident_epoch_blocked') is not False
                or ctx.get('recovery_observing') is not False or plan.get('action') != 'WAIT'
                or result.get('status') != 'wait'):
            continue
        original = read_tape(tape, conn, slot, decode_legacy=review_text)
        digest = hashlib.sha256(original.encode('utf-8')).hexdigest()
        decision = hashlib.sha256(json.dumps([context, proposal, outcome], ensure_ascii=True,
                                              separators=(',', ':')).encode()).hexdigest()
        found.append({'rowid': rowid, 'slot': slot, 'tape_sha256': digest, 'decision_sha256': decision})
    return {'cutoff_slot': cutoff_slot, 'last_scanned_slot': last, 'scanned': scanned, 'candidates': found}


def snapshot_index(conn, now, *, guard=None):
    """Bound the inventory; the receiver can independently recompute it."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'llm_scenario_review_tapes', 'llm_scenario_decisions'}
    if not required & tables:
        return None
    if not required <= tables:
        raise ValueError('retention_schema_incomplete')
    last, scanned, found = -1, 0, []
    while scanned < 2000:
        size = min(100, 2000 - scanned)
        result = candidates(conn, now, after_slot=last, limit=size, guard=guard)
        found.extend(result['candidates'])
        scanned += result['scanned']
        last = result['last_scanned_slot']
        if result['scanned'] < size:
            break
    return {'version': 1, 'as_of': int(now), 'cutoff_slot': int((now - 7 * 86400) // 300),
            'last_scanned_slot': last, 'scanned': scanned, 'truncated': scanned == 2000,
            'candidates': found}


def matching_evidence(current, archived, delivered, source_identity):
    """Match snapshots and ClickStack summaries; archive verification is separate."""
    if (not isinstance(source_identity, str) or len(source_identity) != 64
            or any(c not in '0123456789abcdef' for c in source_identity)):
        raise ValueError('retention_identity_invalid')
    source_rows = current['candidates']
    archive_rows = archived['candidates']
    if not all(isinstance(rows, list) for rows in (source_rows, archive_rows, delivered)):
        raise ValueError('retention_evidence_invalid')
    archive_by_slot = {row['slot']: row for row in archive_rows}
    if len(archive_by_slot) != len(archive_rows):
        raise ValueError('retention_archive_ambiguous')
    delivery_by_id = {}
    for row in delivered:
        event_id = row['event_id']
        if event_id in delivery_by_id and delivery_by_id[event_id] != row:
            raise ValueError('retention_delivery_ambiguous')
        delivery_by_id[event_id] = row
    approved = []
    seen = set()
    for row in source_rows:
        slot, rowid = row['slot'], row['rowid']
        if type(slot) is not int or type(rowid) is not int or slot in seen:
            raise ValueError('retention_source_ambiguous')
        seen.add(slot)
        if archive_by_slot.get(slot) != row:
            continue
        event_id = hashlib.sha256(f'{source_identity}:llm_scenario_review_tapes:{rowid}'.encode()).hexdigest()[:32]
        receipt = delivery_by_id.get(event_id)
        if (receipt and receipt.get('source_identity') == source_identity
                and receipt.get('event_type') == 'btc.research.review_tape'
                and receipt.get('body_sha256') == row['tape_sha256']
                and receipt.get('tape_hash_status') == 'MATCH'):
            approved.append(row)
    return approved


def _delete_matched(conn, exporter, archived, delivered, source_identity, now, *, guard, lease_alive):
    """Delete only rechecked rows; caller must verify archive and hold its lease."""
    if (conn.in_transaction or exporter.in_transaction or not callable(guard) or not callable(lease_alive)
            or not isinstance(archived.get('candidates'), list) or len(archived['candidates']) > 100):
        raise ValueError('retention_transaction_invalid')
    if guard() is not True or lease_alive() is not True:
        raise ValueError('retention_guard_failed')
    try:
        conn.execute('BEGIN IMMEDIATE')
        exporter.execute('BEGIN IMMEDIATE')
        identity = exporter.execute("SELECT value FROM meta WHERE key='source_identity'").fetchone()
        cursor = exporter.execute("SELECT seq FROM cursors WHERE stream='llm_scenario_review_tapes'").fetchone()
        saved_anchor = exporter.execute("SELECT ref FROM anchors WHERE stream='llm_scenario_review_tapes'").fetchone()
        pending = exporter.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        if (not identity or identity[0] != source_identity or not cursor or type(cursor[0]) is not int
                or cursor[0] <= 0 or not saved_anchor or pending):
            raise ValueError('retention_export_state_invalid')
        anchor = conn.execute('SELECT slot,body FROM llm_scenario_review_tapes WHERE rowid=?', (cursor[0],)).fetchone()
        from observability.btc_scenario_export import _anchor_hash
        if not anchor or _anchor_hash('llm_scenario_review_tapes', {'slot': anchor[0], 'body': anchor[1]}, conn) != saved_anchor[0]:
            raise ValueError('retention_export_anchor_changed')
        current = []
        for item in archived['candidates']:
            slot = item['slot']
            if type(slot) is not int or slot < 0:
                raise ValueError('retention_source_invalid')
            rows = candidates(conn, now, after_slot=slot - 1, limit=1)['candidates']
            if rows and rows[0]['slot'] == slot:
                current.append(rows[0])
        matched = matching_evidence({'candidates': current}, archived, delivered, source_identity)
        removed = []
        for item in matched:
            if item['rowid'] >= cursor[0]:
                continue
            if guard() is not True or lease_alive() is not True:
                raise ValueError('retention_guard_failed')
            changed = conn.execute('DELETE FROM llm_scenario_review_tapes WHERE rowid=? AND slot=?',
                                   (item['rowid'], item['slot'])).rowcount
            if changed != 1:
                raise ValueError('retention_source_changed')
            removed.append(item['slot'])
        if guard() is not True or lease_alive() is not True:
            raise ValueError('retention_guard_failed')
        conn.commit()
        exporter.rollback()
        return removed
    except BaseException:
        conn.rollback()
        exporter.rollback()
        raise


def fetch_deliveries(rows, source_identity):
    """Read a bounded set of ClickStack summaries using the SELECT-only account."""
    if (not isinstance(source_identity, str) or not re.fullmatch(r'[a-f0-9]{64}', source_identity)
            or not isinstance(rows, list) or len(rows) > 100
            or any(not isinstance(row, dict) or type(row.get('rowid')) is not int or row['rowid'] < 0 for row in rows)):
        raise ValueError('retention_query_invalid')
    if not rows:
        return []
    ids = {hashlib.sha256(f'{source_identity}:llm_scenario_review_tapes:{row["rowid"]}'.encode()).hexdigest()[:32]
           for row in rows}
    sql = ("SELECT JSONExtractString(Body,'event_id') AS event_id, "
           "JSONExtractString(Body,'event_type') AS event_type, "
           "JSONExtractString(Body,'attributes','source_identity') AS source_identity, "
           "JSONExtractString(Body,'attributes','body_sha256') AS body_sha256, "
           "JSONExtractString(Body,'attributes','tape_hash_status') AS tape_hash_status "
           "FROM otel_logs WHERE LogAttributes['event.name']='btc.research.review_tape' "
           "AND JSONExtractString(Body,'event_id') IN (" + ','.join("'" + item + "'" for item in sorted(ids)) + ') LIMIT 201')
    from tools.clickstack_query import build_program
    from utils.btc_offsite_archive import backend_host
    host = backend_host(Path('/etc/prism-observability/tunnel.env'))
    if host is None:
        raise ValueError('retention_backend_missing')
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@' + host,
               'set -a; . /etc/prism-observability/reader.env; set +a; python3 -']
    result = subprocess.run(command, input=build_program(sql, {}), text=True,
                            capture_output=True, check=True, timeout=45)
    delivered = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(delivered) > 200 or any(not isinstance(row, dict) or row.get('event_id') not in ids for row in delivered):
        raise ValueError('retention_delivery_invalid')
    return delivered


def verified_archive_index(manifest_bytes, ack, source_identity):
    """Bind a candidate index to the receiver's independently verified archive."""
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > 4 * 1024 * 1024:
        raise ValueError('retention_manifest_invalid')
    try:
        manifest = json.loads(manifest_bytes)
        started, completed = manifest['started_at'], manifest['completed_at']
        index = manifest['btc_retention_index']
        if (not isinstance(source_identity, str) or not re.fullmatch(r'[a-f0-9]{64}', source_identity)
                or not isinstance(ack, dict) or ack.get('status') != 'VERIFIED'
                or ack.get('retention_index_verified') is not True
                or not isinstance(ack.get('archive'), str)
                or not re.fullmatch(r'archive-[a-f0-9]{32}', ack['archive'])
                or ack.get('manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest()
                or manifest.get('source_identity_sha256') != source_identity
                or any(not isinstance(manifest.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', manifest[key])
                       or ack.get(label) != manifest[key] for key, label in
                       (('raw_sha256', 'raw_sha256'), ('compressed_sha256', 'compressed_sha256')))
                or type(started) not in (int, float) or type(completed) not in (int, float)
                or not math.isfinite(started) or not math.isfinite(completed)
                or started <= 7 * 86400 or completed < started
                or not isinstance(index, dict) or type(index.get('version')) is not int
                or index['version'] != 1 or type(index.get('as_of')) is not int or index['as_of'] != int(started)
                or type(index.get('cutoff_slot')) is not int or index['cutoff_slot'] != int((started - 7 * 86400) // 300)
                or not isinstance(index.get('candidates'), list)):
            raise ValueError('retention_archive_unverified')
        return index
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('retention_archive_unverified') from exc


@contextmanager
def locked_remote_archive(ack):
    """Verify the archive while holding the receiver's lock until cleanup ends."""
    if (not isinstance(ack, dict) or ack.get('status') != 'VERIFIED'
            or ack.get('retention_index_verified') is not True
            or not isinstance(ack.get('archive'), str)
            or not re.fullmatch(r'archive-[a-f0-9]{32}', ack['archive'])
            or any(not isinstance(ack.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', ack[key])
                   for key in ('raw_sha256', 'compressed_sha256', 'manifest_sha256',
                               'bundle_sha256', 'archive_metadata_sha256'))):
        raise ValueError('retention_archive_unverified')
    expected = {key: ack[key] for key in ('archive', 'raw_sha256', 'compressed_sha256', 'manifest_sha256',
                                           'bundle_sha256', 'archive_metadata_sha256')}
    remote = r'''
import fcntl,hashlib,json,os,select,stat,sys,time
from pathlib import Path
expected=json.loads(sys.stdin.readline())
root=Path('/root/prism-btc-archive'); target=root/expected['archive']
if root.is_symlink() or target.is_symlink() or not target.is_dir() or target.resolve().parent!=root.resolve():
    raise ValueError('archive_path_invalid')
lock=root/'.receive.lock'
if lock.is_symlink():raise ValueError('archive_lock_invalid')
lock_fd=os.open(lock,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
lock_stat=os.fstat(lock_fd)
if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_uid!=os.getuid() or lock_stat.st_nlink!=1:
    raise ValueError('archive_lock_invalid')
os.fchmod(lock_fd,0o600);fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
if target.stat().st_uid!=os.getuid() or target.stat().st_mode & 0o077:
    raise ValueError('archive_permissions_invalid')
deadline=time.monotonic()+35
files={'snapshot.sqlite.gz':'compressed_sha256','snapshot.manifest.json':'manifest_sha256',
       'history.bundle':'bundle_sha256','archive.json':'archive_metadata_sha256'}
for name,key in files.items():
    p=target/name; before=p.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_mode & 0o077:
        raise ValueError('archive_file_invalid')
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            if time.monotonic()>deadline:raise TimeoutError('archive_check_deadline')
            h.update(chunk)
    after=p.lstat()
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or h.hexdigest()!=expected[key]:
        raise ValueError('archive_file_changed')
receipt=target/'receipt.json'; st=receipt.lstat()
if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode & 0o077 or st.st_size>65536:
    raise ValueError('archive_receipt_invalid')
record=json.loads(receipt.read_text())
if record.get('status')!='VERIFIED' or record.get('retention_index_verified') is not True or any(record.get(k)!=v for k,v in expected.items() if k!='archive'):
    raise ValueError('archive_receipt_mismatch')
print('VERIFIED',flush=True)
select.select([sys.stdin],[],[],30)
'''
    from utils.btc_offsite_archive import backend_host
    host = backend_host(Path('/etc/prism-observability/tunnel.env'))
    if host is None:
        raise ValueError('retention_backend_missing')
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@' + host,
               '/usr/bin/python3 -c ' + shlex.quote(remote)]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    try:
        proc.stdin.write(json.dumps(expected) + '\n')
        proc.stdin.flush()
        readable, _, _ = select.select([proc.stdout], [], [], 45)
        if not readable or proc.stdout.readline().strip() != 'VERIFIED' or proc.poll() is not None:
            raise ValueError('retention_archive_unverified')
        yield lambda: proc.poll() is None
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        proc.stdout.close()


def verify_remote_archive(ack):
    with locked_remote_archive(ack):
        pass
