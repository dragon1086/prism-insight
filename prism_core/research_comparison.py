"""Deterministic structural checks on supplied observations, never fact checking.

An observation requires the string fields in REQUIRED_FIELDS plus ``value`` (a
plain decimal string, Decimal, or integer; floats/bools are intentionally rejected).
Dates are inclusive ISO YYYY-MM-DD. ``unit='percent'`` means 0..100, not a ratio;
dimensionless observations must still explicitly specify ``currency='none'``.
``scope`` is 'consolidated' or 'segment'; ``actual_or_estimate`` is 'actual' or
'estimate'. Sources map unique source IDs to dictionaries containing ``text``.
Literal excerpt and numeric-token presence establish text support ONLY: entity,
metric, period attribution and publication metadata remain unverified. Callers
must obtain structured observations independently; this module extracts none.
"""

import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext

REQUIRED_FIELDS = (
    'entity', 'business_segment', 'metric', 'period_start', 'period_end',
    'geography', 'unit', 'currency', 'actual_or_estimate', 'scope',
    'source_id', 'excerpt',
)
DIMENSIONS = ('business_segment', 'metric', 'geography', 'unit', 'currency',
              'actual_or_estimate', 'scope')
WARNINGS = [
    'SOURCE_TEXT_MATCH_IS_NOT_FACT_VERIFICATION',
    'ENTITY_VALUE_PERIOD_ATTRIBUTION_UNVERIFIED',
    'PUBLICATION_METADATA_UNVERIFIED',
    'SOURCE_VALUES_MAY_BE_ROUNDED_CHANGE_USES_DISPLAYED_VALUES',
]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        return None
    text = str(value)
    if len(text) > 100 or not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return None
    try:
        number = Decimal(text)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def validate_observation(observation, sources):
    """Return structural status plus errors, with factual status always UNKNOWN.

    Source publication dates and caller-provided ``verified`` labels are ignored.
    Upstream retrieval/as-of validation is separately required before report use.
    """
    result = {'status': 'UNKNOWN', 'fact_status': 'UNKNOWN',
              'publication_status': 'UNKNOWN', 'provenance_status': 'UNKNOWN',
              'errors': [], 'warnings': list(WARNINGS)}
    if not isinstance(observation, Mapping):
        result['errors'].append('INVALID_OBSERVATION')
        return result
    for field in REQUIRED_FIELDS:
        value = observation.get(field)
        if not isinstance(value, str) or not value.strip() or value.strip().lower() in {'unknown', 'n/a', '미확인'}:
            result['errors'].append('MISSING_OR_UNKNOWN:' + field)
    for field, allowed in [('scope', {'consolidated', 'segment'}),
                           ('actual_or_estimate', {'actual', 'estimate'})]:
        if not isinstance(observation.get(field), str) or observation[field] not in allowed:
            result['errors'].append('INVALID:' + field)
    parsed = {}
    for field in ('period_start', 'period_end'):
        value = observation.get(field)
        try:
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise ValueError
            parsed[field] = date.fromisoformat(value)
        except ValueError:
            result['errors'].append('INVALID:' + field)
    if len(parsed) == 2 and parsed['period_start'] > parsed['period_end']:
        result['errors'].append('REVERSED_PERIOD')
    number = _number(observation.get('value'))
    if number is None:
        result['errors'].append('INVALID:value')
    elif observation.get('unit') == 'percent' and not 0 <= number <= 100:
        result['errors'].append('INVALID_PERCENT_RANGE')
    source_id = observation.get('source_id')
    source = sources.get(source_id) if isinstance(sources, Mapping) and isinstance(source_id, str) else None
    text = source.get('text') if isinstance(source, Mapping) else None
    excerpt = observation.get('excerpt')
    if not isinstance(text, str) or not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in text:
        result['errors'].append('LITERAL_EXCERPT_NOT_SUPPORTED')
    else:
        result['provenance_status'] = 'SOURCE_TEXT_MATCHED_NOT_FACT_VALIDATED'
        if number is not None and not re.search(
                r'(?<![\d.,+\-])' + re.escape(str(observation['value'])) + r'(?!\d|[.,]\d)', excerpt):
            result['errors'].append('LITERAL_VALUE_NOT_IN_EXCERPT')
    if not result['errors']:
        result['status'] = 'STRUCTURALLY_SUPPORTED'
    return result


def compare_observations(baseline, comparison, sources, *, mode='change'):
    """Compare supplied dimensions only; never infer peers, ranks or dominance.

    ``change`` requires the same entity and nonoverlapping chronological periods.
    ``peer`` permits different supplied entities but requires identical periods;
    it produces no arithmetic or ranking. COMPARABLE means structural agreement,
    NOT factual verification or proof that the metric definitions are equivalent.
    """
    if mode not in {'change', 'peer'}:
        raise ValueError("mode must be 'change' or 'peer'")
    left, right = [validate_observation(item, sources) for item in (baseline, comparison)]
    result = {'status': 'UNKNOWN', 'fact_status': 'UNKNOWN', 'mode': mode,
              'baseline': left, 'comparison': right, 'mismatches': [],
              'change': None, 'warnings': list(WARNINGS)}
    if left['errors'] or right['errors']:
        return result
    dimensions = DIMENSIONS + (('entity',) if mode == 'change' else ('period_start', 'period_end'))
    result['mismatches'] = [field for field in dimensions if baseline[field] != comparison[field]]
    if mode == 'change' and baseline['period_end'] >= comparison['period_start']:
        result['mismatches'].append('PERIODS_NOT_ORDERED_NONOVERLAPPING')
    if result['mismatches']:
        result['status'] = 'INCOMPARABLE'
        return result
    result['status'] = 'COMPARABLE'
    if mode == 'change':
        with localcontext() as context:
            # Inputs have at most 100 characters; subtraction is exact even when
            # large integer and very small fractional observations are combined.
            context.prec = 205
            delta = _number(comparison['value']) - _number(baseline['value'])
        result['change'] = {
            'value': format(delta, 'f'),
            'unit': 'percentage_points' if baseline['unit'] == 'percent' else baseline['unit'],
            'baseline_period': {field: baseline[field] for field in ('period_start', 'period_end')},
            'comparison_period': {field: comparison[field] for field in ('period_start', 'period_end')},
            'basis': 'SUPPLIED_DISPLAYED_VALUES_NOT_VERIFIED_FACTS',
        }
    return result
