"""Narrow Markdown table candidates, not a general extractor or fact verifier.

Recognized caption: ``Global <business> [segment] Market Share by Revenue``
with optional ``(%)``, ``(actual)``, ``(estimate)`` or combined qualifiers.
Each value must carry a literal %. Headers must be Qn YYYY or YYYY Qn
(optionally prefixed 'Calendar'); fiscal labels are never translated.
Missing explicit 'segment' scope or actual/estimate stays UNKNOWN and prevents
arithmetic. No aliases, peer identity, ranks or publication dates are inferred.
"""

import calendar
import re
from collections import Counter
from collections.abc import Mapping
from itertools import pairwise

from prism_core.research_comparison import compare_observations, validate_observation


def _cells(line):
    return [cell.strip() for cell in line.strip().strip('|').split('|')]


def _period(header):
    match = re.fullmatch(r'(?:Calendar\s+)?(?:Q([1-4])\s+(\d{4})|(\d{4})\s+Q([1-4]))', header, re.IGNORECASE)
    if not match:
        return None
    quarter, year = int(match[1] or match[4]), int(match[2] or match[3])
    if not 1 <= year <= 9999:
        return None
    first, last = 3 * quarter - 2, 3 * quarter
    return {'period_start': f'{year:04d}-{first:02d}-01',
            'period_end': f'{year:04d}-{last:02d}-{calendar.monthrange(year, last)[1]}'}


def extract_table_observations(source):
    """Return observations, conditional changes and explicit skipped-table reasons.

    ``source`` contains source_id, excerpt, published and publication_basis.
    Publication fields are not verification and are deliberately not promoted.
    Only adjacent-period arithmetic within the SAME literal entity row is done.
    Caller must additionally validate retrieval provenance and source as-of.
    """
    result = {'observations': [], 'changes': [], 'skipped_tables': [],
              'fact_status': 'UNKNOWN', 'publication_status': 'UNKNOWN',
              'warnings': ['TABLE_CANDIDATES_NOT_VERIFIED_FACTS',
                           'PUBLICATION_METADATA_UNVERIFIED',
                           'SOURCE_VALUES_MAY_BE_ROUNDED']}
    if not isinstance(source, Mapping) or not isinstance(source.get('source_id'), str) or not source['source_id'].strip() or not isinstance(source.get('excerpt'), str):
        result['skipped_tables'].append({'reason': 'INVALID_SOURCE'})
        return result
    text, source_id = source['excerpt'], source['source_id']
    lines = text.splitlines()
    sources = {source_id: {'text': text}}
    for index in range(1, len(lines) - 1):
        if '|' not in lines[index] or not all(re.fullmatch(r':?-{3,}:?', cell) for cell in _cells(lines[index + 1])):
            continue
        caption_index = index - 1
        while caption_index >= 0 and not lines[caption_index].strip():
            caption_index -= 1
        caption = lines[caption_index].strip().strip('#* ').strip() if caption_index >= 0 else ''
        headers = _cells(lines[index])
        reason = None
        match = re.fullmatch(r'Global\s+(.+?)\s+Market Share by Revenue(?:\s*\(([^()]*)\))?', caption, re.IGNORECASE)
        if re.search(r'\b(?:fiscal|FY\d*)\b', caption + ' ' + ' '.join(headers), re.IGNORECASE):
            reason = 'FISCAL_PERIOD_UNKNOWN'
        elif not match:
            reason = 'CAPTION_DIMENSIONS_UNKNOWN'
        elif len(headers) < 2 or len(_cells(lines[index + 1])) != len(headers) or headers[0].lower() not in {'company', 'entity', 'vendor', 'supplier'}:
            reason = 'ENTITY_HEADER_UNKNOWN'
        periods = [_period(header) for header in headers[1:]]
        if not reason and (not all(periods) or len({str(period) for period in periods}) != len(periods)):
            reason = 'PERIOD_HEADERS_UNKNOWN_OR_DUPLICATE'
        qualifiers = {part.strip().lower() for part in (match[2] or '').split(',') if part.strip()} if match else set()
        if not reason and (qualifiers - {'%', 'actual', 'estimate'} or {'actual', 'estimate'} <= qualifiers):
            reason = 'CAPTION_QUALIFIERS_UNKNOWN'
        rows = []
        for row in lines[index + 2:]:
            if not row.strip() or '|' not in row:
                break
            rows.append((row, _cells(row)))
        counts = Counter(cells[0] for _, cells in rows)
        if not reason and any(count > 1 for count in counts.values()):
            reason = 'DUPLICATE_ENTITY_ROWS'
        if reason:
            result['skipped_tables'].append({'line': index + 1, 'reason': reason})
            continue
        business = match[1].strip()
        scope = 'segment' if business.lower().endswith(' segment') else 'UNKNOWN'
        business = business[:-8].strip() if scope == 'segment' else business
        actual = next((value for value in ('actual', 'estimate') if value in qualifiers), 'UNKNOWN')
        for row, cells in rows:
            entity = cells[0]
            if len(cells) != len(headers) or not entity or entity.strip('*_ ').lower() in {'total', 'totals', 'subtotal', 'grand total', 'others', 'other', 'unknown', 'n/a'}:
                continue
            # Reject the complete row rather than bridge missing/ambiguous cells.
            if not all(re.fullmatch(r'\d{1,3}(?:\.\d+)?\s*%', cell) for cell in cells[1:]):
                continue
            observations = []
            for cell, period in zip(cells[1:], periods):
                observation = dict(entity=entity, business_segment=business,
                                   metric='revenue_share', geography='global',
                                   unit='percent', currency='none', scope=scope,
                                   actual_or_estimate=actual, source_id=source_id,
                                   excerpt=row, value=cell.rstrip('%').strip(), **period)
                observation['unresolved_dimensions'] = [field for field in ('scope', 'actual_or_estimate') if observation[field] == 'UNKNOWN']
                observation['validation'] = validate_observation(observation, sources)
                observation['fact_status'] = 'UNKNOWN'
                observation['publication_status'] = 'UNKNOWN'
                observations.append(observation)
            result['observations'].extend(observations)
            observations.sort(key=lambda item: item['period_start'])
            for left, right in pairwise(observations):
                comparison = compare_observations(left, right, sources)
                if comparison['change'] is not None:
                    result['changes'].append({'status': 'CONDITIONAL_ARITHMETIC',
                                              'fact_status': 'UNKNOWN', 'entity': entity,
                                              'business_segment': business, 'source_id': source_id,
                                              'change': comparison['change'],
                                              'warnings': comparison['warnings']})
    return result
