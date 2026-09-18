import asyncio
import json

import pytest

from prism_core import report_research_prefetch as research


@pytest.mark.parametrize('url', [
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000123',
    'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20260918000123&docno=&viewerhost=',
])
def test_public_document_identifier_preserved(url):
    assert research.public_url(url) == url


@pytest.mark.parametrize('url', [
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000123&token=secret',
    'https://dart.fss.or.kr.evil.com/dsaf001/main.do?rcpNo=20260918000123',
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000123&rcpNo=20260918000124',
    'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20260918000123&viewerhost=https://evil.com',
])
def test_document_allowlist_not_query_bypass(url):
    assert research.public_url(url) is None


def test_whole_table_retains_following_rounding_caveat():
    table = 'Global revenue share Q2 2026\n| Entity | Share |\n|---|---|\n| Alpha | 33% |\n| Beta | 68% |'
    note = 'Note: figures are rounded and totals may not sum to 100%.'
    excerpt, _ = research._bounded_excerpt(table + '\n' + note, len(table) + 1)
    assert '| Alpha' not in excerpt  # never publish table with its essential caveat removed


def test_two_comparison_sources_can_both_reach_news_budget():
    sources = []
    for i in range(2):
        text = 'Example Corp competitor segment revenue 2026. ' * 30
        source, _ = research._source({'markdown': text}, f'https://example.com/ir/{i}',
                                    None, '2026-09-18', 'Example Corp', 'EXM')
        sources.append(source)
    packet = research._packet('US', 'EXM', '2026-09-18', sources, [], 3)
    payload = json.loads(packet['section_notes']['news_analysis'])
    assert len(payload['sources']) == 2
    assert len(packet['section_notes']['news_analysis']) <= 3500


def test_timeout_receipt_retains_completed_source_and_actual_calls(tmp_path):
    async def transport(server, tool, args):
        if server == 'perplexity':
            return {'results': [{'url': 'https://example.com/ir/one'}, {'url': 'https://example.org/ir/two'}]}
        if args['url'].endswith('two'):
            await asyncio.sleep(10)
        return {'markdown': 'Example Corp revenue by business segment. ' * 30}
    packet = asyncio.run(research.prefetch_report_research('US', 'EXM', '20260918', 'Example Corp',
        _transport=transport, _cache_dir=tmp_path,
        _config={'enabled': True, 'version': research.VERSION, 'timeout_seconds': .05}))
    assert packet['receipt']['calls'] == 3
    assert packet['receipt']['usable_sources'] == 1
    assert 'TIME_BUDGET_EXHAUSTED' in packet['receipt']['gaps']


def test_discovery_hints_do_not_establish_peer_or_company_type():
    profile = research.company_research_context({'company_profile': {
        'name': 'Example Corp', 'industry': 'Banks', 'description': 'Diversified business.',
        'website': 'https://example.com', 'peers': ['Unverified Peer']}}, None, 'EXM')
    assert profile['industry'] == 'Banks'
    assert profile['entity_type'] == 'UNKNOWN'
    assert profile['peer_status'] == 'UNKNOWN'
    assert 'Unverified Peer' not in json.dumps(profile)


def test_filing_index_follows_actual_original_within_existing_budget(tmp_path):
    calls = []
    original = 'https://www.sec.gov/Archives/edgar/data/123/000123456789012345/example-20251231.htm'
    async def transport(server, tool, args):
        calls.append((tool, args))
        if server == 'perplexity':
            return {'results': [{'url': 'https://example.com/ir/annual-report'},
                                {'url': 'https://example.org/irrelevant'}]}
        if args['url'].endswith('annual-report'):
            return {'markdown': '# SEC Filing Details\nExample Corp\n[10-K](' + original + ')\n' +
                    '[Revenue](https://example.com/menu)\n' * 30}
        return {'markdown': 'Example Corp directly competes with Other Corp in optical components. ' * 15}
    packet = asyncio.run(research.prefetch_report_research('US', 'EXM', '20260918', 'Example Corp',
        _transport=transport, _cache_dir=tmp_path, _config={'enabled': True, 'version': research.VERSION}))
    assert len(calls) == 3
    assert calls[2][1]['url'] == original
    assert packet['receipt']['usable_sources'] == 1
    assert 'DOCUMENT_INDEX_NOT_BODY' in packet['receipt']['gaps']


def test_link_only_navigation_cannot_fill_evidence():
    text = 'Example Corp\n' + '[Revenue](https://example.com/revenue)\n' * 40
    source, gap = research._source({'markdown': text}, 'https://example.com/ir',
                                  None, '2026-09-18', 'Example Corp', 'EXM')
    assert source is None
    assert gap == 'NO_SUBSTANTIVE_SOURCE_BODY'


def test_original_link_chase_never_strips_secret_query():
    links = research._document_links('[10-K](https://www.sec.gov/Archives/edgar/data/123/123/example-20251231.htm?token=x)',
                                     'https://example.com/ir')
    assert links == []


def test_conditional_change_injected_with_periods_not_fact_label():
    text = ('Global optical components segment Market Share by Revenue (%, actual)\n'
            '| Company | Q2 2025 | Q2 2026 |\n| --- | --- | --- |\n| Example Corp | 22% | 24% |\n' +
            'Other company background. ' * 15)
    source, _ = research._source({'markdown': text}, 'https://example.com/ir/report',
                                 None, '2026-09-18', 'Example Corp', 'EXM')
    packet = research._packet('US', 'EXM', '2026-09-18', [source], [], 2)
    payload = json.loads(packet['section_notes']['news_analysis'])
    item = payload['conditional_changes'][0]
    assert item['change']['value'] == '2'
    assert item['change']['baseline_period']['period_end'] == '2025-06-30'
    assert item['fact_status'] == 'UNKNOWN'
    assert packet['receipt']['injected_conditional_changes'] == 1


def test_future_period_cannot_be_injected_as_achieved_change():
    text = ('Global optical components segment Market Share by Revenue (%, actual)\n'
            '| Company | Q2 2025 | Q2 2099 |\n| --- | --- | --- |\n| Example Corp | 22% | 24% |\n' +
            'Other company background. ' * 15)
    source, _ = research._source({'markdown': text}, 'https://example.com/ir/report',
                                 None, '2026-09-18', 'Example Corp', 'EXM')
    packet = research._packet('US', 'EXM', '2026-09-18', [source], [], 2)
    assert packet['receipt']['injected_conditional_changes'] == 0


def test_linked_table_headers_not_removed_as_navigation():
    header = '| [Company](https://example.com) | [Q2 2025](https://example.com/a) | [Q2 2026](https://example.com/b) |'
    text = header + '\n| --- | --- | --- |\n| Example Corp | 22% | 24% |'
    assert header in research._body_text(text)


def test_malformed_candidate_cannot_discard_valid_candidates(tmp_path):
    async def transport(server, tool, args):
        if server == 'perplexity':
            return {'results': [{'url': 'https://[invalid'}, {'url': 'https://example.com/ir/a'}]}
        return {'markdown': 'Example Corp competes with Other Corp. ' * 30}
    packet = asyncio.run(research.prefetch_report_research('US', 'EXM', '20260918', 'Example Corp',
        _transport=transport, _cache_dir=tmp_path, _config={'enabled': True, 'version': research.VERSION}))
    assert packet['receipt']['usable_sources'] == 1


def test_original_sec_case_preserved_but_priority_case_insensitive(tmp_path):
    calls = []
    original = 'https://www.sec.gov/Archives/edgar/data/123/123/example-20251231.htm'
    async def transport(server, tool, args):
        calls.append((tool, args))
        if server == 'perplexity':
            return {'results': [{'url': 'https://example.org/summary'}, {'url': original}]}
        return {'markdown': 'Example Corp competes with Other Corp. ' * 30}
    asyncio.run(research.prefetch_report_research('US', 'EXM', '20260918', 'Example Corp',
        _transport=transport, _cache_dir=tmp_path, _config={'enabled': True, 'version': research.VERSION}))
    assert calls[1][1]['url'] == original


def test_linked_filing_body_outranks_mirror_when_context_is_limited(tmp_path):
    original = 'https://www.sec.gov/Archives/edgar/data/123/123/example-20251231.htm'
    async def transport(server, tool, args):
        if server == 'perplexity':
            return {'results': [{'url': 'https://example.org/filing-mirror'}]}
        body = 'Example Corp competes with Other Corp in optical components. ' * 25
        if args['url'].endswith('filing-mirror'):
            body += '\n[Original filing](' + original + ')'
        return {'markdown': body}
    packet = asyncio.run(research.prefetch_report_research('US', 'EXM', '20260918', 'Example Corp',
        _transport=transport, _cache_dir=tmp_path, _config={'enabled': True, 'version': research.VERSION}))
    assert packet['receipt']['calls'] == 3
    payload = json.loads(packet['section_notes']['news_analysis'])
    assert payload['sources'][0]['url'] == original
