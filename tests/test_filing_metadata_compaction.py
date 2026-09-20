import json

import pytest

from prism_core.report_insight_prefetch import expand_filing_record, packet


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
