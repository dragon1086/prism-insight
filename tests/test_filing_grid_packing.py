"""Compression must not evict whole evidence retained by the baseline packer."""
import copy
import json
from datetime import datetime, timezone

import pytest

from prism_core import report_insight_prefetch as packing

TOPIC = 'catalysts_risks_counterevidence'


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    class Clock:
        @staticmethod
        def now(_zone):
            return datetime(2026, 9, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(packing, 'datetime', Clock)


def block(label, size, compact=None):
    value = {'topic': TOPIC, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
             'excerpt': label + 'x' * size, 'provenance': {'parser_version': 'structured_v1', 'locator': label}}
    if compact is not None:
        value['_packing_original'] = {'excerpt': value['excerpt'], 'excerpt_encoding': None}
        value['excerpt'] = label + 'x' * compact
        value['provenance']['excerpt_encoding'] = 'html_cell_grid_v1'
    return value


def state(blocks):
    return {'sources': [{'source_id': 'D1', 'filing': {'period': '2026'}, 'blocks': blocks}],
            'gaps': [], 'calls': 0}


def payload(value):
    return json.loads(value['section_notes']['news_analysis'])


def retained(value):
    return [row['provenance']['locator'] for row in payload(value)['sources']]


def build(progress, budget=6000):
    return packing.packet('KR', 'TEST', '2026-09-21', progress, source_budget_bytes=budget)


@pytest.mark.parametrize('budget', [6000, 12000, 24000, 32000])
def test_compression_preserves_baseline_in_greedy_admission_paradox(budget):
    # Baseline A+C fit; B does not. A shrinking makes B fit but crowds out C.
    scale = budget - 6000
    original = state([block('A', 2000 + scale), block('B', 3300), block('C', 1200)])
    compressed = state([block('A', 2000 + scale, 1000 + scale), block('B', 3300), block('C', 1200)])
    before = copy.deepcopy(compressed)
    baseline = build(original, budget)
    assert retained(baseline) == ['A', 'C']
    actual = build(compressed, budget)
    assert set(retained(baseline)) <= set(retained(actual))
    assert len(actual['section_notes']['news_analysis'].encode()) <= budget
    assert compressed == before
    assert actual == build(compressed, budget)
    assert '_packing_original' not in json.dumps(actual)


def test_candidate_frontier_does_not_expand_for_compression():
    blocks = [block(str(i), 100, 1) for i in range(25)]
    value = build(state(blocks), 32000)
    assert len(retained(value)) == 24
    assert '24' not in retained(value)


def test_original_representation_marker_restores_without_mutation():
    original = block('A', 2000)
    original['provenance']['excerpt_encoding'] = 'html_cell_tuples_v1'
    compressed = block('A', 2000, 1000)
    compressed['_packing_original']['excerpt_encoding'] = 'html_cell_tuples_v1'
    baseline = build(state([original, block('B', 3300), block('C', 1200)]))
    actual = build(state([compressed, block('B', 3300), block('C', 1200)]))
    assert set(retained(baseline)) <= set(retained(actual))


def test_equal_text_different_provenance_remains_distinct():
    blocks = [block('A', 1000, 100), block('A', 1000, 100)]
    blocks[1]['provenance']['locator'] = 'different-period'
    assert retained(build(state(blocks))) == ['A', 'different-period']


def test_conflicting_filing_still_rejected():
    progress = state([block('A', 1000, 100)])
    progress['sources'].append({'source_id': 'D1', 'filing': {'period': '2025'},
                                'blocks': [block('B', 1000, 100)]})
    value = build(progress)
    assert retained(value) == ['A']
    assert 'SOURCE_FILING_CONFLICT' in payload(value)['gaps']


def test_no_grid_path_keeps_existing_result():
    progress = state([block('A', 1000)])
    assert build(progress) == packing._packet('KR', 'TEST', '2026-09-21', progress)


def test_failed_pin_returns_entire_baseline_not_a_partial_union():
    original = state([block('A', 2000), block('B', 3300), block('C', 1200)])
    compressed = state([block('A', 2000, 10000), block('B', 3300), block('C', 1200)])
    assert build(compressed) == build(original)


def test_conflicting_source_provenance_keeps_original_rejection():
    progress = state([block('A', 1000, 100)])
    progress['sources'].append({'source_id': 'D1', 'filing': {'period': '2026'},
                                'blocks': [block('B', 1000, 100)]})
    for index, source in enumerate(progress['sources']):
        source['blocks'][0]['provenance'].update(parser_version='material_v2',
            representation='DART_VIEWER_HTML', representation_sha256=str(index) * 64)
    value = build(progress)
    assert retained(value) == ['A']
    assert 'SOURCE_PROVENANCE_CONFLICT' in payload(value)['gaps']


def test_packing_identity_is_source_bound_and_metadata_type_exact():
    a, b = block('same', 100), block('same', 100, 10)
    assert packing._packing_identity({'source_id': 'D1'}, a) == packing._packing_identity({'source_id': 'D1'}, b)
    assert packing._packing_identity({'source_id': 'D1'}, a) != packing._packing_identity({'source_id': 'D2'}, b)
    assert packing._packing_identity({'source_id': 'D1'}, a) != packing._packing_identity({'source_id': 'D1', 'filing': None}, a)
    assert packing._packing_identity({'source_id': 'D1', 'filing': False}, a) != packing._packing_identity({'source_id': 'D1', 'filing': 0}, a)
    b['provenance']['locator'] = 'other'
    assert packing._packing_identity({'source_id': 'D1'}, a) != packing._packing_identity({'source_id': 'D1'}, b)


@pytest.mark.parametrize('key', ['topic', 'status', 'url', 'published', 'publication_basis'])
def test_packing_identity_preserves_full_final_semantics(key):
    source = {'source_id': 'D1'}
    candidate = block('same', 100)
    original = packing._packing_identity(source, candidate)
    (candidate if key in {'topic', 'status'} else source)[key] = 'different'
    assert packing._packing_identity(source, candidate) != original


@pytest.mark.parametrize('compressed', [False, True])
def test_final_metadata_pruning_keeps_capture_and_reference_cleanup_separate(compressed, monkeypatch):
    original_size = packing._size
    def metadata_heavy_size(value):
        return original_size(value) + (1600 if 'topic_gaps' in value else 0)
    monkeypatch.setattr(packing, '_size', metadata_heavy_size)
    originals = state([block(str(i), 600) for i in range(8)])
    baseline = build(originals)
    assert len(retained(baseline)) < 8
    actual = build(state([block(str(i), 600, 400) for i in range(8)])) if compressed else baseline
    assert set(retained(baseline)) <= set(retained(actual))
    assert payload(actual)['source_filings'] == {'D1': {'period': '2026'}}


@pytest.mark.parametrize('budget', [6000, 12000, 24000, 32000])
def test_baseline_preserved_across_many_candidate_sizes(budget):
    for seed in range(12):
        originals, compressed = [], []
        for i in range(26):
            size = ((i * 137 + seed * 311) % 3500) + 30
            originals.append(block(str(i), size))
            compressed.append(block(str(i), size, size // 3))
        baseline = build(state(originals), budget)
        actual = build(state(compressed), budget)
        assert set(retained(baseline)) <= set(retained(actual))
        assert set(retained(actual)) <= {str(i) for i in range(24)}
        assert len(actual['section_notes']['news_analysis'].encode()) <= budget
