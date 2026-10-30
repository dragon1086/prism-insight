"""Bounded read-only fingerprints for review-event retention evidence.

This module neither selects safe WAITs nor deletes records. Callers must bind
these fingerprints to an independently verified backup and delivery receipts.
"""
import hashlib
import json
import re
import subprocess  # nosec B404 - fixed host-verified SSH and bounded query
import sys
import zlib

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
MAX_SLOTS = 400
MAX_SCANNED = 2000
MAX_INDEX_BYTES = 700_000
MAX_DELIVERY_QUERIES = 48
MAX_QUERY_EVENTS = 512


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


def event_proof(conn, slot, *, guard=None):
    """Read at most one slot, restoring all references without modifying them."""
    if type(slot) is not int or slot < 0:
        raise ValueError('proof_slot_invalid')
    rows = conn.execute('SELECT rowid,sequence,body FROM llm_scenario_review_events '
                        'WHERE slot=? ORDER BY sequence LIMIT ?', (slot, MAX_EVENTS + 1)).fetchall()
    if not rows or len(rows) > MAX_EVENTS:
        raise ValueError('proof_event_count_invalid')
    events, size = [], 0
    for expected, (rowid, sequence, stored) in enumerate(rows):
        if guard is not None:
            guard()
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
        if guard is not None:
            guard()
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


def snapshot_event_index(conn, now, *, after_slot=-1, guard=None):
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
    items, scanned, size, last = [], 0, 0, after_slot
    cutoff = None
    while scanned < MAX_SCANNED:
        page = candidates(conn, now, after_slot=last, limit=min(100, MAX_SCANNED - scanned), guard=guard)
        cutoff = page['cutoff_slot']
        for row in page['candidates']:
            if guard is not None:
                guard()
            if len(items) >= MAX_SLOTS:
                return {'version': 2, 'cutoff_slot': cutoff, 'after_slot': after_slot,
                        'last_scanned_slot': items[-1]['candidate']['slot'], 'truncated': True, 'items': items}
            try:
                decision = conn.execute('SELECT proposal FROM llm_scenario_decisions WHERE slot=?', (row['slot'],)).fetchone()
                proof = event_proof(conn, row['slot'], guard=guard) if decision and _flat_noop_plan(decision[0]) else {
                    'slot': row['slot'], 'unavailable': True}
            except ValueError:
                proof = {'slot': row['slot'], 'unavailable': True}
            item = {'candidate': row, 'proof': proof}
            item_bytes = len(json.dumps(item, sort_keys=True, separators=(',', ':')).encode())
            if size + item_bytes > MAX_INDEX_BYTES:
                if items:
                    return {'version': 2, 'cutoff_slot': cutoff, 'after_slot': after_slot,
                            'last_scanned_slot': items[-1]['candidate']['slot'], 'truncated': True, 'items': items}
                item['proof'] = {'slot': row['slot'], 'unavailable': True}
                item_bytes = len(json.dumps(item, sort_keys=True, separators=(',', ':')).encode())
                if item_bytes > MAX_INDEX_BYTES:
                    raise ValueError('proof_index_item_too_large')
            items.append(item)
            size += item_bytes
        scanned += page['scanned']
        last = page['last_scanned_slot']
        if page['scanned'] < 100:
            return {'version': 2, 'cutoff_slot': cutoff, 'after_slot': after_slot,
                    'last_scanned_slot': last, 'truncated': False, 'items': items}
    return {'version': 2, 'cutoff_slot': cutoff, 'after_slot': after_slot,
            'last_scanned_slot': last, 'truncated': True, 'items': items}


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
            or not 1 <= len(proof['events']) <= MAX_QUERY_EVENTS):
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
           "AND JSONExtractString(Body,'event_id') IN {ids:Array(String)} LIMIT 1025")
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
    if len(rows) > 1024 or any(not isinstance(row, dict) or row.get('event_id') not in ids for row in rows):
        raise ValueError('event_delivery_query_invalid')
    return rows


def _required_event_sequences(conn, slot, recovery_rows):
    """Keep direct and transitive review sources needed by recovery records."""
    from utils.btc_recovery_references import MAGIC as RECOVERY_MAGIC, TAG, INPUT_TAG, MAX_BYTES as RECOVERY_MAX
    from utils.btc_review_event_references import MAGIC as EVENT_MAGIC, _unpack
    rows = conn.execute('SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence LIMIT ?',
                        (slot, MAX_EVENTS + 1)).fetchall()
    by_sequence = {sequence: body for sequence, body in rows}
    if len(rows) > MAX_EVENTS or len(by_sequence) != len(rows):
        raise ValueError('event_delete_reference_invalid')
    required = set()
    for _, _, _, stored in recovery_rows:
        if not isinstance(stored, bytes) or not stored.startswith(RECOVERY_MAGIC):
            continue
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = decoder.decompress(stored[len(RECOVERY_MAGIC):], RECOVERY_MAX + 1)
        if len(raw) > RECOVERY_MAX or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('event_delete_reference_invalid')
        marker = json.loads(raw)
        if not isinstance(marker, dict):
            raise ValueError('event_delete_reference_invalid')
        if TAG in marker:
            seq = marker.get('sequence')
            if type(seq) is not int or seq not in by_sequence:
                raise ValueError('event_delete_reference_invalid')
            required.add(seq)
        elif INPUT_TAG not in marker:
            raise ValueError('event_delete_reference_invalid')
    pending = list(required)
    while pending:
        seq = pending.pop()
        body = by_sequence[seq]
        if isinstance(body, bytes) and body.startswith(EVENT_MAGIC):
            source = _unpack(body).get('source_sequence')
            if type(source) is not int or source >= seq or source not in by_sequence:
                raise ValueError('event_delete_reference_invalid')
            if source not in required:
                required.add(source)
                pending.append(source)
    return required


def _flat_noop_plan(raw):
    """A WAIT can still carry cancellations or renewal instructions."""
    try:
        plan = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if (not isinstance(plan, dict) or plan.get('action') != 'WAIT'
            or type(plan.get('schema_version')) is not int or plan['schema_version'] != 1):
        return False
    if any(plan.get(key) != [] for key in ('entries', 'take_profits', 'partial_stops', 'cancel_entry_ids')):
        return False
    # The v1 wire validator removes these null fields from persisted WAIT plans.
    if any(key in plan and plan[key] is not None for key in ('side', 'hard_stop', 'chase')):
        return False
    return all(plan.get(key) is None for key in ('stop_revision', 'pending_renewal'))


def delete_confirmed_slot(conn, exporter, archived, tape_deliveries, event_deliveries,
                          source_identity, now, *, guard, lease_alive):
    """Atomically delete one verified flat WAIT and its events under a held archive lease.

    This is not a standalone authority: the caller must validate archived using
    verified_event_index while holding the matching verified remote archive lease.
    Review-event sources required by recovery records remain in the database.
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
        decision = conn.execute('SELECT proposal FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()
        if not decision or not _flat_noop_plan(decision[0]):
            return {'status': 'DEFERRED_NONNOOP_PLAN'}
        current_proof = event_proof(conn, slot)
        if (not delivered_event_proof(current_proof, proof, event_deliveries, source_identity)
                or any(event['rowid'] >= cursors['llm_scenario_review_events'] for event in current_proof['events'])):
            return {'status': 'DEFERRED_EVENT_EVIDENCE'}
        def stored_bytes():
            queries = (
                'SELECT coalesce(sum(length(cast(body AS BLOB))),0) FROM llm_scenario_review_tapes WHERE slot=?',
                'SELECT coalesce(sum(length(cast(body AS BLOB))),0) FROM llm_scenario_review_events WHERE slot=?',
                'SELECT coalesce(sum(length(cast(body AS BLOB))),0) FROM llm_scenario_recovery_events WHERE slot=?',
            )
            return sum(conn.execute(query, (slot,)).fetchone()[0] for query in queries)
        before_bytes = stored_bytes()
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
        required = _required_event_sequences(conn, slot, recovery_rows)
        removed_events = 0
        for event in current_proof['events']:
            if event['sequence'] in required:
                continue
            changed = conn.execute('DELETE FROM llm_scenario_review_events WHERE rowid=? AND slot=? AND sequence=?',
                                   (event['rowid'], slot, event['sequence'])).rowcount
            if changed != 1:
                raise ValueError('event_delete_source_changed')
            removed_events += 1
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
        if stored_bytes() >= before_bytes:
            return {'status': 'DEFERRED_NO_STORAGE_GAIN'}
        if guard() is not True or lease_alive() is not True:
            raise ValueError('event_delete_guard_failed')
        conn.commit()
        return {'status': 'REMOVED', 'events': removed_events}
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
    progress = index
    def save_cursor():
        path = retention.BACKUP_ROOT / '.btc-retention-cursor.json'
        if retention.read_cursor(path, identity) != index['after_slot']:
            raise ValueError('event_delete_cursor_changed')
        retention._save_cursor(progress, identity)
    if retention.read_cursor(retention.BACKUP_ROOT / '.btc-retention-cursor.json', identity) != index['after_slot']:
        raise ValueError('event_delete_cursor_changed')
    if not index['items']:
        save_cursor()
        return {'status': 'MORE_TO_SCAN' if index['truncated'] else 'NO_CANDIDATES',
                'removed': 0, 'removed_slots': 0, 'removed_events': 0, 'more_to_scan': index['truncated']}
    if retention.EXPORT_STATE.is_symlink() or not retention.EXPORT_STATE.is_file():
        raise ValueError('event_delete_export_state_invalid')
    at = time.time() if now is None else now
    selected, usable, event_count = [], [], 0
    for item in index['items']:
        proof = item['proof']
        if proof.get('unavailable') is not True:
            rows = proof.get('events')
            if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EVENTS:
                raise ValueError('event_delivery_query_invalid')
            next_usable, next_events = len(usable) + 1, event_count + len(rows)
            query_count = (next_usable + 99) // 100 + (next_events + MAX_QUERY_EVENTS - 1) // MAX_QUERY_EVENTS
            if query_count > MAX_DELIVERY_QUERIES:
                break
            usable.append(item)
            event_count = next_events
        selected.append(item)
    budget_capped = len(selected) < len(index['items'])
    if budget_capped:
        if not selected:
            raise ValueError('event_delivery_budget_invalid')
        progress = {'last_scanned_slot': selected[-1]['candidate']['slot'], 'truncated': True}
    if not usable:
        save_cursor()
        return {'status': 'DEFERRED', 'removed': 0, 'removed_slots': 0, 'removed_events': 0,
                'outcomes': {'DEFERRED_EVENT_PROOF': len(selected)}, 'more_to_scan': index['truncated']}
    tape_receipts = []
    for start in range(0, len(usable), 100):
        tape_receipts.extend(retention.fetch_deliveries([item['candidate'] for item in usable[start:start + 100]], identity))
    events = [event for item in usable for event in item['proof'].get('events', [])]
    rowids = [event.get('rowid') if isinstance(event, dict) else None for event in events]
    if any(type(rowid) is not int or rowid <= 0 for rowid in rowids) or len(set(rowids)) != len(rowids):
        raise ValueError('event_delivery_query_invalid')
    deliveries = []
    for start in range(0, len(events), MAX_QUERY_EVENTS):
        deliveries.extend(fetch_event_deliveries({'events': events[start:start + MAX_QUERY_EVENTS]}, identity))
    by_id = {}
    for receipt in deliveries:
        by_id.setdefault(receipt['event_id'], []).append(receipt)
    prepared = []
    for item in selected:
        if item['proof'].get('unavailable') is True:
            prepared.append((item, None, None))
            continue
        ids = [hashlib.sha256(f'{identity}:llm_scenario_review_events:{event["rowid"]}'.encode()).hexdigest()[:32]
               for event in item['proof']['events']]
        prepared.append((item, tape_receipts, [receipt for event_id in ids for receipt in by_id.get(event_id, [])]))
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
    return {'status': ('DEFERRED' if any(k != 'REMOVED' for k in statuses) else
                       'BUDGET_LIMIT' if budget_capped else 'MORE_TO_SCAN' if index['truncated'] else 'COMPLETE'),
            'removed': removed_slots, 'removed_slots': removed_slots, 'removed_events': removed_events,
            'outcomes': dict(statuses), 'more_to_scan': budget_capped or index['truncated']}
