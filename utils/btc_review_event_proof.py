"""Bounded read-only fingerprints for review-event retention evidence.

This module neither selects safe WAITs nor deletes records. Callers must bind
these fingerprints to an independently verified backup and delivery receipts.
"""
import hashlib
import json
import re

from observability.review_storage import review_text
from utils.btc_recovery_references import read_recovery
from utils.btc_review_event_references import read_event

MAX_EVENTS = 128
MAX_RECOVERY = 16
MAX_BYTES = 8_000_000


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
        if (not isinstance(event_id, str) or not event_id or event_id in seen
                or not isinstance(kind, str) or not kind):
            raise ValueError('proof_recovery_identity_invalid')
        seen.add(event_id)
        text = read_recovery(stored, conn, slot, decode_legacy=review_text, expected_kind=kind)
        digest, n = _digest(text)
        size += n
        if size > MAX_BYTES:
            raise ValueError('proof_size_exceeded')
        recovery.append({'event_id': event_id, 'kind': kind, 'sha256': digest})
    return {'slot': slot, 'events': events, 'recovery': recovery}


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
                or not isinstance(item['event_id'], str) or not item['event_id']
                or not isinstance(item['kind'], str) or not item['kind']
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
