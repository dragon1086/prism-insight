"""Network-free adversarial coverage for optional competitive-source retrieval."""
import asyncio
import json

import pytest

from prism_core import report_research_prefetch as research

CONFIG = {'version': research.VERSION, 'enabled': True}
TEXT = 'Example Corp revenue and market share require same-period comparison. ' * 12


class Transport:
    def __init__(self, slow_second=False):
        self.calls = 0
        self.slow_second = slow_second

    async def __call__(self, server, tool, arguments):
        self.calls += 1
        if server == 'perplexity':
            return {'results': [{'url': 'https://example.com/news/one'},
                                {'url': 'https://example.com/news/two'}]}
        if self.slow_second and self.calls == 3:
            await asyncio.sleep(10)
        return {'data': {'markdown': TEXT, 'metadata': {'publishedTime': '2025-01-01'}}}


def run(path, transport, **kwargs):
    config = kwargs.pop('_config', CONFIG)
    return asyncio.run(research.prefetch_report_research(
        'US', 'EXM', '20250918', 'Example Corp', _transport=transport,
        _config=config, _cache_dir=path, **kwargs))


@pytest.mark.parametrize('url', [
    'https://localhost./', 'https://service.internal./', 'https://foo.localhost./',
    'https://example.com/a\nb', 'https://example.com/a\tb',
])
def test_reject_dns_root_suffix_bypass_and_silent_locator_rewrite(url):
    assert research.public_url(url) is None


@pytest.mark.parametrize('url', [
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000001',
    'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20260918000001&docno=123&viewerhost=',
])
def test_approved_disclosure_identity_is_kept_exactly(url):
    assert research.public_url(url) == url


@pytest.mark.parametrize('url', [
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000001&rcpNo=20260918000002',
    'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260918000001&token=secret',
    'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20260918000001&viewerhost=https://localhost',
    'https://dart.fss.or.kr.evil.example/dsaf001/main.do?rcpNo=20260918000001',
])
def test_query_scope_and_disclosure_host_cannot_be_widened(url):
    assert research.public_url(url) is None


@pytest.mark.parametrize('damage', ['minimal', 'wrong_symbol', 'stale_version', 'missing_notes'])
def test_malformed_or_wrong_identity_cache_is_recollected(tmp_path, damage):
    transport = Transport()
    packet = run(tmp_path, transport)
    if damage == 'minimal':
        packet = {'receipt': {'usable_sources': 1}}
    elif damage == 'wrong_symbol':
        packet['receipt']['symbol'] = 'OTHER'
    elif damage == 'stale_version':
        packet['receipt']['collector_version'] = 'obsolete'
    else:
        packet.pop('section_notes')
    path = next(tmp_path.glob('*.json'))
    path.write_text(json.dumps(packet))
    second = run(tmp_path, transport)
    assert transport.calls == 6
    assert second['receipt']['symbol'] == 'EXM'
    assert second['receipt']['collector_version'] == research.COLLECTOR_VERSION
    assert second['section_notes']['news_analysis']
    assert second['receipt']['cache_hit'] is False


def test_context_change_uses_new_cache_and_unchanged_context_reuses_it(tmp_path):
    transport = Transport()
    first = run(tmp_path, transport, company_context={'industry': 'Banking'})
    second = run(tmp_path, transport, company_context={'industry': 'Optical components'})
    third = run(tmp_path, transport, company_context={'industry': 'Optical components'})
    assert transport.calls == 6
    assert first['receipt']['discovery_context']['industry'] == 'Banking'
    assert second['receipt']['discovery_context']['industry'] == 'Optical components'
    assert third['receipt']['cache_hit'] is True
    assert third['receipt']['calls_this_run'] == 0


def test_partial_timeout_retains_completed_source_and_counts_started_calls(tmp_path):
    transport = Transport(slow_second=True)
    packet = run(tmp_path, transport, _config={**CONFIG, 'timeout_seconds': .03})
    receipt = packet['receipt']
    assert receipt['calls'] == transport.calls == 3
    assert receipt['usable_sources'] == 1
    assert 'TIME_BUDGET_EXHAUSTED' in receipt['gaps']
    assert receipt['collection_complete'] is False
    assert receipt['injected_sources'] == 1
    assert len(packet['section_notes']['news_analysis']) <= 3500


def test_oversized_provider_candidate_list_cannot_overflow_context_budget(tmp_path):
    async def oversized(*args):
        return {'results': [{'url': 'http://localhost/x'}] * 100}

    packet = run(tmp_path, oversized)
    assert len(packet['section_notes']['news_analysis']) <= 3500
    assert packet['receipt']['calls'] == 1
    assert packet['receipt']['usable_sources'] == 0


def test_balanced_memory_tables_retain_both_businesses_and_all_peer_rows():
    text = '''# Global DRAM revenue market share Q2 2026
| Company | Q2 2025 | Q2 2026 |
| --- | --- | --- |
| Samsung | 35% | 38% |
| SK Hynix | 43% | 38% |
| Micron | 22% | 24% |
Note: Global revenue share; figures rounded.
# Global HBM revenue market share Q2 2026
| Company | Q2 2025 | Q2 2026 |
| --- | --- | --- |
| Samsung | 15% | 33% |
| SK Hynix | 64% | 50% |
| Micron | 21% | 18% |
Note: Global revenue share; figures rounded.
'''
    first, _ = research._source({'data': {'markdown': text}}, research.MEMORY_SOURCE,
                                None, '2026-09-18', 'Micron', 'MU')
    second, _ = research._source({'data': {'markdown': 'Micron revenue grows. ' * 100}},
                                 'https://example.com/news/report', None, '2026-09-18', 'Micron', 'MU')
    packet = research._packet('US', 'MU', '2026-09-18', [first, second], [], 3)
    note = packet['section_notes']['news_analysis']
    assert len(note) <= 3500
    excerpt = json.loads(note)['sources'][0]['excerpt']
    for line in text.splitlines():
        assert line in excerpt
