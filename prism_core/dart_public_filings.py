"""Bounded public DART acquisition; diagnostic metadata, never certified facts."""
import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timezone
from itertools import pairwise
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from lxml import html as lhtml

from prism_core.dart_section_html import SectionHTML, SectionHTMLLimit
from prism_core.filing_catalog import FilingCandidate, select_periodic_filings
from prism_core.filing_html_policy import MAX_HTML_BYTES

BASE = 'https://dart.fss.or.kr'
_ZONE = ZoneInfo('Asia/Seoul')
_KINDS = {'사업보고서': 'annual', '반기보고서': 'interim', '분기보고서': 'quarterly'}
_FIELDS = ('text', 'rcpNo', 'dcmNo', 'eleId', 'offset', 'length', 'dtd')
_LIMIT = 2 * 1024 * 1024
_TOTAL_LIMIT = 8 * 1024 * 1024


class _SourceError(ValueError):
    """Only internal code-only failures may appear in diagnostic results."""


class _SectionBodyLimitExceeded(_SourceError):
    """Only a single body limit, never aggregate or access failure."""


def _fail(code):
    raise _SourceError(code)


def _tree(text):
    try:
        return lhtml.fromstring(text)
    except (ValueError, TypeError, lhtml.etree.ParserError):
        _fail('HTML_INVALID')


def _text(node):
    return ' '.join(node.text_content().split())


def _compact(text):
    return re.sub(r'\s+', '', text)


def _corp_ids(text):
    return set(re.findall(r"""openCorpInfoNew\(\s*['"](\d{8})['"]""", text))


def _main_url(href):
    url = urljoin(BASE, href)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    if (parsed.scheme != 'https' or parsed.netloc != 'dart.fss.or.kr'
            or parsed.path != '/dsaf001/main.do' or parsed.fragment
            or set(query) != {'rcpNo'} or len(query['rcpNo']) != 1
            or not re.fullmatch(r'\d{14}', query['rcpNo'][0])):
        _fail('CATALOG_RECEIPT_URL_INVALID')
    return url, query['rcpNo'][0]


def parse_catalog_page(html, corp_code):
    """Parse verified-shape rows. Query-wide completeness is assessed separately."""
    root = _tree(html)
    infos = root.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " pageInfo ")]')
    bodies = root.xpath('//tbody[@id="tbody"]')
    if len(bodies) != 1:
        _fail('CATALOG_STRUCTURE_INVALID')
    placeholders = bodies[0].xpath('./tr')
    cells = placeholders[0].xpath('./td') if len(placeholders) == 1 else []
    normal_empty = (
        len(cells) == 1 and not bodies[0].xpath('.//a|.//script|.//table')
        and _compact(_text(cells[0])) == '조회결과가없습니다.')
    if (not infos and normal_empty and cells[0].get('colspan') == '6'
            and set(cells[0].get('class', '').split()) == {'no_data', 'end'}
            and cells[0].get('align') == 'center'):
        return {'rows': [], 'page': 1, 'total_pages': 1, 'total_count': 0,
                'empty_basis': 'CANONICAL_NO_DATA_PLACEHOLDER'}
    if len(infos) != 1:
        _fail('CATALOG_PAGE_INFO_MISSING')
    match = re.fullmatch(r'\[\s*(\d+)\s*/\s*(\d+)\s*\]\s*\[\s*총\s*([\d,]+)\s*건\s*\]', _text(infos[0]))
    if not match:
        _fail('CATALOG_PAGE_INFO_INVALID')
    page, pages, total = (int(x.replace(',', '')) for x in match.groups())
    if len(bodies) != 1 or not 0 <= page <= max(pages, 1):
        _fail('CATALOG_STRUCTURE_INVALID')
    if total == 0:
        if page != 1 or pages != 1 or not normal_empty:
            _fail('CATALOG_EMPTY_UNCONFIRMED')
        return {'rows': [], 'page': page, 'total_pages': pages, 'total_count': 0}
    if page < 1 or pages < 1:
        _fail('CATALOG_PAGE_INFO_INVALID')
    rows = []
    for tr in bodies[0].xpath('./tr'):
        cells = tr.xpath('./td')
        if len(cells) != 6 or not _text(cells[0]).isdigit():
            _fail('CATALOG_ROW_INVALID')
        if _corp_ids(lhtml.tostring(cells[1], encoding='unicode')) != {corp_code}:
            _fail('CATALOG_CORP_MISMATCH')
        anchors = cells[2].xpath('.//a[@href]')
        if len(anchors) != 1:
            _fail('CATALOG_RECEIPT_MISSING')
        url, receipt = _main_url(anchors[0].get('href'))
        title = _text(anchors[0])
        label = re.fullmatch(r'(?:\[[^\]]*정정[^\]]*\]\s*)?(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)', title)
        if not label or not 1 <= int(label[3]) <= 12:
            _fail('CATALOG_KIND_OR_PERIOD_INVALID')
        prefix = re.match(r'^\[[^\]]+\]', title)
        correction_type = ({'[첨부정정]': 'attachment', '[기재정정]': 'body', '[정정]': 'body'}
                           .get(prefix[0], 'unknown') if prefix else None)
        try:
            submitted = date.fromisoformat(_text(cells[4]).replace('.', '-'))
        except ValueError:
            _fail('CATALOG_DATE_INVALID')
        rows.append({'ordinal': int(_text(cells[0])), 'receipt_id': receipt,
                     'source_url': url, 'display_name': _text(cells[1]),
                     'kind': _KINDS[label[1]], 'report_period_label': f'{label[2]}.{label[3]}',
                     'submitted_date': submitted, 'is_correction': correction_type is not None,
                     'correction_type': correction_type})
    ids = [r['receipt_id'] for r in rows]
    ordinals = [r['ordinal'] for r in rows]
    expected = list(range((page - 1) * 100 + 1, min(page * 100, total) + 1))
    if len(ids) != len(set(ids)) or ordinals != expected:
        _fail('CATALOG_ROW_COVERAGE_INVALID')
    if pages != max(1, (total + 99) // 100):
        _fail('CATALOG_PAGE_COUNT_INVALID')
    return {'rows': rows, 'page': page, 'total_pages': pages, 'total_count': total}


def _lex_js(scripts):
    """Mask strings/comments in one bounded pass; this is not a JS interpreter."""
    if len(scripts) > _LIMIT:
        _fail('VIEWER_SCRIPT_LIMIT')
    code, uncommented, tokens, previous, i = [], [], [], 0, 0
    while i < len(scripts):
        start, char = i, scripts[i]
        if char == chr(96):
            _fail('VIEWER_TEMPLATE_UNSUPPORTED')
        comment = scripts.startswith('//', i) or scripts.startswith('/*', i)
        if char not in {'"', "'"} and not comment:
            i += 1
            continue
        if scripts.startswith('//', i):
            end = scripts.find('\n', i + 2)
            i = len(scripts) if end < 0 else end
        elif scripts.startswith('/*', i):
            end = scripts.find('*/', i + 2)
            if end < 0:
                _fail('VIEWER_SCRIPT_UNTERMINATED')
            i = end + 2
        else:
            i += 1
            while i < len(scripts) and scripts[i] != char:
                i += 2 if scripts[i] == '\\' else 1
            if i >= len(scripts):
                _fail('VIEWER_SCRIPT_UNTERMINATED')
            i += 1
        if len(tokens) >= 50_000:
            _fail('VIEWER_SCRIPT_LIMIT')
        value = scripts[start:i]
        gap = scripts[previous:start]
        mask = ' ' * len(value)
        code.extend((gap, mask))
        uncommented.extend((gap, mask if comment else value))
        tokens.append((start, value))
        previous = i
    code.append(scripts[previous:])
    uncommented.append(scripts[previous:])
    return ''.join(code), ''.join(uncommented), tokens


def _viewer_function_present(code, tokens):
    # Only the observed viewDoc declaration is accepted. Never rescan a suffix
    # for every function (adversarial nested declarations were quadratic).
    declarations = list(re.finditer(r'\bfunction\s+viewDoc\b', code))
    if len(declarations) != 1:
        return False
    starts = list(re.finditer(r'\bfunction\s{1,64}viewDoc\s{0,64}\([^()]{0,512}\)\s{0,64}\{', code))
    if len(starts) != 1:
        return False
    start, end, depth = starts[0].end(), None, 1
    for i in range(start, len(code)):
        if code[i] == '{':
            depth += 1
        elif code[i] == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    return end is not None and any(
        start <= position < end and re.fullmatch(r"""['"]/report/viewer\.do\??['"]""", value)
        for position, value in tokens)


def _node_assignments(code, uncommented, tokens):
    """Inspect node property writes with bounded cursors, not suffix regexes."""
    strings = {position: value for position, value in tokens if value.startswith(('"', "'"))}
    assignments = []
    for node in re.finditer(r'\bnode\d+\b', code):
        cursor = node.end()
        while cursor < len(code) and code[cursor].isspace():
            cursor += 1
        if cursor >= len(code) or code[cursor] not in '[.':
            continue
        dot = code[cursor] == '.'
        if dot:
            match = re.match(r'\.\w{1,64}', code[cursor:cursor + 65])
            if not match:
                _fail('VIEWER_NODE_UNSUPPORTED_WRITE')
            end = cursor + match.end()
            key = None
        else:
            end = code.find(']', cursor + 1, cursor + 66)
            if end < 0 or '[' in code[cursor + 1:end]:
                _fail('VIEWER_NODE_UNSUPPORTED_WRITE')
            match = re.fullmatch(r"""\[\s{0,16}['"](\w{1,32})['"]\s{0,16}\]""", uncommented[cursor:end + 1])
            if not match:
                _fail('VIEWER_NODE_UNSUPPORTED_WRITE')
            key = match[1]
            end += 1
        cursor = end
        while cursor < len(code) and code[cursor].isspace():
            cursor += 1
        op = re.match(r'[+\-*/%&|^!?<>=~]{1,8}', code[cursor:cursor + 8])
        if op is None:
            continue
        operator = op.group()
        if '=' not in operator and operator not in {'++', '--'}:
            continue
        if dot or operator != '=':
            _fail('VIEWER_NODE_UNSUPPORTED_WRITE')
        if key not in _FIELDS:
            continue
        cursor += 1
        while cursor < len(uncommented) and uncommented[cursor].isspace():
            cursor += 1
        literal = strings.get(cursor)
        if literal is None:
            _fail('VIEWER_NODE_NONLITERAL')
        end = cursor + len(literal)
        while end < len(uncommented) and uncommented[end].isspace():
            end += 1
        if end >= len(uncommented) or uncommented[end] != ';':
            _fail('VIEWER_NODE_NONLITERAL')
        assignments.append((node.group(), key, literal))
    return assignments


def parse_viewer_nodes(html, receipt_id, corp_code):
    """Read literal records without executing JS; DART reuses node variables."""
    if _corp_ids(html) != {corp_code}:
        _fail('MAIN_CORP_MISMATCH')
    scripts = '\n'.join(_tree(html).xpath('//script/text()'))
    code, uncommented, tokens = _lex_js(scripts)
    if not _viewer_function_present(code, tokens):
        _fail('VIEWER_FUNCTION_UNCONFIRMED')
    assignments = _node_assignments(code, uncommented, tokens)
    records, current, variable = [], None, None
    for var, key, literal in assignments:
        if key not in _FIELDS:
            continue
        if key == 'text':
            if current is not None:
                records.append(current)
            current, variable = {}, var
        if current is None or variable != var or key in current:
            _fail('VIEWER_NODE_AMBIGUOUS')
        try:
            value = json.loads(literal)
        except (ValueError, TypeError):
            _fail('VIEWER_NODE_NONLITERAL')
        if not isinstance(value, str):
            _fail('VIEWER_NODE_NONLITERAL')
        current[key] = value
    if current is not None:
        records.append(current)
    if not records:
        _fail('VIEWER_NODES_MISSING')
    identities = set()
    for record in records:
        if set(record) != set(_FIELDS) or record['rcpNo'] != receipt_id:
            _fail('VIEWER_NODE_INCOMPLETE')
        for key in ('rcpNo', 'dcmNo', 'eleId', 'offset', 'length'):
            if not re.fullmatch(r'\d{1,14}', record[key]) or int(record[key]) > (10**14 if key == 'rcpNo' else 100_000_000):
                _fail('VIEWER_NODE_RANGE_INVALID')
        if int(record['length']) == 0 or not re.fullmatch(r'dart\d{1,2}\.xsd', record['dtd']):
            _fail('VIEWER_NODE_RANGE_INVALID')
        identity = (record['dcmNo'], record['eleId'])
        if identity in identities:
            _fail('VIEWER_NODE_DUPLICATE')
        identities.add(identity)
        record['viewer_url'] = BASE + '/report/viewer.do?' + urlencode({k: record[k] for k in _FIELDS if k != 'text'})
    return records


def _dates(text):
    values = re.findall(r'(?<!\d)(\d{4})\s*(?:년|[-.])\s*(\d{1,2})\s*(?:월|[-.])\s*(\d{1,2})(?!\d)\s*일?', text)
    try:
        return [date(*map(int, v)) for v in values]
    except ValueError:
        _fail('COVER_DATE_INVALID')


def parse_cover_metadata(html):
    root = _tree(html)
    headings = {_KINDS[k] for k in _KINDS if any(_compact(_text(n)) == k for n in root.xpath('//h1|//h2|//p|//td'))}
    if len(headings) != 1:
        _fail('COVER_KIND_AMBIGUOUS')
    rows = [[_text(c) for c in r.xpath('./td|./th')] for r in root.xpath('//tr')]
    starts = [i for i, r in enumerate(rows) if r and _compact(r[0]) == '사업연도']
    if len(starts) != 1:
        _fail('COVER_PERIOD_MISSING')
    i = starts[0]
    period_rows = rows[i:i + 2]
    start = [d for r in period_rows if '부터' in _compact(''.join(r)) for d in _dates(' '.join(r))]
    end = [d for r in period_rows if '까지' in _compact(''.join(r)) for d in _dates(' '.join(r))]
    names = [r[1:] for r in rows if r and _compact(r[0]).rstrip(':') == '회사명']
    submitted = [d for r in rows if r and _compact(r[0]) == '한국거래소귀중' for d in _dates(' '.join(r[1:]))]
    if len(start) != 1 or len(end) != 1 or start[0] > end[0] or len(names) != 1 or not ' '.join(names[0]).strip() or len(submitted) != 1:
        _fail('COVER_METADATA_AMBIGUOUS')
    if end[0] > submitted[0]:
        _fail('COVER_PERIOD_AFTER_PUBLICATION')
    return {'kind': headings.pop(), 'period_start': start[0], 'period_end': end[0],
            'submitted_date': submitted[0], 'legal_name': ' '.join(names[0])}


def _scope_body(html, scope, scan=None):
    scan = scan or _section_scan(html)
    if scan is not None:
        title = '연결재무상태표' if scope == 'consolidated' else '재무상태표'
        if not any(scan.text(n, compact=True) == title for n in
                   scan.nodes({'h1', 'h2', 'h3', 'p', 'caption', 'td'})):
            _fail('SCOPE_BODY_UNVERIFIED')
        for table in scan.nodes({'table'}):
            text = scan.text(table, compact=True)
            if '자산' in text and '부채' in text and any(
                    re.fullmatch(r'\(?-?\d[\d,.]*\)?', scan.text(n, compact=True))
                    for n in scan.nodes({'td', 'th'}, table)):
                scan.check()
                return
        _fail('SCOPE_FINANCIAL_TABLE_MISSING')
    root = _tree(html)
    title = '연결재무상태표' if scope == 'consolidated' else '재무상태표'
    headings = [_compact(_text(n)) for n in root.xpath('//h1|//h2|//h3|//p|//caption|//td')]
    if title not in headings:
        _fail('SCOPE_BODY_UNVERIFIED')
    for table in root.xpath('//table'):
        text = _compact(_text(table))
        cells = [_compact(_text(n)) for n in table.xpath('.//td|.//th')]
        if '자산' in text and '부채' in text and any(re.fullmatch(r'\(?-?\d[\d,.]*\)?', c) for c in cells):
            return
    _fail('SCOPE_FINANCIAL_TABLE_MISSING')


def _consolidated_not_applicable(body):
    """Only an explicit, otherwise empty official consolidated statement.

    A fetch failure, missing table, search snippet, or unrelated footnote is not
    permission to substitute standalone financials.
    """
    if len(body.encode('utf-8')) > 16384:
        return False
    text = _compact(_text(_tree(body)))
    return bool(re.fullmatch(
        r'\d+\.연결재무제표(?:당사는)?(?:보고서작성기준일현재)?'
        r'해당사항(?:이)?없(?:습니다\.?|음\.?)', text))


def _section_scan(body):
    return SectionHTML(body) if len(body.encode('utf-8')) > _LIMIT else None


def _provenance(body, node, scan=None):
    scan = scan or _section_scan(body)
    preview = scan.text(scan.root, limit=1200) if scan else _text(_tree(body))[:1200]
    raw = body.encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    if scan:
        scan.check()
    return {'url': node['viewer_url'], 'tuple': {k: node[k] for k in _FIELDS if k != 'text'},
            'utf8_bytes': len(raw), 'sha256': digest,
            'preview': preview, 'preview_not_complete': True}


def _section_scope(body, scope, *, notes=False, scan=None):
    """Reject conflicting scope; never manufacture missing section context."""
    opposite = ({'재무상태표', '재무제표주석'} if scope == 'consolidated'
                else {'연결재무상태표', '연결재무제표주석'})
    scan = scan or _section_scan(body)
    if scan:
        title = '연결재무제표주석' if scope == 'consolidated' else '재무제표주석'
        found = False
        for node in scan.nodes({'h1', 'h2', 'h3', 'p', 'caption', 'td'}):
            heading = re.sub(r'^\d+\.', '', scan.text(node, compact=True))
            scan.check()
            if heading in opposite:
                _fail('SECTION_SCOPE_MISMATCH')
            found = found or heading == title
        scan.check()
        if notes and not found:
            _fail('NOTES_BODY_UNVERIFIED')
        return
    headings = {_compact(_text(n)) for n in _tree(body).xpath('//h1|//h2|//h3|//p|//caption|//td')}
    headings = {re.sub(r'^\d+\.', '', h) for h in headings}
    if headings & opposite:
        _fail('SECTION_SCOPE_MISMATCH')
    if notes:
        title = '연결재무제표주석' if scope == 'consolidated' else '재무제표주석'
        if title not in headings:
            _fail('NOTES_BODY_UNVERIFIED')


def _family(html, row):
    """Literal official edition evidence only; never attachment or receipt order."""
    selectors = _tree(html).xpath('//select[@id="family"]')
    if not selectors:
        _fail('FAMILY_MISSING')
    if len(selectors) != 1:
        _fail('FAMILY_INTEGRITY_CONFLICT')
    options = selectors[0].xpath('./option')
    # Official UI prompt is not an edition. Accept it only once, first, with
    # the observed literal value and whitespace-normalized label.
    if (options and options[0].get('value') == 'null'
            and _text(options[0]) == '+본문선택+'):
        options = options[1:]
    if not 1 <= len(options) <= 100:
        _fail('FAMILY_INTEGRITY_CONFLICT')
    members = []
    for option in options:
        value = re.fullmatch(r'rcpNo=(\d{14})', option.get('value', ''))
        label = re.fullmatch(r'(\d{4}\.\d{2}\.\d{2})\s+(\[정정\]\s*)?(사업보고서|반기보고서|분기보고서)', _text(option))
        if not value or not label:
            _fail('FAMILY_INTEGRITY_CONFLICT')
        try:
            submitted = date.fromisoformat(label[1].replace('.', '-'))
        except ValueError:
            _fail('FAMILY_INTEGRITY_CONFLICT')
        members.append({'receipt_id': value[1], 'submitted_date': submitted.isoformat(),
                        'kind': _KINDS[label[3]], 'is_correction': bool(label[2])})
    if (len({m['receipt_id'] for m in members}) != len(members)
            or any(m['kind'] != row['kind'] for m in members)
            or members[-1]['is_correction']
            or any(not m['is_correction'] for m in members[:-1])):
        _fail('FAMILY_INTEGRITY_CONFLICT')
    dates = [m['submitted_date'] for m in members]
    if any(a < b for a, b in pairwise(dates)):
        _fail('FAMILY_INTEGRITY_CONFLICT')
    current = next((m for m in members if m['receipt_id'] == row['receipt_id']), None)
    if (current is None or current['submitted_date'] != row['submitted_date'].isoformat()
            or current['is_correction'] != row['is_correction']):
        _fail('FAMILY_INTEGRITY_CONFLICT')
    # Same-day editions are ordered only when the official list order and the
    # sequential receipt numbers agree (newest first); otherwise stay unresolved.
    for newer, older in pairwise(members):
        if newer['submitted_date'] == older['submitted_date'] and not newer['receipt_id'] > older['receipt_id']:
            _fail('FAMILY_SAME_DAY_UNRESOLVED')
    return members


def _resolve_lineage(rows):
    by_id = {row['receipt_id']: row for row in rows}
    for row in rows:
        members = row.get('family_members', [])
        if row.get('correction_type') != 'body' or not members:
            continue
        index = next(i for i, m in enumerate(members) if m['receipt_id'] == row['receipt_id'])
        parent_evidence = members[index + 1]
        parent = by_id.get(parent_evidence['receipt_id'])
        if parent is None:
            row['lineage_errors'].append('FAMILY_PARENT_NOT_ACQUIRED')
            continue
        if (parent['submitted_date'].isoformat() != parent_evidence['submitted_date']
                or parent['kind'] != parent_evidence['kind']
                or parent['is_correction'] != parent_evidence['is_correction']):
            row['lineage_errors'].append('FAMILY_INTEGRITY_CONFLICT')
            continue
        if not all(r.get('scope_verified') and r.get('body_status') == 'available' for r in (row, parent)):
            row['lineage_errors'].append('FAMILY_PARENT_OR_BODY_UNVERIFIED')
            continue
        if (any(row[k] != parent[k] for k in ('kind', 'period_start', 'period_end', 'scope'))
                or row['cover_submitted_date'].isoformat() not in {m['submitted_date'] for m in members}
                or 'FAMILY_INTEGRITY_CONFLICT' in parent.get('lineage_errors', [])):
            row['lineage_errors'].append('FAMILY_INTEGRITY_CONFLICT')
            continue
        row['amendment_of'] = parent['receipt_id']

    # An immediate edge is insufficient when an ancestor remains unresolved.
    def verified(row, seen):
        if not row['is_correction']:
            return True
        if row['receipt_id'] in seen or not row.get('amendment_of'):
            return False
        return verified(by_id[row['amendment_of']], seen | {row['receipt_id']})
    for row in rows:
        row['lineage_verified'] = verified(row, set())


def _fragment_state(row, parent):
    return {'version': 'dart-note-fragments-v1', 'basis': 'title-label-presence-v1',
            'parent_key': parent['dcmNo'] + ':' + parent['eleId'], 'main_sha256': row['main_sha256'],
            'child_total': None, 'eligible_total': None, 'planned_keys': [], 'requested_keys': [],
            'acquired_keys': [], 'budget_omitted_keys': [], 'unselected_count': None,
            'failures': [], 'full_notes_acquired': False, 'stop_reason': None}


def _supplementary_failure(code):
    return code.startswith('HTTP_') or code in {'RESPONSE_BYTES_EXCEEDED', 'CALL_BUDGET_EXHAUSTED', 'TOTAL_TIMEOUT'}


def _finish_fragment_state(state, stopped):
    state['budget_omitted_keys'] = [key for key in state['planned_keys'] if key not in state['requested_keys']]
    if state['child_total'] is not None:
        state['unselected_count'] = state['child_total'] - len(state['planned_keys'])
    if stopped:
        state['stop_reason'] = stopped
        failure = {'child_key': None, 'code': stopped}
        if failure not in state['failures']:
            state['failures'].append(failure)


def _fragment_candidates(row, parent, main_html, corp_code):
    from prism_core.dart_viewer_tree import parse_viewer_tree
    from prism_core.filing_materiality import material_topics

    graph = parse_viewer_tree(main_html, row['receipt_id'], corp_code)
    state = row['note_fragment_selection']
    nodes = {node['key']: node for node in graph['nodes']}
    verified = nodes[state['parent_key']]
    label, opposite = ('연결', '별도') if row['scope'] == 'consolidated' else ('별도', '연결')
    prefix = '연결' if row['scope'] == 'consolidated' else ''
    if (graph['main_sha256'] != state['main_sha256']
            or any(verified[key] != parent[key] for key in _FIELDS)
            or not re.fullmatch(r'\d+\.' + prefix + '재무제표주석', _compact(verified['text']))):
        raise ValueError
    children = [nodes[key] for key in verified['children_keys']]
    eligible = [child for child in children
                if child['parent_key'] == state['parent_key'] and child['dcmNo'] == verified['dcmNo']
                and child['rcpNo'] == row['receipt_id']
                and opposite not in child['text'] and '개별' not in child['text']
                and re.fullmatch(r'\d{1,3}(?:-\d{1,3})*[.)].+\(' + label + r'\)', _compact(child['text']))]
    state.update(child_total=len(children), eligible_total=len(eligible))
    return sorted(eligible, key=lambda child: not bool(material_topics('', (child['text'],))))


async def collect_dart_periodic_filings(*, corp_code, decision_at, start_date, scope,
                                       client_factory=None, max_pages=3, max_filings=8,
                                       max_calls=28, timeout_seconds=90,
                                       include_section_bodies=False, _metrics=None):
    """Return provenance, optionally exact bounded viewer-section HTML.

    Opt-in bodies are section-local originals, not a reconstructed whole filing.
    Individual sections retain the 2 MiB cap; all requests share the 8 MiB/call
    budgets. A missing or oversized section is a gap, never a truncated success.
    """
    now = datetime.now(timezone.utc)
    try:
        cutoff = decision_at.astimezone(_ZONE).date() if isinstance(decision_at, datetime) else None
    except (ValueError, OverflowError):
        raise ValueError('INVALID_ACQUISITION_POLICY') from None
    if (not isinstance(corp_code, str) or not re.fullmatch(r'\d{8}', corp_code)
            or not isinstance(decision_at, datetime) or decision_at.utcoffset() is None
            or decision_at > now or type(start_date) is not date
            or start_date > cutoff
            or not isinstance(scope, str) or scope not in {'consolidated', 'standalone'}
            or type(include_section_bodies) is not bool
            or any(type(v) is not int or not 1 <= v <= cap for v, cap in ((max_pages, 5), (max_filings, 20), (max_calls, 64)))
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 180):
        raise ValueError('INVALID_ACQUISITION_POLICY')
    metrics = _metrics if _metrics is not None else {}
    metrics.update(calls=0, response_bytes=0)
    metrics.pop('transport_retries', None)
    out = {'status': 'FAILED', 'query': {'corp_code': corp_code, 'decision_at': decision_at.isoformat(),
            'start_date': start_date.isoformat(), 'end_date': cutoff.isoformat(), 'scope': scope},
           'observed_at': now.isoformat(), 'coverage': {'complete_within_query': False,
            'global_complete': False, 'expected_count': None, 'seen_count': 0}, 'filings': [],
           'limitations': ['QUERY_WINDOW_ONLY', 'CURRENT_RETRIEVAL_NOT_HISTORICAL_SNAPSHOT',
                          'DATE_ONLY_PUBLICATION', 'SELECTED_SECTIONS_NOT_FULL_DOCUMENT'],
           'metrics': metrics, 'historical_version_verified': False,
           'fact_validated': False, 'errors': []}
    rows, candidates = [], []
    section_nodes, main_by_receipt = {}, {}
    page_complete, empty = False, False

    async def acquire():
        nonlocal page_complete, empty
        factory = client_factory or httpx.AsyncClient
        async with factory(timeout=15, follow_redirects=False, trust_env=False) as client:
            last_request_at = None

            async def request_once(method, url, *, _on_request=None, _body_limit=_LIMIT, **kwargs):
                nonlocal last_request_at
                if out['metrics']['calls'] >= max_calls:
                    _fail('CALL_BUDGET_EXHAUSTED')
                if out['metrics']['response_bytes'] >= _TOTAL_LIMIT:
                    _fail('RESPONSE_BYTES_EXCEEDED')
                if include_section_bodies and last_request_at is not None:
                    delay = 0.2 - (asyncio.get_running_loop().time() - last_request_at)
                    if delay > 0:
                        await asyncio.sleep(delay)
                last_request_at = asyncio.get_running_loop().time()
                out['metrics']['calls'] += 1
                if _on_request is not None:
                    _on_request()
                try:
                    client.cookies.clear()
                    async with client.stream(method, url, headers={'Accept-Encoding': 'identity'}, **kwargs) as response:
                        if response.status_code != 200:
                            _fail('HTTP_STATUS_FAILURE')
                        if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                            _fail('HTTP_ENCODING_REJECTED')
                        body = bytearray()
                        # MockTransport may supply an already-buffered response.
                        chunks = response.aiter_bytes() if response.is_stream_consumed else response.aiter_raw()
                        async for chunk in chunks:
                            out['metrics']['response_bytes'] += len(chunk)
                            if out['metrics']['response_bytes'] > _TOTAL_LIMIT:
                                _fail('RESPONSE_BYTES_EXCEEDED')
                            if len(body) + len(chunk) > _body_limit:
                                raise _SectionBodyLimitExceeded('RESPONSE_BYTES_EXCEEDED')
                            body.extend(chunk)
                        try:
                            return body.decode('utf-8')
                        except UnicodeDecodeError:
                            _fail('HTTP_UTF8_INVALID')
                except (httpx.RemoteProtocolError, httpx.ConnectError):
                    raise
                except httpx.HTTPError:
                    _fail('HTTP_TRANSPORT_FAILURE')

            async def request(method, url, *, _on_request=None, **kwargs):
                for attempt in range(2):
                    try:
                        return await request_once(method, url, _on_request=_on_request, **kwargs)
                    except (httpx.RemoteProtocolError, httpx.ConnectError):
                        if attempt or not include_section_bodies or method != 'GET':
                            _fail('HTTP_TRANSPORT_FAILURE')
                        if out['metrics']['calls'] >= max_calls:
                            _fail('CALL_BUDGET_EXHAUSTED')
                        out['metrics']['transport_retries'] = out['metrics'].get('transport_retries', 0) + 1
                _fail('HTTP_TRANSPORT_FAILURE')

            expected = None
            for number in range(1, max_pages + 1):
                data = {'currentPage': str(number), 'maxResults': '100', 'maxLinks': '10',
                        'sort': 'date', 'series': 'desc', 'textCrpCik': corp_code, 'pageGubun': 'corp',
                        'startDate': start_date.strftime('%Y%m%d'), 'endDate': cutoff.strftime('%Y%m%d'),
                        'publicType': ['A001', 'A002', 'A003']}
                page = parse_catalog_page(await request('POST', BASE + '/dsab001/searchCorp.ax', data=data), corp_code)
                stamp = page['total_pages'], page['total_count']
                if ((page['page'] != number and page['total_count'])
                        or (expected is not None and expected != stamp)):
                    _fail('CATALOG_PAGE_INCONSISTENT')
                expected = stamp
                out['coverage']['expected_count'] = page['total_count']
                if any(not start_date <= r['submitted_date'] <= cutoff for r in page['rows']):
                    _fail('CATALOG_DATE_OUTSIDE_QUERY')
                if {r['receipt_id'] for r in rows} & {r['receipt_id'] for r in page['rows']}:
                    _fail('CATALOG_DUPLICATE_RECEIPT')
                rows.extend(page['rows'])
                if number >= page['total_pages']:
                    page_complete = len(rows) == page['total_count']
                    empty = page_complete and not rows
                    break
            for row in rows:
                row.update(period_start=None, period_end=None, scope=None, body_status='unread',
                           scope_verified=False, sections={}, errors=[], body_coverage='selected_sections',
                           cover_submitted_date=None, amendment_of=None, family_members=[], lineage_errors=[])
                if include_section_bodies:
                    row['section_delivery'] = {'status': 'UNAVAILABLE',
                        'missing_sections': ['financial_statements', 'financial_notes'], 'errors': []}
                if row['correction_type'] == 'attachment':
                    row['errors'].append('ATTACHMENT_CONTENT_NOT_ACQUIRED')
            # Attachment amendments may contain material audit/charter changes,
            # but are not whole periodic financial-body editions. Preserve their
            # explicit gaps without spending periodic-body acquisition slots.
            ordered = sorted((r for r in rows if r['correction_type'] != 'attachment'),
                             key=lambda r: (r['report_period_label'], r['submitted_date']), reverse=True)
            for row in ordered[:max_filings]:
                try:
                    main_html = await request('GET', row['source_url'])
                    row['main_sha256'] = hashlib.sha256(main_html.encode('utf-8')).hexdigest()
                    if include_section_bodies:
                        main_by_receipt[row['receipt_id']] = main_html
                    nodes = parse_viewer_nodes(main_html, row['receipt_id'], corp_code)
                    try:
                        row['family_members'] = _family(main_html, row)
                    except _SourceError as exc:
                        row['lineage_errors'].append(str(exc))
                    covers = [n for n in nodes if _compact(n['text']) in _KINDS]
                    if len(covers) != 1:
                        _fail('COVER_NODE_AMBIGUOUS')
                    body = await request('GET', covers[0]['viewer_url'])
                    meta = parse_cover_metadata(body)
                    cover_date = meta.pop('submitted_date')
                    if (meta['kind'] != row['kind']
                            or (cover_date > row['submitted_date'] if row['is_correction']
                                else cover_date != row['submitted_date'])
                            or meta['period_end'].strftime('%Y.%m') != row['report_period_label']):
                        _fail('COVER_CATALOG_MISMATCH')
                    row.update(meta, scope=scope, cover_submitted_date=cover_date)
                    row['sections']['cover'] = _provenance(body, covers[0])
                    label = '연결재무제표' if scope == 'consolidated' else '재무제표'
                    sections = [n for n in nodes if re.fullmatch(r'\d+\.' + label, _compact(n['text']))]
                    if len(sections) != 1:
                        _fail('SCOPE_NODE_AMBIGUOUS')
                    if include_section_bodies and sections[0]['dcmNo'] != covers[0]['dcmNo']:
                        _fail('SECTION_DOCUMENT_MISMATCH')
                    body = await request('GET', sections[0]['viewer_url'],
                                         _body_limit=MAX_HTML_BYTES if include_section_bodies else _LIMIT)
                    scan = _section_scan(body)
                    if scope == 'consolidated' and _consolidated_not_applicable(body):
                        row['scope_absence_evidence'] = {
                            'scope': 'consolidated', 'reason': 'OFFICIAL_NOT_APPLICABLE',
                            'receipt_id': row['receipt_id'],
                            **_provenance(body, sections[0], scan),
                        }
                        _fail('CONSOLIDATED_NOT_APPLICABLE')
                    _scope_body(body, scope, scan)
                    if include_section_bodies:
                        _section_scope(body, scope, scan=scan)
                    row['sections']['financial_statements'] = _provenance(body, sections[0], scan)
                    if include_section_bodies:
                        row['sections']['financial_statements']['html'] = body
                        section_nodes[row['receipt_id']] = [n for n in nodes if re.fullmatch(
                            r'\d+\.' + label + '주석', _compact(n['text']))]
                    row.update(body_status='available', scope_verified=True)
                except (_SourceError, SectionHTMLLimit) as exc:
                    row.update(body_status='unavailable')
                    row['errors'].append(str(exc))
            # Supplementary notes cannot consume calls needed to establish the
            # catalog's latest available edition. Acquire them only afterwards.
            if include_section_bodies:
                # Resolve tentative priorities from the same verified core
                # evidence, without changing final lineage/blocker admission.
                priority_rows = [{**r, 'lineage_errors': list(r['lineage_errors'])} for r in rows]
                _resolve_lineage(priority_rows)
                priority_candidates = [FilingCandidate(
                    r['receipt_id'], f'DART:{corp_code}', r['source_url'], r['kind'],
                    r.get('period_start'), r.get('period_end'), r.get('scope'),
                    r['submitted_date'], True, r.get('amendment_of'), r.get('body_status', 'unread'))
                    for r in priority_rows if not r['is_correction'] or r['lineage_verified']]
                priorities = select_periodic_filings(priority_candidates, entity_id=f'DART:{corp_code}',
                    scope=scope, decision_at=decision_at, market_timezone='Asia/Seoul', listing_complete=False)
                selected_ids = [priorities['primary_id'], priorities['annual_supplement_id']]
                note_order = sorted(ordered[:max_filings], key=lambda r: (
                    selected_ids.index(r['receipt_id']) if r['receipt_id'] in selected_ids else 2))
                selected_rows = [r for r in note_order if r['scope_verified'] and r['receipt_id'] in selected_ids]
                oversized, stop_reason = [], None

                async def read_notes(row, *, selected):
                    nonlocal stop_reason
                    try:
                        notes = section_nodes[row['receipt_id']]
                        if len(notes) != 1:
                            _fail('NOTES_NODE_AMBIGUOUS')
                        if notes[0]['dcmNo'] != row['sections']['financial_statements']['tuple']['dcmNo']:
                            _fail('SECTION_DOCUMENT_MISMATCH')
                        body = await request('GET', notes[0]['viewer_url'], _body_limit=MAX_HTML_BYTES)
                        scan = _section_scan(body)
                        _section_scope(body, scope, notes=True, scan=scan)
                        row['sections']['financial_notes'] = {**_provenance(body, notes[0], scan), 'html': body}
                    except _SectionBodyLimitExceeded as exc:
                        row['section_delivery']['errors'].append(str(exc))
                        if selected:
                            row['note_fragment_selection'] = _fragment_state(row, notes[0])
                            oversized.append((row, notes[0]))
                    except (_SourceError, SectionHTMLLimit) as exc:
                        row['section_delivery']['errors'].append(str(exc))
                        if selected and _supplementary_failure(str(exc)):
                            stop_reason = str(exc)
                            parent = section_nodes[row['receipt_id']][0]
                            row['note_fragment_selection'] = _fragment_state(row, parent)

                for row in selected_rows:
                    if stop_reason:
                        row['section_delivery']['errors'].append('SUPPLEMENTARY_STOPPED')
                        continue
                    await read_notes(row, selected=True)

                queues, planned = [], []
                if not stop_reason:
                    for row, parent in oversized:
                        try:
                            children = _fragment_candidates(row, parent, main_by_receipt[row['receipt_id']], corp_code)
                            queues.append((row, children))
                        except (KeyError, TypeError, ValueError):
                            row['note_fragment_selection']['failures'].append(
                                {'child_key': None, 'code': 'DART_NOTE_GRAPH_UNVERIFIED'})
                    unique = set()
                    while len(planned) < 2 and any(children for _, children in queues):
                        for row, children in queues:
                            if not children or len(planned) >= 2:
                                continue
                            child = children.pop(0)
                            identity = (row['receipt_id'], child['key'])
                            if identity in unique:
                                continue
                            unique.add(identity)
                            row['note_fragment_selection']['planned_keys'].append(child['key'])
                            planned.append((row, child))
                for row, child in planned:
                    state = row['note_fragment_selection']
                    if stop_reason:
                        continue
                    if metrics['calls'] >= max_calls:
                        stop_reason = 'CALL_BUDGET_EXHAUSTED'
                        continue
                    if metrics['response_bytes'] >= _TOTAL_LIMIT:
                        stop_reason = 'RESPONSE_BYTES_EXCEEDED'
                        continue
                    def started(state=state, key=child['key']):
                        if key not in state['requested_keys']:
                            state['requested_keys'].append(key)

                    try:
                        body = await request('GET', child['viewer_url'], _on_request=started,
                                             _body_limit=MAX_HTML_BYTES)
                        fragment = {**_provenance(body, child), 'html': body,
                                    'parent_key': state['parent_key'], 'child_key': child['key']}
                        row.setdefault('note_fragments', []).append(fragment)
                        row['note_main_html'] = main_by_receipt[row['receipt_id']]
                        state['acquired_keys'].append(child['key'])
                    except _SectionBodyLimitExceeded as exc:
                        state['failures'].append({'child_key': child['key'], 'code': str(exc)})
                    except (_SourceError, SectionHTMLLimit) as exc:
                        state['failures'].append({'child_key': child['key'], 'code': str(exc)})
                        if _supplementary_failure(str(exc)):
                            stop_reason = str(exc)
                for row in selected_rows:
                    if 'note_fragment_selection' in row:
                        _finish_fragment_state(row['note_fragment_selection'], stop_reason)
                if not stop_reason:
                    for row in note_order:
                        if row['scope_verified'] and row['receipt_id'] not in selected_ids:
                            await read_notes(row, selected=False)

    try:
        await asyncio.wait_for(acquire(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        out['errors'].append('TOTAL_TIMEOUT')
    except _SourceError as exc:
        out['errors'].append(str(exc))
    except ValueError:
        out['errors'].append('ACQUISITION_INVALID_RESPONSE')
    except httpx.HTTPError:
        out['errors'].append('HTTP_TRANSPORT_FAILURE')
    except Exception:  # noqa: BLE001 -- public boundary must redact provider exceptions
        # CancelledError is a BaseException and deliberately propagates.
        out['errors'].append('ACQUISITION_FAILURE')
    _resolve_lineage(rows)
    for row in rows:
        if row['is_correction'] and not row['lineage_verified']:
            continue
        candidates.append(FilingCandidate(row['receipt_id'], f'DART:{corp_code}', row['source_url'],
            row['kind'], row.get('period_start'), row.get('period_end'), row.get('scope'),
            row['submitted_date'], True, row.get('amendment_of'), row.get('body_status', 'unread')))
    selection = select_periodic_filings(candidates, entity_id=f'DART:{corp_code}', scope=scope,
        decision_at=decision_at, market_timezone='Asia/Seoul', listing_complete=False)
    primary = next((r for r in rows if r['receipt_id'] == selection['primary_id']), None)
    corrections = [r for r in rows if r['is_correction'] and not r['lineage_verified']]
    unresolved = [r for r in rows if r.get('body_status') != 'available'
                  or r['submitted_date'] == cutoff or r in corrections
                  or 'FAMILY_INTEGRITY_CONFLICT' in r.get('lineage_errors', [])]

    def blocks(row, chosen):
        if (chosen is None or 'FAMILY_INTEGRITY_CONFLICT' in row.get('lineage_errors', [])
                or any(e in {'COVER_CATALOG_MISMATCH', 'MAIN_CORP_MISMATCH'} for e in row.get('errors', []))):
            return True
        if row.get('period_end') is not None and chosen.get('period_end') is not None:
            return row['period_end'] >= chosen['period_end']
        # A literal catalog month proves only strict oldness, never a day/period.
        return not row.get('report_period_label') or row['report_period_label'] >= chosen['report_period_label']

    blockers = [r['receipt_id'] for r in unresolved if blocks(r, primary)]
    selection['unresolved_corrections'] = [r['receipt_id'] for r in corrections]
    selection['attachment_gaps'] = [r['receipt_id'] for r in rows if r['correction_type'] == 'attachment']
    selection['unresolved_older_corrections'] = [r['receipt_id'] for r in corrections if not blocks(r, primary)]
    annual = next((r for r in rows if r['receipt_id'] == selection['annual_supplement_id']), None)
    if annual and any(r['kind'] == 'annual' and blocks(r, annual) for r in unresolved):
        selection['annual_supplement_id'] = None
        selection['reasons'].append('ANNUAL_SUPPLEMENT_ACQUISITION_INCOMPLETE')
    if not page_complete:
        blockers.append('PAGE_COVERAGE_UNCONFIRMED')
    if blockers:
        reason = 'CORRECTION_LINEAGE_UNRESOLVED' if any(r['receipt_id'] in blockers for r in corrections) else 'ACQUISITION_INCOMPLETE'
        selection.update(status=reason, best_known_candidate_id=selection['latest_candidate_id'],
                         primary_id=None, latest_candidate_id=None, annual_supplement_id=None,
                         latest_confirmed=False, blocked_by=sorted(set(blockers)))
        selection['reasons'].append(reason)
    out['selection'] = selection
    final_ids = {selection['primary_id'], selection['annual_supplement_id']} - {None}
    for row in rows:
        if 'note_fragment_selection' in row:
            if 'TOTAL_TIMEOUT' in out['errors']:
                _finish_fragment_state(row['note_fragment_selection'], 'TOTAL_TIMEOUT')
            if row['receipt_id'] not in final_ids:
                row.pop('note_fragments', None)
                row.pop('note_main_html', None)
                row['note_fragment_selection']['discarded_due_to_final_selection'] = True
    out['coverage'].update(complete_within_query=page_complete, seen_count=len(rows))
    out['status'] = ('EMPTY' if empty else 'COMPLETE_WITHIN_QUERY'
                     if page_complete and not unresolved and not corrections else 'PARTIAL' if rows else 'FAILED')
    if include_section_bodies:
        for row in rows:
            delivery = row.get('section_delivery')
            if delivery is None:
                continue
            missing = [name for name in ('financial_statements', 'financial_notes')
                       if 'html' not in row['sections'].get(name, {})]
            delivery['missing_sections'] = missing
            delivery['status'] = 'AVAILABLE' if not missing else 'UNAVAILABLE' if len(missing) == 2 else 'PARTIAL'
            delivery['errors'] = sorted(set(delivery['errors'] + row['errors'] + out['errors']))
    out['filings'] = [{k: v.isoformat() if isinstance(v, date) else v for k, v in r.items()} for r in rows]
    return out
