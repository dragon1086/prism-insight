import asyncio
import hashlib
import json
from datetime import datetime, timezone

import httpx

from prism_core import report_research_prefetch as research
from prism_core import sec_report_evidence as evidence
from prism_core.report_insight_prefetch import packet

URL = 'https://www.sec.gov/Archives/edgar/data/789019/000119312526191507/msft-20260331.htm'
CUTOFF = datetime(2026, 9, 20, 1, tzinfo=timezone.utc)


def catalog():
    row = {'accession': '0001193125-26-191507', 'form': '10-Q', 'source_url': URL,
           'report_date': '2026-03-31', 'filing_date': '2026-04-29',
           'acceptance_at': '2026-04-29T20:00:00+00:00'}
    return {'status': 'PARTIAL', 'identity': {'cik': '0000789019', 'ticker': 'MSFT', 'verified': True},
            'primary': row, 'annual_supplement': None, 'events': [], 'amendments': [],
            'selection': {'primary_id': row['accession'], 'annual_supplement_id': None,
                          'latest_confirmed': False, 'blocked_by': []},
            'coverage': {'complete_within_query': True}, 'gaps': [], 'metrics': {'calls': 2, 'response_bytes': 1000}}


def install(monkeypatch, result=None, status=200):
    from prism_core import sec_public_filings

    async def collect(**kwargs):
        kwargs['_metrics'].update(calls=2, response_bytes=1000)
        return result or catalog()
    monkeypatch.setattr(sec_public_filings, 'collect_sec_public_filings', collect)
    body = '<html>fixture</html>'
    facts = [{'concept': '{http://fasb.org/us-gaap/2026}Revenues', 'context_ref': 'c1',
              'entity': {'scheme': 'http://www.sec.gov/CIK', 'identifier': '0000789019'},
              'period': {'start': '2026-01-01', 'end': '2026-03-31'},
              'dimensions': [], 'unit': 'USD', 'value': '1234000', 'raw_value': '1,234',
              'scale': 3, 'sign': None, 'decimals': '-3', 'precision': None,
              'source_path': '/html/body/ix:nonfraction[1]', 'source_line': 2}]
    monkeypatch.setattr(evidence, 'parse_inline_revenue', lambda raw, expected_cik: {
        'status': 'ok', 'source_sha256': hashlib.sha256(raw.encode()).hexdigest(), 'facts': facts, 'gaps': []})
    requests = []
    def handler(req):
        requests.append(str(req.url))
        return httpx.Response(status, text=body)
    return requests, lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def run(factory, state=None):
    state = state or {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(evidence.collect_latest('MSFT', CUTOFF, 'Example contact@example.org', state,
                                        client_factory=factory))
    return packet('US', 'MSFT', '2026-09-19', state)


def test_selected_raw_facts_keep_period_unit_scale_and_source(monkeypatch):
    requests, factory = install(monkeypatch)
    result = run(factory)
    records = [r for note in result['section_notes'].values() for r in json.loads(note)['sources']]
    assert len(requests) == 1 and records
    text = json.dumps(records)
    for value in ('1234000', 'USD', '2026-01-01', '2026-03-31', URL, 'context_ref'):
        assert value in text
    assert result['receipt']['sec_calls'] == 3
    assert all(len(note.encode()) <= 6000 for note in result['section_notes'].values())
    assert 'SEC_FACTS_ONLY_NOT_FULL_FILING' in result['receipt']['gaps']


def test_failed_latest_document_never_substitutes_old_annual(monkeypatch):
    result = catalog()
    result['annual_supplement'] = {**result['primary'], 'form': '10-K', 'report_date': '2025-06-30'}
    requests, factory = install(monkeypatch, result=result, status=403)
    output = run(factory)
    assert len(requests) == 1
    assert output['receipt']['usable_sources'] == 0
    assert 'SEC_DOCUMENT_HTTP_403' in output['receipt']['gaps']


def test_wrong_issuer_locator_never_requested(monkeypatch):
    result = catalog()
    result['primary']['source_url'] = URL.replace('/789019/', '/123/')
    requests, factory = install(monkeypatch, result=result)
    output = run(factory)
    assert requests == [] and output['receipt']['usable_sources'] == 0


def test_unverified_identity_or_absent_primary_never_gets_body(monkeypatch):
    result = catalog()
    result['identity']['verified'] = False
    requests, factory = install(monkeypatch, result=result)
    assert run(factory)['receipt']['usable_sources'] == 0
    assert requests == []


def test_real_inline_parser_reaches_actual_report_prompt(monkeypatch, tmp_path):
    from test_sec_inline_evidence import document, fact

    from cores.agents.report_agent import ReportAgent
    from prism_core import sec_public_filings
    from prism_core.report_research_context import apply_section_research
    from prism_core.sec_inline_evidence import parse_inline_revenue

    calls = []
    body = document(fact('2', 'scale="3"'), cik='789019', start='2026-01-01', end='2026-03-31')
    async def collector(**kwargs):
        kwargs['_metrics'].update(calls=2, response_bytes=100)
        calls.append(kwargs['decision_at'])
        return catalog()
    monkeypatch.setattr(sec_public_filings, 'collect_sec_public_filings', collector)
    monkeypatch.setattr(evidence, 'parse_inline_revenue', parse_inline_revenue)
    original = evidence.collect_latest
    async def collect(symbol, cutoff, ua, progress):
        return await original(symbol, cutoff, ua, progress, client_factory=lambda **opts: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=body)), **opts))
    monkeypatch.setattr(evidence, 'collect_latest', collect)
    async def transport(*args):
        return {'results': []}
    config = {'enabled': True, 'version': research.VERSION, 'research_profile': 'insight_prefetch_v5',
              'filing_parser': 'sec_inline_v1', 'latest_periodic_filings': True,
              'sec_user_agent': 'Example contact@example.org'}
    def prefetch(**kwargs):
        return asyncio.run(research.prefetch_report_research('US', 'MSFT', '20260919', 'Example',
            decision_at=CUTOFF, _config={**config, **kwargs}, _cache_dir=tmp_path, _transport=transport))
    result = prefetch()
    note = result['section_notes']['company_overview']
    assert '2000' in note and '}EUR' in note and '2026-01-01' in note
    assert 'source_xpath' in note and 'SOURCE_TEXT_NOT_FACT_VALIDATED' in note
    agent = apply_section_research(ReportAgent('overview', 'Existing rules.', []), 'company_overview',
        {'report_research': result}, '20260919', 'en')
    assert '2000' in agent.instruction and 'CloudMember' in agent.instruction
    assert len(calls) == 1
    cached = prefetch()['receipt']
    assert cached['cache_hit'] and cached['sec_calls_this_run'] == 0
    assert len(calls) == 1
    prefetch(latest_periodic_filings=False)
    assert len(calls) == 1


def test_us_sec_flag_without_contact_does_not_fetch_or_fallback(monkeypatch, tmp_path):
    from prism_core import sec_public_filings
    requests = []
    def forbidden(*args, **kwargs):
        requests.append(1)
        raise AssertionError('no network')
    monkeypatch.setattr(sec_public_filings.httpx, 'AsyncClient', forbidden)
    async def transport(server, tool, args):
        assert tool == 'perplexity_search'
        return {'results': [{'url': URL}]}
    result = asyncio.run(research.prefetch_report_research('US', 'MSFT', '20260919', 'Example',
        _config={'enabled': True, 'version': research.VERSION, 'research_profile': 'insight_prefetch_v5',
                 'filing_parser': 'sec_inline_v1', 'latest_periodic_filings': True},
        _cache_dir=tmp_path, _transport=transport))
    assert not requests
    assert 'SEC_USER_AGENT_REQUIRED' in result['receipt']['gaps']
    assert result['receipt']['sec_calls'] == 0
