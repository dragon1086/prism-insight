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
import subprocess
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
_current = ContextVar('scenario_audit', default=None)


def _gap():
    try:
        logging.getLogger(__name__).warning('AUDIT_GAP')
    except Exception:
        pass


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
                    'live/scenario_preview.py', 'engine/scenario_snapshot.py',
                    'engine/indicators.py']
    execution_files = ['live/scenario_execution.py', 'live/scenario_accounting.py',
                       'core/scenario_limit_prices.py', 'core/llm_scenario.py']
    sources, policy, execution, parity = {}, {}, {}, {}
    for name in policy_files + execution_files + ['live/scenario_runtime.py', 'live/scenario_broker.py']:
        path = root / name
        source = path.read_text()
        sources[name] = hashlib.sha256(source.encode()).hexdigest()
        tree = ast.parse(source)
        selected_names = None
        if name in policy_files:
            policy[name] = ast.dump(tree, include_attributes=False)
        elif name in execution_files:
            execution[name] = ast.dump(tree, include_attributes=False)
        else:
            context_name = '_context' if 'runtime' in name else 'context'
            execution_names = ({'_tick', '_reconcile', '_enabled'} if 'runtime' in name else
                {'_accounting', '_funding_schedule', 'capture_financial_evidence',
                 '_risk_accounting', '_daily', 'reconcile', 'capture_account'})
            selected_names = {context_name} | execution_names
            selected = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                        and node.name == context_name]
            policy[name] = [ast.dump(node, include_attributes=False) for node in selected]
            execution[name] = [ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                               if isinstance(node, ast.FunctionDef) and node.name in execution_names]
        parity[name] = _loaded_parity(name, source, selected_names)
    try:
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                                  text=True, timeout=1, check=True).stdout.strip()
    except Exception:
        revision = None
    status = 'MIXED' if 'MIXED' in parity.values() else (
        'VERIFIED' if set(parity.values()) == {'VERIFIED'} else 'UNKNOWN')
    # Git is an observed checkout, never proof of the code loaded before deploy.
    return dict(schema_version=1, git_revision=revision, source_hashes=sources,
                policy_hash=digest(policy), execution_hash=digest(execution),
                hash_basis='OBSERVED_DISK_AST', loaded_code_status=status,
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
    with sqlite3.connect(capture['path'], timeout=0) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_audit_manifests (manifest_id TEXT PRIMARY KEY, body TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS llm_scenario_audit_events (event_id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, observed_at REAL, scenario_id TEXT, intent_id TEXT, decision_slot INTEGER, manifest_id TEXT, body TEXT)')
        conn.execute('INSERT OR IGNORE INTO llm_scenario_audit_manifests VALUES (?,?)',
                     (capture['manifest_id'], capture['manifest']))
        for event in capture['events']:
            body = json.loads(event[8])
            if event[2] == 'settlement_recorded':
                row = conn.execute('SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?',
                                   (event[4],)).fetchone()
                if not row or json.loads(row[0]) != body['settlement']:
                    _gap()
                    continue
            conn.execute('INSERT OR IGNORE INTO llm_scenario_audit_events VALUES (?,?,?,?,?,?,?,?,?)', event)


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
