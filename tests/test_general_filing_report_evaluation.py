import asyncio
import json

import pytest

from tools.evaluate_general_filing_reports import evaluate, main

MANIFEST = {'version': 1, 'decision_at': '2026-09-20T01:28:11+00:00', 'scope': 'consolidated',
            'KR': {'development': [{'ticker': '000001', 'name': 'Example', 'sector': 'test'},
                                   {'ticker': '000002', 'name': 'Failure', 'sector': 'test'}]}}


def test_real_packet_and_failed_denominator_without_provider_text():
    seen = []
    async def collector(ticker, name, cutoff, scope, progress):
        seen.append(ticker)
        progress['dart_metrics'] = {'identity': {'calls': 2, 'response_bytes': 30}}
        if ticker == '000002':
            raise RuntimeError('secret provider html text')
        progress['filing_selection'] = {'identity': {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE',
            'corp_code': '12345678', 'ticker_verified_from_company_profile': True},
            'selection': {'status': 'INCOMPLETE', 'primary_id': '123', 'annual_supplement_id': '456',
                          'latest_confirmed': False, 'reasons': ['CATALOG_COVERAGE_UNCONFIRMED']},
            'selected_section_delivery': {'123': {'status': 'AVAILABLE', 'missing_sections': [], 'errors': []}}}
        progress['sources'].append({'source_id': 'D-123', 'url': 'https://dart.fss.or.kr/',
            'filing': {'role': 'primary', 'receipt_id': '123', 'period_start': '2026-01-01',
                       'period_end': '2026-06-30', 'scope': scope, 'section': 'financial_notes'},
            'blocks': [{'topic': 'ownership_governance', 'excerpt': 'private fixture text not to output',
                        'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
                        'provenance': {'source_path': '/p', 'representation_sha256': 'a' * 64}}]})
    result = asyncio.run(evaluate(MANIFEST, 'development', collector=collector))
    assert seen == ['000001', '000002']
    assert result['summary']['case_count'] == 2
    assert result['summary']['failed_count'] == 1
    assert result['summary']['identity_verified_count'] == 1
    assert result['summary']['latest_confirmed_count'] == 0
    assert result['summary']['final_records_present_count'] == 1
    assert result['summary']['calls'] == 4 and result['summary']['response_bytes'] == 60
    assert result['cases'][0]['candidate_sources'][0]['period_end'] == '2026-06-30'
    assert result['cases'][0]['candidate_sources'][0]['representation_sha256'] == ['a' * 64]
    assert result['cases'][1]['failure'] == 'COLLECTION_FAILED'
    assert 'private fixture' not in json.dumps(result) and 'secret provider' not in json.dumps(result)


@pytest.mark.parametrize('changes', [{'version': True}, {'decision_at': '2026-09-20'},
                                     {'scope': 'bad'}, {'KR': {'development': []}}])
def test_invalid_manifest_does_not_collect(changes):
    async def forbidden(*args):
        pytest.fail('must not collect')
    with pytest.raises(ValueError, match='INVALID_MANIFEST'):
        asyncio.run(evaluate({**MANIFEST, **changes}, 'development', collector=forbidden))


def test_packet_failure_retains_measured_case():
    async def collector(*args):
        args[-1]['dart_metrics'] = {'filings': {'calls': 3, 'response_bytes': 40}}
    def fail(*args):
        raise RuntimeError('secret')
    result = asyncio.run(evaluate(MANIFEST, 'development', collector=collector, packet_builder=fail))
    assert result['summary']['failed_count'] == 2
    assert result['summary']['calls'] == 6
    assert all(r['failure'] == 'PACKET_EVALUATION_FAILED' for r in result['cases'])
    assert 'secret' not in json.dumps(result)


def test_cli_explicit_live_and_no_overwrite(tmp_path, capsys):
    path = tmp_path / 'out'
    assert main(['--group', 'development', '--out', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'LIVE_ACK_REQUIRED'
    path.touch()
    assert main(['--live', '--group', 'development', '--out', str(path)]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'OUTPUT_EXISTS'


def test_cancellation_propagates():
    async def cancel(*args):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(evaluate(MANIFEST, 'development', collector=cancel))


def test_provider_outage_stops_repeated_requests_without_removing_denominator():
    cases = [*MANIFEST['KR']['development'], {'ticker': '000003', 'name': 'Not requested', 'sector': 'test'}]
    seen = []
    async def unavailable(symbol, name, cutoff, scope, progress):
        seen.append(symbol)
        progress['dart_metrics'] = {'identity': {'calls': 1, 'response_bytes': 0}}
        progress['filing_selection'] = {'identity': {'reason': 'IDENTITY_TRANSPORT_FAILURE'}}
    result = asyncio.run(evaluate({**MANIFEST, 'KR': {'development': cases}}, 'development', collector=unavailable))
    assert len(seen) == 2
    assert result['summary']['case_count'] == 3
    assert result['summary']['unavailable_count'] == 2
    assert result['summary']['provider_outage_not_run_count'] == 1
    assert result['summary']['general_use_certified'] is False
    assert result['summary']['calls'] == 2


def test_selected_body_hash_survives_zero_admissible_blocks(monkeypatch):
    import hashlib
    from datetime import datetime
    from urllib.parse import urlencode

    from prism_core import dart_identity, dart_public_filings
    from prism_core.dart_report_evidence import collect_latest

    raw = '<p>No source-local scope declaration.</p>'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    node = {'rcpNo': '20260814000001', 'dcmNo': '12345678', 'eleId': '3',
            'offset': '100', 'length': '1000', 'dtd': 'dart4.xsd'}
    url = 'https://dart.fss.or.kr/report/viewer.do?' + urlencode(node)
    async def identity(*args, **kwargs):
        return {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE', 'corp_code': '12345678',
                'ticker_verified_from_company_profile': True}
    async def filings(**kwargs):
        return {'metrics': {'calls': 1, 'response_bytes': len(raw.encode())},
            'selection': {'primary_id': '20260814000001', 'annual_supplement_id': None,
                          'latest_confirmed': False, 'reasons': []},
            'coverage': {}, 'observed_at': '2026-09-20T00:00:00+00:00', 'limitations': [], 'errors': [],
            'filings': [{'receipt_id': '20260814000001', 'scope_verified': True,
                         'body_status': 'available', 'scope': 'consolidated',
                         'section_delivery': {'status': 'PARTIAL'},
                         'sections': {'financial_notes': {'html': raw, 'sha256': digest,
                             'utf8_bytes': len(raw.encode()), 'url': url, 'tuple': node}}}]}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', filings)
    progress = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(collect_latest('000001', 'Example', datetime.fromisoformat(MANIFEST['decision_at']),
                               'consolidated', progress))
    assert progress['sources'] == []
    assert 'DART_SECTION_SCOPE_UNRESOLVED' in progress['gaps']
    preserved = progress['filing_selection']['selected_section_provenance']['20260814000001']['financial_notes']
    assert preserved == {'sha256': digest, 'utf8_bytes': len(raw.encode()),
                         'url': url}
    assert raw not in json.dumps(progress['filing_selection'])
