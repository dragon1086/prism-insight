"""Real collector -> section adapter -> packet -> report prompt; network disabled."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from test_dart_public_filings import BODY, RECORDS, catalog, cover, main

from cores.agents.report_agent import ReportAgent
from prism_core import dart_identity, dart_public_filings
from prism_core import report_insight_prefetch as insight
from prism_core import report_research_prefetch as research
from prism_core.report_research_context import apply_section_research

DAY = '2026-09-18'
CONFIG = {'enabled': True, 'version': research.VERSION, 'research_profile': insight.PROFILE,
          'filing_parser': 'material_v2', 'latest_periodic_filings': True}
NOTES = ('<h2>3. 연결재무제표 주석</h2><h3>38. 차입금 및 약정사항</h3>'
         '<p>차입금 약정이 있으나 위반한 사실은 없습니다.</p>'
         '<p>다만 향후 조건 변경 시 조기상환 의무가 발생할 수 있습니다.</p>')


def install(monkeypatch, *, records=None, missing_latest=False, identity_ok=True):
    records = records or [RECORDS[0], RECORDS[2]]
    calls = []
    async def identity(*args, **kwargs):
        calls.append(('identity', args))
        kwargs.get('_metrics', {}).update(calls=2, response_bytes=400)
        return {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE' if identity_ok else 'IDENTITY_UNRESOLVED',
                'corp_code': '01343665' if identity_ok else None,
                'ticker_verified_from_company_profile': identity_ok, 'metrics': {'calls': 2, 'response_bytes': 400}}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    def handler(request):
        rid = request.url.params.get('rcpNo')
        calls.append(('http', str(request.url)))
        if request.method == 'POST':
            return httpx.Response(200, text=catalog(records))
        row = next(r for r in records if r[0] == rid)
        if request.url.path.endswith('main.do'):
            fields = {'text': '3. 연결재무제표 주석', 'rcpNo': rid, 'dcmNo': '12345678',
                      'eleId': '3', 'offset': '100', 'length': '1000', 'dtd': 'dart4.xsd'}
            script = ''.join(f'node3["{k}"] = {json.dumps(v, ensure_ascii=False)};\n' for k, v in fields.items())
            body = main(rid).replace('</script>', script + '</script>')
        elif request.url.params['eleId'] == '0':
            body = cover(row)
        elif request.url.params['eleId'] == '3':
            body = NOTES
        else:
            if missing_latest and rid == records[0][0]:
                return httpx.Response(503)
            body = '<h2>2. 연결재무제표</h2>' + BODY
        return httpx.Response(200, text=body)
    original = dart_public_filings.collect_dart_periodic_filings
    async def collector(**kwargs):
        return await original(**kwargs, client_factory=lambda **opts: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **opts))
    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', collector)
    return calls


async def empty_transport(*args):
    return {'results': []}


def run(tmp_path, **kwargs):
    return asyncio.run(research.prefetch_report_research('KR', '327260', DAY, '샘플',
        _cache_dir=tmp_path, _transport=empty_transport, _config=CONFIG, **kwargs))


def test_real_latest_and_annual_are_distinct_in_actual_report_prompt(monkeypatch, tmp_path):
    calls = install(monkeypatch)
    result = run(tmp_path)
    receipt = result['receipt']
    assert receipt['filing_selection']['selection']['primary_id'] == RECORDS[0][0]
    assert receipt['filing_selection']['selection']['annual_supplement_id'] == RECORDS[2][0]
    assert receipt['dart_calls'] > 2
    assert receipt['filing_selection']['decision_at'] == DAY + 'T00:00:00+09:00'
    assert '"html":' not in json.dumps(receipt).lower()
    records = [insight.expand_filing_record(r, json.loads(note))
               for note in result['section_notes'].values() for r in json.loads(note)['sources']]
    assert records
    roles = {r['filing']['role'] for r in records}
    assert roles == {'primary', 'annual_supplement'}
    assert all(r['filing']['latest_confirmed'] is False for r in records)
    assert all(r['filing']['event_date'] == 'UNKNOWN' for r in records)
    for record in records:
        expected = '2026-06-30' if record['filing']['role'] == 'primary' else '2025-12-31'
        assert record['filing']['period_end'] == expected
    owner = next(k for k, note in result['section_notes'].items() if '다만' in note)
    agent = apply_section_research(ReportAgent('review', 'Existing rules.', []), owner,
        {'report_research': result}, DAY.replace('-', ''), 'ko')
    assert '다만' in agent.instruction and '2026-06-30' in agent.instruction
    assert all(len(note.encode()) <= 6000 for note in result['section_notes'].values())
    before = len(calls)
    cached = run(tmp_path)['receipt']
    assert cached['cache_hit']
    assert cached['dart_calls_this_run'] == cached['total_calls_this_run'] == 0
    assert len(calls) == before


def test_latest_missing_does_not_fallback_to_annual(monkeypatch, tmp_path):
    install(monkeypatch, missing_latest=True)
    result = run(tmp_path)
    assert result['receipt']['filing_selection']['selection']['primary_id'] is None
    assert result['receipt']['usable_sources'] == 0
    assert 'DART_LATEST_FILING_UNAVAILABLE' in result['receipt']['gaps']


def test_same_day_date_only_publication_blocks_older_fallback(monkeypatch, tmp_path):
    install(monkeypatch, records=[(RECORDS[0][0], '반기', '2026.06', DAY), RECORDS[2]])
    result = run(tmp_path, decision_at=datetime(2026, 9, 18, 1, tzinfo=timezone.utc))
    assert result['receipt']['usable_sources'] == 0
    assert result['receipt']['filing_selection']['selection']['primary_id'] is None


@pytest.mark.parametrize('market,config', [('US', CONFIG), ('KR', {**CONFIG, 'latest_periodic_filings': False}),
    ('KR', {**CONFIG, 'filing_parser': 'structured_v1'}), ('KR', {**CONFIG, 'enabled': False})])
def test_off_us_and_old_parser_never_contact_dart(monkeypatch, tmp_path, market, config):
    calls = install(monkeypatch)
    result = asyncio.run(research.prefetch_report_research(market, '327260', DAY, '샘플',
        _config=config, _cache_dir=tmp_path, _transport=empty_transport))
    assert calls == []
    if result:
        assert 'filing_selection' not in result['receipt']


def test_cutoffs_have_distinct_cache_and_invalid_cutoff_never_fetches(monkeypatch, tmp_path):
    calls = install(monkeypatch)
    run(tmp_path)
    before = len(calls)
    assert not run(tmp_path, decision_at=datetime(2026, 9, 18, 1, tzinfo=timezone.utc))['receipt']['cache_hit']
    assert len(calls) > before
    before = len(calls)
    result = run(tmp_path, decision_at=datetime(2026, 9, 19, tzinfo=timezone.utc))
    assert 'PREFETCH_UNAVAILABLE' in result['receipt']['gaps']
    assert len(calls) == before


def test_identity_failure_does_not_promote_search_periodic_body(monkeypatch, tmp_path):
    calls = install(monkeypatch, identity_ok=False)
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260318001224'}]}
        return {'markdown': '# 사업보고서\n\n샘플의 매출액은 증가했습니다.'}
    result = asyncio.run(research.prefetch_report_research('KR', '327260', DAY, '샘플',
        _config=CONFIG, _cache_dir=tmp_path, _transport=transport))
    assert result['receipt']['usable_sources'] == 0
    assert 'DART_ISSUER_UNRESOLVED' in result['receipt']['gaps']
    assert len(calls) == 1


@pytest.mark.parametrize('title,expected', [('주요사항보고서 유상증자 결정', True),
                                          ('반 기 보 고 서', False)])
def test_official_event_with_company_cover_is_not_mistaken_for_periodic(monkeypatch, tmp_path, title, expected):
    install(monkeypatch, identity_ok=False)
    async def transport(server, tool, args):
        if tool == 'perplexity_search':
            return {'results': [{'url': 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260917000001'}]}
        return {'markdown': f'# {title}\n\n| 회사명 | 샘플 |\n\n'
                '샘플의 매출액과 영업이익률에 영향을 줄 수 있는 계약 조건과 위험을 확인합니다.\n\n' +
                '샘플의 수주 계약은 후속 조건에 따라 달라질 수 있습니다. ' * 12,
                'metadata': {'title': title, 'publishedTime': '2026-09-17'}}
    result = asyncio.run(research.prefetch_report_research('KR', '327260', DAY, '샘플',
        _config=CONFIG, _cache_dir=tmp_path, _transport=transport))
    assert bool(result['receipt']['usable_sources']) is expected


def test_outer_timeout_retains_dart_metrics(monkeypatch, tmp_path):
    async def identity(*args, **kwargs):
        kwargs['_metrics'].update(calls=2, response_bytes=1234)
        await asyncio.sleep(10)
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    result = asyncio.run(research.prefetch_report_research('KR', '327260', DAY, '샘플',
        _config={**CONFIG, 'timeout_seconds': .01}, _cache_dir=tmp_path, _transport=empty_transport))
    assert result['receipt']['dart_calls'] == 2
    assert result['receipt']['dart_response_bytes'] == 1234
    assert 'TIME_BUDGET_EXHAUSTED' in result['receipt']['gaps']
