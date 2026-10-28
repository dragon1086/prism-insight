"""Bounded read-only BTC evidence bridge. Delivery is at-least-once by stable ID.

No trading imports, source writes, network calls or order authority. Only the new
review streams have an explicit retained-history backfill; old cutovers stay intact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

from .events import build_event
from .review_storage import review_text
from utils.btc_review_references import read_tape
from utils.btc_financial_references import restore as restore_financial
from utils.btc_audit_references import read_audit

STREAMS = ('llm_scenario_audit_events', 'llm_scenario_recovery_events',
           'llm_scenario_broker_evidence', 'llm_scenario_outbox', 'llm_scenario_review_events',
           'llm_scenario_review_tapes')
# All SQL text is literal. Source keys select a statement, never a table-name splice.
STREAM_SQL = {
    'llm_scenario_review_tapes': (
        'SELECT MAX(rowid) FROM llm_scenario_review_tapes',
        'SELECT slot,body FROM llm_scenario_review_tapes WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_review_tapes WHERE rowid>? ORDER BY rowid LIMIT ?'),
    'llm_scenario_review_events': (
        'SELECT MAX(rowid) FROM llm_scenario_review_events',
        'SELECT slot,sequence,body FROM llm_scenario_review_events WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_review_events WHERE rowid>? ORDER BY rowid LIMIT ?'),
    'llm_scenario_audit_events': (
        'SELECT MAX(rowid) FROM llm_scenario_audit_events',
        'SELECT kind,decision_slot,body FROM llm_scenario_audit_events WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_audit_events WHERE rowid>? ORDER BY rowid LIMIT ?'),
    'llm_scenario_recovery_events': (
        'SELECT MAX(rowid) FROM llm_scenario_recovery_events',
        'SELECT body FROM llm_scenario_recovery_events WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_recovery_events WHERE rowid>? ORDER BY rowid LIMIT ?'),
    'llm_scenario_broker_evidence': (
        'SELECT MAX(rowid) FROM llm_scenario_broker_evidence',
        'SELECT kind,body FROM llm_scenario_broker_evidence WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_broker_evidence WHERE rowid>? ORDER BY rowid LIMIT ?'),
    'llm_scenario_outbox': (
        'SELECT MAX(rowid) FROM llm_scenario_outbox',
        'SELECT body FROM llm_scenario_outbox WHERE rowid=?',
        'SELECT rowid AS source_rowid,* FROM llm_scenario_outbox WHERE rowid>? ORDER BY rowid LIMIT ?'),
}
QUERY_SQL = {
    'review_manifests': "SELECT DISTINCT manifest_id,CASE WHEN length(json_extract(body,'$.input_id'))<=4096 THEN json_extract(body,'$.input_id') END AS input_id FROM llm_scenario_audit_events WHERE decision_slot=? AND kind='decision_input' LIMIT 3",
    'queue': 'INSERT OR IGNORE INTO outbox VALUES(?,?)',
    'manifest': 'SELECT body FROM llm_scenario_audit_manifests WHERE manifest_id=?',
    'notice_status': 'SELECT status FROM notices WHERE id=?',
    'notice_save': 'INSERT OR REPLACE INTO notices VALUES(?,?,?)',
    'model_ref_save': 'INSERT OR REPLACE INTO model_refs VALUES(?,?,?)',
    'model_meta_save': 'INSERT OR REPLACE INTO model_metadata VALUES(?,?,?)',
    'decision': 'SELECT snapshot,context,proposal,outcome FROM llm_scenario_decisions WHERE slot=?',
    'model_refs': 'SELECT kind,ref FROM model_refs WHERE slot=?',
    'model_metadata': 'SELECT kind,body FROM model_metadata WHERE slot=?',
    'delete_refs': 'DELETE FROM model_refs WHERE slot=?',
    'delete_metadata': 'DELETE FROM model_metadata WHERE slot=?',
    'save_identity': "INSERT INTO meta VALUES('source_identity',?)",
    'cursor': 'SELECT seq FROM cursors WHERE stream=?',
    'save_cursor': 'INSERT INTO cursors VALUES(?,?)',
    'save_anchor': 'INSERT INTO anchors VALUES(?,?)',
    'anchor': 'SELECT ref FROM anchors WHERE stream=?',
    'update_cursor': 'UPDATE cursors SET seq=? WHERE stream=?',
    'update_anchor': 'UPDATE anchors SET ref=? WHERE stream=?',
    'watches': "SELECT id FROM notices WHERE status IN ('QUEUED','SENDING') ORDER BY checked LIMIT ?",
    'notice': 'SELECT * FROM llm_scenario_outbox WHERE event_id=?',
    'checked': 'UPDATE notices SET checked=? WHERE id=?',
    'queued': 'SELECT id,body FROM outbox ORDER BY rowid LIMIT ?',
    'delete_queued': 'DELETE FROM outbox WHERE id=?',
}


def _query(connection, name, *, parameters=()):
    """Closed statement registry; values are exclusively SQLite bound parameters."""
    if isinstance(name, tuple):
        stream, operation = name
        if type(operation) is not int or operation not in (0, 1, 2):
            raise ValueError('unknown_statement')
        statement = STREAM_SQL[stream][operation]
    else:
        statement = QUERY_SQL[name]
    return connection.execute(statement, parameters)


NUMBERS = {'quantity', 'price', 'mark_price', 'hard_stop', 'previous_hard_stop', 'initial_equity',
           'realized_loss', 'fees_paid', 'funding_paid', 'net_pnl', 'gross_pnl', 'fees', 'funding',
           'budget', 'total_risk', 'available', 'proposed_risk', 'reserved_risk', 'confidence',
           'ma10', 'ma35', 'open', 'high', 'low', 'close', 'volume', 'progress', 'remaining_ms',
           'captured_at', 'observed_at_ms', 'open_time_ms', 'revision', 'scenario_risk_fraction'}
NUMBERS |= {'realized_pnl', 'net_realized_pnl', 'funding_net', 'cumulative_qty', 'entry_quantity',
            'exit_quantity', 'remaining_quantity', 'entry_notional', 'exit_notional',
            'fraction', 'avgPrice', 'stopLoss', 'takeProfit', 'markPrice', 'size', 'equity',
            'old_stop', 'new_stop', 'proposed_stop', 'old_hard_stop', 'new_hard_stop',
            'initial_hard_stop', 'totalEquity', 'positionValue', 'positionIM', 'leverage',
            'old_risk', 'new_risk', 'risk_before', 'risk_after', 'ma_gap', 'ma_gap_fraction'}
BOOLEANS = {'protection_confirmed', 'terminal', 'protection_ok', 'orders_reconciled'}
ENUMS = {'action', 'side', 'status', 'accounting_status', 'loaded_code_status',
         'price_position', 'ma_order', 'gap_state'}
ENUM_VALUES = frozenset(('WAIT OPEN ADJUST EXIT LONG SHORT Buy Sell buy sell confirmed pending '
    'unknown UNKNOWN VERIFIED MIXED blocked ok accepted rejected intent_pending terminal '
    'TERMINAL PENDING SUBMITTED FILLED CANCELLED CANCELED QUEUED SENDING SENT ACK '
    'ABOVE BELOW BETWEEN AT_MA10 AT_MA35 AT_BOTH BULLISH BEARISH EQUAL WIDENING '
    'NARROWING FLAT UNAVAILABLE unavailable available').split())
REASON_CODES = frozenset(('input_size snapshot_unavailable snapshot_stale invalid_contract_context '
    'invalid_input oauth_model_failed late_response invalid_json model_contract_failed '
    'llm_input_preparation_failed llm_output_contract_failed llm_call_failed '
    'reconciliation_or_proposal_failed awaiting_exact_evidence submission_unknown '
    'another_protection_or_execution_tick').split())
CONTAINERS = {'risk', 'positions', 'pending_entries', 'before', 'after', 'presented', 'outcome',
              'confirmed', 'forming', 'ohlcv', 'timeframes', '15m', '30m', '1h', '4h', '12h', '1d', '1w'}
CONTAINERS |= {'result', 'observation', 'owned_orders', 'position', 'take_profits',
              'partial_stops', 'current_plan', 'stop_policy', 'risk_before', 'risk_after',
              'consumed_costs', 'settlement'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _facts(value, prefix='', depth=0):
    """Exact typed numeric facts and code enums, never arbitrary free text or raw IDs."""
    out = {}
    if depth > 5 or not isinstance(value, dict):
        return out
    for key, item in value.items():
        path = prefix + ('ledger_status' if key == 'accounting_status' else key)
        if key in NUMBERS and type(item) in (int, float) and math.isfinite(item):
            out[path] = item
        elif key in NUMBERS and isinstance(item, str) and len(item) <= 80:
            try:
                if Decimal(item).is_finite():
                    out[path] = item  # Preserve broker numeric-string representation exactly.
            except InvalidOperation:
                pass
        elif key in BOOLEANS and type(item) is bool:
            out[path] = item
        elif key in ENUMS and isinstance(item, str) and item in ENUM_VALUES:
            out[path] = item
        elif key in {'reason', 'failure_code'} and isinstance(item, str):
            if item in REASON_CODES:
                out[path] = item
            else:
                out[path + '_sha256'] = _hash(item)
                out[path + '_text_omitted'] = True
        elif key in CONTAINERS:
            if isinstance(item, dict):
                out.update(_facts(item, path + '.', depth + 1))
            elif isinstance(item, list):
                out[path + '.source_count'] = len(item)
                out[path + '.complete'] = len(item) <= 30
                for index, row in enumerate(item[:30]):
                    out.update(_facts(row, path + f'.{index}.', depth + 1))
    return out


def _identity(path):
    stat = path.stat()
    return _hash(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}')


def _guard_paths(source, state, spool):
    """Reject all configured aliases before chmod, creation, or SQLite writes."""
    groups = [[Path(str(base) + suffix) for suffix in ('', '-wal', '-shm', '-journal')]
              for base in (source, state)] + [[spool]]
    if state.is_symlink() or spool.is_symlink():
        raise ValueError('unsafe_output_symlink')
    for i, group in enumerate(groups):
        for other in groups[i + 1:]:
            for left in group:
                for right in other:
                    if left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right)):
                        raise ValueError('source_state_spool_alias')


def _queue(state, identity, key, kind, attributes, stamp):
    event_id = _hash(identity + ':' + key)[:32]
    if attributes.get('scenario_id_ref'):
        trace_key = 'scenario:' + attributes['scenario_id_ref']
    elif attributes.get('decision_slot') is not None:
        trace_key = 'slot:' + str(attributes['decision_slot'])
    elif attributes.get('input_id_ref'):
        trace_key = 'input:' + attributes['input_id_ref']
    else:
        trace_key = 'source:' + key
    unknown_review_time = (kind in ('review','review_tape') and
        attributes.get('source_time_basis') == 'UNKNOWN_EVENT_TIME_EXPORT_OBSERVATION_ONLY')
    attrs = dict(attributes, source_identity=identity, export_observed_at=time.time(),
                 source_event_time=None if unknown_review_time else stamp, deployment_scope='UNKNOWN_NOT_ATTESTED',
                 evidence_scope='OBSERVED_SOURCE_ONLY_NOT_FILL_OR_DELIVERY_INFERENCE')
    value = dict(event_type='btc.research.' + kind, service='prism-btc-research-export',
                 event_id=event_id, market='CRYPTO', ticker='BTCUSDT', attributes=attrs,
                 event_time=datetime.fromtimestamp(stamp, timezone.utc),
                 trace_id=_hash(identity + ':' + trace_key)[:32], span_id=event_id[:16])
    if len(_json(attrs).encode()) > 56000:
        value['attributes'] = dict(source_identity=identity, gap='SUMMARY_SIZE_LIMIT',
                                    retained_summary_sha256=_hash(_json(attrs)))
    built = build_event(**value)
    built['attributes']['exporter_git_revision'] = built.pop('git_sha', None)
    built['git_sha'] = built['attributes'].get('source_git_revision')
    built['policy_version'] = built['attributes'].get('source_policy_hash')
    _query(state, 'queue', parameters=(event_id, _json(built)))


def _manifest(source, manifest_id):
    if not manifest_id:
        return {'policy_evidence': 'UNKNOWN'}
    row = _query(source, 'manifest', parameters=(manifest_id,)).fetchone()
    if not row:
        return {'policy_evidence': 'MISSING_MANIFEST'}
    body = json.loads(row[0])
    result = {'manifest_ref': _hash(str(manifest_id)), 'manifest_sha256': _hash(row[0])}
    revision = body.get('git_revision')
    if isinstance(revision, str) and re.fullmatch('[a-f0-9]{40}', revision):
        result['source_git_revision'] = revision
    for key in ('policy_hash', 'execution_hash'):
        if isinstance(body.get(key), str) and re.fullmatch('[a-f0-9]{64}', body[key]):
            result['source_' + key] = body[key]
    result.update(_facts(body))
    return result


def _notice(state, identity, row, now):
    event = row['event_id']
    status = row['status']
    previous = _query(state, 'notice_status', parameters=(event,)).fetchone()
    if previous and previous[0] == status:
        return
    attrs = {'notice_ref': _hash(event), 'body_sha256': _hash(row['body']),
             'notice_status': status if status in {'QUEUED', 'SENDING', 'SENT', 'UNKNOWN'} else 'UNRECOGNIZED',
             'receipt_observed_at': now, 'actual_delivery_time': 'UNKNOWN',
             'delivery_evidence': 'SOURCE_STATUS_ONLY'}
    if row['message_id'] is not None:
        attrs['receipt_ref'] = _hash(str(row['message_id']))
    _queue(state, identity, 'notice:' + event + ':' + str(status), 'notice', attrs, now)
    _query(state, 'notice_save', parameters=(event, status, now))


def _references(*values):
    refs = {}
    for key in ('event_id', 'scenario_id', 'intent_id', 'run_id', 'input_id', 'action_id'):
        found = {v[key] for v in values if isinstance(v, dict) and isinstance(v.get(key), str) and v[key]}
        if len(found) == 1:
            refs[key + '_ref'] = _hash(next(iter(found)))
        elif len(found) > 1:
            refs[key + '_ref_status'] = 'CONFLICT'
    return refs


def _model_metadata(body):
    result = {}
    model = body.get('model')
    if isinstance(model, str) and re.fullmatch(r'gpt-[a-z0-9.-]{1,40}', model):
        result['model'] = model
    if body.get('effort') in ('low', 'medium', 'high', 'xhigh', 'max'):
        result['effort'] = body['effort']
    if type(body.get('fast')) is bool:
        result['fast'] = body['fast']
    for source, target in (('request_hash', 'source_request_hash'), ('sha256', 'source_raw_sha256')):
        if isinstance(body.get(source), str) and re.fullmatch('[a-f0-9]{64}', body[source]):
            result[target] = body[source]
    for key in ('system_prompt', 'user_prompt', 'raw'):
        if isinstance(body.get(key), str):
            result[key + '_sha256'] = _hash(body[key])
            if key == 'user_prompt':
                result['input_utf8_bytes'] = len(body[key].encode())
    if isinstance(body.get('response_schema'), dict):
        result['response_schema_sha256'] = _hash(_json(body['response_schema']))
    return result


def _source_text(stream, row, source=None):
    if stream == 'llm_scenario_review_tapes':
        return read_tape(row['body'], source, row['slot'], decode_legacy=review_text)
    if stream in ('llm_scenario_review_events', 'llm_scenario_recovery_events'):
        return review_text(row['body'])
    if stream == 'llm_scenario_broker_evidence' and row['kind'] == 'financial':
        return restore_financial(row['body'])
    if stream == 'llm_scenario_audit_events' and row['kind'] == 'decision_input':
        return read_audit(row['body'], source, row['decision_slot'])
    return row['body']


def _anchor_hash(stream, row, source=None):
    if row is None:
        return None
    if stream == 'llm_scenario_recovery_events':
        return _hash(review_text(row['body']))
    if stream == 'llm_scenario_review_events':
        return _hash(_json([row['slot'], row['sequence'], review_text(row['body'])]))
    if stream == 'llm_scenario_review_tapes':
        return _hash(_json([row['slot'], _source_text(stream, row, source)]))
    if stream == 'llm_scenario_broker_evidence' and row['kind'] == 'financial':
        return _hash(_source_text(stream, row, source))
    if stream == 'llm_scenario_audit_events' and row['kind'] == 'decision_input':
        return _hash(_source_text(stream, row, source))
    return _hash(row['body'])


def _hash_fields(body, keys):
    return {key:body[key] for key in keys if isinstance(body.get(key), str)
            and re.fullmatch('[a-f0-9]{64}', body[key])}


def _review_manifest(source, slot, input_ref=None, manifest_cache=None):
    """Exact-slot references only, never latest/global/exporter deployment policy."""
    try:
        if manifest_cache is not None and slot in manifest_cache:
            rows = manifest_cache[slot]
        else:
            rows = _query(source, 'review_manifests', parameters=(slot,)).fetchall()
            if manifest_cache is not None and len(manifest_cache) < 100:
                manifest_cache[slot] = rows
        if len(rows) != 1 or not rows[0][0]:
            return dict(policy_evidence='UNKNOWN_CONFLICTING_MANIFESTS' if len(rows)>1 else 'UNKNOWN')
        original_input = rows[0][1]
        if input_ref and (not isinstance(original_input,str) or _hash(original_input)!=input_ref):
            return dict(policy_evidence='UNKNOWN_INPUT_BINDING_CONFLICT')
        evidence = _manifest(source, rows[0][0])
        if evidence.get('loaded_code_status') != 'VERIFIED':
            return dict(policy_evidence='UNKNOWN_UNVERIFIED_MANIFEST',
                        **{k:v for k,v in evidence.items() if k in ('manifest_ref','manifest_sha256','loaded_code_status')})
        return dict(evidence, policy_evidence='EXACT_SLOT_UNIQUE_MANIFEST_OBSERVED_NOT_CURRENT')
    except (sqlite3.Error, ValueError, TypeError):
        return dict(policy_evidence='UNKNOWN_MANIFEST_UNAVAILABLE')


def _review_candidate(value):
    if not isinstance(value, dict):
        return dict(status='UNKNOWN')
    result = _hash_fields(value, ('receipt_sha256','raw_sha256','effective_sha256',
        'raw_allocation_sha256','context_sha256','input_sha256'))
    ident = value.get('id')
    if ident in ('WAIT','c0','c1'):
        result['candidate_id'] = ident
    elif isinstance(ident, str):
        result['candidate_id_ref'] = _hash(ident)
    if type(value.get('eligible')) is bool:
        result['eligible'] = value['eligible']
    for name in ('effective_plan','raw_proposal','raw_allocation_proposal'):
        plan = value.get(name)
        if isinstance(plan, dict):
            result[name + '_facts'] = _facts(plan)
            result.update(_references(plan))
    if isinstance(value.get('rejection'), str):
        result['rejection_sha256'] = _hash(value['rejection'])
    result['semantics'] = 'CANDIDATE_NOT_ORDER_OR_FILL'
    return result


def _capture_review(source, state, identity, row, body, now, manifest_cache=None):
    if any(type(row[k]) is not int or row[k] < 0 for k in ('slot','sequence')):
        raise ValueError('review_identity_invalid')
    kinds = {'MODEL_REQUEST','MODEL_RAW','MODEL_ERROR','CALCULATED_CANDIDATES',
             'CALCULATED_CANDIDATE_REJECTED','RESEARCH_REJECTION','RESEARCH_RESULT',
             'FRESH_REVIEW_RECEIPT','FINAL_FRESH_REVIEW_RECEIPT'}
    kind = body.get('kind')
    attrs = dict(source_stream='llm_scenario_review_events',source_rowid=row['source_rowid'],
        source_sequence=row['sequence'],decision_slot=row['slot'],
        source_kind=kind if isinstance(kind,str) and kind in kinds else 'UNKNOWN',
        body_sha256=_hash(row['body']),facts_scope='ALLOWLISTED_SUBSET_RAW_SOURCE_RETAINED',
        observation_origin='OBSERVED_NOT_CURRENT',granular_fill_coverage='UNKNOWN_NOT_EXTRACTED')
    attrs.update(_review_manifest(source, row['slot'], manifest_cache=manifest_cache))
    stamp = body.get('started_at')
    if type(stamp) in (int,float) and math.isfinite(stamp) and 0 <= stamp <= now:
        attrs['source_time_basis'] = 'EVENT_STARTED_AT'
    else:
        stamp = now
        attrs['source_time_basis'] = 'UNKNOWN_EVENT_TIME_EXPORT_OBSERVATION_ONLY'
    if body.get('phase') in ('candidate','selection'):
        attrs['phase'] = body['phase']
    attrs.update(_hash_fields(body, ('request_sha256','prepared_request_sha256','actual_request_sha256','sha256')))
    for key in ('request','actual_request'):
        request = body.get(key)
        if isinstance(request,dict):
            metadata = dict(request)
            metadata.update(effort=request.get('reasoning_effort'),fast=request.get('fast_tier'))
            attrs[key+'_metadata'] = _model_metadata(metadata)
            attrs[key+'_observed_sha256'] = _hash(_json(request))
            declared = body.get(key+'_sha256')
            attrs[key+'_hash_status'] = ('MATCH' if declared == attrs[key+'_observed_sha256'] else
                'CONFLICT' if declared is not None else 'UNKNOWN')
            prompt = request.get('user_prompt')
            if isinstance(prompt,str) and len(prompt.encode()) <= 100000:
                try:
                    decoded = json.loads(prompt)
                    refs = _references(decoded.get('contract_context')) if isinstance(decoded,dict) else {}
                    if refs.get('input_id_ref'):
                        attrs.update(refs)
                        # A supplied request identity must agree with the exact
                        # decision-input audit, not merely share its time slot.
                        for field in ('source_git_revision','source_policy_hash','source_execution_hash',
                                      'manifest_ref','manifest_sha256','loaded_code_status'):
                            attrs.pop(field,None)
                        attrs.update(_review_manifest(source,row['slot'],refs['input_id_ref'],manifest_cache))
                except (ValueError,TypeError):
                    pass
    if isinstance(body.get('raw'),str):
        attrs['raw_sha256'] = _hash(body['raw'])
    if isinstance(body.get('code'),str):
        attrs['error_code_sha256'] = _hash(body['code'])
    if type(body.get('elapsed_seconds')) in (int,float) and math.isfinite(body['elapsed_seconds']) and body['elapsed_seconds'] >= 0:
        attrs['elapsed_seconds'] = body['elapsed_seconds']
    nested = body.get('body')
    if isinstance(nested,dict):
        attrs.update(_hash_fields(nested, ('bundle_sha256','input_sha256','receipt_sha256')))
        attrs['facts'] = _facts(nested)
        candidates = nested.get('candidates')
        if isinstance(candidates,list):
            attrs['candidate_count'] = len(candidates)
            attrs['candidate_summaries_complete'] = len(candidates) <= 2
            attrs['candidates'] = [_review_candidate(value) for value in candidates[:2]]
        if isinstance(nested.get('selected'),dict):
            attrs['selected'] = _review_candidate(nested['selected'])
        if isinstance(nested.get('selection'),dict):
            attrs['selection'] = _hash_fields(nested['selection'], ('receipt_sha256',))
            if nested['selection'].get('selection') in ('WAIT','c0','c1'):
                attrs['selection']['candidate_id'] = nested['selection']['selection']
        if kind == 'CALCULATED_CANDIDATE_REJECTED':
            attrs['candidate'] = _review_candidate(nested)
    receipt = body.get('receipt')
    if isinstance(receipt,dict):
        attrs['receipt_body_sha256'] = _hash(_json(receipt))
        attrs['receipt_facts'] = _facts(receipt)
        attrs.update(_hash_fields(receipt, ('bundle_sha256','input_sha256','envelope_sha256',
            'selected_receipt_sha256','original_context_sha256','fresh_context_sha256',
            'payload_sha256','validated_sha256')))
        if _hash_fields(receipt, ('sha256',)):
            attrs['receipt_sha256'] = receipt['sha256']
        if receipt.get('status') == 'FRESH_VALIDATED_NOT_AUTHORIZED':
            attrs['receipt_status'] = receipt['status']
        if type(receipt.get('execution_authorized')) is bool:
            attrs['receipt_execution_authorized'] = receipt['execution_authorized']
    _queue(state, identity, 'llm_scenario_review_events:'+str(row['source_rowid']), 'review', attrs, stamp)


def _capture_review_tape(source, state, identity, row, body, now, manifest_cache=None):
    if type(row['slot']) is not int or row['slot'] < 0:
        raise ValueError('review_identity_invalid')
    attrs = dict(source_stream='llm_scenario_review_tapes',source_rowid=row['source_rowid'],
        decision_slot=row['slot'],body_sha256=_hash(row['body']),observation_origin='OBSERVED_NOT_CURRENT',
        source_time_basis='UNKNOWN_EVENT_TIME_EXPORT_OBSERVATION_ONLY',
        evidence_scope='RETAINED_REVIEW_TAPE_NOT_EXECUTION_OR_PROFIT_PROOF')
    attrs.update(_review_manifest(source,row['slot'],manifest_cache=manifest_cache))
    attrs.update(_hash_fields(body,('sha256','inputs_sha256')))
    # Tape digests use ensure_ascii=True, unlike the model request digest.
    unsealed = {key:value for key,value in body.items() if key != 'sha256'}
    sealed_hash = _hash(json.dumps(unsealed,sort_keys=True,separators=(',',':'),allow_nan=False))
    attrs['tape_hash_status'] = 'MATCH' if body.get('sha256') == sealed_hash else 'UNKNOWN_OR_CONFLICT'
    inputs = body.get('inputs')
    if isinstance(inputs,dict):
        attrs.update(_references(inputs.get('context')))
        if attrs.get('input_id_ref'):
            for field in ('source_git_revision','source_policy_hash','source_execution_hash',
                          'manifest_ref','manifest_sha256','loaded_code_status'):
                attrs.pop(field,None)
            attrs.update(_review_manifest(source,row['slot'],attrs['input_id_ref'],manifest_cache))
        computed_inputs = _hash(json.dumps(inputs,sort_keys=True,separators=(',',':'),allow_nan=False))
        attrs['inputs_hash_status'] = 'MATCH' if body.get('inputs_sha256') == computed_inputs else 'UNKNOWN_OR_CONFLICT'
        bindings = inputs.get('bindings')
        if isinstance(bindings,dict):
            attrs['review_policy_binding_sha256'] = _hash(_json(bindings))
            if bindings.get('policy') in ('calculated-review-v1','allocation-single','allocation-calculated',
                    'action-allocation-single-v1','action-allocation-calculated-v1',
                    'economic-reject-single-v1','economic-reject-calculated-v1'):
                attrs['review_policy'] = bindings['policy']
            sources = bindings.get('source_sha256')
            if isinstance(sources,dict):
                attrs['binding_source_count'] = len(sources)
                attrs['binding_sources_sha256'] = _hash(_json(sources))
            parity = bindings.get('loaded_parity')
            attrs['review_loaded_parity_status'] = ('VERIFIED' if isinstance(parity,dict) and parity
                and all(value == 'VERIFIED' for value in parity.values()) else 'UNKNOWN_OR_MIXED')
    if type(body.get('calls')) is int and 0 <= body['calls'] <= 2:
        attrs['model_call_count'] = body['calls']
    outcome = body.get('outcome')
    if isinstance(outcome,dict):
        if outcome.get('status') in ('COMPLETE','FAILED'):
            attrs['review_outcome_status'] = outcome['status']
        result = outcome.get('result')
        if isinstance(result,dict):
            attrs.update(_hash_fields(result,('bundle_sha256',)))
            attrs['selected'] = _review_candidate(result.get('selected'))
    operations = body.get('operations')
    if isinstance(operations,list):
        attrs['operation_count'] = len(operations)
        generated = [op for op in operations[:128] if isinstance(op,dict) and op.get('kind')=='generate']
        attrs['request_refs_complete'] = len(operations) <= 128 and len(generated)<=2
        attrs['request_refs'] = []
        for operation in operations[:128]:
            if isinstance(operation,dict) and operation.get('kind') == 'generate' and isinstance(operation.get('request'),dict):
                request = operation['request']
                attrs['request_refs'].append(dict(actual_request_sha256=_hash(_json(request)),
                    **({'raw_sha256':_hash(operation['text'])} if isinstance(operation.get('text'),str) else {})))
                if len(attrs['request_refs']) >= 2:
                    break
    _queue(state,identity,'llm_scenario_review_tapes:'+str(row['source_rowid']),'review_tape',attrs,now)


def _capture(source, state, identity, stream, row, manifest_cache=None):
    now = time.time()
    if stream == 'llm_scenario_outbox':
        _notice(state, identity, row, now)
        return
    if stream in ('llm_scenario_review_events', 'llm_scenario_review_tapes', 'llm_scenario_recovery_events'):
        row = dict(row)
        row['body'] = _source_text(stream, row, source)
    raw = row['body']
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('source_body_not_object')
    if stream == 'llm_scenario_review_events':
        _capture_review(source, state, identity, row, body, now, manifest_cache)
        return
    if stream == 'llm_scenario_review_tapes':
        _capture_review_tape(source,state,identity,row,body,now,manifest_cache)
        return
    kind = row['kind']
    attrs = {'source_stream': stream, 'source_rowid': row['source_rowid'],
             'source_kind': kind if re.fullmatch(r'[A-Za-z_]{1,80}', kind) else 'UNKNOWN',
             'body_sha256': _hash(raw), 'facts': _facts(body), 'policy_evidence': 'UNKNOWN'}
    attrs['facts_scope'] = 'ALLOWLISTED_SUBSET_RAW_SOURCE_RETAINED'
    attrs['granular_fill_coverage'] = 'UNKNOWN_NOT_EXTRACTED'
    stamp = row['observed_at'] if 'observed_at' in row.keys() else row['captured_at']
    slot = row['slot'] if 'slot' in row.keys() else row['decision_slot'] if 'decision_slot' in row.keys() else None
    if slot is not None:
        attrs['decision_slot'] = slot
    if 'manifest_id' in row.keys():
        attrs.update(_manifest(source, row['manifest_id']))
    if kind == 'accounting_observation':
        ledger = body.get('result', {})
        settlement = ledger.get('settlement') if isinstance(ledger, dict) else None
        confirmed = isinstance(ledger, dict) and ledger.get('status') == 'confirmed' and 'execution_ids' in ledger
        if isinstance(ledger, dict) and ledger.get('status') == 'available' and isinstance(settlement, dict):
            confirmed = all(settlement.get(k) is True for k in ('flat_confirmed', 'orders_terminal', 'executions_complete'))
            ids = settlement.get('execution_ids')
        else:
            ids = ledger.get('execution_ids') if isinstance(ledger, dict) else None
        if confirmed and isinstance(ids, list) and all(isinstance(i, str) and i for i in ids):
            attrs['confirmed_ledger_execution_refs'] = [_hash(i) for i in ids[:200]]
            attrs['ledger_execution_ref_count'] = len(ids)
            attrs['ledger_execution_refs_complete'] = len(ids) <= 200
        else:
            attrs['ledger_execution_evidence'] = 'UNKNOWN_OR_UNCONFIRMED'
    refs_sources = [dict(row), body]
    attrs.update(_references(*refs_sources))
    if stream == 'llm_scenario_recovery_events' and kind in {'MODEL_REQUEST', 'MODEL_RAW'}:
        _query(state, 'model_ref_save', parameters=(slot, kind, _hash(raw)))
        metadata = dict(_model_metadata(body), source_event_time=stamp)
        _query(state, 'model_meta_save', parameters=(slot, kind, _json(metadata)))
        attrs['model_metadata'] = metadata
        attrs['raw_manifest_only'] = True
    if stream == 'llm_scenario_recovery_events' and kind == 'OUTCOME' and slot is not None:
        decision = _query(source, 'decision', parameters=(slot,)).fetchone()
        if decision:
            for key in decision.keys():
                value = decision[key]
                if value:
                    attrs[key + '_sha256'] = _hash(value)
                    decoded = json.loads(value)
                    attrs[key + '_facts'] = _facts(decoded)
                    if key in ('context', 'proposal'):
                        refs_sources.append(decoded)
            # Recompute conflicts across immutable body and exact-slot context/proposal.
            for key in list(attrs):
                if key.endswith('_ref') or key.endswith('_ref_status'):
                    if key.removesuffix('_status').removesuffix('_ref') in {'event_id', 'scenario_id', 'intent_id', 'run_id', 'input_id', 'action_id'}:
                        del attrs[key]
            attrs.update(_references(*refs_sources))
            attrs['proposal_semantics'] = 'PROPOSED_NOT_INSTALLED_PROTECTION'
            attrs['context_semantics'] = 'DECISION_INPUT_OBSERVATION_NOT_LATER_EXECUTION'
            attrs['model_evidence_refs'] = dict(_query(state, 'model_refs', parameters=(slot,)).fetchall())
            metadata = {kind: json.loads(text) for kind, text in _query(state, 'model_metadata', parameters=(slot,))}
            attrs['model_metadata'] = metadata
            if 'MODEL_REQUEST' in metadata and 'MODEL_RAW' in metadata:
                duration = metadata['MODEL_RAW']['source_event_time'] - metadata['MODEL_REQUEST']['source_event_time']
                if duration >= 0:
                    attrs['request_to_raw_observed_seconds'] = duration
                    attrs['latency_basis'] = 'JOURNAL_TIMESTAMPS_NOT_PROVIDER_LATENCY'
            _query(state, 'delete_refs', parameters=(slot,))
            _query(state, 'delete_metadata', parameters=(slot,))
        else:
            attrs['decision_evidence'] = 'MISSING_EXACT_SLOT'
    _queue(state, identity, stream + ':' + str(row['source_rowid']), 'source', attrs, stamp)


def _append(event, spool_path):
    # Leading newline seals any old short-write prefix without truncating shared data.
    # Persisted, already-sanitized event keeps every retry byte-identical.
    raw = ('\n' + _json(event) + '\n').encode()
    if len(raw) > 65536:
        raise ValueError('event_bound')
    path = Path(spool_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_RDWR, 0o600)
    try:
        written = os.write(fd, raw)
        if written != len(raw):
            raise OSError('partial_spool_write')
        os.fsync(fd)
        end = os.lseek(fd, 0, os.SEEK_END)
        if os.pread(fd, len(raw), end-len(raw)) != raw:
            raise OSError('concurrent_append_verification_gap')
    finally:
        os.close(fd)


def export_once(db_path, state_path, spool_path, limit=100):
    """Old streams retain cutover; new review stream explicitly drains retained history."""
    source = state = None
    counts = {'status': 'UNAVAILABLE', 'captured': 0, 'emitted': 0, 'pending': 0,
              'source_bytes': 0, 'backlog': False, 'source_errors': []}
    try:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid_limit')
        db_path, state_path, spool_path = Path(db_path), Path(state_path), Path(spool_path)
        _guard_paths(db_path, state_path, spool_path)
        identity = _identity(db_path)
        source = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        # Transaction-local only: later audit flushes become visible next call.
        manifest_cache = {}
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(state_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(state_path, 0o600)
        state = sqlite3.connect(state_path, timeout=.05)
        state.executescript('CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY,value); CREATE TABLE IF NOT EXISTS cursors(stream PRIMARY KEY,seq); CREATE TABLE IF NOT EXISTS anchors(stream PRIMARY KEY,ref); CREATE TABLE IF NOT EXISTS outbox(id PRIMARY KEY,body); CREATE TABLE IF NOT EXISTS notices(id PRIMARY KEY,status,checked); CREATE TABLE IF NOT EXISTS model_refs(slot,kind,ref,PRIMARY KEY(slot,kind)); CREATE TABLE IF NOT EXISTS model_metadata(slot,kind,body,PRIMARY KEY(slot,kind));')
        state.execute('BEGIN IMMEDIATE')
        previous = state.execute("SELECT value FROM meta WHERE key='source_identity'").fetchone()
        if previous and previous[0] != identity:
            raise ValueError('source_identity_changed')
        if not previous:
            _query(state, 'save_identity', parameters=(identity,))
        for stream in STREAMS:
            if stream not in tables:
                continue
            cursor = _query(state, 'cursor', parameters=(stream,)).fetchone()
            maximum = _query(source, (stream, 0)).fetchone()[0] or 0
            if cursor is None:
                backfill = stream in ('llm_scenario_review_events','llm_scenario_review_tapes')
                cutover = 0 if backfill else maximum
                _query(state, 'save_cursor', parameters=(stream, cutover))
                anchor = _query(source, (stream, 1), parameters=(cutover,)).fetchone()
                _query(state, 'save_anchor', parameters=(stream, _anchor_hash(stream, anchor, source)))
                _queue(state, identity, 'coverage:' + stream, 'coverage',
                       {'stream': stream, 'coverage_start_after_rowid': cutover,
                        'historical_backfill': backfill,
                        **({'retained_backfill_through_rowid':maximum,
                            'coverage_policy':'RETAINED_HISTORY_NOT_CURRENT_STATE'} if backfill else {})}, time.time())
                counts['backlog'] |= backfill and maximum > 0
                continue
            if maximum < cursor[0]:
                raise ValueError('source_rowid_regressed')
            anchor = _query(source, (stream, 1), parameters=(cursor[0],)).fetchone()
            expected = _query(state, 'anchor', parameters=(stream,)).fetchone()
            if not expected or expected[0] != _anchor_hash(stream, anchor, source):
                raise ValueError('source_anchor_changed')
            if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                break
            for row in _query(source, (stream, 2), parameters=(cursor[0], max(0, limit-counts['captured']))):
                if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                    counts['backlog'] = True
                    break
                state.execute('SAVEPOINT source_row')
                try:
                    raw_body = _source_text(stream, row, source)
                    if raw_body is not row['body']:
                        row = dict(row)
                        row['body'] = raw_body
                    size = len(raw_body.encode())
                    if counts['source_bytes'] + size > 4 * 1024 * 1024:
                        state.execute('RELEASE source_row')
                        counts['backlog'] = True
                        counts['source_errors'].append({'stream': stream, 'error_type': 'SOURCE_BYTE_BUDGET'})
                        break
                    _capture(source, state, identity, stream, row, manifest_cache)
                    _query(state, 'update_cursor', parameters=(row['source_rowid'], stream))
                    _query(state, 'update_anchor', parameters=(_anchor_hash(stream, row, source), stream))
                    state.execute('RELEASE source_row')
                    counts['captured'] += 1
                    counts['source_bytes'] += size
                except Exception as exc:  # Isolate malformed source, not source-identity failure.
                    state.execute('ROLLBACK TO source_row')
                    state.execute('RELEASE source_row')
                    counts['source_errors'].append({'stream': stream, 'rowid': row['source_rowid'],
                                                    'error_type': type(exc).__name__})
                    counts['backlog'] = True
                    break
            current = _query(state, 'cursor', parameters=(stream,)).fetchone()[0]
            counts['backlog'] |= current < maximum
        if 'llm_scenario_outbox' in tables:
            watches = _query(state, 'watches', parameters=(limit,)).fetchall()
            for (event_id,) in watches:
                if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                    counts['backlog'] = True
                    break
                row = _query(source, 'notice', parameters=(event_id,)).fetchone()
                if row:
                    _notice(state, identity, row, time.time())
                    _query(state, 'checked', parameters=(time.time(), event_id))
        source.rollback()
        if _identity(db_path) != identity:
            raise ValueError('source_identity_changed')
        state.commit()
        for event_id, body in _query(state, 'queued', parameters=(limit,)).fetchall():
            event = json.loads(body)
            _append(event, spool_path)
            _query(state, 'delete_queued', parameters=(event_id,))
            state.commit()
            counts['emitted'] += 1
        counts['pending'] = state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        counts['status'] = 'DEGRADED' if counts['source_errors'] else 'OK'
    except Exception as exc:  # fail-open boundary; never expose raw source/exception text
        counts['error_type'] = type(exc).__name__
        if state:
            state.rollback()
    finally:
        if source:
            source.close()
        if state:
            state.close()
    return counts
