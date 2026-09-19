"""No network or model: comparisons are on unverified structured fixtures."""

from decimal import Decimal

import pytest

from prism_core.research_comparison import (
    DIMENSIONS,
    REQUIRED_FIELDS,
    compare_observations,
    validate_observation,
)


@pytest.fixture
def sample():
    left = {'entity': 'Example Corp', 'business_segment': 'DRAM', 'metric': 'revenue_share',
            'period_start': '2025-04-01', 'period_end': '2025-06-30', 'geography': 'global',
            'unit': 'percent', 'currency': 'none', 'actual_or_estimate': 'actual',
            'scope': 'segment', 'source_id': 's1', 'excerpt': 'Share was 22%.', 'value': '22'}
    right = dict(left, period_start='2026-04-01', period_end='2026-06-30',
                 source_id='s2', excerpt='Share was 24%.', value='24')
    sources = {'s1': {'text': left['excerpt']}, 's2': {'text': right['excerpt']}}
    return left, right, sources


def test_change_is_percentage_points_not_percent_growth(sample):
    left, right, _sources = sample
    result = compare_observations(*sample)
    assert result['status'] == 'COMPARABLE'
    assert result['change']['value'] == '2'
    assert result['change']['unit'] == 'percentage_points'
    assert result['change']['baseline_period']['period_start'] == left['period_start']
    assert result['change']['comparison_period']['period_end'] == right['period_end']
    assert result['fact_status'] == 'UNKNOWN'
    assert 'SOURCE_VALUES_MAY_BE_ROUNDED_CHANGE_USES_DISPLAYED_VALUES' in result['warnings']
    assert 'rank' not in result


@pytest.mark.parametrize('field', REQUIRED_FIELDS + ('value',))
def test_missing_fields_stay_unknown(sample, field):
    left, right, sources = sample
    del right[field]
    result = compare_observations(left, right, sources)
    assert result['status'] == 'UNKNOWN'
    assert result['change'] is None


@pytest.mark.parametrize('field', DIMENSIONS + ('entity',))
def test_dimension_mismatches_are_incomparable(sample, field):
    left, right, sources = sample
    right[field] = {'scope': 'consolidated', 'actual_or_estimate': 'estimate'}.get(field, 'different')
    result = compare_observations(left, right, sources)
    assert result['status'] == 'INCOMPARABLE'
    assert field in result['mismatches']
    assert result['change'] is None


@pytest.mark.parametrize('value', [True, False, float('nan'), float('inf'), 1.5,
                                  'NaN', 'Infinity', Decimal('NaN'), None, '1e2', '9' * 101])
def test_unsafe_numbers_are_unknown(sample, value):
    sample[1]['value'] = value
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


def test_exact_decimal_precision_independent_of_default_context(sample):
    left, right, sources = sample
    for item, value in [(left, '123456789012345678901234567890.123'),
                        (right, '123456789012345678901234567890.124')]:
        item.update(value=value, unit='USD_million', currency='USD', excerpt=f'Revenue {value}.')
        sources[item['source_id']]['text'] = item['excerpt']
    assert compare_observations(*sample)['change']['value'] == '0.001'


def test_decrease_and_decimal_integer_input(sample):
    left, right, sources = sample
    left.update(value=Decimal(24), excerpt='Share was 24%.')
    right.update(value=22, excerpt='Share was 22%.')
    sources['s1']['text'], sources['s2']['text'] = left['excerpt'], right['excerpt']
    assert compare_observations(*sample)['change']['value'] == '-2'


@pytest.mark.parametrize('start,end', [('2025-04-01', '2025-06-30'),
                                     ('2025-06-30', '2025-09-30'),
                                     ('2024-04-01', '2024-06-30')])
def test_change_periods_must_be_ordered_and_nonoverlapping(sample, start, end):
    sample[1].update(period_start=start, period_end=end)
    assert compare_observations(*sample)['status'] == 'INCOMPARABLE'


@pytest.mark.parametrize('value', ['20260401', '2026-02-30', 'unknown', [], None])
def test_invalid_dates(sample, value):
    sample[1]['period_start'] = value
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


def test_reversed_period_unknown(sample):
    sample[1].update(period_start='2026-07-01')
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


def test_peer_mode_no_inferred_rank_or_peer_identity(sample):
    left, right, _sources = sample
    right.update(entity='Supplied peer', period_start=left['period_start'], period_end=left['period_end'])
    result = compare_observations(*sample, mode='peer')
    assert result['status'] == 'COMPARABLE'
    assert result['change'] is None
    assert 'rank' not in result and 'peers' not in result
    right['period_end'] = '2025-09-30'
    assert compare_observations(*sample, mode='peer')['status'] == 'INCOMPARABLE'


def test_source_match_is_not_fact_or_publication_verification(sample):
    left, _right, sources = sample
    sources['s1'].update(publication_date='2099-01-01', verified=True)
    left.update(verified=True, entity='Unverified attribution')
    result = validate_observation(left, sources)
    assert result['status'] == 'STRUCTURALLY_SUPPORTED'
    assert result['fact_status'] == result['publication_status'] == 'UNKNOWN'
    assert 'PUBLICATION_METADATA_UNVERIFIED' in result['warnings']


@pytest.mark.parametrize('text', ['', 'Share was 23%.', 'Share was  22%.'])
def test_excerpt_match_is_literal(sample, text):
    sample[2]['s1']['text'] = text
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


def test_number_must_be_literal_token_not_substring(sample):
    _left, right, _sources = sample
    right.update(value='4')
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


@pytest.mark.parametrize('field', ['scope', 'actual_or_estimate', 'source_id'])
def test_malformed_field_does_not_raise(sample, field):
    sample[1][field] = []
    assert compare_observations(*sample)['status'] == 'UNKNOWN'


def test_invalid_container_and_mode(sample):
    assert validate_observation(None, {})['status'] == 'UNKNOWN'
    assert validate_observation(sample[0], None)['status'] == 'UNKNOWN'
    with pytest.raises(ValueError):
        compare_observations(*sample, mode='rank')


def test_unknown_dimension_is_not_a_shared_comparison_key(sample):
    for item in sample[:2]:
        item['geography'] = 'UNKNOWN'
    assert compare_observations(*sample)['status'] == 'UNKNOWN'
