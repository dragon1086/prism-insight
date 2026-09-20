import json

import pytest

from prism_core.report_insight_prefetch import expand_filing_record, packet


@pytest.mark.parametrize('field,value', [
    ('context_before', '전반기 (단위: 천원)'),
    ('footnotes', '조건이 충족되지 않으면 반환해야 합니다.'),
    ('source_path', '/html/body/table[2]'),
])
def test_identical_cells_with_distinct_source_context_are_not_deduplicated(field, value):
    first = {'topic': 'catalysts_risks_counterevidence', 'excerpt': '{"cells":["100"]}',
             'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
             'provenance': {'context_before': '당반기 (단위: 백만원)', 'footnotes': '반환 의무가 없습니다.',
                            'source_path': '/html/body/table[1]'}}
    second = {**first, 'provenance': {**first['provenance'], field: value}}
    state = {'sources': [{'source_id': 'same', 'filing': {'role': 'primary'}, 'blocks': [first, second]}],
             'gaps': [], 'calls': 0}
    result = json.loads(packet('KR', 'TEST', '2026-09-20', state)['section_notes']['news_analysis'])
    assert len(result['sources']) == 2
    assert result['sources'][0]['provenance'][field] != result['sources'][1]['provenance'][field]


def test_exact_context_duplicates_ignore_dictionary_key_insertion_order():
    first = {'topic': 'catalysts_risks_counterevidence', 'excerpt': '원문과 모든 조건이 같습니다.',
             'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
             'provenance': {'context_before': '당반기', 'footnotes': '확정되지 않았습니다.', 'scope': 'consolidated'}}
    second = {**first, 'provenance': dict(reversed(list(first['provenance'].items())))}
    state = {'sources': [{'source_id': 'same', 'filing': {'role': 'primary'}, 'blocks': [first, second]}],
             'gaps': [], 'calls': 0}
    result = json.loads(packet('KR', 'TEST', '2026-09-20', state)['section_notes']['news_analysis'])
    assert len(result['sources']) == 1


@pytest.mark.parametrize('second_metadata', [{'period': '2025'}, {'period': '2026', 'verified': 0}])
def test_same_excerpt_cannot_hide_conflicting_filing_metadata(second_metadata):
    block = {'topic': 'catalysts_risks_counterevidence', 'excerpt': '동일한 원문입니다.',
             'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}
    state = {'sources': [
        {'source_id': 'same', 'filing': {'period': '2026', 'verified': False}, 'blocks': [block]},
        {'source_id': 'same', 'filing': second_metadata, 'blocks': [block]},
    ], 'gaps': [], 'calls': 0}
    result = json.loads(packet('KR', 'TEST', '2026-09-20', state)['section_notes']['news_analysis'])
    assert 'SOURCE_FILING_CONFLICT' in result['gaps']
    assert result['source_filings']['same'] == {'period': '2026', 'verified': False}
    assert len(result['sources']) == 1


def test_identical_excerpt_in_different_sources_retains_both_source_identities():
    block = {'topic': 'catalysts_risks_counterevidence', 'excerpt': '동일하게 기재된 별개 공시입니다.',
             'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}
    state = {'sources': [{'source_id': sid, 'filing': {'period': '2026'}, 'blocks': [block]}
                         for sid in ('primary', 'supplement')], 'gaps': [], 'calls': 0}
    result = json.loads(packet('KR', 'TEST', '2026-09-20', state)['section_notes']['news_analysis'])
    assert {r['source_id'] for r in result['sources']} == {'primary', 'supplement'}

def source(key, role, year):
    filing = {'receipt_id': key, 'role': role, 'entity_id': 'DART:12345678',
              'period_start': f'{year}-01-01', 'period_end': f'{year}-06-30', 'scope': 'consolidated',
              'observed_at': '2026-09-20T01:28:11+00:00', 'decision_at': '2026-09-20T01:28:11+00:00',
              'event_date': 'UNKNOWN', 'kind': 'interim', 'latest_confirmed': False,
              'section': 'financial_notes'}
    return {'source_id': key, 'url': 'https://example.org/' + key,
            'filing': filing, 'blocks': [
                {'topic': 'catalysts_risks_counterevidence', 'excerpt': f'약정 {n}: 위반은 없으나 조건 변경 시 상환 의무가 발생할 수 있습니다.',
                 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'} for n in range(12)]}


def test_one_source_metadata_copy_exactly_restores_each_period_and_condition():
    sources = [source('primary', 'primary', 2026), source('annual', 'annual_supplement', 2025)]
    state = {'sources': sources, 'calls': 0, 'gaps': [], 'filing_selection': {'version': 'test'}}
    output = packet('KR', '123456', '2026-09-20', state)
    data = json.loads(output['section_notes']['news_analysis'])
    assert data['sources']
    assert all('filing' not in r and r['filing_ref'] == r['source_id'] for r in data['sources'])
    for row in data['sources']:
        expanded = expand_filing_record(row, data)
        original = next(s for s in sources if s['source_id'] == row['source_id'])
        assert expanded['filing'] == original['filing']
        assert any(expanded['excerpt'] == b['excerpt'] for b in original['blocks'])
    repeated = {k: v for k, v in data.items() if k != 'source_filings'}
    repeated['sources'] = [expand_filing_record(r, data) for r in data['sources']]
    size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
    assert size(data) < size(repeated)
    assert all(len(v.encode()) <= 6000 for v in output['section_notes'].values())
    assert len(data['source_filings']) <= len(data['sources'])


def test_conflicting_metadata_for_same_source_id_is_not_joined():
    primary, wrong = source('same', 'primary', 2026), source('same', 'annual_supplement', 2025)
    wrong['blocks'] = [{**wrong['blocks'][0], 'excerpt': '다른 회계기간에만 적용되는 조건입니다.'}]
    data = json.loads(packet('KR', '123456', '2026-09-20', {
        'sources': [primary, wrong], 'calls': 0, 'gaps': []})['section_notes']['news_analysis'])
    assert 'SOURCE_FILING_CONFLICT' in data['gaps']
    assert all(r['excerpt'] != wrong['blocks'][0]['excerpt'] for r in data['sources'])


@pytest.mark.parametrize('record,payload', [
    ({'source_id': 'a', 'filing_ref': 'b'}, {'source_filings': {'b': {}}}),
    ({'source_id': 'a', 'filing_ref': 'a'}, {'source_filings': {}}),
    ({'source_id': 'a', 'filing_ref': 'a', 'filing': {}}, {'source_filings': {'a': {}}}),
])
def test_invalid_reference_is_not_silently_resolved(record, payload):
    with pytest.raises(ValueError, match='INVALID_FILING_REFERENCE'):
        expand_filing_record(record, payload)
