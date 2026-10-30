"""Identify strictly flat WAIT tapes for later archive and delivery verification.

This module never deletes data. A candidate is not proof of safe deletion.
"""
import hashlib
import json
import math
from collections import Counter
from contextlib import contextmanager
from contextlib import closing
import os
from pathlib import Path
import re
import select
import shlex
import subprocess  # nosec B404 - validated SSH host and fixed commands
import sqlite3
import stat
import sys
import tempfile
import time

if 'btc_review_storage_embedded' in sys.modules:
    from btc_review_storage_embedded import review_text
else:
    from observability.review_storage import review_text
if 'btc_review_refs_embedded' in sys.modules:
    from btc_review_refs_embedded import MAGIC as REVIEW_REFERENCE_MAGIC, read_tape
else:
    from utils.btc_review_references import MAGIC as REVIEW_REFERENCE_MAGIC, read_tape


def _review_event_text(value, conn, slot, sequence):
    if not isinstance(value, bytes) or not value.startswith(b'PRISM_REVIEW_EVENT_REF_V1\x00'):
        return review_text(value)
    if 'btc_review_event_refs_embedded' in sys.modules:
        from btc_review_event_refs_embedded import read_event
    else:
        from utils.btc_review_event_references import read_event
    return read_event(value, conn, slot, sequence, decode_legacy=review_text)


def _recovery_wait_proven(conn, slot, snapshot, context, plan, outcome, original, *, reference_verified=False):
    """Require the independently retained recovery journal for a flat WAIT."""
    try:
        recovery = context.get('recovery')
        if (context.get('recovery_observing') is not True or not isinstance(recovery, dict)
                or recovery.get('phase') != 'OBSERVING' or recovery.get('consumed') is not False
                or type(context.get('recovery_contract_version')) is not int
                or context['recovery_contract_version'] not in (1, 2)
                or not isinstance(context.get('input_id'), str) or not context['input_id']
                or plan.get('entries') != [] or plan.get('take_profits') != [] or plan.get('partial_stops') != []
                or plan.get('cancel_entry_ids') != [] or plan.get('side') is not None
                or plan.get('hard_stop') is not None or plan.get('chase') is not None
                or plan.get('pending_renewal') is not None
                or not isinstance(plan.get('recovery'), dict) or plan['recovery'].get('decision') != 'OBSERVE'):
            return False
        if [row[2] for row in conn.execute('PRAGMA index_info(llm_scenario_recovery_slot_kind)')] != ['slot', 'kind']:
            return False
        rows = conn.execute('SELECT kind,body FROM llm_scenario_recovery_events '
                            'INDEXED BY llm_scenario_recovery_slot_kind WHERE slot=? LIMIT 17',
                            (slot,)).fetchall()
        if len(rows) > 16:
            return False
        required = {'INPUT', 'RECHECK', 'CALCULATED_REVIEW_SELECTION', 'DECISION', 'OUTCOME'}
        if any(kind not in required | {'MODEL_REQUEST', 'MODEL_RAW'} for kind, _ in rows):
            return False
        if 'btc_recovery_refs_embedded' in sys.modules:
            from btc_recovery_refs_embedded import read_recovery
        else:
            from utils.btc_recovery_references import read_recovery
        journal = [(kind, json.loads(read_recovery(body, conn, slot, decode_legacy=review_text,
                                                   expected_kind=kind))) for kind, body in rows]
        if any(not isinstance(body, dict) or body.get('input_id') != context['input_id'] for _, body in journal):
            return False
        by_kind = {kind: [body for key, body in journal if key == kind] for kind in required}
        if any(len(values) != 1 for values in by_kind.values()):
            return False
        inp, recheck, selection, decision, result = (by_kind[k][0] for k in
            ('INPUT', 'RECHECK', 'CALCULATED_REVIEW_SELECTION', 'DECISION', 'OUTCOME'))
        full_hash = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                            allow_nan=False).encode()).hexdigest()
        compact_hash = lambda value: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
            allow_nan=False, separators=(',', ':')).encode()).hexdigest()
        tape = json.loads(original)
        if not isinstance(tape, dict):
            return False
        tape_outcome = tape.get('outcome')
        review_result = tape_outcome.get('result') if isinstance(tape_outcome, dict) else None
        selected = review_result.get('selected') if isinstance(review_result, dict) else None
        seal = {key: value for key, value in tape.items() if key != 'sha256'}
        tape_hash = hashlib.sha256(json.dumps(seal, sort_keys=True, separators=(',', ':'),
                                              allow_nan=False).encode()).hexdigest()
        receipt = {key: value for key, value in selection.items() if key not in ('sha256', 'input_id')}
        expected_outcome = dict(outcome)
        expected_outcome.setdefault('input_id', context['input_id'])
        if (inp.get('decision_slot') != slot or inp.get('snapshot_hash') != full_hash(snapshot)
                or inp.get('context_hash') != full_hash(context) or inp.get('recovery') != recovery
                or 'active' not in recheck or recheck['active'] is not None
                or recheck.get('recovery_observing_requested') is not True
                or selection.get('status') != 'FRESH_VALIDATED_NOT_AUTHORIZED'
                or selection.get('execution_authorized') is not False
                or type(selection.get('orders_submitted')) is not int or selection['orders_submitted'] != 0
                or selection.get('sha256') != compact_hash(receipt) or tape.get('sha256') != tape_hash
                or not isinstance(tape_outcome, dict) or tape_outcome.get('status') != 'COMPLETE'
                or not isinstance(selected, dict) or selected.get('id') != 'WAIT'
                or not isinstance(selected.get('raw_proposal'), dict)
                or selected['raw_proposal'].get('action') != 'WAIT'
                or selection.get('payload_sha256') != compact_hash(plan)
                or selection.get('envelope_sha256') != tape_hash
                or decision.get('proposal') != plan or decision.get('metadata') != plan['recovery']
                or result != expected_outcome):
            return False
        operations = tape.get('operations')
        if (not isinstance(operations, list) or len(operations) > 128
                or any(not isinstance(op, dict) or op.get('kind') not in ('clock', 'generate', 'record')
                       for op in operations)):
            return False
        records = [op.get('event') for op in operations if op['kind'] == 'record']
        if not records or any(not isinstance(event, dict) for event in records):
            return False
        if not reference_verified:
            if [row[2] for row in conn.execute('PRAGMA index_info(sqlite_autoindex_llm_scenario_review_events_1)')] != ['slot', 'sequence']:
                return False
            review_rows = conn.execute('SELECT sequence,body FROM llm_scenario_review_events '
                                       'WHERE slot=? ORDER BY sequence LIMIT ?', (slot, len(records))).fetchall()
            if (len(review_rows) != len(records) or any(
                    sequence != index or full_hash(json.loads(_review_event_text(body, conn, slot, sequence))) != full_hash(records[index])
                    for index, (sequence, body) in enumerate(review_rows))):
                return False
        final_rows = conn.execute('SELECT sequence,body FROM llm_scenario_review_events '
                                  'INDEXED BY sqlite_autoindex_llm_scenario_review_events_1 '
                                  'WHERE slot=? AND sequence>=? ORDER BY sequence LIMIT 2',
                                  (slot, len(records))).fetchall()
        if len(final_rows) != 1 or final_rows[0][0] != len(records):
            return False
        final_event = json.loads(_review_event_text(final_rows[0][1], conn, slot, final_rows[0][0]))
        expected_final = {'kind': 'FRESH_REVIEW_RECEIPT',
                          'receipt': {key: value for key, value in selection.items() if key != 'input_id'}}
        if full_hash(final_event) != full_hash(expected_final):
            return False
        expected, phases = [], Counter()
        for operation in operations:
            event = operation.get('event') if isinstance(operation, dict) and operation.get('kind') == 'record' else None
            if isinstance(event, dict) and event.get('kind') in ('MODEL_REQUEST', 'MODEL_RAW'):
                value = dict(event, calculated_review_version=1)
                value.setdefault('input_id', context['input_id'])
                expected.append((event['kind'], full_hash(value)))
                phases[(event['kind'], event.get('phase'))] += 1
        actual = [(kind, full_hash(body)) for kind, body in journal if kind in ('MODEL_REQUEST', 'MODEL_RAW')]
        calls = tape.get('calls')
        if (type(calls) is not int or not 1 <= calls <= 2 or len(expected) != 2 * calls
                or review_result.get('calls') != calls):
            return False
        names = {phase for _, phase in phases}
        if (len(names) != calls or not names <= {'single', 'candidate', 'selection'}
                or any(phases[('MODEL_REQUEST', phase)] != 1 or phases[('MODEL_RAW', phase)] != 1
                       for phase in names)):
            return False
        return Counter(actual) == Counter(expected)
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        return False


def candidates(conn, now, *, after_slot=-1, limit=100, guard=None):
    if (type(now) not in (int, float) or not math.isfinite(now) or now <= 7 * 86400
            or type(after_slot) is not int or after_slot < -1
            or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError('retention_input_invalid')
    cutoff_slot = int((now - 7 * 86400) // 300)
    rows = conn.execute(
        'SELECT t.rowid,t.slot,t.body,d.snapshot,d.context,d.proposal,d.outcome '
        'FROM llm_scenario_review_tapes t LEFT JOIN llm_scenario_decisions d ON d.slot=t.slot '
        'WHERE t.slot>? AND t.slot<? ORDER BY t.slot LIMIT ?', (after_slot, cutoff_slot, limit))
    found, last, scanned = [], after_slot, 0
    for rowid, slot, tape, snapshot, context, proposal, outcome in rows:
        if guard is not None:
            guard()
        if type(rowid) is not int or type(slot) is not int or slot < 0:
            raise ValueError('retention_source_invalid')
        last = slot
        scanned += 1
        try:
            if isinstance(snapshot, bytes):
                snapshot = review_text(snapshot)
            market, ctx, plan, result = json.loads(snapshot), json.loads(context), json.loads(proposal), json.loads(outcome)
        except (TypeError, ValueError):
            continue
        if not all(isinstance(item, dict) for item in (market, ctx, plan, result)):
            continue
        historical = ctx.get('historical_unsettled')
        if (not isinstance(historical, list) or any(
                not isinstance(item, dict) or item.get('status') != 'HISTORICAL_UNSETTLED'
                or item.get('excluded_from_performance') is not True for item in historical)):
            continue
        if (ctx.get('scenario_id', True) is not None or ctx.get('positions') != []
                or ctx.get('pending_entries') != [] or ctx.get('unknown_intents') is not False
                or ctx.get('incident_epoch_blocked') is not False or plan.get('action') != 'WAIT'
                or result.get('status') != 'wait'):
            continue
        observing = ctx.get('recovery_observing', False)
        if observing is not True and (observing is not False or 'recovery' in ctx or 'recovery_contract_version' in ctx):
            continue
        if observing is True and [row[2] for row in conn.execute(
                'PRAGMA index_info(sqlite_autoindex_llm_scenario_review_events_1)')] != ['slot', 'sequence']:
            continue
        try:
            original = read_tape(tape, conn, slot, decode_legacy=review_text)
        except (ValueError, TypeError):
            if observing is True:
                continue
            raise
        if observing is True:
            reference_verified = isinstance(tape, bytes) and tape.startswith(REVIEW_REFERENCE_MAGIC)
            if not _recovery_wait_proven(conn, slot, market, ctx, plan, result, original,
                                         reference_verified=reference_verified):
                continue
        digest = hashlib.sha256(original.encode('utf-8')).hexdigest()
        decision = hashlib.sha256(json.dumps([snapshot, context, proposal, outcome], ensure_ascii=True,
                                              separators=(',', ':')).encode()).hexdigest()
        found.append({'rowid': rowid, 'slot': slot, 'tape_sha256': digest, 'decision_sha256': decision})
    return {'cutoff_slot': cutoff_slot, 'last_scanned_slot': last, 'scanned': scanned, 'candidates': found}


def snapshot_index(conn, now, *, after_slot=-1, guard=None):
    """Bound the inventory; the receiver can independently recompute it."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'llm_scenario_review_tapes', 'llm_scenario_decisions'}
    if not required & tables:
        return None
    if not required <= tables:
        raise ValueError('retention_schema_incomplete')
    if type(after_slot) is not int or after_slot < -1:
        raise ValueError('retention_cursor_invalid')
    last, scanned, found = after_slot, 0, []
    while scanned < 2000:
        size = min(100, 2000 - scanned)
        result = candidates(conn, now, after_slot=last, limit=size, guard=guard)
        found.extend(result['candidates'])
        scanned += result['scanned']
        last = result['last_scanned_slot']
        if result['scanned'] < size:
            break
    return {'version': 1, 'as_of': int(now), 'cutoff_slot': int((now - 7 * 86400) // 300),
            'after_slot': after_slot, 'last_scanned_slot': last, 'scanned': scanned, 'truncated': scanned == 2000,
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
           "AND JSONExtractString(Body,'event_id') IN {ids:Array(String)} LIMIT 201")
    from tools.clickstack_query import build_program
    from utils.btc_offsite_archive import backend_host
    host = backend_host(Path('/etc/prism-observability/tunnel.env'))
    if host is None:
        raise ValueError('retention_backend_missing')
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@' + host,
                             'set -a; . /etc/prism-observability/reader.env; set +a; python3 -'],
                            input=build_program(sql, {'ids': sorted(ids)}), text=True,
                            capture_output=True, check=True, timeout=45)  # nosec B603  # nosemgrep
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
                or type(index.get('after_slot')) is not int or index['after_slot'] < -1
                or type(index.get('last_scanned_slot')) is not int or index['last_scanned_slot'] < index['after_slot']
                or type(index.get('scanned')) is not int or not 0 <= index['scanned'] <= 2000
                or type(index.get('truncated')) is not bool
                or not isinstance(index.get('candidates'), list)):
            raise ValueError('retention_archive_unverified')
        return index
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('retention_archive_unverified') from exc


def _window_delay(second):
    if 25 <= second <= 49:
        return 0
    return 25 - second if second < 25 else 85 - second


@contextmanager
def locked_remote_archive(ack, *, align=False, require_event_proof=False):
    """Verify the archive while holding the receiver's lock until cleanup ends."""
    if (not isinstance(ack, dict) or ack.get('status') != 'VERIFIED'
            or ack.get('retention_index_verified') is not True
            or not isinstance(ack.get('archive'), str)
            or not re.fullmatch(r'archive-[a-f0-9]{32}', ack['archive'])
            or any(not isinstance(ack.get(key), str) or not re.fullmatch(r'[a-f0-9]{64}', ack[key])
                   for key in ('raw_sha256', 'compressed_sha256', 'manifest_sha256',
                               'bundle_sha256', 'archive_metadata_sha256'))):
        raise ValueError('retention_archive_unverified')
    if type(require_event_proof) is not bool or (require_event_proof and
                                                 ack.get('review_event_proof_index_verified') is not True):
        raise ValueError('retention_archive_unverified')
    expected = {key: ack[key] for key in ('archive', 'raw_sha256', 'compressed_sha256', 'manifest_sha256',
                                           'bundle_sha256', 'archive_metadata_sha256')}
    if require_event_proof:
        expected['review_event_proof_index_verified'] = True
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
select.select([sys.stdin],[],[],90)
'''
    from utils.btc_offsite_archive import backend_host
    host = backend_host(Path('/etc/prism-observability/tunnel.env'))
    if host is None:
        raise ValueError('retention_backend_missing')
    proc = subprocess.Popen(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@' + host,
                             '/usr/bin/python3 -c ' + shlex.quote(remote)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)  # nosec B603  # nosemgrep
    try:
        proc.stdin.write(json.dumps(expected) + '\n')
        proc.stdin.flush()
        readable, _, _ = select.select([proc.stdout], [], [], 45)
        if not readable or proc.stdout.readline().strip() != 'VERIFIED' or proc.poll() is not None:
            raise ValueError('retention_archive_unverified')
        if align:
            delay = _window_delay(time.localtime().tm_sec)
            if delay:
                readable, _, _ = select.select([proc.stdout], [], [], delay)
                if readable or proc.poll() is not None:
                    raise ValueError('retention_archive_lease_lost')
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


SOURCE = Path('/root/prism-insight/stock_tracking_db.sqlite')
EXPORT_STATE = Path('/root/prism-insight/logs/btc-research-export.sqlite')
BACKUP_ROOT = Path('/root/prism_backups')


def _private_read(path, maximum):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or before.st_size > maximum):
            raise ValueError('retention_private_file_invalid')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(maximum + 1)
        after = os.fstat(fd)
        if (len(data) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError('retention_private_file_changed')
        return data
    finally:
        os.close(fd)


def read_cursor(path, source_identity):
    try:
        value = json.loads(_private_read(Path(path), 4096))
        if (not isinstance(value, dict) or set(value) != {'source_identity', 'after_slot'}
                or value['source_identity'] != source_identity or type(value['after_slot']) is not int
                or value['after_slot'] < -1):
            return -1
        return value['after_slot']
    except (OSError, ValueError, TypeError):
        return -1


def _save_cursor(index, source_identity):
    after = index['last_scanned_slot'] if index['truncated'] else -1
    path = BACKUP_ROOT / '.btc-retention-cursor.json'
    if path.is_symlink():
        raise ValueError('retention_cursor_invalid')
    fd, name = tempfile.mkstemp(prefix='.btc-retention-', dir=BACKUP_ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump({'source_identity': source_identity, 'after_slot': after}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(BACKUP_ROOT, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _runtime_safe(conn):
    from live.scenario_incident_epoch import unresolved
    row = conn.execute('SELECT body FROM llm_scenario_state WHERE id=1').fetchone()
    if not row:
        return False
    state = json.loads(row[0])
    return isinstance(state, dict) and 'active' in state and state['active'] is None and not unresolved(conn)


def _resource_guard(source, identity):
    from tools.btc_review_event_compaction import resource_guard
    return resource_guard(source, identity) and _backup_idle(source)


def _backup_idle(source, proc_root=Path('/proc'), *, allow_reader=None):
    """Do not write while a snapshot or a streamed DB reader is active."""
    try:
        source_stat = source.stat()
        names = {'sqlite_snapshot.py', 'btc_offsite_archive.py', 'backup_configs.sh'}
        for process in proc_root.iterdir():
            if not process.name.isdecimal() or int(process.name) == os.getpid():
                continue
            try:
                args = (process / 'cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            if any(Path(os.fsdecode(arg)).name in names for arg in args if arg):
                return False
            try:
                for fd in (process / 'fd').iterdir():
                    try:
                        seen = fd.stat()
                    except FileNotFoundError:
                        continue
                    if (seen.st_dev, seen.st_ino) == (source_stat.st_dev, source_stat.st_ino):
                        if allow_reader is None or allow_reader(process, fd) is not True:
                            return False
            except FileNotFoundError:
                continue
        return True
    except (OSError, ValueError):
        return False


def _backup_evidence(backup_dir):
    directory = Path(backup_dir)
    if (BACKUP_ROOT.is_symlink() or directory.is_symlink() or not directory.is_dir()
            or directory.resolve().parent != BACKUP_ROOT.resolve()
            or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077):
        raise ValueError('retention_backup_directory_invalid')
    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise ValueError('retention_source_invalid')
    from observability.btc_scenario_export import _identity
    identity = _identity(SOURCE)
    manifest = _private_read(directory / 'stock_tracking_db.sqlite.gz.manifest.json', 4 * 1024 * 1024)
    ack = json.loads(_private_read(directory / 'offsite_ack.json', 65536))
    index = verified_archive_index(manifest, ack, identity)
    return directory, identity, manifest, ack, index


def remove_uploaded_snapshot(backup_dir):
    """Remove only this redundant local gzip after rechecking the remote archive."""
    directory, _, raw_manifest, ack, _ = _backup_evidence(backup_dir)
    manifest = json.loads(raw_manifest)
    path = directory / 'stock_tracking_db.sqlite.gz'
    if path.is_symlink():
        raise ValueError('retention_local_snapshot_invalid')
    if not path.exists():
        return {'status': 'ALREADY_REMOVED'}
    with locked_remote_archive(ack) as lease:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_mode & 0o077
                    or before.st_nlink != 1 or before.st_size != manifest.get('compressed_bytes')):
                raise ValueError('retention_local_snapshot_invalid')
            digest = hashlib.sha256()
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    if lease() is not True:
                        raise ValueError('retention_archive_lease_lost')
                    digest.update(chunk)
            after, current = os.fstat(fd), path.lstat()
            if (digest.hexdigest() != manifest.get('compressed_sha256') or lease() is not True
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError('retention_local_snapshot_changed')
            path.unlink()
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.close(fd)
    return {'status': 'REMOVED'}


def cleanup(backup_dir, *, now=None):
    """Apply one bounded batch only after archive, delivery and runtime rechecks."""
    directory, identity, _, ack, index = _backup_evidence(backup_dir)
    if EXPORT_STATE.is_symlink() or not EXPORT_STATE.is_file():
        raise ValueError('retention_source_invalid')
    at = time.time() if now is None else now
    with closing(sqlite3.connect(SOURCE.resolve().as_uri() + '?mode=rw', uri=True, timeout=.1)) as conn:
        selected = []
        for item in index['candidates']:
            slot = item['slot']
            if type(slot) is not int or slot < 0:
                raise ValueError('retention_source_invalid')
            current = conn.execute('SELECT rowid FROM llm_scenario_review_tapes WHERE slot=?', (slot,)).fetchone()
            if current and current[0] == item.get('rowid'):
                selected.append(item)
                if len(selected) == 400:
                    break
        if not selected:
            _save_cursor(index, identity)
            return {'status': 'NO_CANDIDATES', 'removed': 0}
        delivered = []
        for start in range(0, len(selected), 100):
            delivered.extend(fetch_deliveries(selected[start:start + 100], identity))
        matched = matching_evidence({'candidates': selected}, {'candidates': selected}, delivered, identity)
        progress = ({'truncated': True, 'last_scanned_slot': selected[-1]['slot']}
                    if len(selected) == 400 else index)
        def unchanged(item):
            current = candidates(conn, at, after_slot=item['slot'] - 1, limit=1)['candidates']
            return bool(current and current[0] == item)
        if not matched:
            remaining = [item['slot'] for item in selected if unchanged(item)]
            _save_cursor({'truncated': True, 'last_scanned_slot': min(remaining) - 1} if remaining else progress,
                         identity)
            return {'status': 'DEFERRED' if remaining else 'NO_CANDIDATES', 'removed': 0}
        removed = []
        try:
            with locked_remote_archive(ack, align=True) as lease, closing(sqlite3.connect(
                    EXPORT_STATE.resolve().as_uri() + '?mode=rw', uri=True, timeout=.1)) as exporter:
                for start in range(0, len(matched), 100):
                    cached_state = []
                    def guard():
                        if not _resource_guard(SOURCE, identity):
                            return False
                        if not conn.in_transaction:
                            return _runtime_safe(conn)
                        if not cached_state:
                            trigger = conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' "
                                                   "AND tbl_name='llm_scenario_review_tapes' LIMIT 1").fetchone()
                            foreign_keys = conn.execute('PRAGMA foreign_keys').fetchone()
                            cached_state.append(None if trigger or foreign_keys != (0,) else _runtime_safe(conn))
                        return _runtime_safe(conn) if cached_state[0] is None else cached_state[0]
                    batch = {'candidates': matched[start:start + 100]}
                    removed.extend(_delete_matched(conn, exporter, batch, delivered, identity, at,
                                                   guard=guard, lease_alive=lease))
        except Exception as exc:
            if removed:
                return {'status': 'PARTIAL', 'removed': len(removed), 'error_type': type(exc).__name__}
            raise
        removed_slots = set(removed)
        remaining = [item['slot'] for item in selected if item['slot'] not in removed_slots and unchanged(item)]
        if remaining:
            progress = {'truncated': True, 'last_scanned_slot': min(remaining) - 1}
        _save_cursor(progress, identity)
        return {'status': 'COMPLETE', 'removed': len(removed)}
