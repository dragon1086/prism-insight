"""Identify strictly flat WAIT tapes for later archive and delivery verification.

This module never deletes data. A candidate is not proof of safe deletion.
"""
import hashlib
import json
import math
from pathlib import Path
import re
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
