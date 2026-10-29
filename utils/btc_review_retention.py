"""Identify strictly flat WAIT tapes for later archive and delivery verification.

This module never deletes data. A candidate is not proof of safe deletion.
"""
import hashlib
import json
import math

from observability.review_storage import review_text
from utils.btc_review_references import read_tape


def candidates(conn, now, *, after_slot=-1, limit=100):
    if (type(now) not in (int, float) or not math.isfinite(now) or now <= 7 * 86400
            or type(after_slot) is not int or after_slot < -1
            or type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError('retention_input_invalid')
    cutoff_slot = int((now - 7 * 86400) // 300)
    rows = conn.execute(
        'SELECT t.rowid,t.slot,t.body,d.context,d.proposal,d.outcome '
        'FROM llm_scenario_review_tapes t LEFT JOIN llm_scenario_decisions d ON d.slot=t.slot '
        'WHERE t.slot>? AND t.slot<? ORDER BY t.slot LIMIT ?', (after_slot, cutoff_slot, limit))
    found, last = [], after_slot
    for rowid, slot, tape, context, proposal, outcome in rows:
        if type(rowid) is not int or type(slot) is not int or slot < 0:
            raise ValueError('retention_source_invalid')
        last = slot
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
    return {'cutoff_slot': cutoff_slot, 'last_scanned_slot': last, 'candidates': found}
