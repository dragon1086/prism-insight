"""Bounded read-only fingerprints for review-event retention evidence.

This module neither selects safe WAITs nor deletes records. Callers must bind
these fingerprints to an independently verified backup and delivery receipts.
"""
import hashlib
import gzip
import json
import re
import subprocess  # nosec B404 - fixed host-verified SSH and bounded query
import sys

if 'btc_review_storage_embedded' in sys.modules:
    from btc_review_storage_embedded import review_text
    from btc_recovery_refs_embedded import read_recovery
    from btc_review_event_refs_embedded import read_event
else:
    from observability.review_storage import review_text
    from utils.btc_recovery_references import read_recovery
    from utils.btc_review_event_references import read_event

MAX_EVENTS = 128
MAX_RECOVERY = 16
MAX_BYTES = 8_000_000
MAX_SLOTS = 20


def _digest(text):
    if not isinstance(text, str):
        raise ValueError('proof_text_invalid')
    raw = text.encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('proof_size_exceeded')
    body = json.loads(text)
    if not isinstance(body, dict):
        raise ValueError('proof_body_invalid')
    return hashlib.sha256(raw).hexdigest(), len(raw)


def event_proof(conn, slot):
    """Read at most one slot, restoring all references without modifying them."""
    if type(slot) is not int or slot < 0:
        raise ValueError('proof_slot_invalid')
    rows = conn.execute('SELECT rowid,sequence,body FROM llm_scenario_review_events '
                        'WHERE slot=? ORDER BY sequence LIMIT ?', (slot, MAX_EVENTS + 1)).fetchall()
    if not rows or len(rows) > MAX_EVENTS:
        raise ValueError('proof_event_count_invalid')
    events, size = [], 0
    for expected, (rowid, sequence, stored) in enumerate(rows):
        if type(rowid) is not int or rowid <= 0 or type(sequence) is not int or sequence != expected:
            raise ValueError('proof_event_sequence_invalid')
        text = read_event(stored, conn, slot, sequence, decode_legacy=review_text)
        digest, n = _digest(text)
        size += n
        if size > MAX_BYTES:
            raise ValueError('proof_size_exceeded')
        events.append({'rowid': rowid, 'sequence': sequence, 'sha256': digest})
    rows = conn.execute('SELECT event_id,kind,body FROM llm_scenario_recovery_events '
                        'WHERE slot=? ORDER BY event_id LIMIT ?', (slot, MAX_RECOVERY + 1)).fetchall()
    if len(rows) > MAX_RECOVERY:
        raise ValueError('proof_recovery_count_invalid')
    recovery, seen = [], set()
    for event_id, kind, stored in rows:
        if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or event_id in seen
                or not isinstance(kind, str) or not 1 <= len(kind) <= 80):
            raise ValueError('proof_recovery_identity_invalid')
        seen.add(event_id)
        text = read_recovery(stored, conn, slot, decode_legacy=review_text, expected_kind=kind)
        digest, n = _digest(text)
        size += n
        if size > MAX_BYTES:
            raise ValueError('proof_size_exceeded')
        recovery.append({'event_id': event_id, 'kind': kind, 'sha256': digest})
    return {'slot': slot, 'events': events, 'recovery': recovery}


def snapshot_event_index(conn, now, *, after_slot=-1):
    """Index a bounded page of eligible WAITs within an existing read snapshot.

    The caller must bind and verify the result against its backup. This index
    alone is not authority to delete any data.
    """
    if not conn.in_transaction:
        raise ValueError('proof_snapshot_transaction_required')
    if 'btc_retention_embedded' in sys.modules:
        from btc_retention_embedded import candidates
    else:
        from utils.btc_review_retention import candidates
    page = candidates(conn, now, after_slot=after_slot, limit=100)
    selected = page['candidates'][:MAX_SLOTS]
    items = []
    for row in selected:
        try:
            proof = event_proof(conn, row['slot'])
        except ValueError:
            # Preserve the unavailable slot, but do not block other verified slots.
            proof = {'slot': row['slot'], 'unavailable': True}
        items.append({'candidate': row, 'proof': proof})
    capped = len(page['candidates']) > MAX_SLOTS
    last = selected[-1]['slot'] if capped else page['last_scanned_slot']
    return {'version': 2, 'cutoff_slot': page['cutoff_slot'], 'after_slot': after_slot,
            'last_scanned_slot': last, 'truncated': capped or page['scanned'] == 100, 'items': items}


def delivered_event_proof(current, archived, deliveries, source_identity):
    """Require exact source/backup equality and one matching receipt per event.

    This does not verify the backup, the export cursor, active positions, or
    whether an event can be deleted; callers must establish those separately.
    """
    if not isinstance(source_identity, str) or not re.fullmatch('[a-f0-9]{64}', source_identity):
        raise ValueError('proof_source_invalid')
    if not isinstance(current, dict) or not isinstance(archived, dict) or not isinstance(deliveries, list):
        raise ValueError('proof_input_invalid')
    if current != archived:
        return False
    slot, events, recovery = current.get('slot'), current.get('events'), current.get('recovery')
    if (set(current) != {'slot', 'events', 'recovery'} or type(slot) is not int or slot < 0
            or not isinstance(recovery, list) or len(recovery) > MAX_RECOVERY or not isinstance(events, list)
            or not 1 <= len(events) <= MAX_EVENTS or len(deliveries) != len(events)):
        return False
    recovery_ids = []
    for item in recovery:
        if (not isinstance(item, dict) or set(item) != {'event_id', 'kind', 'sha256'}
                or not isinstance(item['event_id'], str) or not 1 <= len(item['event_id']) <= 128
                or not isinstance(item['kind'], str) or not 1 <= len(item['kind']) <= 80
                or not isinstance(item['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', item['sha256'])):
            return False
        recovery_ids.append(item['event_id'])
    if recovery_ids != sorted(set(recovery_ids)):
        return False
    receipt_by_id = {}
    for receipt in deliveries:
        if not isinstance(receipt, dict) or not isinstance(receipt.get('event_id'), str):
            return False
        if receipt['event_id'] in receipt_by_id:
            return False
        receipt_by_id[receipt['event_id']] = receipt
    seen = set()
    for sequence, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {'rowid', 'sequence', 'sha256'}:
            return False
        rowid, sha = event['rowid'], event['sha256']
        if (type(rowid) is not int or rowid <= 0 or rowid in seen or type(event['sequence']) is not int
                or event['sequence'] != sequence or not isinstance(sha, str)
                or not re.fullmatch('[a-f0-9]{64}', sha)):
            return False
        seen.add(rowid)
        event_id = hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{rowid}'.encode()).hexdigest()[:32]
        receipt = receipt_by_id.get(event_id)
        if (not receipt or receipt.get('event_type') != 'btc.research.review'
                or receipt.get('source_identity') != source_identity or receipt.get('body_sha256') != sha
                or type(receipt.get('source_rowid')) is not int or receipt['source_rowid'] != rowid
                or type(receipt.get('source_sequence')) is not int or receipt['source_sequence'] != sequence
                or type(receipt.get('decision_slot')) is not int or receipt['decision_slot'] != slot):
            return False
    return True


def verified_event_index(manifest_bytes, ack, source_identity):
    """Bind event evidence to an archive receipt; callers must hold its lease."""
    from utils.btc_review_retention import verified_archive_index
    base = verified_archive_index(manifest_bytes, ack, source_identity)
    manifest = json.loads(manifest_bytes)
    index = manifest.get('btc_review_event_proof_index')
    if (manifest.get('btc_review_event_proof_status') != 'CAPTURED'
            or ack.get('review_event_proof_index_verified') is not True or not isinstance(index, dict)
            or set(index) != {'version', 'cutoff_slot', 'after_slot', 'last_scanned_slot', 'truncated', 'items'}
            or type(index.get('version')) is not int or index['version'] != 2
            or type(index.get('cutoff_slot')) is not int or index['cutoff_slot'] != base['cutoff_slot']
            or type(index.get('after_slot')) is not int or index['after_slot'] != base['after_slot']
            or type(index.get('last_scanned_slot')) is not int
            or not index['after_slot'] <= index['last_scanned_slot'] <= base['last_scanned_slot']
            or type(index.get('truncated')) is not bool or not isinstance(index.get('items'), list)
            or len(index['items']) > MAX_SLOTS):
        raise ValueError('event_proof_archive_unverified')
    base_rows = base['candidates']
    if any(not isinstance(row, dict) or type(row.get('slot')) is not int for row in base_rows):
        raise ValueError('event_proof_archive_unverified')
    by_slot = {row['slot']: row for row in base_rows}
    if len(by_slot) != len(base_rows):
        raise ValueError('event_proof_archive_unverified')
    seen = []
    for item in index['items']:
        if (not isinstance(item, dict) or set(item) != {'candidate', 'proof'}
                or not isinstance(item['candidate'], dict) or type(item['candidate'].get('slot')) is not int
                or not isinstance(item['proof'], dict)):
            raise ValueError('event_proof_archive_unverified')
        slot = item['candidate']['slot']
        if (not index['after_slot'] < slot <= index['last_scanned_slot']
                or by_slot.get(slot) != item['candidate'] or item['proof'].get('slot') != slot):
            raise ValueError('event_proof_archive_unverified')
        seen.append(slot)
    if seen != sorted(set(seen)):
        raise ValueError('event_proof_archive_unverified')
    return index


def fetch_event_deliveries(proof, source_identity):
    """Read only the bounded event IDs needed for one archived slot."""
    if (not isinstance(source_identity, str) or not re.fullmatch('[a-f0-9]{64}', source_identity)
            or not isinstance(proof, dict) or not isinstance(proof.get('events'), list)
            or not 1 <= len(proof['events']) <= MAX_EVENTS):
        raise ValueError('event_delivery_query_invalid')
    rowids = [row.get('rowid') if isinstance(row, dict) else None for row in proof['events']]
    if any(type(rowid) is not int or rowid <= 0 for rowid in rowids) or len(set(rowids)) != len(rowids):
        raise ValueError('event_delivery_query_invalid')
    ids = [hashlib.sha256(f'{source_identity}:llm_scenario_review_events:{rowid}'.encode()).hexdigest()[:32]
           for rowid in rowids]
    sql = ("SELECT JSONExtractString(Body,'event_id') AS event_id, "
           "JSONExtractString(Body,'event_type') AS event_type, "
           "JSONExtractString(Body,'attributes','source_identity') AS source_identity, "
           "JSONExtractString(Body,'attributes','body_sha256') AS body_sha256, "
           "JSONExtractInt(Body,'attributes','source_rowid') AS source_rowid, "
           "JSONExtractInt(Body,'attributes','source_sequence') AS source_sequence, "
           "JSONExtractInt(Body,'attributes','decision_slot') AS decision_slot "
           "FROM otel_logs WHERE LogAttributes['event.name']='btc.research.review' "
           "AND JSONExtractString(Body,'event_id') IN {ids:Array(String)} LIMIT 257")
    from pathlib import Path
    from tools.clickstack_query import build_program
    from utils.btc_offsite_archive import backend_host
    host = backend_host(Path('/etc/prism-observability/tunnel.env'))
    if host is None:
        raise ValueError('event_delivery_backend_missing')
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', 'root@' + host,
                             'set -a; . /etc/prism-observability/reader.env; set +a; python3 -'],
                            input=build_program(sql, {'ids': ids}), text=True, capture_output=True,
                            check=True, timeout=45)  # nosec B603  # nosemgrep
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if len(rows) > 256 or any(not isinstance(row, dict) or row.get('event_id') not in ids for row in rows):
        raise ValueError('event_delivery_query_invalid')
    return rows


def delete_confirmed_slot(conn, exporter, archived, tape_deliveries, event_deliveries,
                          source_identity, now, *, guard, lease_alive):
    """Atomically delete one verified flat WAIT and its events under a held archive lease.

    This is not a standalone authority: the caller must validate archived using
    verified_event_index while holding the matching verified remote archive lease.
    Recovery references are detached losslessly within the same transaction.
    """
    from observability.btc_scenario_export import _anchor_hash
    from utils.btc_recovery_references import MAGIC as RECOVERY_MAGIC
    from utils.btc_review_retention import candidates, matching_evidence

    if (conn.in_transaction or exporter.in_transaction or not callable(guard) or not callable(lease_alive)
            or not isinstance(archived, dict) or set(archived) != {'candidate', 'proof'}
            or not isinstance(archived['candidate'], dict) or not isinstance(archived['proof'], dict)):
        raise ValueError('event_delete_context_invalid')
    if guard() is not True or lease_alive() is not True:
        raise ValueError('event_delete_guard_failed')
    candidate, proof = archived['candidate'], archived['proof']
    slot = candidate.get('slot')
    if type(slot) is not int or slot < 0 or proof.get('slot') != slot:
        raise ValueError('event_delete_context_invalid')
    try:
        conn.execute('BEGIN IMMEDIATE')
        exporter.execute('BEGIN IMMEDIATE')
        triggers = conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' "
                                "AND tbl_name IN ('llm_scenario_review_tapes','llm_scenario_review_events',"
                                "'llm_scenario_recovery_events') LIMIT 1").fetchone()
        if triggers or conn.execute('PRAGMA foreign_keys').fetchone() != (0,):
            raise ValueError('event_delete_schema_unsupported')
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (table,) in tables:
            if any(row[0] in ('llm_scenario_review_tapes', 'llm_scenario_review_events') for row in
                   conn.execute('SELECT "table" FROM pragma_foreign_key_list(?)', (table,))):
                raise ValueError('event_delete_schema_unsupported')
        identity = exporter.execute("SELECT value FROM meta WHERE key='source_identity'").fetchone()
        pending = exporter.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        if not identity or identity[0] != source_identity or pending:
            raise ValueError('event_delete_export_state_invalid')
        cursors = {}
        for stream in ('llm_scenario_review_tapes', 'llm_scenario_review_events'):
            cursor = exporter.execute('SELECT seq FROM cursors WHERE stream=?', (stream,)).fetchone()
            saved = exporter.execute('SELECT ref FROM anchors WHERE stream=?', (stream,)).fetchone()
            if not cursor or type(cursor[0]) is not int or cursor[0] <= 0 or not saved:
                raise ValueError('event_delete_export_state_invalid')
            if stream == 'llm_scenario_review_tapes':
                row = conn.execute('SELECT slot,body FROM llm_scenario_review_tapes WHERE rowid=?', (cursor[0],)).fetchone()
                anchor = {'slot': row[0], 'body': row[1]} if row else None
            else:
                row = conn.execute('SELECT slot,sequence,body FROM llm_scenario_review_events WHERE rowid=?', (cursor[0],)).fetchone()
                anchor = {'slot': row[0], 'sequence': row[1], 'body': row[2]} if row else None
            if anchor is None or not isinstance(saved[0], str) or _anchor_hash(stream, anchor, conn) != saved[0]:
                raise ValueError('event_delete_export_anchor_changed')
            cursors[stream] = cursor[0]
        current = candidates(conn, now, after_slot=slot - 1, limit=1)['candidates']
        if (not current or current[0] != candidate or
                matching_evidence({'candidates': current}, {'candidates': [candidate]},
                                  tape_deliveries, source_identity) != [candidate] or
                candidate['rowid'] >= cursors['llm_scenario_review_tapes']):
            return {'status': 'DEFERRED_TAPE_EVIDENCE'}
        current_proof = event_proof(conn, slot)
        if (not delivered_event_proof(current_proof, proof, event_deliveries, source_identity)
                or any(event['rowid'] >= cursors['llm_scenario_review_events'] for event in current_proof['events'])):
            return {'status': 'DEFERRED_EVENT_EVIDENCE'}
        recovery_rows = conn.execute('SELECT rowid,event_id,kind,body FROM llm_scenario_recovery_events '
                                     'WHERE slot=? ORDER BY event_id LIMIT ?',
                                     (slot, MAX_RECOVERY + 1)).fetchall()
        refs = [row for row in recovery_rows if isinstance(row[3], bytes) and row[3].startswith(RECOVERY_MAGIC)]
        recovery_anchor = None
        if refs:
            stream = 'llm_scenario_recovery_events'
            cursor = exporter.execute('SELECT seq FROM cursors WHERE stream=?', (stream,)).fetchone()
            saved = exporter.execute('SELECT ref FROM anchors WHERE stream=?', (stream,)).fetchone()
            if not cursor or type(cursor[0]) is not int or cursor[0] <= 0 or not saved or not isinstance(saved[0], str):
                return {'status': 'DEFERRED_RECOVERY_EXPORT'}
            row = conn.execute('SELECT slot,kind,body FROM llm_scenario_recovery_events WHERE rowid=?', (cursor[0],)).fetchone()
            recovery_anchor = {'slot': row[0], 'kind': row[1], 'body': row[2]} if row else None
            if recovery_anchor is None or _anchor_hash(stream, recovery_anchor, conn) != saved[0]:
                raise ValueError('event_delete_export_anchor_changed')
        if guard() is not True or lease_alive() is not True:
            raise ValueError('event_delete_guard_failed')
        if refs:
            from observability.review_storage import MAGIC as REVIEW_MAGIC
            for rowid, event_id, kind, stored in refs:
                raw = read_recovery(stored, conn, slot, decode_legacy=review_text, expected_kind=kind)
                packed = REVIEW_MAGIC + gzip.compress(raw.encode('utf-8'), compresslevel=1, mtime=0)
                replacement = packed if len(packed) < len(raw.encode('utf-8')) else raw
                if review_text(replacement) != raw or conn.execute(
                        'UPDATE llm_scenario_recovery_events SET body=? WHERE rowid=? AND slot=? AND event_id=? AND body=?',
                        (replacement, rowid, slot, event_id, stored)).rowcount != 1:
                    raise ValueError('event_delete_recovery_changed')
        for event in current_proof['events']:
            changed = conn.execute('DELETE FROM llm_scenario_review_events WHERE rowid=? AND slot=? AND sequence=?',
                                   (event['rowid'], slot, event['sequence'])).rowcount
            if changed != 1:
                raise ValueError('event_delete_source_changed')
        if conn.execute('DELETE FROM llm_scenario_review_tapes WHERE rowid=? AND slot=?',
                        (candidate['rowid'], slot)).rowcount != 1:
            raise ValueError('event_delete_source_changed')
        remaining = conn.execute('SELECT event_id,kind,body FROM llm_scenario_recovery_events '
                                 'WHERE slot=? ORDER BY event_id LIMIT ?', (slot, MAX_RECOVERY + 1)).fetchall()
        recovered = []
        for event_id, kind, stored in remaining:
            raw = read_recovery(stored, conn, slot, decode_legacy=review_text, expected_kind=kind)
            digest, _ = _digest(raw)
            recovered.append({'event_id': event_id, 'kind': kind, 'sha256': digest})
        if recovered != current_proof['recovery']:
            raise ValueError('event_delete_recovery_changed')
        if recovery_anchor is not None:
            row = conn.execute('SELECT slot,kind,body FROM llm_scenario_recovery_events WHERE rowid=?', (cursor[0],)).fetchone()
            anchor = {'slot': row[0], 'kind': row[1], 'body': row[2]} if row else None
            if anchor is None or _anchor_hash('llm_scenario_recovery_events', anchor, conn) != saved[0]:
                raise ValueError('event_delete_export_anchor_changed')
        if guard() is not True or lease_alive() is not True:
            raise ValueError('event_delete_guard_failed')
        conn.commit()
        return {'status': 'REMOVED', 'events': len(current_proof['events'])}
    finally:
        if conn.in_transaction:
            conn.rollback()
        if exporter.in_transaction:
            exporter.rollback()


def cleanup_events(backup_dir, *, now=None):
    """Opt-in bounded cleanup; verify every source under the archive lease.

    The existing cursor advances by scanned page and wraps to the beginning.
    Deferred slots remain untouched and are revisited after a complete pass.
    """
    import sqlite3
    import time
    from collections import Counter
    from contextlib import closing
    from utils import btc_review_retention as retention

    _, identity, manifest, ack, _ = retention._backup_evidence(backup_dir)
    index = verified_event_index(manifest, ack, identity)
    def save_cursor():
        path = retention.BACKUP_ROOT / '.btc-retention-cursor.json'
        if retention.read_cursor(path, identity) != index['after_slot']:
            raise ValueError('event_delete_cursor_changed')
        retention._save_cursor(index, identity)
    if retention.read_cursor(retention.BACKUP_ROOT / '.btc-retention-cursor.json', identity) != index['after_slot']:
        raise ValueError('event_delete_cursor_changed')
    if not index['items']:
        save_cursor()
        return {'status': 'NO_CANDIDATES', 'removed': 0, 'removed_slots': 0, 'removed_events': 0}
    if retention.EXPORT_STATE.is_symlink() or not retention.EXPORT_STATE.is_file():
        raise ValueError('event_delete_export_state_invalid')
    at = time.time() if now is None else now
    prepared = []
    for item in index['items']:
        if item['proof'].get('unavailable') is True:
            prepared.append((item, None, None))
            continue
        tape_receipts = retention.fetch_deliveries([item['candidate']], identity)
        event_receipts = fetch_event_deliveries(item['proof'], identity)
        prepared.append((item, tape_receipts, event_receipts))
    statuses, removed_slots, removed_events = Counter(), 0, 0
    try:
        with retention.locked_remote_archive(ack, require_event_proof=True) as lease, closing(sqlite3.connect(
                retention.SOURCE.resolve().as_uri() + '?mode=rw', uri=True, timeout=.1)) as conn, closing(sqlite3.connect(
                retention.EXPORT_STATE.resolve().as_uri() + '?mode=rw', uri=True, timeout=.1)) as exporter:
            def guard():
                return retention._resource_guard(retention.SOURCE, identity) and retention._runtime_safe(conn)
            for item, tape_receipts, event_receipts in prepared:
                if tape_receipts is None:
                    statuses['DEFERRED_EVENT_PROOF'] += 1
                    continue
                result = delete_confirmed_slot(conn, exporter, item, tape_receipts, event_receipts,
                                               identity, at, guard=guard, lease_alive=lease)
                statuses[result['status']] += 1
                if result['status'] == 'REMOVED':
                    removed_slots += 1
                    removed_events += result['events']
        save_cursor()
    except Exception as exc:
        if removed_slots:
            return {'status': 'PARTIAL', 'removed': removed_slots, 'removed_slots': removed_slots, 'removed_events': removed_events,
                    'error_type': type(exc).__name__}
        raise
    return {'status': 'DEFERRED' if any(k != 'REMOVED' for k in statuses) else 'COMPLETE',
            'removed': removed_slots, 'removed_slots': removed_slots, 'removed_events': removed_events, 'outcomes': dict(statuses)}
