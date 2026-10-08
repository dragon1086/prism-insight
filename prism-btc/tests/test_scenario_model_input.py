"""Lossless input factoring on full-shaped synthetic market facts; no model/network."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from live.scenario_llm import ScenarioModelError, _current_primary_frame_facts, propose
from live.scenario_model_input import project_model_input, restore_model_input
from live.scenario_preview import response_contract
from live.scenario_recovery import numeric_market
from tests.test_scenario_llm import context, wire
from tests.test_scenario_snapshot import snapshot


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def payload():
    market = snapshot()
    ctx = context()
    ctx.update(recovery={'current': numeric_market(market), 'baseline': {'preserved': 7},
                         'changed_evidence': [{'path': 'timeframes.30m.forming.ma10', 'before': 99, 'after': 100}],
                         'risk_fraction': .005},
               ma_structure=build_ma_structure_context(market, ctx),
               current_primary_frame_facts=_current_primary_frame_facts(market))
    return {'market_snapshot': market, 'contract_context': ctx, 'response_contract': response_contract(ctx)}


def test_complete_shape_round_trip_and_untouched_original_all_risk_evidence():
    source = payload()
    before = deepcopy(source)
    projected, audit = project_model_input(source)
    assert source == before
    assert restore_model_input(projected) == source
    assert audit['original_utf8_bytes'] == len(encoded(source))
    assert audit['projected_utf8_bytes'] == len(encoded(projected))
    assert audit['projected_sha256'] == hashlib.sha256(encoded(projected)).hexdigest()
    restored_canonical = json.dumps(restore_model_input(projected), ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':'), sort_keys=True)
    assert audit['original_semantic_sha256'] == hashlib.sha256(restored_canonical.encode()).hexdigest()
    assert audit['projected_utf8_bytes'] < audit['original_utf8_bytes'] - 5000
    assert projected['contract_context']['recovery']['baseline'] == source['contract_context']['recovery']['baseline']
    assert projected['contract_context']['recovery']['changed_evidence'] == source['contract_context']['recovery']['changed_evidence']
    assert all(path.startswith(('contract_context.recovery.current', 'market_snapshot.timeframes.')) for path in audit['factored_paths'])


@pytest.mark.parametrize('fault', ['changed', 'missing', 'extra', 'bool', 'int', 'list'])
def test_nonidentical_current_maps_are_never_discarded(fault):
    source = payload()
    current = source['contract_context']['recovery']['current']
    key = next(iter(current))
    if fault == 'changed':
        current[key] += 1
    elif fault == 'missing':
        del current[key]
    elif fault == 'extra':
        current['unknown'] = 3
    elif fault == 'bool':
        current[key] = True
    elif fault == 'int':
        current[key] = int(current[key])
    else:
        source['contract_context']['recovery']['current'] = [1, 2]
    projected, audit = project_model_input(source)
    assert projected['contract_context']['recovery']['current'] == source['contract_context']['recovery']['current']
    assert 'contract_context.recovery.current' not in audit['factored_paths']
    assert restore_model_input(projected) == source


def test_different_feature_definitions_preserved_without_false_shared_meaning():
    source = payload()
    source['market_snapshot']['timeframes']['30m']['confirmed']['compression_definition'] = 'different meaning'
    projected, audit = project_model_input(source)
    assert not any(p.endswith('compression_definition') for p in audit['factored_paths'])
    assert restore_model_input(projected) == source


def test_current_key_order_not_wire_bytes_is_reconstructed_losslessly():
    source = payload()
    current = source['contract_context']['recovery']['current']
    source['contract_context']['recovery']['current'] = dict(reversed(list(current.items())))
    projected, audit = project_model_input(source)
    restored = restore_model_input(projected)
    assert encoded(restored) != encoded(source)
    canonical = json.dumps(restored, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True)
    assert hashlib.sha256(canonical.encode()).hexdigest() == audit['original_semantic_sha256']
    assert restored == source


def test_utf8_byte_count_and_essential_oversize_still_blocks_before_model(monkeypatch):
    from live import scenario_recovery
    requests, raw = [], []
    monkeypatch.setattr(scenario_recovery, 'record_model_request', lambda **kw: requests.append(kw))
    monkeypatch.setattr(scenario_recovery, 'record_model_wire', lambda *a, **kw: raw.append(kw))
    source = payload()
    source['contract_context']['essential'] = '관측근거' * 10000
    projected, audit = project_model_input(source)
    assert audit['projected_utf8_bytes'] == len(encoded(projected))
    assert audit['projected_utf8_bytes'] > len(encoded(projected).decode())
    ctx = context()
    ctx['essential'] = '가' * 34000
    def forbidden(**kwargs):
        pytest.fail('oversized essential facts must not call a model')
    with pytest.raises(ScenarioModelError, match='input_size'):
        propose({'valid': True, 'as_of_ms': 1000000}, ctx, response_contract(ctx),
                generate=forbidden, clock=lambda: 1000)
    assert requests == raw == []


def test_actual_assembled_payload_retains_projection_and_original_objects(monkeypatch):
    from live import scenario_recovery
    journal = []
    monkeypatch.setattr(scenario_recovery, 'record_model_request', lambda **kw: journal.append(kw))
    source = payload()
    market, ctx = source['market_snapshot'], source['contract_context']
    now = market['as_of_ms']/1000
    ctx.update(now=now, input_captured_at=now)
    p = wire(ctx)
    p['expires_at'] = now+300
    original, calls = deepcopy((market, ctx)), []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(p))
    propose(market, ctx, response_contract(ctx), generate=generate, clock=lambda: now)
    sent = json.loads(calls[0]['user_prompt'])
    assert journal[0]['user_prompt'] == calls[0]['user_prompt']
    assert journal[0]['input_projection']['projected_sha256'] == hashlib.sha256(calls[0]['user_prompt'].encode()).hexdigest()
    assert 'model_input_projection' in sent
    assert restore_model_input(sent)['contract_context']['recovery'] == ctx['recovery']
    assert (market, ctx) == original
