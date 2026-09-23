"""Selected official filing sections into bounded report evidence, never signals."""
import asyncio
import hashlib
import re
from datetime import timedelta
from time import monotonic
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from prism_core.dart_viewer_tree import parse_viewer_tree
from prism_core.filing_html import parse_filing_html
from prism_core.filing_html_policy import MAX_HTML_BYTES
from prism_core.filing_report_evidence import _record_blocks
from prism_core.material_filing_selection import material_html_records

VERSION = 'dart-selected-sections-v2'
_PLAIN_FINANCIAL_TITLE = re.compile(
    r'(?:연결|별도|개별)?(?:재무제표(?:주석)?|재무상태표|(?:포괄)?손익계산서|현금흐름표)'
    r'(?:\((?:연결|별도|개별)\))?')


def _admit_section(row, section):
    """Admit an exact hashed viewer HTML representation within its own byte cap."""
    try:
        body = section['html']
        if not isinstance(body, str) or len(body) > MAX_HTML_BYTES:
            raise ValueError
        raw = body.encode('utf-8')
        node = section['tuple']
        url = urlsplit(section['url'])
        if (len(raw) > MAX_HTML_BYTES or len(raw) != section['utf8_bytes']
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
        return None
    return body


def section_blocks(row, section):
    """Admit only exact hashed viewer representations with explicit local scope."""
    deadline = monotonic() + 10.0
    body = _admit_section(row, section)
    if body is None:
        return [], ['DART_SECTION_PROVENANCE_INVALID']
    parsed = parse_filing_html(body, _deadline=deadline)
    if parsed['status'] not in {'COMPLETE', 'PARTIAL'}:
        return [], ['DART_SECTION_PARSE_UNAVAILABLE']
    # Do not synthesize parent headings/units or assign a guessed scope to a
    # fragment; missing context is an omission, never a successful excerpt.
    records = [r for r in parsed['records'] if r['scope'] == row['scope']]
    gaps = ['DART_SECTION_SCOPE_UNRESOLVED'] if len(records) != len(parsed['records']) else []
    if not records:
        return [], gaps or ['DART_SECTION_NO_RECORDS']
    return _section_record_blocks(records, parsed, section, gaps, deadline=deadline)


def _section_record_blocks(records, parsed, section, gaps, *, deadline=None):
    records, grouping_gaps = material_html_records(records)
    blocks, gaps = _record_blocks(records, material_notes=True, representation='DART_VIEWER_HTML',
        digest=section['sha256'], md_hash=None, gaps=[*gaps, *grouping_gaps,
            *('DART_SECTION_' + e for e in parsed['errors'])], parsed=parsed, deadline=deadline)
    for block in blocks:
        block['provenance'].pop('markdown_sha256', None)
    return blocks, gaps


def _graph_section(meta, nodes, receipt, document, *, max_bytes=MAX_HTML_BYTES):
    """Check retained cover/financial provenance without inventing body hashes."""
    if not isinstance(meta, dict) or not isinstance(meta['url'], str):
        raise TypeError
    fields = meta['tuple']
    node = nodes[fields['dcmNo'] + ':' + fields['eleId']]
    url = urlsplit(meta['url'])
    query = parse_qsl(url.query, keep_blank_values=True)
    if (set(fields) != {'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd'}
            or any(fields[key] != node[key] for key in fields)
            or fields['rcpNo'] != receipt or fields['dcmNo'] != document
            or url.scheme != 'https' or url.netloc != 'dart.fss.or.kr'
            or url.path != '/report/viewer.do' or url.fragment
            or len(query) != len(fields) or dict(query) != fields
            or not isinstance(meta['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', meta['sha256'])
            or type(meta['utf8_bytes']) is not int or not 0 < meta['utf8_bytes'] <= max_bytes):
        raise ValueError
    return node


def _compact_title(text):
    return re.sub(r'\s+', '', text)


def _fragment_context(row, section, main_html, corp_code, parent_key):
    graph = parse_viewer_tree(main_html, row['receipt_id'], corp_code)
    if graph['main_sha256'] != row['main_sha256']:
        raise ValueError
    nodes = {node['key']: node for node in graph['nodes']}
    parent = nodes[parent_key]
    child = _graph_section(section, nodes, row['receipt_id'], parent['dcmNo'])
    if child['parent_key'] != parent_key or child['key'] not in parent['children_keys']:
        raise ValueError
    cover = _graph_section(row['sections']['cover'], nodes, row['receipt_id'], parent['dcmNo'],
                           max_bytes=2 * 1024 * 1024)
    financial = _graph_section(row['sections']['financial_statements'], nodes, row['receipt_id'], parent['dcmNo'])
    for name in ('cover', 'financial_statements'):
        meta = row['sections'][name]
        if 'html' in meta and _admit_section(row, meta) is None:
            raise ValueError
    label = '연결' if row['scope'] == 'consolidated' else ''
    qualifier = '연결' if row['scope'] == 'consolidated' else '별도'
    kind = {'annual': '사업보고서', 'interim': '반기보고서', 'quarterly': '분기보고서'}[row['kind']]
    if (_compact_title(cover['text']) != kind
            or not re.fullmatch(r'\d+\.' + label + '재무제표', _compact_title(financial['text']))
            or not re.fullmatch(r'\d+\.' + label + '재무제표주석', _compact_title(parent['text']))
            or not re.fullmatch(r'\d{1,3}(?:-\d{1,3})*[.)].+\(' + qualifier + r'\)', _compact_title(child['text']))):
        raise ValueError
    return {'basis': 'DART_EXPLICIT_VIEWER_TREE', 'main_sha256': graph['main_sha256'],
            'main_url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + row['receipt_id'],
            'parent_key': parent_key, 'child_key': child['key'],
            'parent_title': parent['text'], 'child_title': child['text']}


def _fragment_scope_confirmed(parsed, context, scope):
    records, headings = parsed['records'], parsed['heading_events']
    if not records or not headings:
        return False
    # Plain p/div titles are intentionally not a new global heading grammar.
    # Reject only exact financial-title prose before adjacent records merge.
    if any(record['kind'] == 'prose' and _PLAIN_FINANCIAL_TITLE.fullmatch(_compact_title(record['text']))
           for record in records):
        return False
    child = _compact_title(context['child_title'])
    label, opposite = ('연결', '별도') if scope == 'consolidated' else ('별도', '연결')
    for item in [*records, *headings]:
        path = item['section_path']
        if item['scope'] != 'unknown' or not path or _compact_title(path[0]) != child:
            return False
        for title in path:
            title = _compact_title(title)
            if opposite in title or '개별' in title:
                return False
            if (re.search(r'연결|별도|개별|재무제표|재무상태표|손익계산서|현금흐름표', title)
                    and not title.endswith('(' + label + ')')):
                return False
    return _compact_title(headings[0]['text']) == child


def note_fragment_blocks(row, section, *, main_html, corp_code, parent_key):
    """Admit explicit external parent scope, never claim full note coverage."""
    deadline = monotonic() + 10.0
    if not isinstance(section, dict) or not isinstance(section.get('url'), str):
        return [], ['DART_SECTION_PROVENANCE_INVALID']
    body = _admit_section(row, section)
    if body is None:
        return [], ['DART_SECTION_PROVENANCE_INVALID']
    try:
        context = _fragment_context(row, section, main_html, corp_code, parent_key)
    except (KeyError, TypeError, ValueError, UnicodeError):
        return [], ['DART_NOTE_CONTEXT_UNVERIFIED']
    parsed = parse_filing_html(body, _capture_headings=True, _deadline=deadline)
    if parsed['status'] not in {'COMPLETE', 'PARTIAL'}:
        return [], ['DART_SECTION_PARSE_UNAVAILABLE']
    if not _fragment_scope_confirmed(parsed, context, row['scope']):
        return [], ['DART_NOTE_FRAGMENT_SCOPE_UNRESOLVED']
    context['child_heading_paths'] = list(parsed['heading_events'][0]['source_paths'])
    records = [{**record, 'scope': row['scope'], 'scope_context': dict(context)} for record in parsed['records']]
    return _section_record_blocks(records, parsed, section, ['DART_NOTE_FRAGMENT_PARTIAL_COVERAGE'], deadline=deadline)


def _fragment_selection_receipt(state):
    receipt = {key: state[key] for key in (
        'version', 'basis', 'parent_key', 'main_sha256', 'child_total', 'eligible_total',
        'unselected_count', 'full_notes_acquired', 'discarded_due_to_final_selection', 'stop_reason') if key in state}
    for key in ('planned_keys', 'requested_keys', 'acquired_keys', 'budget_omitted_keys'):
        receipt[key] = list(state.get(key, []))
    receipt['failures'] = [{key: item[key] for key in ('child_key', 'code') if key in item}
                           for item in state.get('failures', [])]
    receipt['fragments'] = {}
    return receipt


async def collect_latest(symbol, company, decision_at, scope, progress, *, client_factory=None, source_sink=None):
    """Separate bounded DART budget; no raw HTML is retained in report receipts."""
    from prism_core.dart_identity import resolve_dart_identity
    from prism_core.dart_public_filings import collect_dart_periodic_filings

    start = decision_at.astimezone(ZoneInfo('Asia/Seoul')).date() - timedelta(days=550)
    metrics = {'identity': {}, 'filings': {}}
    progress['dart_metrics'] = metrics
    client_options = {'client_factory': client_factory} if client_factory is not None else {}
    identity = await resolve_dart_identity(company, symbol, start, decision_at,
        _metrics=metrics['identity'], **client_options)
    progress['dart_calls'] = identity.get('metrics', {}).get('calls', 0)
    progress['dart_response_bytes'] = identity.get('metrics', {}).get('response_bytes', 0)
    progress['filing_selection'] = {'version': VERSION, 'decision_at': decision_at.isoformat(),
                                  'scope': scope, 'identity_status': identity['status'], 'identity': identity}
    if identity.get('ticker_verified_from_company_profile') is not True or not identity.get('corp_code'):
        progress['gaps'].append('DART_ISSUER_UNRESOLVED')
        return
    result = await collect_dart_periodic_filings(corp_code=identity['corp_code'],
        decision_at=decision_at, start_date=start, scope=scope, include_section_bodies=True,
        max_filings=8, max_calls=28, timeout_seconds=55, _metrics=metrics['filings'], **client_options)
    progress['dart_calls'] += result['metrics']['calls']
    progress['dart_response_bytes'] += result['metrics']['response_bytes']
    selection = result['selection']
    progress['filing_selection'].update(selection=selection, coverage=result['coverage'],
        observed_at=result['observed_at'], limitations=result['limitations'], errors=result['errors'])
    fragment_states = {row['receipt_id']: _fragment_selection_receipt(row['note_fragment_selection'])
                       for row in result.get('filings', []) if 'note_fragment_selection' in row}
    if fragment_states:
        progress['filing_selection']['selected_note_fragments'] = fragment_states
    progress['gaps'].extend(selection['reasons'])
    if not selection.get('primary_id'):
        progress['gaps'].append('DART_LATEST_FILING_UNAVAILABLE')
        return
    rows = {r['receipt_id']: r for r in result['filings']}
    progress['filing_selection']['selected_section_delivery'] = {}
    progress['filing_selection']['selected_section_provenance'] = {}

    def append_source(row, role, name, section, blocks, source_id):
        filing = {k: row[k] for k in ('receipt_id', 'kind', 'period_start', 'period_end', 'scope')}
        filing.update(role=role, entity_id='DART:' + identity['corp_code'],
                      event_date='UNKNOWN', observed_at=result['observed_at'],
                      decision_at=decision_at.isoformat(), latest_confirmed=selection['latest_confirmed'], section=name)
        progress['sources'].append({'source_id': source_id, 'url': section['url'],
            'published': row['submitted_date'], 'publication_basis': 'OFFICIAL_CATALOG_DATE',
            'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED', 'blocks': blocks, 'filing': filing})

    def retain_source(row, role, name, section, source_id, context=None):
        if source_sink is None or _admit_section(row, section) is None:
            return
        source_sink.append({'source_id': source_id, 'html': section['html'],
            'sha256': section['sha256'], 'url': section['url'],
            'published': row['submitted_date'], 'scope_context': context,
            'filing': {**{k: row[k] for k in ('receipt_id', 'kind', 'period_start', 'period_end', 'scope')},
                       'entity_id': 'DART:' + identity['corp_code'], 'role': role, 'section': name}})

    for role, key in (('primary', selection['primary_id']),
                      ('annual_supplement', selection.get('annual_supplement_id'))):
        if not key:
            continue
        row = rows[key]
        delivery = row.get('section_delivery', {})
        progress['filing_selection']['selected_section_delivery'][key] = delivery
        progress['filing_selection']['selected_section_provenance'][key] = {
            name: {field: section[field] for field in ('sha256', 'utf8_bytes', 'url')}
            for name, section in row['sections'].items()
            if name in {'financial_statements', 'financial_notes'} and 'html' in section}
        if delivery.get('status') != 'AVAILABLE':
            progress['gaps'].append('DART_SELECTED_SECTIONS_PARTIAL')
        for section_name, section in row['sections'].items():
            if section_name not in {'financial_statements', 'financial_notes'} or 'html' not in section:
                continue
            blocks, gaps = await asyncio.to_thread(section_blocks, row, section)
            progress['gaps'].extend(gaps)
            if not any(g in gaps for g in ('DART_SECTION_PROVENANCE_INVALID', 'DART_SECTION_PARSE_UNAVAILABLE',
                                           'DART_SECTION_SCOPE_UNRESOLVED', 'DART_SECTION_NO_RECORDS')):
                retain_source(row, role, section_name, section, 'D-' + key + '-' + section_name)
            if not blocks:
                continue
            append_source(row, role, section_name, section, blocks, 'D-' + key + '-' + section_name)
        for fragment in row.get('note_fragments', []):
            blocks, gaps = await asyncio.to_thread(
                note_fragment_blocks, row, fragment, main_html=row.get('note_main_html'),
                corp_code=identity['corp_code'], parent_key=fragment['parent_key'])
            progress['gaps'].extend(gaps)
            entry = {field: fragment[field] for field in ('sha256', 'utf8_bytes', 'url')}
            entry.update(context_verified='DART_NOTE_FRAGMENT_PARTIAL_COVERAGE' in gaps,
                         candidate_count=len(blocks), gaps=list(gaps))
            fragment_states[key]['fragments'][fragment['child_key']] = entry
            if entry['context_verified']:
                context = _fragment_context(row, fragment, row.get('note_main_html'), identity['corp_code'], fragment['parent_key'])
                retain_source(row, role, 'financial_notes_fragment', fragment,
                    'D-' + key + '-financial_notes_fragment-' + fragment['child_key'].replace(':', '-'), context)
            if blocks:
                source_id = 'D-' + key + '-financial_notes_fragment-' + fragment['child_key'].replace(':', '-')
                append_source(row, role, 'financial_notes_fragment', fragment, blocks, source_id)
    progress['gaps'].append('DART_SELECTED_SECTIONS_NOT_FULL_DOCUMENT')
