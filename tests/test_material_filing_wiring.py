import asyncio
import json

from cores.agents.report_agent import ReportAgent
from prism_core import report_insight_prefetch as insight
from prism_core import report_research_prefetch as research
from prism_core.filing_report_evidence import filing_blocks
from prism_core.report_research_context import apply_section_research

URL = 'https://kind.krx.co.kr/external/2026/03/18/001712/20260318007942/11011.htm'
TEXT = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n38. 우발채무 및 약정사항\n'
        '(3) 기술이전계약\n\nExample Corp의 계약금은 반환의무가 없습니다.\n\n'
        '다만 일정 조건 충족 시 원천징수세액 반환의무가 발생할 수 있습니다.\n\n'
        '기술이전수익은 임상시험 성공 여부에 따라 달라질 수 있습니다.\n\n'
        '### 5. 재무제표 주석\n1. 일반사항\n' + '일반적인 배경 설명입니다. ' * 40)


def test_material_source_terms_reach_actual_report_prompt_without_trade_rules():
    blocks, gaps = filing_blocks({'markdown': TEXT}, URL, material_notes=True)
    state = {'sources': [{'source_id': 'M-1', 'url': URL, 'blocks': blocks}],
             'gaps': gaps, 'calls': 0, 'filing_parser': 'material_v2'}
    packet = insight.packet('KR', '327260', '2026-09-18', state)
    record = next(row for note in packet['section_notes'].values()
                  for row in json.loads(note)['sources'] if '다만' in row['excerpt'])
    assert record['provenance']['material_topics']
    assert record['provenance']['scope'] == 'consolidated'
    assert '반환의무가 없습니다' in record['excerpt']
    owner = next(owner for owner, note in packet['section_notes'].items() if '다만' in note)
    agent = ReportAgent('test', 'Existing rules unchanged.', ['firecrawl'])
    output = apply_section_research(agent, owner, {'report_research': packet}, '20260918', 'ko')
    assert 'Material filing review topics' in output.instruction
    assert 'not an automatic BUY/SELL rule' in output.instruction
    assert '다만' in output.instruction and 'M-1' in output.instruction
    assert output.server_names == agent.server_names
    assert all(len(v.encode()) <= 6000 for v in packet['section_notes'].values())


def test_v2_config_is_separate_from_v1_cache_and_off_us_unchanged(tmp_path):
    calls = []
    async def transport(server, tool, args):
        calls.append(tool)
        return {'results': [{'url': URL}]} if tool == 'perplexity_search' else {'markdown': TEXT}
    config = {'enabled': True, 'version': research.VERSION, 'research_profile': insight.PROFILE}
    def run(version, market='KR'):
        return asyncio.run(research.prefetch_report_research(market, '327260', '20260918', 'Example Corp',
            _config={**config, 'filing_parser': version}, _cache_dir=tmp_path, _transport=transport))
    old = run('structured_v1')
    n = len(calls)
    new = run('material_v2')
    assert len(calls) == 2 * n
    assert old['receipt']['filing_parser'] == 'structured_v1'
    assert new['receipt']['filing_parser'] == 'material_v2'
    assert run('material_v2')['receipt']['cache_hit']
    assert len(calls) == 2 * n
    us = run('material_v2', 'US')
    assert 'filing_parser' not in us['receipt']


def test_v1_prompt_guard_unchanged_and_tags_not_exposed_as_confirmed_risk():
    agent = ReportAgent('test', 'Original rules.', [])
    data = {'report_research': {'receipt': {'filing_parser': 'structured_v1'},
                               'section_notes': {'company_status': '{"sources":[]}'}}}
    output = apply_section_research(agent, 'company_status', data, '20260918', 'ko')
    assert 'Material filing review topics' not in output.instruction
    assert 'risk_score' not in output.instruction


def test_material_shared_hashes_are_metered_once_and_conflicts_not_merged():
    common = {'parser_version': 'material_v2', 'representation': 'FIRECRAWL_MARKDOWN',
              'representation_sha256': 'a' * 64, 'markdown_sha256': 'a' * 64}
    blocks = [{'topic': 'financial_quality_valuation', 'excerpt': f'매출채권 {i}를 확인합니다.',
               'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
               'provenance': {**common, 'scope': 'consolidated', 'source_spans': [[i, i + 1]]}}
              for i in range(3)]
    bad = {**blocks[0], 'excerpt': 'conflicting source', 'provenance': {**common, 'markdown_sha256': 'b' * 64}}
    state = {'sources': [{'source_id': 'M-1', 'url': URL, 'blocks': [*blocks, bad]}], 'gaps': [], 'calls': 0}
    payload = json.loads(insight.packet('KR', '327260', '2026-09-18', state)['section_notes']['company_status'])
    assert payload['source_provenance']['M-1'] == common
    assert len(payload['sources']) == 3
    assert all('markdown_sha256' not in r['provenance'] for r in payload['sources'])
    assert 'SOURCE_PROVENANCE_CONFLICT' in payload['gaps']


def test_html_material_exception_stays_with_no_obligation_clause():
    markdown = ('## II. 사업의 내용\n### 1. 기술이전계약\n\n계약금은 반환의무가 없습니다.\n\n'
                '다만 재무약정 위반 시 계약금 반환의무가 발생할 수 있습니다.\n')
    html = ('<h2>II. 사업의 내용</h2><h3>1. 기술이전계약</h3><p>계약금은 반환의무가 없습니다.</p>'
            '<p>다만 재무약정 위반 시 계약금 반환의무가 발생할 수 있습니다.</p>')
    blocks, gaps = filing_blocks({'markdown': markdown, 'html': html,
        'metadata': {'sourceURL': URL, 'statusCode': 200}}, URL, material_notes=True)
    assert len(blocks) == 1 and not gaps
    assert '없습니다' in blocks[0]['excerpt'] and '다만' in blocks[0]['excerpt']
    assert len(blocks[0]['provenance']['source_paths']) == 2


def test_legacy_peer_fragment_cannot_escape_an_oversized_material_group():
    text = ('## II. 사업의 내용\n\n우리의 경쟁업체는 Alpha이며 계약금에 대해서는 반환의무가 없습니다.\n\n'
            + '다만 특정 조건 충족 시에는 계약금 반환의무가 발생할 수 있습니다. ' * 100)
    blocks, gaps = filing_blocks({'markdown': text}, URL, material_notes=True)
    assert not blocks
    assert 'MATERIAL_PROSE_GROUP_OVERSIZE' in gaps


def test_tax_table_dilution_word_does_not_turn_it_into_an_issuance_event():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n27. 이연법인세\n'
            '(단위: 원)\n| 구분 | 당기 |\n|---|---|\n| 주식선택권 | 100 |\n')
    blocks, _ = filing_blocks({'markdown': text}, URL, material_notes=True)
    assert blocks and all(b['topic'] == 'financial_quality_valuation' for b in blocks)
