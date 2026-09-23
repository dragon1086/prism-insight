import copy
import hashlib
from urllib.parse import urlencode

import pytest
from test_dart_viewer_tree import CORP, RECEIPT, node, page

from prism_core.dart_report_evidence import note_fragment_blocks, section_blocks
from prism_core.filing_html import parse_filing_html
from prism_core.filing_report_evidence import (
    compact_html_provenance,
    expand_html_provenance,
)


def section(ele, html):
    fields = {'rcpNo': RECEIPT, 'dcmNo': '123', 'eleId': str(ele), 'offset': '100',
              'length': '50', 'dtd': 'dart4.xsd'}
    return {'html': html, 'tuple': fields,
            'url': 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(fields),
            'sha256': hashlib.sha256(html.encode()).hexdigest(), 'utf8_bytes': len(html.encode())}


def fixture(scope='consolidated', *, parent_title=None, child_title=None, cover_title='사업보고서', financial_title=None):
    label = '연결' if scope == 'consolidated' else '별도'
    prefix = '연결' if scope == 'consolidated' else ''
    title = child_title or f'15-3. 차입금 및 약정사항 ({label})'
    builder = node('node1', 1, text=cover_title) + 'treeData.push(node1);'
    builder += node('node1', 2, text=financial_title or f'2. {prefix}재무제표') + 'treeData.push(node1);'
    builder += node('node1', 3, text=parent_title or f'3. {prefix}재무제표 주석') + "node1['children']=[];"
    builder += node('node2', 4, text=title) + "node1['children'].push(node2);treeData.push(node1);"
    main = page(builder)
    body = (f'<p>{title}</p><p>단위: 백만원</p>'
            '<table><tr><th>구분</th><th>차입금 한도</th></tr><tr><td>담보부 차입금</td><td>100</td></tr></table>'
            '<p>주1) 다만 약정 위반 시 조기상환 조건이 적용됩니다.</p>'
            '<p>차입금 약정은 위반하지 않았습니다.</p>')
    child = section(4, body)
    cover = section(1, '<p>사업보고서</p>')
    cover.pop('html')  # The existing collector retains only cover provenance.
    row = {'receipt_id': RECEIPT, 'scope': scope, 'scope_verified': True, 'body_status': 'available',
           'kind': 'annual', 'main_sha256': hashlib.sha256(main.encode()).hexdigest(),
           'sections': {'cover': cover, 'financial_statements': section(2, '<p>자산 부채</p>')}}
    return row, child, main


def call(row, child, main, **kwargs):
    return note_fragment_blocks(row, child, main_html=main, corp_code=kwargs.get('corp_code', CORP),
                                parent_key=kwargs.get('parent_key', '123:3'))


@pytest.mark.parametrize('title', ['별도 소송', '개별 소송', '연결 소송'])
def test_unknown_fragment_layout_scope_is_not_promoted_by_verified_parent(title):
    row, _, main = fixture()
    body = ('<p>15-3. 차입금 및 약정사항 (연결)</p>'
            f'<table class="nb"><tr><td colspan="2">{title}</td></tr>'
            '<tr><td>당기말</td><td>(단위:백만원)</td></tr></table>'
            '<table><tr><td>소송 충당부채</td><td>100</td></tr></table>')
    parsed = parse_filing_html(body)
    assert parsed['records'][-1]['scope'] == 'unknown'
    assert parsed['records'][-1]['context_incomplete'] is True
    assert call(row, section(4, body), main)[0] == []


@pytest.mark.parametrize('scope', ['consolidated', 'standalone'])
def test_verified_parent_scope_is_external_provenance_without_source_rewrite(scope, monkeypatch):
    row, child, main = fixture(scope)
    before = parse_filing_html(child['html'])
    originals = copy.deepcopy((row, child, main))
    captured = []
    from prism_core import dart_report_evidence as adapter
    group = adapter.material_html_records

    def capture(records):
        captured.extend(copy.deepcopy(records))
        return group(records)

    monkeypatch.setattr(adapter, 'material_html_records', capture)
    blocks, gaps = call(row, child, main)
    assert blocks
    assert 'DART_NOTE_FRAGMENT_PARTIAL_COVERAGE' in gaps
    assert (row, child, main) == originals
    assert [{k: v for k, v in r.items() if k not in {'scope', 'scope_context'}} for r in captured] == [
        {k: v for k, v in r.items() if k != 'scope'} for r in before['records']]
    assert {r['scope'] for r in before['records']} == {'unknown'}
    assert {r['scope'] for r in captured} == {scope}
    for block in blocks:
        provenance = block['provenance']
        context = provenance['scope_context']
        assert context == {
            'basis': 'DART_EXPLICIT_VIEWER_TREE', 'main_sha256': row['main_sha256'],
            'main_url': f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RECEIPT}',
            'parent_key': '123:3', 'child_key': '123:4',
            'parent_title': '3. 연결재무제표 주석' if scope == 'consolidated' else '3. 재무제표 주석',
            'child_title': f"15-3. 차입금 및 약정사항 ({'연결' if scope == 'consolidated' else '별도'})",
            'child_heading_paths': ['/html[1]/body[1]/p[1]'],
        }
        assert provenance['representation_sha256'] == child['sha256']
        assert provenance['section_path'][0] == context['child_title']
        assert context['parent_title'] not in provenance['section_path']
        assert expand_html_provenance(compact_html_provenance(provenance)) == provenance
        assert block['status'] == 'SOURCE_TEXT_NOT_FACT_VALIDATED'
    assert any('백만원' in b['provenance']['context_before'] and '조기상환' in b['provenance']['footnotes'] for b in blocks)
    assert section_blocks(row, child)[0] == []


@pytest.mark.parametrize('mutation', [
    {'main_sha256': '0' * 64}, {'receipt_id': '20260101000000'}, {'scope_verified': False},
    {'body_status': 'unavailable'}, {'scope': 'unknown'}, {'kind': 'quarterly'},
])
def test_row_validation_rejects_unverified_context(mutation):
    row, child, main = fixture()
    row.update(mutation)
    assert call(row, child, main)[0] == []


@pytest.mark.parametrize('options', [{'corp_code': '00999999'}, {'parent_key': '123:2'}, {'parent_key': '999:3'}])
def test_issuer_parent_relationship_required(options):
    assert call(*fixture(), **options)[0] == []


@pytest.mark.parametrize('options', [
    {'parent_title': '3. 연결재무제표'}, {'parent_title': '3. 재무제표 주석'},
    {'child_title': '15-3. 차입금 약정'}, {'child_title': '15-3. 차입금 약정 (별도)'},
    {'cover_title': '분기보고서'}, {'financial_title': '2. 재무제표'},
    {'child_title': '15-3. 별도 재무제표 (연결)'}, {'child_title': '15-3. 개별 차입금 (연결)'},
])
def test_title_contract_rejects_conflicting_or_missing_scope(options):
    assert call(*fixture(**options))[0] == []


@pytest.mark.parametrize('which', ['cover', 'financial_statements'])
@pytest.mark.parametrize('mutation', ['missing', 'document', 'offset', 'length', 'url', 'hash', 'bytes'])
def test_cover_and_financial_provenance_must_link_same_graph_document(which, mutation):
    row, child, main = fixture()
    if mutation == 'missing':
        del row['sections'][which]
    else:
        meta = row['sections'][which]
        if mutation in {'document', 'offset', 'length'}:
            meta['tuple'][{'document': 'dcmNo', 'offset': 'offset', 'length': 'length'}[mutation]] = '999'
            meta['url'] = 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(meta['tuple'])
        elif mutation == 'url':
            meta['url'] = 'https://example.com/'
        elif mutation == 'hash':
            meta['sha256'] = 'invalid'
        else:
            meta['utf8_bytes'] = -1
    assert call(row, child, main)[0] == []


@pytest.mark.parametrize('mutation', ['document', 'offset', 'length', 'url', 'hash', 'bytes', 'oversize'])
def test_child_source_and_tuple_are_not_reconstructed(mutation):
    row, child, main = fixture()
    if mutation in {'document', 'offset', 'length'}:
        child['tuple'][{'document': 'dcmNo', 'offset': 'offset', 'length': 'length'}[mutation]] = '999'
        child['url'] = 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(child['tuple'])
    elif mutation == 'url':
        child['url'] = 'https://example.com'
    elif mutation == 'hash':
        child['sha256'] = '0' * 64
    elif mutation == 'bytes':
        child['utf8_bytes'] += 1
    else:
        child = section(4, ' ' * (8 * 1024 * 1024 + 1))
    assert call(row, child, main)[0] == []


@pytest.mark.parametrize('scope, heading', [
    ('consolidated', '(1) 위험관리 (별도)'), ('standalone', '(1) 위험관리 (연결)'),
    ('consolidated', '(1) 별도 재무제표 (연결)'), ('standalone', '(1) 연결 재무제표 (별도)'),
    ('consolidated', '(1) 개별 재무제표 (연결)'), ('standalone', '(1) 개별 재무제표 (별도)'),
    ('consolidated', '(1) 재무상태표'), ('standalone', '(1) 손익계산서'),
    ('consolidated', '16. 다른 주석 (연결)'),
])
@pytest.mark.parametrize('following_record', ['', '<p>약정 차입금은 없습니다.</p>'])
def test_all_internal_heading_events_checked_even_without_following_record(scope, heading, following_record):
    row, child, main = fixture(scope)
    child = section(4, child['html'] + f'<p>{heading}</p>' + following_record)
    assert call(row, child, main)[0] == []


@pytest.mark.parametrize('body', ['<p>차입금 약정 조건입니다.</p>', '<p>다른 본문</p>{body}', '<p>16. 다른 주석 (연결)</p><p>차입금 조건입니다.</p>', ''])
def test_unaffiliated_missing_or_wrong_heading_rejects_entire_fragment(body):
    row, child, main = fixture()
    child = section(4, body.replace('{body}', child['html']))
    assert call(row, child, main)[0] == []


def test_heading_events_opt_in_retains_default_records_and_original_paths():
    body = '<p>15-3. 차입금 (연결)</p><p>(1) 만기 조건</p><p>조기상환 약정입니다.</p>'
    default = parse_filing_html(body)
    captured = parse_filing_html(body, _capture_headings=True)
    assert 'heading_events' not in default
    assert {k: v for k, v in captured.items() if k != 'heading_events'} == default
    assert captured['heading_events'] == [
        {'text': '15-3. 차입금 (연결)', 'source_path': '/html[1]/body[1]/p[1]', 'source_paths': ['/html[1]/body[1]/p[1]'],
         'level': 4, 'section_path': ['15-3. 차입금 (연결)'], 'scope': 'unknown'},
        {'text': '(1) 만기 조건', 'source_path': '/html[1]/body[1]/p[2]', 'source_paths': ['/html[1]/body[1]/p[2]'],
         'level': 5, 'section_path': ['15-3. 차입금 (연결)', '(1) 만기 조건'], 'scope': 'unknown'},
    ]


def test_heading_event_limit_is_opt_in_and_discards_partial_admission():
    body = '<p>15-3. 차입금 (연결)</p>' * 2001 + '<p>차입금 약정입니다.</p>'
    assert parse_filing_html(body)['records']
    result = parse_filing_html(body, _capture_headings=True)
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert result['errors'] == ['HTML_HEADING_LIMIT']
    assert result['records'] == []
    assert result['heading_events'] == []




@pytest.mark.parametrize('main', ['', None, '<script>var treeData=[];</script>'])
def test_missing_or_unsupported_main_fails_closed(main):
    row, child, _ = fixture()
    assert call(row, child, main) == ([], ['DART_NOTE_CONTEXT_UNVERIFIED'])


@pytest.mark.parametrize('scope', ['consolidated', 'standalone'])
def test_matching_internal_qualified_financial_title_is_permitted(scope):
    row, child, main = fixture(scope)
    label = '연결' if scope == 'consolidated' else '별도'
    child = section(4, child['html'] + f'<p>(1) 재무상태표 ({label})</p><p>차입금 약정 조건입니다.</p>')
    assert call(row, child, main)[0]


def test_financial_body_when_present_must_still_match_its_hash():
    row, child, main = fixture()
    row['sections']['financial_statements']['html'] = '<p>다른 문서</p>'
    assert call(row, child, main)[0] == []


@pytest.mark.parametrize('which', ['cover', 'financial_statements'])
@pytest.mark.parametrize('field, invalid', [('url', 1), ('tuple', []), ('sha256', []), ('utf8_bytes', True)])
def test_malformed_linked_provenance_uses_static_failure(which, field, invalid):
    row, child, main = fixture()
    row['sections'][which][field] = invalid
    assert call(row, child, main) == ([], ['DART_NOTE_CONTEXT_UNVERIFIED'])


def test_malformed_child_url_uses_static_failure():
    row, child, main = fixture()
    child['url'] = 1
    assert call(row, child, main) == ([], ['DART_SECTION_PROVENANCE_INVALID'])


@pytest.mark.parametrize('which, occurrence', [('cover', 0), ('financial_statements', 1)])
def test_real_graph_node_in_other_document_cannot_transfer_scope(which, occurrence):
    row, child, main = fixture()
    parts = main.split('node1[\'dcmNo\'] = "123";')
    assert len(parts) == 4
    main = ''.join(part + (f'node1[\'dcmNo\'] = "{999 if i == occurrence else 123}";' if i < 3 else '')
                   for i, part in enumerate(parts))
    if which == 'cover':
        main = main.replace('"123","1","100"', '"999","1","100"')
    row['main_sha256'] = hashlib.sha256(main.encode()).hexdigest()
    meta = row['sections'][which]
    meta['tuple']['dcmNo'] = '999'
    meta['url'] = 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(meta['tuple'])
    assert call(row, child, main) == ([], ['DART_NOTE_CONTEXT_UNVERIFIED'])


def test_exact_heading_event_limit_is_supported():
    body = '<p>15-3. 차입금 (연결)</p>' * 2000 + '<p>차입금 약정입니다.</p>'
    result = parse_filing_html(body, _capture_headings=True)
    assert result['status'] == 'COMPLETE'
    assert len(result['heading_events']) == 2000
    assert len(result['records']) == 1


@pytest.mark.parametrize('which', ['child', 'cover', 'financial_statements'])
@pytest.mark.parametrize('query', ['offset=&', 'unknown=&'])
def test_blank_query_pairs_cannot_hide_duplicate_or_unknown_parameters(which, query):
    row, child, main = fixture()
    target = child if which == 'child' else row['sections'][which]
    target['url'] = target['url'].replace('?', '?' + query, 1)
    assert call(row, child, main) == ([], ['DART_NOTE_CONTEXT_UNVERIFIED'])


@pytest.mark.parametrize('scope', ['consolidated', 'standalone'])
@pytest.mark.parametrize('title', [
    '연결 재무제표', '별도 재무제표', '개별 재무제표', '재무제표', '재무제표 주석',
    '연결 재무상태표', '별도 손익계산서', '포괄손익계산서', '개별 현금흐름표',
    '별도 재무제표 (연결)', '연결 재무제표 (별도)', '재무제표 (연결)',
    '재무제표 (별도)', '재무제표 (개별)', '연결 재무제표 (연결)', '별도 재무제표 (별도)',
])
@pytest.mark.parametrize('following', ['', '<p>차입금 약정 조건이 있습니다.</p>'])
def test_plain_financial_title_prose_rejects_whole_fragment(scope, title, following):
    row, child, main = fixture(scope)
    child = section(4, child['html'] + f'<p>{title}</p>' + following)
    assert call(row, child, main) == ([], ['DART_NOTE_FRAGMENT_SCOPE_UNRESOLVED'])


@pytest.mark.parametrize('scope', ['consolidated', 'standalone'])
@pytest.mark.parametrize('sentence', [
    '별도 재무제표와 연결 재무제표의 차입금 약정 차이를 설명합니다.',
    '개별 현금흐름표는 약정 금액 산출의 참고 자료입니다.',
    '재무상태표에 기재된 차입금에는 조건이 적용됩니다.',
])
def test_ordinary_financial_explanation_is_not_a_scope_title(scope, sentence):
    row, child, main = fixture(scope)
    child = section(4, child['html'] + f'<p>{sentence}</p>')
    blocks, gaps = call(row, child, main)
    assert blocks and 'DART_NOTE_FRAGMENT_SCOPE_UNRESOLVED' not in gaps
    assert any(sentence in block['excerpt'] for block in blocks)
