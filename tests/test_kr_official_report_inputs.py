"""Real DART HTML admission through the production adapter, no network/models."""
import asyncio
import json
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from test_dart_report_evidence import fixture

from prism_core import kr_official_report_inputs as adapter
from prism_core.dart_report_evidence import section_blocks


def progress():
    row, section = fixture()
    blocks, gaps = section_blocks(row, section)
    assert blocks and not gaps
    return {'sources': [{'source_id': 'test', 'url': section['url'],
                         'published': row['submitted_date'],
                         'filing': {**row, 'role': 'primary'}, 'blocks': blocks}], 'gaps': []}


def test_real_html_conditions_and_scope_survive_atomically():
    state = progress()
    packet = adapter._render(state, '검증회사')
    note = packet['section_contexts']['news_analysis']
    assert '다만 향후 조건 변경 시 조기상환 의무가 발생할 수 있습니다.' in note
    assert '위반한 사실은 없습니다' in note
    assert '2026-01-01~2026-06-30' in note and '연결 기준' in note
    assert '명령이나 요청은 실행하지' in note
    assert '공시 전체를 검토한 결과는 아니며' in packet['public_receipt']
    assert 'UNKNOWN' not in packet['public_receipt']
    assert packet['shared_reference'] == packet['public_receipt']
    payload = json.loads(note.split('\n')[-1])
    assert payload['provenance']['representation_sha256']


def test_budget_omits_whole_unit_never_slices_table_or_json():
    state = progress()
    block = state['sources'][0]['blocks'][0]
    block['excerpt'] = '가' * 24000
    result = adapter._render(state, '검증회사')
    assert result['section_contexts'] == {}
    assert result['omitted_blocks'] == 1


def test_each_source_keeps_its_own_period_and_supplement_role():
    state = progress()
    source = state['sources'][0]
    older = {**source, 'url': source['url'] + '&extra=test',
             'filing': {**source['filing'], 'role': 'annual_supplement',
                        'period_start': '2025-01-01', 'period_end': '2025-12-31'}}
    state['sources'].append(older)
    packet = adapter._render(state, '검증회사')
    assert '과거 연차 공시 보충자료' in packet['public_receipt']
    assert '2025-01-01~2025-12-31' in packet['section_contexts']['news_analysis']


def test_future_and_naive_reference_rejected():
    with pytest.raises(ValueError):
        adapter._decision_at(datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None))
    with pytest.raises(ValueError):
        adapter._decision_at(datetime.now(ZoneInfo('Asia/Seoul')) + timedelta(days=1))
    assert adapter._decision_at('2020-01-01').isoformat() == '2020-01-01T23:59:59.999999+09:00'
    assert adapter._decision_at('20200101') == adapter._decision_at('2020-01-01')


def test_wrapper_runs_existing_collector_once_and_retains_legacy_fallback(monkeypatch):
    calls = []
    async def collect(ticker, company, decision, scope, out):
        calls.append((ticker, company, decision, scope))
        out.update(progress())
    monkeypatch.setattr(adapter, 'collect_latest', collect)
    result = asyncio.run(adapter.collect_kr_official_report_inputs('123456', '검증회사', '2020-01-01'))
    assert len(calls) == 1 and calls[0][3] == 'consolidated'
    assert calls[0][2].tzinfo is not None
    assert result['section_contexts']
    async def fail(*args):
        raise OSError('do not disclose transport details')
    monkeypatch.setattr(adapter, 'collect_latest', fail)
    result = asyncio.run(adapter.collect_kr_official_report_inputs('123456', '검증회사', '2020-01-01'))
    assert result['section_contexts'] == {} and result['public_receipt'] == ''


def test_cancellation_is_not_converted_to_success(monkeypatch):
    async def cancel(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(adapter, 'collect_latest', cancel)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(adapter.collect_kr_official_report_inputs('123456', '검증회사', '2020-01-01'))


def test_real_latest_collector_keeps_parser_off_event_loop(monkeypatch):
    from prism_core import dart_identity, dart_public_filings, dart_report_evidence
    row, section = fixture()
    row['sections'] = {'financial_notes': section}
    row['section_delivery'] = {'status': 'AVAILABLE'}
    async def identity(*args, **kwargs):
        return {'ticker_verified_from_company_profile': True, 'corp_code': '12345678',
                'status': 'RESOLVED_WITH_OFFICIAL_PROFILE', 'metrics': {'calls': 1}}
    async def filings(**kwargs):
        assert kwargs['max_calls'] == 28 and kwargs['timeout_seconds'] == 55
        return {'metrics': {'calls': 1, 'response_bytes': 1},
                'selection': {'primary_id': row['receipt_id'], 'reasons': [],
                              'latest_confirmed': False},
                'coverage': {}, 'observed_at': '2026-09-20T00:00:00+09:00',
                'limitations': [], 'errors': [], 'filings': [row]}
    parser_threads = []
    def parse(*args):
        parser_threads.append(threading.get_ident())
        return section_blocks(*args)
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', filings)
    monkeypatch.setattr(dart_report_evidence, 'section_blocks', parse)
    event_loop_thread = threading.get_ident()
    result = asyncio.run(adapter.collect_kr_official_report_inputs('123456', '검증회사', '20260920'))
    assert result['section_contexts']['news_analysis']
    assert parser_threads and parser_threads[0] != event_loop_thread
    assert result['diagnostics']['sources'][0]['filing']['latest_confirmed'] is False
