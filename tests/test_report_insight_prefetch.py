"""Large source bodies stay outside LLM inputs; complete topic blocks only."""
import asyncio
import json

from prism_core import report_insight_prefetch as insight

KIND = 'https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20260318001712&docno=&viewerhost='
BODY = 'https://kind.krx.co.kr/external/2026/03/18/001712/20260318007942/11011.htm'


def test_kind_anchor_is_resolved_only_for_exact_public_document_path():
    assert insight.document_links({'links': [BODY + '#toc_9']}, KIND) == [BODY]
    assert not insight.document_links({'links': ['https://evil.com/x#toc_9', BODY + '?token=secret#toc_9']}, KIND)


def test_competitive_block_keeps_customer_supplier_and_peer_roles():
    text = '# 광통신 패키지\n\nExample Corp supplies Lumentum and competes with Kyocera in optical packages.\n\n'
    text += '# Financial statements\n\n' + 'Ordinary financial prose. ' * 200
    blocks = insight.topic_blocks(text)
    peers = [row for row in blocks if row['topic'] == 'direct_peers_competitive_position']
    assert peers and 'Lumentum' in peers[0]['excerpt'] and 'Kyocera' in peers[0]['excerpt']
    assert '광통신 패키지' in peers[0]['excerpt']
    assert peers[0]['status'] == 'SOURCE_TEXT_NOT_FACT_VALIDATED'


def test_long_paragraph_not_sliced_and_skip_is_visible():
    text = '# Competition\n\nCompetitors include ' + 'Example corporation ' * 400
    assert not insight.topic_blocks(text, max_block_bytes=500)


def test_kind_viewer_hydration_follows_body_without_exposing_raw_payload():
    calls = []
    async def transport(server, tool, args):
        calls.append((server, tool, args))
        if tool == 'perplexity_search':
            return {'results': [{'url': KIND}]}
        if args.get('url') == KIND:
            assert args['onlyMainContent'] is False and args['waitFor'] >= 2000
            return {'markdown': '자료를 요청 중입니다', 'links': [BODY + '#toc_9']}
        return {'markdown': '# 광통신 패키지\n\nExample Corp supplies Lumentum and competes with Kyocera in optical packages.\n\n' +
                'Background context. ' * 10000}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2026-09-18', 'Example Corp', transport,
                               context={}, progress=state))
    packet = insight.packet('KR', '327260', '2026-09-18', state)
    assert state['calls'] <= 8
    news = packet['section_notes']['news_analysis']
    assert len(news.encode('utf-8')) <= 6000
    assert 'Kyocera' in news and 'Lumentum' in news
    assert 'Background context' not in news
    assert packet['receipt']['raw_response_utf8_bytes'] > len(news.encode('utf-8')) * 10
    assert not packet['news_usable']
    assert all('Background context' not in v for v in packet['section_notes'].values())


def test_packet_preserves_atomic_records_and_real_source_ids():
    sources = [{'source_id': 's1', 'url': 'https://example.com/annual-report',
                'published': 'UNKNOWN', 'publication_basis': 'UNKNOWN',
                'blocks': [{'topic': 'direct_peers_competitive_position', 'excerpt': 'Competitors include Alpha.',
                            'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}]}]
    packet = insight.packet('US', 'EXM', '2026-09-18', {'sources': sources, 'gaps': [], 'calls': 2})
    data = json.loads(packet['section_notes']['news_analysis'])
    assert data['sources'][0]['source_id'] == 's1'
    assert 'Alpha' in data['sources'][0]['excerpt']
    assert packet['receipt']['competitive_complete'] is False


def test_ir_index_uses_existing_map_and_keeps_call_budget():
    calls = []
    async def transport(server, tool, args):
        calls.append((tool, args))
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/ir/'}]}
        if tool == 'firecrawl_map':
            return {'links': [{'url': 'https://example.com/annual-report.pdf'}]}
        if args['url'].endswith('/ir/'):
            return {'markdown': 'Reports loading'}
        return {'markdown': '# Business\n\nExample Corp competes with Alpha in optical components.\n\n' + 'Background. ' * 100}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('US', 'EXM', '2026-09-18', 'Example Corp', transport,
                               context={}, progress=state))
    assert any(tool == 'firecrawl_map' for tool, _ in calls)
    assert state['sources'] and state['calls'] <= 8
    assert calls[0][1]['search_domain_filter'] == ['sec.gov']


def test_enhanced_cache_and_scope_keep_existing_off_behavior(tmp_path):
    from prism_core import report_research_prefetch as base
    calls = []
    async def transport(server, tool, args):
        calls.append(tool)
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/report'}]}
        return {'markdown': '# Optical components\n\nExample Corp competes with Alpha in optical components.\n\n' + 'Other text. ' * 100}
    config = {'enabled': True, 'version': base.VERSION, 'research_profile': insight.PROFILE}
    def run(cfg):
        return asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
            _config=cfg, _cache_dir=tmp_path, _transport=transport))
    first = run(config)
    n = len(calls)
    second = run(config)
    assert first['receipt']['collector_version'] == insight.PROFILE
    assert second['receipt']['cache_hit'] and len(calls) == n
    assert second['receipt']['calls_this_run'] == 0
    assert run({**config, 'validated_symbols': {}}) is None
    assert run({**config, 'enabled': False}) is None
    assert len(calls) == n


def test_enhanced_timeout_keeps_completed_body_and_small_input(tmp_path):
    from prism_core import report_research_prefetch as base
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/one'}, {'url': 'https://example.com/two'}]}
        if args['url'].endswith('two'):
            await asyncio.sleep(10)
        return {'markdown': '# Optical components\n\nExample Corp competes with Alpha in optical components.\n\n' + 'Other text. ' * 100}
    packet = asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
        _config={'enabled': True, 'version': base.VERSION, 'research_profile': insight.PROFILE,
                 'timeout_seconds': .02}, _cache_dir=tmp_path, _transport=transport))
    assert packet['receipt']['usable_sources'] == 1
    assert 'TIME_BUDGET_EXHAUSTED' in packet['receipt']['gaps']
    assert packet['receipt']['calls_this_run'] == 3
    assert all(len(v.encode()) <= 6000 for v in packet['section_notes'].values())


def test_enhanced_future_publication_never_admitted():
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/report'}]}
        return {'markdown': 'Example Corp competes with Alpha. ' * 50,
                'metadata': {'publishedTime': '2099-01-01'}}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('US', 'EXM', '2026-09-18', 'Example Corp', transport,
                               context={}, progress=state))
    assert not state['sources']
    assert 'FUTURE_PUBLICATION' in state['gaps']


def test_issuer_cover_distinguishes_parent_from_subsidiary():
    text = '# 사업보고서\n\n| 회 사 명 : | 주식회사 Example Holdings |\n\nSubsidiary Example 090430 mentioned.'
    assert insight.cover_issuer(text) == '주식회사 Example Holdings'
    assert insight.normalized_issuer('주식회사 Example') == insight.normalized_issuer('(주)Example')
    assert insight.normalized_issuer('Example Holdings') != insight.normalized_issuer('Example')


def test_verified_viewer_ticker_can_link_legal_issuer_without_stock_specific_alias():
    assert insight.viewer_identity('# RF머트리얼즈 (327260)\n\nbody', '327260') is True
    assert insight.viewer_identity('# 아모레퍼시픽홀딩스 (002790)\n\nmentions 090430', '090430') is False


def test_listing_titles_and_phone_headings_are_not_insight_blocks():
    text = '# (408) 546-5483\n\n2025년 매출액 및 손익구조 변동 공시\n\n'
    text += 'We compete with Alpha in optical components.\n\n'
    text += 'Our guidance includes revenue between $44.5 and $45.5 billion,\n\n'
    rows = insight.topic_blocks(text)
    assert rows
    assert all('(408)' not in row['excerpt'] for row in rows)
    assert not any('손익구조' in row['excerpt'] or 'billion,' in row['excerpt'] for row in rows)


def test_news_budget_reserves_room_for_each_topic():
    topics = ['direct_peers_competitive_position', 'earnings_estimates_guidance', 'catalysts_risks_counterevidence']
    source = {'source_id': 's', 'url': 'https://example.com/ir/report', 'published': 'UNKNOWN',
              'blocks': [{'topic': topic, 'excerpt': (topic + ' ') * 30, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'}
                         for topic in topics for _ in range(2)]}
    result = insight.packet('US', 'EXM', '2026-09-18', {'sources': [source], 'gaps': [], 'calls': 1})
    injected = json.loads(result['section_notes']['news_analysis'])['sources']
    assert {row['topic'] for row in injected} == set(topics)


def test_viewer_id_resolves_legal_name_and_rejects_other_issuer():
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': KIND}, {'url': 'https://example.com/parent-report'}]}
        if args['url'] == KIND:
            return {'markdown': '# AB소재 (327260)\n\n| 회 사 명 : | 에이비소재 주식회사 |',
                    'metadata': {'statusCode': 200}, 'links': [BODY + '#toc_1']}
        if args['url'] == BODY:
            return {'markdown': '| 회 사 명 : | 에이비소재 주식회사 |\n\n에이비소재 주식회사는 Alpha에 공급하고 Beta와 경쟁합니다.\n\n' +
                    '보고서 사업 현황 자료입니다. ' * 50}
        return {'markdown': '| 회 사 명 : | AB소재홀딩스 주식회사 |\n\nAB소재는 자회사입니다. 327260\n\n' +
                '경쟁사 Alpha에 대한 보고서 문장입니다. ' * 50}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2026-09-18', 'AB소재', transport,
                               context={}, progress=state))
    assert len(state['sources']) == 1
    assert state['sources'][0]['url'] == BODY
    assert state['sources'][0]['issuer_link']['stock_code'] == '327260'
    assert 'ISSUER_NAME_UNRESOLVED_OR_MISMATCH' in state['gaps']


def test_unit_caption_is_atomic_with_financial_table():
    text = '# Cash flow\n\n(단위: 백만원)\n\n| 현금흐름 | 2025 |\n| --- | --- |\n| 영업현금흐름 | 123 |'
    rows = insight.topic_blocks(text)
    assert rows and all('백만원' in row['excerpt'] for row in rows if '| 123 |' in row['excerpt'])


def test_viewer_without_cover_cannot_authorize_another_issuer():
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': KIND}]}
        if args['url'] == KIND:
            return {'markdown': '# Target (327260)', 'links': [BODY + '#toc_1']}
        return {'markdown': '| 회 사 명 : | Other Holdings |\n\n' + 'Other Holdings competes with Alpha. ' * 50}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2026-09-18', 'Target', transport, context={}, progress=state))
    assert not state['sources']
    assert 'ISSUER_NAME_UNRESOLVED_OR_MISMATCH' in state['gaps']


def test_accounting_guidance_and_rating_outlook_are_not_earnings_guidance():
    text = ('# Outlook\n\nLease accounting guidance requires recognizing right-of-use assets.\n\n'
            'S&P and Fitch ratings outlook is stable.\n\n'
            'For more information refer to Item 1A risk factors.\n\n'
            'We forecast revenue of USD 100 million under our updated guidance.\n\n')
    rows = insight.topic_blocks(text)
    assert not any('Lease accounting' in row['excerpt'] or 'Fitch' in row['excerpt'] or 'For more information' in row['excerpt'] for row in rows)
    assert any('USD 100 million' in row['excerpt'] for row in rows)


def test_pipe_wrapped_unit_caption_stays_with_next_table():
    text = '# Financial statements\n\n| (단위: 백만원) |\n| --- |\n\n| 현금흐름 | 2025 |\n| --- | --- |\n| 영업현금흐름 | 123 |'
    rows = insight.topic_blocks(text)
    assert rows and all('백만원' in row['excerpt'] for row in rows if '| 123 |' in row['excerpt'])


def test_url_keyword_and_rating_legend_do_not_become_business_insights():
    text = ('Share this [article](https://example.com/revenue-guidance).\n\n'
            '| 신용등급의 정의 | 위험 |\n| --- | --- |\n| AAA | 위험이 낮음 |\n\n'
            'We forecast revenue of USD 100 million under our updated guidance.')
    rows = insight.topic_blocks(text)
    assert not any('Share this' in row['excerpt'] or 'AAA' in row['excerpt'] for row in rows)


def test_minority_shareholders_are_not_order_backlog():
    assert not insight._mentions('소수주주권 행사 내역', '수주')
    assert insight._mentions('신규수주 증가', '수주')
    assert insight._mentions('누적 수주 잔고', '수주')


def test_operating_margin_and_non_gaap_guidance_are_valid_positive_controls():
    for sentence in ['Our operating margin guidance for 2027 is 20 percent.',
                     'Our non-GAAP EPS guidance for 2027 is $5 to $6.',
                     'Our GAAP EPS guidance for 2027 is $4 to $5.',
                     'Our operating income outlook for 2027 is USD 100 million.']:
        assert any(row['topic'] == 'earnings_estimates_guidance' for row in insight.topic_blocks(sentence))


def test_static_document_identity_recovery_verifies_viewer_back_link_without_refetch():
    calls = []
    async def transport(server, tool, args):
        calls.append((tool, args.get('url')))
        if tool == 'perplexity_search':
            return {'results': [{'url': BODY}]}
        if args['url'] == KIND:
            return {'markdown': '# AB소재 (327260)\n\n| 회 사 명 : | 에이비소재 주식회사 |',
                    'metadata': {'statusCode': 200}, 'links': [BODY + '#toc_1']}
        return {'markdown': '| 회 사 명 : | 에이비소재 주식회사 |\n\n에이비소재 주식회사는 Alpha에 공급하고 Beta와 경쟁합니다.\n\n' +
                '보고서 사업 현황 자료입니다. ' * 50}
    state = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(insight.collect('KR', '327260', '2026-09-18', 'AB소재', transport, context={}, progress=state))
    assert len(state['sources']) == 1
    assert state['sources'][0]['issuer_link']['stock_code'] == '327260'
    assert sum(url == BODY for _, url in calls) == 1
    assert any(url == KIND for _, url in calls)


def test_acquired_body_without_injected_evidence_uses_negative_cache_ttl(tmp_path):
    import os
    import time

    from prism_core import report_research_prefetch as base

    calls = []
    async def transport(server, tool, args):
        calls.append(tool)
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/report'}]}
        return {'markdown': 'Example Corp historical background without matching topic words. ' * 40}
    config = {'enabled': True, 'version': base.VERSION, 'research_profile': insight.PROFILE}
    def run():
        return asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
            _config=config, _cache_dir=tmp_path, _transport=transport))
    packet = run()
    assert packet['receipt']['usable_sources'] > 0 and packet['receipt']['injected_sources'] == 0
    n = len(calls)
    for path in tmp_path.glob('*.json'):
        os.utime(path, (time.time() - 301, time.time() - 301))
    assert not run()['receipt']['cache_hit']
    assert len(calls) > n


def test_enhanced_cache_io_failure_keeps_scoped_small_failure_packet(tmp_path, monkeypatch):
    from pathlib import Path

    from prism_core import report_research_prefetch as base

    def denied(*args, **kwargs):
        raise OSError('private cache path must not leak')
    monkeypatch.setattr(Path, 'mkdir', denied)
    result = asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
        _config={'enabled': True, 'version': base.VERSION, 'research_profile': insight.PROFILE}, _cache_dir=tmp_path))
    assert result['receipt']['collector_version'] == insight.PROFILE
    assert result['receipt']['gaps'] == ['PREFETCH_UNAVAILABLE']
    assert 'private cache' not in json.dumps(result)


def test_enhanced_lock_timeout_cannot_drop_guard_marker(tmp_path, monkeypatch):
    import fcntl

    from prism_core import report_research_prefetch as base

    def blocked(*args):
        raise BlockingIOError
    monkeypatch.setattr(fcntl, 'flock', blocked)
    async def no_transport(*args):
        raise AssertionError('no network while lock is held')
    result = asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
        _transport=no_transport, _config={'enabled': True, 'version': base.VERSION,
            'research_profile': insight.PROFILE, 'timeout_seconds': .01}, _cache_dir=tmp_path))
    assert result['receipt']['collector_version'] == insight.PROFILE
    assert result['receipt']['gaps'] == ['CACHE_LOCK_TIMEOUT']
    assert result['receipt']['calls_this_run'] == 0


def test_failed_or_http_unknown_viewer_cannot_authorize_issuer_alias():
    for status in (403, 503, None):
        async def transport(server, tool, args, _status=status):
            if tool == 'perplexity_search':
                return {'results': [{'url': BODY}]}
            if args['url'] == KIND:
                return {'markdown': '# Target (327260)\n\n| 회 사 명 : | Legal Name |',
                        'metadata': {'statusCode': _status}, 'links': [BODY + '#toc_1']}
            return {'markdown': '| 회 사 명 : | Legal Name |\n\n' + 'Legal Name competes with Alpha. ' * 50}
        state = {'sources': [], 'gaps': [], 'calls': 0}
        asyncio.run(insight.collect('KR', '327260', '2026-09-18', 'Target', transport, context={}, progress=state))
        assert not state['sources']
        assert ('VIEWER_HTTP_FAILURE' if status else 'VIEWER_HTTP_STATUS_UNKNOWN') in state['gaps']


def test_cache_write_failure_preserves_collected_evidence_and_call_count(tmp_path, monkeypatch):
    from prism_core import report_research_prefetch as base

    calls = []
    async def transport(server, tool, args):
        calls.append(tool)
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://example.com/report'}]}
        return {'markdown': 'Example Corp competes with Alpha in optical components. ' * 12}
    def failed_replace(*args):
        raise OSError('private path')
    monkeypatch.setattr(base.os, 'replace', failed_replace)
    packet = asyncio.run(base.prefetch_report_research('US', 'EXM', '20250918', 'Example Corp',
        _transport=transport, _config={'enabled': True, 'version': base.VERSION,
            'research_profile': insight.PROFILE}, _cache_dir=tmp_path))
    assert packet['receipt']['calls_this_run'] == len(calls)
    assert packet['receipt']['injected_sources'] > 0
    assert 'Alpha' in packet['section_notes']['news_analysis']
    assert 'private path' not in json.dumps(packet)
