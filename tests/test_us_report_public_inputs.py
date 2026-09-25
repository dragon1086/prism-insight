"""Public source collection feeds existing agents without extra models or orders."""
import asyncio

from cores.agents.report_agent import ReportAgent
from prism_core.us_report_public_inputs import (
    apply_public_report_inputs,
    collect_us_public_report_inputs,
    has_macro_evidence,
    public_macro_identity,
    render_public_source_receipt,
)


def company_packet():
    return {'status': 'complete', 'reference_date': '2026-09-23', 'retrieved_at': '2026-09-23T07:00:00+00:00',
            'issues': [], 'sources': [{'filing_type': '8-K', 'publication_date': '2026-07-23',
            'publication_date_basis': 'SEC filing date',
            'report_date': '2026-06-30', 'fiscal_period': 'quarter ended June 30, 2026',
            'url': 'https://www.sec.gov/Archives/edgar/data/1/2/release.htm',
            'retrieval_url': 'https://www.sec.gov/Archives/edgar/data/1/2/release.htm',
            'guidance': ['GUIDANCE_SOURCE: updated USD 11.05–11.25; prior USD 10.63–10.83'],
            'segments': ['SEGMENT_SOURCE: three months; USD millions; DIS 2978'],
            'financials': ['FINANCIAL_SOURCE: quarter revenues USD 3043 million']}]}


def macro_packet():
    return {'as_of': '2026-09-23', 'sources': {
        'cpi': {'status': 'released', 'observed_period': 'August 2026', 'published_date': '2026-09-11',
                'url': 'https://www.bls.gov/news.release/cpi.nr0.htm', 'excerpt': 'CPI_SOURCE_SENTINEL'},
        'pce': {'status': 'released', 'observed_period': 'July 2026', 'published_date': '2026-08-26',
                'url': 'https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026',
                'next_release': {'status': 'not_yet_released', 'date': '2026-09-30'}},
        'treasury': {'status': 'released', 'observed_date': '2026-09-21',
                     'url': 'https://fred.stlouisfed.org/graph/fredgraph.csv',
                     'yield_2y_percent': 4.76, 'yield_10y_percent': 4.96,
                     'spread_10y_minus_2y_pp': .2, 'definition': 'same-date constant maturity'}}}


def test_company_context_scoped_and_existing_servers_unchanged():
    original = ReportAgent('company', 'BASE', ('time',))
    pf = {'official_company': company_packet()}
    status = apply_public_report_inputs(original, 'company_status', pf, 'en')
    overview = apply_public_report_inputs(original, 'company_overview', pf, 'en')
    assert 'GUIDANCE_SOURCE' in status.instruction and 'FINANCIAL_SOURCE' in status.instruction
    assert 'SEGMENT_SOURCE' in overview.instruction and 'GUIDANCE_SOURCE:' not in overview.instruction
    assert 'FINANCIAL_SOURCE' in overview.instruction
    assert 'instructions' in status.instruction and 'Company guidance is not consensus' in status.instruction
    assert original.instruction == 'BASE' and status.server_names == original.server_names


def test_macro_source_data_is_not_an_authoritative_regime():
    original = ReportAgent('market', 'PRICE ONLY', ())
    result = apply_public_report_inputs(original, 'market_index_analysis', {'official_macro': macro_packet()}, 'en')
    assert 'CPI_SOURCE_SENTINEL' in result.instruction
    assert '2026-09-30' in result.instruction and 'Never change trading regime' in result.instruction
    assert has_macro_evidence(macro_packet())
    assert not has_macro_evidence({'sources': {'cpi': {'status': 'not_found'}}})


def test_segment_rows_inside_financial_table_reach_overview_without_category():
    packet = company_packet()
    packet['sources'][0]['segments'] = []
    packet['sources'][0]['financials'] = [('Three months ended June 30; USD millions; '
        '[c1] Diagnostic Information Services revenues | [c2] 2,978 | [c4] 2,699')]
    agent = apply_public_report_inputs(ReportAgent('overview', 'BASE'), 'company_overview',
                                      {'official_company': packet}, 'en')
    assert '2,978' in agent.instruction and 'USD millions' in agent.instruction
    assert 'categories route inputs, not issuer disclosures' in agent.instruction


def test_public_receipts_exclude_raw_excerpts_and_keep_dates_and_definitions():
    company = render_public_source_receipt(company_packet(), 'company', 'ko')
    macro = render_public_source_receipt(macro_packet(), 'macro', 'ko')
    assert 'GUIDANCE_SOURCE' not in company and 'FINANCIAL_SOURCE' not in company
    assert '2026-07-23' in company and '가이던스' in company
    assert 'CPI_SOURCE_SENTINEL' not in macro and '2026-09-30' in macro
    assert '+0.20%p' in macro and '4.76%' in macro
    assert '확정된 매매 국면' not in macro


def test_unavailable_is_not_unreleased_or_negative():
    text = render_public_source_receipt({'sources': {'cpi': {'status': 'not_found', 'error': 'PRIVATE_DETAIL'}}}, 'macro')
    assert '미발표라는 뜻은 아닙니다' in text and 'PRIVATE_DETAIL' not in text
    assert '자료 부재나 부정적 전망' in render_public_source_receipt(None, 'company')


def test_market_prose_cache_identity_tracks_evidence_not_capture_clock():
    import copy

    from prism_core.market_report_singleflight import MarketReportCache
    first = macro_packet()
    recaptured = copy.deepcopy(first)
    recaptured['sources']['cpi']['captured_at'] = '2026-09-23T08:00:00+00:00'
    changed = copy.deepcopy(first)
    changed['sources']['cpi']['excerpt'] = 'NEW_RELEASE_SENTINEL'
    assert public_macro_identity(first) == public_macro_identity(recaptured)
    assert public_macro_identity(first) != public_macro_identity(changed)

    async def run():
        cache = MarketReportCache()
        calls = []

        async def generate(value):
            calls.append(value)
            return value

        for packet, body, expected in ((first, 'FIRST', 'FIRST'), (recaptured, 'IGNORED', 'FIRST'),
                                       (changed, 'NEW', 'NEW')):
            result = await cache.get('20260923', 'ko', lambda body=body: generate(body),
                                     evidence_key=public_macro_identity(packet))
            assert result == expected
        assert calls == ['FIRST', 'NEW']

    asyncio.run(run())


def test_collectors_run_off_event_loop_and_isolate_failure(monkeypatch, tmp_path):
    import threading

    import prism_core.us_official_company_sources as company
    import prism_core.us_official_macro_sources as macro
    main_thread = threading.get_ident()
    calls = []

    def collect_company(ticker, reference, **kwargs):
        assert threading.get_ident() != main_thread
        calls.append((ticker, reference, kwargs['filings'], kwargs['company_website']))
        raise RuntimeError('PRIVATE_ERROR')

    def collect_macro(reference, **kwargs):
        assert threading.get_ident() != main_thread and kwargs['cache_dir'] == tmp_path
        return macro_packet()

    monkeypatch.setattr(company, 'collect_official_company_sources', collect_company)
    monkeypatch.setattr(macro, 'collect_us_official_macro_sources', collect_macro)
    result = asyncio.run(collect_us_public_report_inputs('DGX', '20260923', ['FILINGS'], tmp_path, 'https://example.com'))
    assert calls == [('DGX', '20260923', ['FILINGS'], 'https://example.com')]
    assert result['official_company']['status'] == 'collection_unavailable'
    assert result['official_macro'] == macro_packet() and 'PRIVATE_ERROR' not in str(result)


def test_korean_receipt_labels_issuer_release_and_treasury_in_korean():
    packet = company_packet()
    packet['sources'][0]['filing_type'] = 'Issuer release'
    long_definition = ('Federal Reserve H.15 via FRED; Treasury constant maturity market yields, investment basis, '
                       'percent, not seasonally adjusted; 10Y minus 2Y in percentage points. '
                       'Not the fetched Treasury par-curve source.')
    macro = macro_packet()
    macro['sources']['treasury']['definition'] = long_definition
    company_ko = render_public_source_receipt(packet, 'company', 'ko')
    macro_ko = render_public_source_receipt(macro, 'macro', 'ko')
    assert '[회사 실적 발표문](' in company_ko and 'Issuer release' not in company_ko
    assert '2년물 4.76%, 10년물 4.96%; 10년물−2년물 +0.20%p.' in macro_ko
    assert 'Federal Reserve' not in macro_ko and 'Trea' not in macro_ko
    assert '[Issuer release](' in render_public_source_receipt(packet, 'company', 'en')
    assert long_definition in render_public_source_receipt(macro, 'macro', 'en')  # not truncated
