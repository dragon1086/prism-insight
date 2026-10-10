"""Optional, bounded observations. Never participates in business transactions.

Buffered events are not a durable pre-submit journal. A crash or busy database
can lose them; consumers must report missing evidence, never infer completion.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
# Only fixed absolute git, read-only literal arguments, and no shell below.
import subprocess  # nosec B404
import sys
import time
import uuid
import ast
import types
import importlib.metadata
import inspect

MAX_RECORD_BYTES = 512 * 1024
MAX_RUN_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 128
MAX_REVIEW_BYTES = 16 * 1024
_current = ContextVar('scenario_audit', default=None)


def _gap():
    try:
        logging.getLogger(__name__).warning('AUDIT_GAP')
    except Exception:
        return  # Logging failure must not replace a business result.


def encoded(value):
    parts, size = [], 0
    for part in json.JSONEncoder(sort_keys=True, separators=(',', ':'), allow_nan=False).iterencode(value):
        size += len(part.encode('utf-8'))
        if size > MAX_RECORD_BYTES:
            raise ValueError('audit_bound')
        parts.append(part)
    return ''.join(parts)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def _code_signature(code):
    def constant(value):
        if isinstance(value, types.CodeType):
            return _code_signature(value)
        if isinstance(value, frozenset):
            return ('frozenset', tuple(sorted(repr(item) for item in value)))
        if isinstance(value, tuple):
            return tuple(constant(item) for item in value)
        return repr(value)
    return (code.co_code.hex(), tuple(_code_signature(c) if isinstance(c, types.CodeType)
            else constant(c) for c in code.co_consts), code.co_names, code.co_varnames,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_flags, code.co_freevars, code.co_cellvars)


def _compiled_functions(module_code):
    """Reconstruct lexical names without Python 3.11's CodeType.co_qualname."""
    compiled = {}
    def visit(code, prefix=''):
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                name = prefix + constant.co_name
                compiled[name] = constant
                suffix = '.<locals>.' if constant.co_flags & inspect.CO_NEWLOCALS else '.'
                visit(constant, name + suffix)
    visit(module_code)
    return compiled


def _loaded_parity(name, source, selected=None):
    module = sys.modules.get(name.removesuffix('.py').replace('/', '.'))
    if module is None:
        return 'UNKNOWN'
    compiled = _compiled_functions(compile(source, '<audit>', 'exec', dont_inherit=True))
    functions = []
    for value in vars(module).values():
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
            functions.append(value)
        elif isinstance(value, type) and value.__module__ == module.__name__:
            for member in vars(value).values():
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                if isinstance(member, types.FunctionType):
                    functions.append(member)
    checked = 0
    for function in functions:
        while hasattr(function, '__wrapped__'):
            function = function.__wrapped__
        if selected and function.__name__ not in selected:
            continue
        expected = compiled.get(function.__qualname__)
        if expected is None or _code_signature(expected) != _code_signature(function.__code__):
            return 'MIXED'
        checked += 1
    # Literal configuration (including the system prompt) is mutable separately
    # from code objects, so code equality alone is not enough.
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            try:
                expected = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if getattr(module, target.id, object()) != expected:
                        return 'MIXED'
    return 'VERIFIED' if checked else 'UNKNOWN'


def _manifest():
    root = Path(__file__).resolve().parents[1]
    policy_files = ['live/scenario_llm.py', 'live/scenario_contract.py',
                    'live/scenario_recovery.py',
                    'live/scenario_review_memory.py',
                    'live/scenario_runner_economics.py',
                    'live/scenario_model_input.py',
                    'live/scenario_preview.py', 'engine/scenario_snapshot.py',
                    'engine/scenario_ma_context.py',
                    'engine/indicators.py']
    execution_files = ['live/scenario_execution.py', 'live/scenario_accounting.py',
                       'core/scenario_limit_prices.py', 'core/llm_scenario.py']
    sources, policy, execution, parity = {}, {}, {}, {}
    judgment_parity, execution_parity = {}, {}
    for name in policy_files + execution_files + ['live/scenario_runtime.py', 'live/scenario_broker.py']:
        path = root / name
        source = path.read_text()
        sources[name] = hashlib.sha256(source.encode()).hexdigest()
        tree = ast.parse(source)
        selected_names = None
        if name in policy_files:
            policy[name] = ast.dump(tree, include_attributes=False)
            judgment_parity[name] = _loaded_parity(name, source)
        elif name in execution_files:
            execution[name] = ast.dump(tree, include_attributes=False)
            execution_parity[name] = _loaded_parity(name, source)
        else:
            context_name = '_context' if 'runtime' in name else 'context'
            execution_names = ({'_tick', '_reconcile', '_enabled', '_valid_open_entry', '_apply_review'} if 'runtime' in name else
                {'_accounting', '_funding_schedule', 'capture_financial_evidence',
                 '_risk_accounting', '_daily', 'reconcile', 'capture_account'})
            selected_names = {context_name} | execution_names
            selected = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == context_name]
            policy[name] = [ast.dump(node, include_attributes=False) for node in selected]
            execution[name] = [ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                               if isinstance(node, ast.FunctionDef) and node.name in execution_names]
            judgment_parity[name] = _loaded_parity(name, source, {context_name})
            execution_parity[name] = _loaded_parity(name, source, execution_names)
        parity[name] = _loaded_parity(name, source, selected_names)
    # Recovery contains both judgment evidence and execution authorization.
    execution['live/scenario_recovery.py'] = policy['live/scenario_recovery.py']
    execution_parity['live/scenario_recovery.py'] = judgment_parity['live/scenario_recovery.py']
    try:
        # Fixed absolute executable and literal read-only arguments; no user input.
        revision = subprocess.run(  # nosec B603
            ['/usr/bin/git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
            text=True, timeout=1, check=True).stdout.strip()
    except Exception:
        revision = None
    def status(checks):
        return 'MIXED' if 'MIXED' in checks.values() else (
            'VERIFIED' if set(checks.values()) == {'VERIFIED'} else 'UNKNOWN')
    # Git is an observed checkout, never proof of the code loaded before deploy.
    return dict(schema_version=1, git_revision=revision, source_hashes=sources,
                policy_hash=digest({name: digest(value) for name, value in policy.items()}),
                execution_hash=digest({name: digest(value) for name, value in execution.items()}),
                hash_basis='OBSERVED_DISK_AST_PER_FILE_SHA256', loaded_code_status=status(parity),
                judgment_loaded_code_status=status(judgment_parity),
                execution_loaded_code_status=status(execution_parity),
                judgment_loaded_code_checks=judgment_parity,
                execution_loaded_code_checks=execution_parity,
                loaded_code_checks=parity, git_revision_status='OBSERVED_ONLY',
                verification_scope='SELECTED_FUNCTION_BYTECODE_AND_LITERAL_CONFIG',
                python=sys.version.split()[0],
                libraries={name: importlib.metadata.version(name) for name in ('pandas','numpy')})


def record(kind, body, *, scenario_id=None, intent_id=None, decision_slot=None):
    try:
        capture = _current.get()
        if capture is None:
            return
        value = dict(body, schema_version=1)
        if kind == 'settlement_recorded' and 'detected_at' not in value:
            value['detected_at'] = time.time()
        text = encoded(value)
        size = len(text.encode())
        if len(capture['events']) >= MAX_EVENTS or capture['bytes'] + size > MAX_RUN_BYTES:
            raise ValueError('audit_bound')
        observed_at = value.get('detected_at', time.time()) if kind == 'settlement_recorded' else time.time()
        capture['events'].append((str(uuid.uuid4()), capture['run_id'], kind, observed_at,
                                  scenario_id, intent_id, decision_slot, capture['manifest_id'], text))
        capture['bytes'] += size
    except Exception:
        _gap()


def record_hashed(kind, body, **keys):
    """Hash optional observations inside the non-throwing boundary."""
    try:
        hashes = {name + '_hash': digest(value) for name, value in body.items()}
        record(kind, dict(body, **hashes), **keys)
    except Exception:
        _gap()


def begin_review_capture(active, presented, proposal, validated, *, input_id, slot, applied_at):
    """Optional immediate pre-apply copy; no reads/writes of trading state.

    Hashes use this module's ensure_ascii=True canonical JSON, not the offline
    proof packet's ensure_ascii=False hashes or original wire-byte hashes.
    """
    try:
        if _current.get() is None or not active:
            return None
        before = active.get('review_memory', [])
        review = proposal.get('review')
        from live.scenario_review_memory import _saved_rows_valid, valid_review
        if not _saved_rows_valid(before) or not _saved_rows_valid(presented) or not valid_review(review):
            raise ValueError('invalid_review_boundary')
        value = dict(input_id=input_id, action_id=validated['action_id'],
            scenario_id=validated['scenario_id'], action=validated['action'],
            decision_slot=slot, applied_at=applied_at, before=before, presented=presented,
            original_review=review, original_proposal_hash=digest(proposal),
            validated_payload_hash=digest(validated),
            requires_intent=validated['action'] != 'WAIT' or bool(validated.get('cancel_entry_ids')),
            core_validated=True, core_validation_scope='VALIDATOR_RETURNED_BEFORE_CAPTURE_NOT_REEXECUTED',
            hash_basis='CANONICAL_JSON_SORTED_ASCII_SHA256',
            commit_status='OBSERVATIONAL_UNCOMMITTED')
        text = encoded(value)
        if len(text.encode()) > MAX_REVIEW_BYTES:
            raise ValueError('review_capture_bound')
        return json.loads(text)
    except Exception:
        record('review_transition_gap', dict(reason='INVALID_OR_OVERSIZE_REVIEW_BOUNDARY'), decision_slot=slot)
        _gap()
        return None


def finish_review_capture(boundary, active, receipt):
    """Buffer immediate post-apply facts; flush separately checks commitment."""
    try:
        if boundary is None:
            return
        from live.scenario_review_memory import _saved_rows_valid, _receipt_valid
        after = active.get('review_memory', [])
        if not _saved_rows_valid(after) or not _receipt_valid(receipt):
            raise ValueError('invalid_review_after')
        body = dict(boundary, after=after, receipt=receipt)
        text = encoded(body)
        if len(text.encode()) > MAX_REVIEW_BYTES:
            raise ValueError('review_capture_bound')
        record_hashed('review_transition', body, scenario_id=boundary['scenario_id'],
                      intent_id=boundary['action_id'], decision_slot=boundary['decision_slot'])
    except Exception:
        record('review_transition_gap', dict(reason='INVALID_OR_OVERSIZE_REVIEW_AFTER'))
        _gap()


def _review_commit_matches(conn, body):
    """Committed decision/receipt/intent only, never exchange success evidence."""
    if body.get('core_validated') is not True:
        return False
    row = conn.execute('SELECT context,proposal,outcome FROM llm_scenario_decisions WHERE slot=?',
                       (body['decision_slot'],)).fetchone()
    if not row or any(item is None for item in row):
        return False
    context, proposal, outcome = (json.loads(item) for item in row)
    if (context.get('input_id') != body['input_id']
            or digest(proposal) != body['original_proposal_hash']
            or any(proposal.get(key) != body[key] for key in ('input_id', 'action_id', 'scenario_id', 'action'))
            or digest(outcome.get('review_update_receipt')) != digest(body['receipt'])
            or outcome.get('status') != ('intent_pending' if body['requires_intent'] else 'wait')):
        return False
    if body['requires_intent']:
        intent = conn.execute('SELECT scenario_id,payload FROM llm_scenario_intents WHERE id=?',
                              (body['action_id'],)).fetchone()
        if not intent or intent[0] != body['scenario_id'] or digest(json.loads(intent[1])) != body['validated_payload_hash']:
            return False
    return True


def bind(scenario_id, intent_id=None):
    """Only caller-known exact IDs; no database lookup or temporal inference."""
    try:
        capture = _current.get()
        if capture is not None:
            capture['scenario_id'], capture['intent_id'] = scenario_id, intent_id
    except Exception:
        _gap()


def observed_time():
    try:
        return time.time()
    except Exception:
        _gap()
        return None


def exchange_call(method, request, started_at, *, response=None):
    """Exact request IDs remain available even without a scenario association."""
    try:
        capture = _current.get() or {}
        record_hashed('exchange_call', dict(method=method, request=request,
            started_at=started_at, ended_at=time.time(),
            status='ACK_ONLY' if isinstance(response, dict) and response.get('retCode') == 0 else 'UNKNOWN',
            ret_code=response.get('retCode') if isinstance(response, dict) else None,
            order_id=response.get('result', {}).get('orderId') if isinstance(response, dict)
            and isinstance(response.get('result'), dict) else None),
            scenario_id=capture.get('scenario_id'), intent_id=capture.get('intent_id'))
    except Exception:
        _gap()


def _flush(capture):
    written = []
    with sqlite3.connect(capture['path'], timeout=0) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_audit_manifests (manifest_id TEXT PRIMARY KEY, body TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_audit_events (event_id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, observed_at REAL, scenario_id TEXT, intent_id TEXT, decision_slot INTEGER, manifest_id TEXT, body TEXT)')
        conn.execute('INSERT OR IGNORE INTO llm_scenario_audit_manifests VALUES (?,?)',
                     (capture['manifest_id'], capture['manifest']))
        for event in capture['events']:
            body = json.loads(event[8])
            if event[2] == 'review_transition':
                # A failed state save/outcome commit must not turn an in-memory
                # observation into committed proof. Isolate errors per event so
                # optional review capture cannot discard settlement audit rows.
                try:
                    matched = _review_commit_matches(conn, body)
                except Exception:
                    matched = False
                if matched:
                    body['commit_status'] = 'COMMITTED_DECISION_RECEIPT_MATCH'
                    body['commit_status_hash'] = digest(body['commit_status'])
                    body['commit_scope'] = 'NOT_EXCHANGE_EXECUTION_OR_PROFIT_PROOF'
                    event = (*event[:8], encoded(body))
                else:
                    _gap()
                    body = dict(schema_version=1, reason='COMMITTED_DECISION_RECEIPT_NOT_MATCHED',
                                observed_body_hash=hashlib.sha256(event[8].encode()).hexdigest())
                    event = (*event[:2], 'review_transition_gap', *event[3:8], encoded(body))
            if event[2] == 'settlement_recorded':
                row = conn.execute('SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?',
                                   (event[4],)).fetchone()
                if not row or json.loads(row[0]) != body['settlement']:
                    _gap()
                    continue
            conn.execute('INSERT OR IGNORE INTO llm_scenario_audit_events VALUES (?,?,?,?,?,?,?,?,?)', event)
            written.append(event)
    # Committed rows only; the ClickStack copy is a bounded, ID-free summary.
    try:
        from live.scenario_ledger import forward_audits
        forward_audits(written)
    except Exception:
        logging.getLogger(__name__).debug('scenario ledger forward skipped')


@contextmanager
def run_capture(conn):
    capture, token = None, None
    try:
        path = next(row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main')
        if path:
            capture = dict(path=path, manifest_id=None,
                           run_id=str(uuid.uuid4()), events=[], bytes=0)
            token = _current.set(capture)
    except Exception:
        _gap()
    try:
        yield
    finally:
        if token is not None:
            _current.reset(token)
        if capture is not None and capture['events']:
            try:
                manifest = encoded(_manifest())
                capture['manifest'] = manifest
                capture['manifest_id'] = hashlib.sha256(manifest.encode()).hexdigest()
                capture['events'] = [(*event[:7], capture['manifest_id'], event[8])
                                     for event in capture['events']]
                _flush(capture)
            except Exception:
                _gap()
