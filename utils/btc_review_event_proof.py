"""Bounded read-only fingerprints for review-event retention evidence.

This module neither selects safe WAITs nor deletes records. Callers must bind
these fingerprints to an independently verified backup and delivery receipts.
"""
import hashlib
import json

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
