"""Selected official filing sections into bounded report evidence, never signals."""
import hashlib
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from prism_core.filing_html import parse_filing_html
from prism_core.filing_report_evidence import _record_blocks
from prism_core.material_filing_selection import material_html_records

VERSION = 'dart-selected-sections-v1'


def section_blocks(row, section):
    """Admit only exact hashed viewer representations with explicit local scope."""
    try:
        body = section['html']
        if not isinstance(body, str) or len(body) > 2 * 1024 * 1024:
            raise ValueError
        raw = body.encode('utf-8')
        node = section['tuple']
        url = urlsplit(section['url'])
        if (len(raw) > 2 * 1024 * 1024 or len(raw) != section['utf8_bytes']
                or hashlib.sha256(raw).hexdigest() != section['sha256']
                or url.scheme != 'https' or url.netloc != 'dart.fss.or.kr'
                or url.path != '/report/viewer.do' or url.fragment
                or set(node) != {'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd'}
                or len(parse_qsl(url.query)) != len(node) or dict(parse_qsl(url.query)) != node
                or node['rcpNo'] != row['receipt_id']
                or not re.fullmatch(r'\d{14}', node['rcpNo'])
                or not all(re.fullmatch(r'\d{1,14}', node[k]) for k in ('dcmNo', 'eleId', 'offset', 'length'))
                or int(node['length']) <= 0 or not re.fullmatch(r'dart\d{1,2}\.xsd', node['dtd'])
                or row['scope_verified'] is not True or row['body_status'] != 'available'
                or row['scope'] not in {'consolidated', 'standalone'}):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError):
        return [], ['DART_SECTION_PROVENANCE_INVALID']
    parsed = parse_filing_html(body)
    if parsed['status'] not in {'COMPLETE', 'PARTIAL'}:
        return [], ['DART_SECTION_PARSE_UNAVAILABLE']
    # Do not synthesize parent headings/units or assign a guessed scope to a
    # fragment; missing context is an omission, never a successful excerpt.
    records = [r for r in parsed['records'] if r['scope'] == row['scope']]
    gaps = ['DART_SECTION_SCOPE_UNRESOLVED'] if len(records) != len(parsed['records']) else []
    if not records:
        return [], gaps or ['DART_SECTION_NO_RECORDS']
    records, grouping_gaps = material_html_records(records)
    blocks, gaps = _record_blocks(records, material_notes=True, representation='DART_VIEWER_HTML',
        digest=section['sha256'], md_hash=None, gaps=[*gaps, *grouping_gaps,
            *('DART_SECTION_' + e for e in parsed['errors'])], parsed=parsed)
    for block in blocks:
        block['provenance'].pop('markdown_sha256', None)
    return blocks, gaps


async def collect_latest(symbol, company, decision_at, scope, progress):
    """Separate bounded DART budget; no raw HTML is retained in report receipts."""
    from prism_core.dart_identity import resolve_dart_identity
    from prism_core.dart_public_filings import collect_dart_periodic_filings

    start = decision_at.astimezone(ZoneInfo('Asia/Seoul')).date() - timedelta(days=550)
    metrics = {'identity': {}, 'filings': {}}
    progress['dart_metrics'] = metrics
    identity = await resolve_dart_identity(company, symbol, start, decision_at, _metrics=metrics['identity'])
    progress['dart_calls'] = identity.get('metrics', {}).get('calls', 0)
    progress['dart_response_bytes'] = identity.get('metrics', {}).get('response_bytes', 0)
    progress['filing_selection'] = {'version': VERSION, 'decision_at': decision_at.isoformat(),
                                  'scope': scope, 'identity_status': identity['status'], 'identity': identity}
    if identity.get('ticker_verified_from_company_profile') is not True or not identity.get('corp_code'):
        progress['gaps'].append('DART_ISSUER_UNRESOLVED')
        return
    result = await collect_dart_periodic_filings(corp_code=identity['corp_code'],
        decision_at=decision_at, start_date=start, scope=scope, include_section_bodies=True,
        max_filings=8, max_calls=28, timeout_seconds=55, _metrics=metrics['filings'])
    progress['dart_calls'] += result['metrics']['calls']
    progress['dart_response_bytes'] += result['metrics']['response_bytes']
    selection = result['selection']
    progress['filing_selection'].update(selection=selection, coverage=result['coverage'],
        observed_at=result['observed_at'], limitations=result['limitations'], errors=result['errors'])
    progress['gaps'].extend(selection['reasons'])
    if not selection.get('primary_id'):
        progress['gaps'].append('DART_LATEST_FILING_UNAVAILABLE')
        return
    rows = {r['receipt_id']: r for r in result['filings']}
    progress['filing_selection']['selected_section_delivery'] = {}
    for role, key in (('primary', selection['primary_id']),
                      ('annual_supplement', selection.get('annual_supplement_id'))):
        if not key:
            continue
        row = rows[key]
        delivery = row.get('section_delivery', {})
        progress['filing_selection']['selected_section_delivery'][key] = delivery
        if delivery.get('status') != 'AVAILABLE':
            progress['gaps'].append('DART_SELECTED_SECTIONS_PARTIAL')
        for section_name, section in row['sections'].items():
            if section_name not in {'financial_statements', 'financial_notes'} or 'html' not in section:
                continue
            blocks, gaps = section_blocks(row, section)
            progress['gaps'].extend(gaps)
            if not blocks:
                continue
            filing = {k: row[k] for k in ('receipt_id', 'kind', 'period_start', 'period_end', 'scope')}
            filing.update(role=role, entity_id='DART:' + identity['corp_code'],
                          event_date='UNKNOWN', observed_at=result['observed_at'],
                          decision_at=decision_at.isoformat(), latest_confirmed=selection['latest_confirmed'],
                          section=section_name)
            progress['sources'].append({'source_id': 'D-' + key + '-' + section_name,
                'url': section['url'], 'published': row['submitted_date'], 'publication_basis': 'OFFICIAL_CATALOG_DATE',
                'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED', 'blocks': blocks, 'filing': filing})
    progress['gaps'].append('DART_SELECTED_SECTIONS_NOT_FULL_DOCUMENT')
