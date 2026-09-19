import asyncio
import json

from prism_core import report_insight_prefetch as insight
from prism_core import report_research_prefetch as base

BODY = 'https://kind.krx.co.kr/external/2026/03/18/001712/20260318007942/11011.htm'
TEXT = '# II. 사업의 내용\n\nExample Corp 경쟁업체는 Alpha이며 광통신 패키지 사업부문에서 경쟁하고 있습니다.\n\n' + '배경 설명입니다. ' * 60


def test_markdown_provenance_and_packet_budget():
    from prism_core.filing_report_evidence import filing_blocks
    blocks, gaps = filing_blocks({'markdown': TEXT}, BODY)
    assert blocks and not gaps
    block = blocks[0]
    assert block['provenance']['source_spans']
    assert block['provenance']['section_path'] == ['II. 사업의 내용']
    state = {'sources': [{'source_id': 's', 'url': BODY, 'blocks': blocks}], 'gaps': [], 'calls': 1,
             'filing_parser': 'structured_v1'}
    packet = insight.packet('KR', '327260', '2026-09-18', state)
    records = [r for note in packet['section_notes'].values() for r in json.loads(note)['sources']]
    assert records and all('provenance' in r for r in records)
    assert all(len(note.encode()) <= 6000 for note in packet['section_notes'].values())
    assert packet['receipt']['filing_parser'] == 'structured_v1'


def test_html_failure_never_falls_back_to_markdown():
    from prism_core.filing_report_evidence import filing_blocks
    blocks, gaps = filing_blocks({'markdown': TEXT, 'html': '<p>unrelated</p>'}, BODY)
    assert not blocks and 'FILING_HTML_PROVENANCE_UNVERIFIED' in gaps


def test_html_preserves_table_cells_and_context_without_numeric_conversion():
    from prism_core.filing_report_evidence import filing_blocks
    html = '<h2>III. 재무에 관한 사항</h2><h3>2. 연결재무제표</h3><p>단위: 백만원</p>' \
           '<table><tr><th>매출액</th><th>당기</th></tr><tr><td>상품</td><td>-</td></tr></table>' \
           '<p>주) 금액은 확인되지 않았습니다.</p>'
    markdown = '## III. 재무에 관한 사항\n### 2. 연결재무제표\n단위: 백만원\n' \
               '|매출액|당기|\n|---|---|\n|상품|-|\n주) 금액은 확인되지 않았습니다.'
    data = {'markdown': markdown, 'html': html, 'metadata': {'sourceURL': BODY, 'statusCode': 200}}
    blocks, gaps = filing_blocks(data, BODY)
    assert blocks and not gaps
    table = blocks[0]
    assert json.loads(table['excerpt'])['cells'][-1]['text'] == '-'
    provenance = table['provenance']
    assert provenance['scope'] == 'consolidated'
    assert provenance['source_path']
    assert '백만원' in provenance['context_before']
    assert '확인되지' in provenance['footnotes']
    assert provenance['representation'] == 'FIRECRAWL_CLEANED_HTML'
    assert provenance['representation_sha256'] != provenance['markdown_sha256']
    bad = {**data, 'html': html.replace('상품', '다른회사')}
    assert filing_blocks(bad, BODY) == ([], ['FILING_HTML_MARKDOWN_MISMATCH'])


def test_optin_cache_separation_default_requests_and_us_unchanged(tmp_path):
    calls = []
    async def transport(server, tool, args):
        calls.append((tool, args.copy()))
        if tool == 'perplexity_search':
            return {'results': [{'url': BODY}]}
        return {'markdown': TEXT}
    config = {'enabled': True, 'version': base.VERSION, 'research_profile': insight.PROFILE}
    def run(cfg, market='KR'):
        return asyncio.run(base.prefetch_report_research(market, '327260', '20250918', 'Example Corp',
                          _config=cfg, _cache_dir=tmp_path, _transport=transport))
    run(config)
    assert all('html' not in a.get('formats', []) for _, a in calls)
    n = len(calls)
    run({**config, 'filing_parser': True})
    assert len(calls) == n
    result = run({**config, 'filing_parser': 'structured_v1'})
    assert len(calls) == n * 2  # same scrapes, no extra parser retrieval
    assert any('html' in a.get('formats', []) for _, a in calls[n:])
    assert result['receipt']['filing_parser'] == 'structured_v1'
    calls.clear()
    run({**config, 'filing_parser': 'structured_v1'}, 'US')
    assert all('html' not in a.get('formats', []) for _, a in calls)


def test_future_source_is_rejected_before_parser():
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': BODY}]}
        return {'markdown': TEXT, 'html': '<broken>', 'metadata': {'publishedTime': '2099-01-01'}}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2025-09-18', 'Example Corp', transport,
                               context={}, progress=state, filing_parser=True))
    assert not state['sources']
    assert 'FUTURE_PUBLICATION' in state['gaps']
    assert not any('HTML' in gap for gap in state['gaps'])


def test_issuer_mismatch_stays_rejected_and_ordinary_url_never_requests_html():
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': BODY}, {'url': 'https://example.com/report'}]}
        if args['url'] == BODY:
            return {'markdown': '|회사명 :|Other Company|\n\n' + TEXT}
        assert 'html' not in args['formats']
        return {'markdown': TEXT}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2025-09-18', 'Example Corp', transport,
                               context={}, progress=state, filing_parser=True))
    assert 'ISSUER_NAME_UNRESOLVED_OR_MISMATCH' in state['gaps']
    assert all(source['url'] != BODY for source in state['sources'])


def test_malformed_html_is_explicit_gap():
    from prism_core.filing_report_evidence import filing_blocks
    blocks, gaps = filing_blocks({'markdown': TEXT, 'html': '',
                                 'metadata': {'sourceURL': BODY, 'statusCode': 200}}, BODY)
    assert not blocks and gaps


def test_owner_filters_keep_original_source_spans():
    from prism_core.filing_report_evidence import filing_blocks
    text = '# II. 사업의 내용\n\n경쟁업체 Alpha와 사업부문에서 경쟁하고 있으며 거래 조건이 다릅니다.\n\n' \
           '# III. 재무에 관한 사항\n\n## 연결재무제표 주석\n\n' \
           '1. 현금흐름\n\n|영업활동 현금흐름|당기|\n|---|---|\n|금액|120|\n\n' \
           '2. 소송\n\n소송 관련 우발부채는 300억원이며 최종 결과는 아직 확인되지 않았습니다.\n'
    blocks, _ = filing_blocks({'markdown': text}, BODY)
    assert {'direct_peers_competitive_position', 'financial_quality_valuation',
            'catalysts_risks_counterevidence'} <= {block['topic'] for block in blocks}
    for block in blocks:
        if not block['provenance'].get('projected'):
            assert ''.join(text[a:b] for a, b in block['provenance']['source_spans']) == block['excerpt']


def test_structured_candidates_are_not_silently_chopped_to_two():
    blocks = [{'topic': 'financial_quality_valuation', 'excerpt': f'원문 매출액 {n}억원입니다.',
               'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
               'provenance': {'parser_version': 'structured_v1'}} for n in range(6)]
    state = {'sources': [{'source_id': 's', 'url': BODY, 'blocks': blocks}], 'gaps': [], 'calls': 1}
    result = insight.packet('KR', '327260', '2026-09-18', state)
    assert len(json.loads(result['section_notes']['company_status'])['sources']) == 6
    assert all(len(note.encode()) <= 6000 for note in result['section_notes'].values())
    legacy = [{k: v for k, v in block.items() if k != 'provenance'} for block in blocks]
    state['sources'][0]['blocks'] = legacy
    old = insight.packet('KR', '327260', '2026-09-18', state)
    assert len(json.loads(old['section_notes']['company_status'])['sources']) == 2


def test_html_table_cells_cannot_be_borrowed_from_other_table_or_scope():
    from prism_core.filing_report_evidence import filing_blocks
    html = '<h2>III. 재무에 관한 사항</h2><h3>2. 연결재무제표</h3>' \
           '<table><tr><td>매출액</td><td>200</td></tr></table>'
    markdown = '## III. 재무에 관한 사항\n### 2. 연결재무제표\n\n' \
               '|매출액|100|\n|---|---|\n\n### 4. 재무제표\n\n|매출액|200|\n|---|---|'
    data = {'html': html, 'markdown': markdown, 'metadata': {'sourceURL': BODY, 'statusCode': 200}}
    assert filing_blocks(data, BODY) == ([], ['FILING_HTML_MARKDOWN_MISMATCH'])
    data['html'] = html.replace('200', '10')
    assert filing_blocks(data, BODY) == ([], ['FILING_HTML_MARKDOWN_MISMATCH'])


def test_small_peer_paragraphs_survive_new_note_ranking():
    from prism_core.filing_report_evidence import filing_blocks
    from prism_core.filing_selection import select_filing_evidence
    paragraphs = [f'제품군{n}의 주요 거래처는 Client{n}이며 경쟁업체는 Rival{n}입니다.' for n in range(3)]
    text = '# II. 사업의 내용\n\n' + '\n\n'.join(paragraphs)
    ranked = select_filing_evidence(text, method='structured_projected')
    assert len(ranked['records']) < len(paragraphs)
    blocks, gaps = filing_blocks({'markdown': text}, BODY)
    peers = [b for b in blocks if b['topic'] == 'direct_peers_competitive_position']
    assert all(any(p in b['excerpt'] for b in peers) for p in paragraphs)
    assert not gaps
    assert len({b['excerpt'] for b in peers}) == len(peers)
    state = {'sources': [{'source_id': 's', 'url': BODY, 'blocks': blocks}], 'gaps': gaps, 'calls': 1}
    packet = insight.packet('KR', '327260', '2026-09-18', state)
    assert all(p in packet['section_notes']['news_analysis'] for p in paragraphs)
    assert all(len(note.encode()) <= 6000 for note in packet['section_notes'].values())


def test_duplicate_legacy_peer_location_is_not_guessed():
    from prism_core.filing_report_evidence import filing_blocks
    paragraph = '광학 사업부문에서 경쟁업체는 Rival이며 시장에 제품을 공급하고 있습니다.'
    text = '# II. 사업의 내용\n\n' + paragraph + '\n\n' + paragraph
    _, gaps = filing_blocks({'markdown': text}, BODY)
    assert 'FILING_LEGACY_PEER_SPAN_AMBIGUOUS' in gaps


def test_invalid_or_oversized_markdown_never_reaches_parser(monkeypatch):
    from prism_core import filing_report_evidence as adapter
    def forbidden(*args, **kwargs):
        raise AssertionError('must reject before parsing')
    monkeypatch.setattr(adapter, 'parse_filing', forbidden)
    monkeypatch.setattr(adapter, 'select_filing_evidence', forbidden)
    for value in (None, [], 4, 'a' * (2 * 1024 * 1024 + 1), '가' * 700000):
        blocks, gaps = adapter.filing_blocks({'markdown': value}, BODY)
        assert not blocks and gaps


def test_failed_html_never_evaluates_markdown(monkeypatch):
    from prism_core import filing_html
    from prism_core import filing_report_evidence as adapter
    def forbidden(*args, **kwargs):
        raise AssertionError('failed HTML must not parse Markdown')
    monkeypatch.setattr(adapter, 'parse_filing', forbidden)
    for status in ('UNSUPPORTED', 'LIMIT_EXCEEDED'):
        monkeypatch.setattr(filing_html, 'parse_filing_html', lambda value, s=status:
                            {'status': s, 'errors': ['SOURCE_REJECTED'], 'records': [], 'source_sha256': None})
        blocks, gaps = adapter.filing_blocks({'markdown': TEXT, 'html': 'x',
                                              'metadata': {'sourceURL': BODY, 'statusCode': 200}}, BODY)
        assert not blocks and gaps


def test_representation_bridge_preserves_major_numeric_hierarchy_and_joins_only_prose():
    from prism_core.filing_report_evidence import _same_markdown_unit
    from prism_core.filing_structure import parse_filing
    text = '# 사업보고서\n\n## II. 사업의 내용\n\n### 2. 주요 제품\n\n첫 문장입니다.\n\n둘째 문장입니다.\n'
    units = parse_filing(text)
    record = {'kind': 'prose', 'text': '첫 문장입니다. 둘째 문장입니다.', 'scope': 'unknown',
              'section_path': ['II. 사업의 내용', '2. 주요 제품', '가. 제품의 구분']}
    assert _same_markdown_unit(record, units, text)
    assert not _same_markdown_unit({**record, 'section_path': ['II. 사업의 내용', '3. 주요 제품']}, units, text)
    assert not _same_markdown_unit({**record, 'scope': 'consolidated'}, units, text)
    separated = text.replace('둘째 문장', '### 3. 다른 내용\n\n둘째 문장')
    assert not _same_markdown_unit(record, parse_filing(separated), separated)
    table_boundary = text.replace('둘째 문장', '|항목|값|\n|---|---|\n|매출|100|\n\n둘째 문장')
    assert not _same_markdown_unit(record, parse_filing(table_boundary), table_boundary)
    duplicate = text + '\n첫 문장입니다.\n\n둘째 문장입니다.\n'
    assert not _same_markdown_unit(record, parse_filing(duplicate), duplicate)
    other_chapter = {**record, 'section_path': ['IV. 사업의 내용', '2. 주요 제품']}
    assert not _same_markdown_unit(other_chapter, units, text)


def test_representation_bridge_rejects_duplicate_tables_and_numeric_note_mismatch():
    from prism_core.filing_report_evidence import _same_markdown_unit
    from prism_core.filing_structure import parse_filing
    text = '## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n1. 매출액\n\n|구분|당기|\n|---|---|\n|매출|100|\n'
    record = {'kind': 'table', 'text': '', 'scope': 'consolidated',
              'section_path': ['III. 재무에 관한 사항', '3. 연결재무제표 주석', '1. 매출액'],
              'table': {'cells': [{'text': v} for v in ('구분', '당기', '매출', '100')]}}
    assert _same_markdown_unit(record, parse_filing(text), text)
    wrong = {**record, 'section_path': record['section_path'][:-1] + ['2. 매출액']}
    assert not _same_markdown_unit(wrong, parse_filing(text), text)
    duplicate = text + '\n본문입니다.\n\n|구분|당기|\n|---|---|\n|매출|100|\n'
    assert not _same_markdown_unit(record, parse_filing(duplicate), duplicate)


def test_bridge_never_erases_conflicting_present_korean_subheadings():
    from prism_core.filing_report_evidence import _same_markdown_unit
    from prism_core.filing_structure import parse_filing
    text = '## II. 사업의 내용\n\n### 2. 주요 제품\n\n#### 나. 해외매출\n\n|구분|당기|\n|---|---|\n|매출|100|\n'
    record = {'kind': 'table', 'text': '', 'scope': 'unknown',
              'section_path': ['II. 사업의 내용', '2. 주요 제품', '가. 국내매출'],
              'table': {'cells': [{'text': value} for value in ('구분', '당기', '매출', '100')]}}
    assert not _same_markdown_unit(record, parse_filing(text), text)
    missing = {**record, 'section_path': record['section_path'][:-1]}
    assert _same_markdown_unit(missing, parse_filing(text), text)
    same = {**record, 'section_path': record['section_path'][:-1] + ['나. 해외매출']}
    assert _same_markdown_unit(same, parse_filing(text), text)
