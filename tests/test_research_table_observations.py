import pytest

from prism_core.research_table_observations import extract_table_observations


def source(caption='Global optical components segment Market Share by Revenue (%, actual)',
           headers='| Company | Q2 2025 | Q2 2026 |', rows='| Example A | 22% | 24% |'):
    return {'source_id': 'source-1', 'excerpt': f'{caption}\n\n{headers}\n| --- | --- | --- |\n{rows}',
            'published': '2099-01-01', 'publication_basis': 'caller_claimed_verified'}


def test_explicit_dimensions_compute_only_conditional_arithmetic():
    result = extract_table_observations(source())
    assert len(result['observations']) == 2
    assert result['changes'][0]['status'] == 'CONDITIONAL_ARITHMETIC'
    assert result['changes'][0]['change']['value'] == '2'
    assert result['changes'][0]['change']['unit'] == 'percentage_points'
    assert result['publication_status'] == result['fact_status'] == 'UNKNOWN'
    assert result['observations'][0]['business_segment'] == 'optical components'
    assert result['observations'][0]['period_end'] == '2025-06-30'
    assert result['observations'][0]['validation']['fact_status'] == 'UNKNOWN'


def test_missing_scope_and_actual_are_not_inferred():
    result = extract_table_observations(source('Global DRAM Market Share by Revenue'))
    assert len(result['observations']) == 2
    assert result['observations'][0]['unresolved_dimensions'] == ['scope', 'actual_or_estimate']
    assert result['changes'] == []


@pytest.mark.parametrize('caption', ['DRAM Market Share by Revenue', 'Global DRAM revenue',
                                   'Global DRAM Market Share by Shipments',
                                   'Global DRAM Market Share by Revenue (adjusted)',
                                   'Global DRAM Market Share by Revenue (actual, estimate)'])
def test_unsupported_captions_do_not_guess(caption):
    result = extract_table_observations(source(caption))
    assert not result['observations'] and not result['changes']
    assert result['skipped_tables']


@pytest.mark.parametrize('header', ['FY2025 Q2', 'Fiscal Q2 2025', 'Q2 FY25', 'Q2 25', '2025', '2025-06-30'])
def test_fiscal_and_ambiguous_periods_are_not_converted(header):
    result = extract_table_observations(source(headers=f'| Company | {header} | Q2 2026 |'))
    assert not result['observations'] and result['skipped_tables']


def test_calendar_headers_and_reverse_column_order():
    result = extract_table_observations(source(headers='| Company | Calendar 2026 Q1 | 2025 Q4 |'))
    change = result['changes'][0]['change']
    assert change['value'] == '-2'
    assert change['baseline_period']['period_end'] == '2025-12-31'
    assert change['comparison_period']['period_start'] == '2026-01-01'


@pytest.mark.parametrize('rows', ['| Total | 22% | 24% |', '| **Total** | 22% | 24% |', '| Others | 22% | 24% |',
                                '| | 22% | 24% |', '| Example | 22 | 24 |',
                                '| Example | 22% | N/A |', '| Example | ~22% | 24% |',
                                '| Example | 22% |', '| Example | 22% | 24%* |'])
def test_no_total_or_missing_value_invention(rows):
    result = extract_table_observations(source(rows=rows))
    assert result['observations'] == result['changes'] == []


def test_duplicate_entities_or_periods_are_ambiguous():
    result = extract_table_observations(source(rows='| Example | 22% | 24% |\n| Example | 21% | 25% |'))
    assert not result['observations']
    result = extract_table_observations(source(headers='| Company | Q2 2025 | Q2 2025 |'))
    assert not result['observations']


def test_multiple_entities_are_not_cross_compared_or_ranked():
    result = extract_table_observations(source(rows='| Full Company Ltd. | 22.05% | 24.10% |\n| Different Corp | 55% | 54% |'))
    assert [item['entity'] for item in result['changes']] == ['Full Company Ltd.', 'Different Corp']
    assert [item['change']['value'] for item in result['changes']] == ['2.05', '-1']
    assert all('rank' not in item for item in result['changes'])


def test_invalid_percent_cannot_compute():
    result = extract_table_observations(source(rows='| Example | 120% | 24% |'))
    assert result['changes'] == []
    assert result['observations'][0]['validation']['status'] == 'UNKNOWN'


def test_unrelated_prose_cannot_supply_caption():
    item = source()
    item['excerpt'] = item['excerpt'].replace('\n\n', '\nUnrelated paragraph\n')
    assert not extract_table_observations(item)['observations']


def test_invalid_source_and_no_table():
    assert extract_table_observations(None)['skipped_tables']
    assert extract_table_observations({'source_id': 's', 'excerpt': 'ordinary prose'})['observations'] == []


def test_separator_width_must_match_headers():
    item = source()
    item['excerpt'] = item['excerpt'].replace('| --- | --- | --- |', '| --- | --- |')
    assert not extract_table_observations(item)['observations']
