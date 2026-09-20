"""Explicit offline budgets never raise default runtime admission implicitly."""
import json

import pytest

from cores.agents.report_agent import ReportAgent
from prism_core.report_insight_manifest import build_insight_manifest
from prism_core.report_insight_prefetch import packet
from prism_core.report_research_context import apply_section_research


def note(size, character='가'):
    shell = json.dumps({'sources': [{'source_id': 'S1', 'topic': 'catalysts_risks_counterevidence',
                                    'excerpt': ''}]}, ensure_ascii=False, separators=(',', ':'))
    remaining = size - len(shell.encode())
    fill = character * (remaining // len(character.encode())) + 'x' * (remaining % len(character.encode()))
    return shell.replace('"excerpt":""', '"excerpt":' + json.dumps(fill, ensure_ascii=False))


def evidence(text):
    return {'evidence_id': 'frozen', 'news_usable': False, 'receipt': {'usable_sources': 1},
            'section_notes': {'news_analysis': text}}


def inject(value, **kwargs):
    agent = ReportAgent('news', 'Keep original discovery.', ['perplexity'])
    out = apply_section_research(agent, 'news_analysis', {'report_research': value}, '20260920', 'ko', **kwargs)
    return agent, out


def refs(value, **kwargs):
    result = build_insight_manifest('KR', 'TEST', '20260920', {'report_research': value}, {}, **kwargs)
    return result['categories']['catalysts_risks_counterevidence']['input_refs']


@pytest.mark.parametrize('character', ['x', '가', '😀'])
@pytest.mark.parametrize('size', [5999, 6000, 6001])
def test_default_utf8_boundary_is_identical_for_injector_and_manifest(character, size):
    text = note(size, character)
    assert len(text.encode()) == size
    value = evidence(text)
    original, out = inject(value)
    assert ('"source_material":' in out.instruction) is (size <= 6000)
    assert bool(refs(value)) is (size <= 6000)
    assert out.server_names == original.server_names
    if size > 6000:
        assert 'UNKNOWN' in out.instruction and 'oversized_source_material_omitted' in out.instruction
        assert value['section_notes']['news_analysis'] not in out.instruction


def test_invalid_utf8_is_omitted_without_throwing_or_injecting():
    value = evidence('{"source":"\ud800"}')
    original, out = inject(value)
    assert 'source_material":' not in out.instruction
    assert 'invalid_source_material_omitted' in out.instruction
    assert out.server_names == original.server_names
    assert not refs(value)


@pytest.mark.parametrize('budget', [6000, 12000, 24000, 32000])
@pytest.mark.parametrize('extra', [0, 1])
def test_explicit_budget_boundary_is_byte_exact_and_roundtrips(budget, extra):
    text = note(budget + extra)
    value = evidence(text)
    _, out = inject(value, source_budget_bytes=budget)
    assert ('"source_material":' in out.instruction) is (extra == 0)
    assert bool(refs(value, source_budget_bytes=budget)) is (extra == 0)
    if extra == 0:
        restored = json.loads(out.instruction[out.instruction.index('{"evidence_id"'):])
        assert restored['source_material'] == text


@pytest.mark.parametrize('budget', [True, False, 6000.0, '12000', 0, -1, 6001, 64000, None])
def test_unapproved_budget_is_rejected_by_all_three_boundaries(budget):
    with pytest.raises(ValueError):
        packet('KR', 'TEST', '2026-09-20', {'sources': [], 'gaps': [], 'calls': 0}, source_budget_bytes=budget)
    with pytest.raises(ValueError):
        inject(evidence(note(200)), source_budget_bytes=budget)
    with pytest.raises(ValueError):
        refs(evidence(note(200)), source_budget_bytes=budget)


def test_packet_fields_and_environment_cannot_raise_default_budget(monkeypatch):
    monkeypatch.setenv('PRISM_SOURCE_BUDGET_BYTES', '32000')
    value = evidence(note(9000))
    value['source_budget_bytes'] = value['receipt']['source_budget_bytes'] = 32000
    _, out = inject(value)
    assert 'oversized_source_material_omitted' in out.instruction
    assert not refs(value)


def test_explicit_builder_does_not_change_default_or_mutate_source():
    source = {'source_id': 'S1', 'blocks': [{'topic': 'catalysts_risks_counterevidence',
              'excerpt': '가' * 2600, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}]}
    state = {'sources': [source], 'gaps': [], 'calls': 0, 'source_budget_bytes': 32000}
    before = json.dumps(state, sort_keys=True)
    expanded = packet('KR', 'TEST', '2026-09-20', state, source_budget_bytes=12000)
    default = packet('KR', 'TEST', '2026-09-20', state)
    assert json.loads(expanded['section_notes']['news_analysis'])['sources']
    assert not json.loads(default['section_notes']['news_analysis'])['sources']
    assert json.dumps(state, sort_keys=True) == before


def test_actual_kr_factory_stays_at_default_even_for_self_declared_large_packet():
    from cores.agents import get_agent_directory

    value = evidence(note(24000))
    value['source_budget_bytes'] = 24000
    arguments = ('Example', '005930', '20260920', ['news_analysis'], 'ko')
    original = get_agent_directory(*arguments)['news_analysis']
    actual = get_agent_directory(*arguments, prefetched_data={'report_research': value})['news_analysis']
    assert 'oversized_source_material_omitted' in actual.instruction
    assert '"source_material":' not in actual.instruction
    assert actual.server_names == original.server_names


def test_experimental_manifest_keeps_independent_eight_record_cap():
    text = json.dumps({'sources': [{'source_id': f'S{i}', 'topic': 'catalysts_risks_counterevidence',
                                    'excerpt': f'조건과 출처 {i}'} for i in range(9)]}, ensure_ascii=False)
    actual = refs(evidence(text), source_budget_bytes=32000)
    assert len(actual) == 8
    assert all(row['source_id'] != 'S8' for row in actual)


@pytest.mark.asyncio
async def test_explicit_experiment_agent_reaches_recording_backend_without_source_changes(monkeypatch):
    from cores import report_generation as generation
    from cores.llm.ports import LLMResult

    captured = []

    class Recorder:
        async def run(self, spec, message):
            captured.append((spec, message))
            return LLMResult(text='Fixture response; no model execution.')

    monkeypatch.setattr(generation, '_report_backend', Recorder())
    text = note(24000)
    _, agent = inject(evidence(text), source_budget_bytes=24000)
    await generation._generate_agent_text(agent, 'Offline boundary test', max_tokens=10, max_iterations=1)
    assert len(captured) == 1
    spec, _ = captured[0]
    assert spec.instructions == agent.instruction
    envelope = json.loads(spec.instructions[spec.instructions.index('{"evidence_id"'):])
    assert envelope['source_material'] == text


def test_long_invalid_unicode_is_rejected_by_size_before_encoding():
    from prism_core.report_source_budget import source_note_rejection

    assert source_note_rejection('\ud800' * 6001) == 'SOURCE_NOTE_BYTE_LIMIT'
    assert source_note_rejection('\ud800') == 'SOURCE_NOTE_INVALID_UTF8'


def test_manifest_skips_decoded_invalid_unicode_without_losing_valid_sibling():
    text = json.dumps({'sources': [
        {'source_id': 'BAD', 'topic': 'catalysts_risks_counterevidence', 'excerpt': '\ud800'},
        {'source_id': 'GOOD', 'topic': 'catalysts_risks_counterevidence', 'excerpt': '유효한 원문'},
    ]})
    assert text.isascii()
    assert [r['source_id'] for r in refs(evidence(text))] == ['GOOD']
