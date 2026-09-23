"""Bounded raw DART section delivery; synthetic bodies, no live network."""
import asyncio
import hashlib
import json

import httpx
import pytest
from test_dart_public_filings import (
    BODY,
    RECORDS,
    catalog,
    collect_with_transport,
    cover,
    main,
)

from prism_core import dart_public_filings

NOTES = ('<h2>3. 연결재무제표 주석</h2><p>(단위: 백만원)</p>'
         '<p>계약금은 허가 취득 조건 충족 시 수령합니다.</p><p>주1) 반환 의무가 있습니다.</p>')


@pytest.fixture(autouse=True)
def no_wall_pacing(monkeypatch):
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(dart_public_filings.asyncio, 'sleep', no_wait)


def run_sections(*, notes=NOTES, financial=BODY, include=True, node=True, main_mutate=None,
                 response_hook=None, **kwargs):
    requests = []
    def handler(request):
        requests.append(request)
        if response_hook:
            response = response_hook(request)
            if response is not None:
                return response
        if request.method == 'POST':
            body = catalog(RECORDS[:1])
        elif request.url.path.endswith('main.do'):
            body = main(RECORDS[0][0])
            if node:
                fields = {'text': '3. 연결재무제표 주석', 'rcpNo': RECORDS[0][0],
                          'dcmNo': '12345678', 'eleId': '3', 'offset': '100',
                          'length': '100', 'dtd': 'dart4.xsd'}
                script = ''.join(f'node3["{k}"] = {json.dumps(v, ensure_ascii=False)};\n' for k, v in fields.items())
                body = body.replace('</script>', script + '</script>')
            if main_mutate:
                body = main_mutate(body)
        elif request.url.params['eleId'] == '0':
            body = cover(RECORDS[0])
        elif request.url.params['eleId'] == '3':
            body = notes
        else:
            body = financial
        return httpx.Response(200, text=body)
    return asyncio.run(collect_with_transport(handler, include_section_bodies=include, **kwargs)), requests


def test_default_provenance_only_and_no_extra_requests():
    result, requests = run_sections(include=False)
    assert len(requests) == 4
    assert 'section_delivery' not in result['filings'][0]
    assert 'html' not in result['filings'][0]['sections']['financial_statements']


def test_exact_sections_keep_units_footnotes_conditions_and_original_hashes():
    result, requests = run_sections()
    row = result['filings'][0]
    assert row['section_delivery']['status'] == 'AVAILABLE'
    assert len(requests) == 5
    for name, body in [('financial_statements', BODY), ('financial_notes', NOTES)]:
        section = row['sections'][name]
        assert section['html'] == body
        assert section['sha256'] == hashlib.sha256(body.encode()).hexdigest()
        assert section['tuple']['rcpNo'] == row['receipt_id']
    assert row['period_end'] == '2026-06-30'
    assert row['scope'] == 'consolidated'


def test_document_larger_than_limit_is_not_fetched_or_truncated():
    financial = BODY + '<p>' + 'x' * 1_100_000 + '</p>'
    notes = NOTES + '<p>' + 'y' * 1_100_000 + '</p>'
    result, requests = run_sections(financial=financial, notes=notes)
    row = result['filings'][0]
    assert row['section_delivery']['status'] == 'AVAILABLE'
    assert row['sections']['financial_statements']['html'] == financial
    assert row['sections']['financial_notes']['html'] == notes
    assert len(requests) == 5


@pytest.mark.parametrize('kwargs,code', [
    ({'node': False}, 'NOTES_NODE_AMBIGUOUS'),
    ({'notes': '<h2>5. 재무제표 주석</h2>'}, 'SECTION_SCOPE_MISMATCH'),
    ({'notes': '<p>자료 없음</p>'}, 'NOTES_BODY_UNVERIFIED'),
    ({'notes': NOTES + 'x' * (8 * 1024 * 1024)}, 'RESPONSE_BYTES_EXCEEDED'),
    ({'max_calls': 4}, 'CALL_BUDGET_EXHAUSTED'),
])
def test_missing_notes_explicit_without_poisoning_verified_latest(kwargs, code):
    result, _ = run_sections(**kwargs)
    row = result['filings'][0]
    assert result['selection']['primary_id'] == row['receipt_id']
    assert row['section_delivery']['status'] == 'PARTIAL'
    assert row['section_delivery']['missing_sections'] == ['financial_notes']
    assert code in row['section_delivery']['errors']
    assert 'financial_notes' not in row['sections']


def test_invalid_opt_in_fails_before_network():
    with pytest.raises(ValueError, match='INVALID_ACQUISITION_POLICY'):
        run_sections(include='yes')


def test_metrics_sink_keeps_actual_call_and_bytes_on_external_cancellation():
    async def exercise():
        metrics = {}
        started = asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(200, text=catalog(RECORDS[:1]))
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(collect_with_transport(handler, _metrics=metrics))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert metrics == {'calls': 2, 'response_bytes': len(catalog(RECORDS[:1]).encode())}

    asyncio.run(exercise())


def test_metrics_sink_is_the_returned_metrics_object():
    metrics = {'calls': 999, 'response_bytes': 999}
    result, requests = run_sections(_metrics=metrics)
    assert result['metrics'] is metrics
    assert metrics['calls'] == len(requests) == 5


def test_notes_from_different_document_are_not_requested():
    result, requests = run_sections(main_mutate=lambda body: body.replace(
        'node3["dcmNo"] = "12345678"', 'node3["dcmNo"] = "87654321"'))
    row = result['filings'][0]
    assert 'SECTION_DOCUMENT_MISMATCH' in row['section_delivery']['errors']
    assert len(requests) == 4
    assert 'financial_notes' not in row['sections']


def test_ambiguous_notes_are_not_requested():
    def duplicate(body):
        script = body[body.index('node3["text"]'):body.index('</script>')]
        return body.replace('</script>', script.replace('node3', 'node4').replace(
            '["eleId"] = "3"', '["eleId"] = "4"') + '</script>')
    result, requests = run_sections(main_mutate=duplicate)
    assert 'NOTES_NODE_AMBIGUOUS' in result['filings'][0]['section_delivery']['errors']
    assert len(requests) == 4


@pytest.mark.parametrize('financial,code', [
    (BODY + '<h2>재무상태표</h2>', 'SECTION_SCOPE_MISMATCH'),
    (BODY + 'x' * (8 * 1024 * 1024), 'RESPONSE_BYTES_EXCEEDED'),
])
def test_bad_financial_section_is_never_delivered(financial, code):
    result, _ = run_sections(financial=financial)
    row = result['filings'][0]
    assert result['selection']['primary_id'] is None
    assert row['section_delivery']['status'] == 'UNAVAILABLE'
    assert code in row['section_delivery']['errors']
    assert 'financial_statements' not in row['sections']


def test_selected_annual_notes_precede_unused_interim_notes_with_same_budget():
    records = RECORDS[:3]
    notes_requested = []
    def handler(request):
        rid = request.url.params.get('rcpNo')
        record = next((r for r in records if r[0] == rid), None)
        if request.method == 'POST':
            body = catalog(records)
        elif request.url.path.endswith('main.do'):
            body = main(rid)
            fields = {'text': '3. 연결재무제표 주석', 'rcpNo': rid,
                      'dcmNo': '12345678', 'eleId': '3', 'offset': '100',
                      'length': '100', 'dtd': 'dart4.xsd'}
            script = ''.join(f'node3["{k}"] = {json.dumps(v, ensure_ascii=False)};\n' for k, v in fields.items())
            body = body.replace('</script>', script + '</script>')
        elif request.url.params['eleId'] == '0':
            body = cover(record)
        elif request.url.params['eleId'] == '3':
            notes_requested.append(rid)
            body = NOTES
        else:
            body = BODY
        return httpx.Response(200, text=body)
    result = asyncio.run(collect_with_transport(handler, include_section_bodies=True, max_calls=12))
    assert result['metrics']['calls'] == 12  # catalog + 3 cores * 3 + 2 selected notes
    assert notes_requested == [records[0][0], records[2][0]]
    assert result['selection']['primary_id'] == records[0][0]
    assert result['selection']['annual_supplement_id'] == records[2][0]
    rows = {r['receipt_id']: r for r in result['filings']}
    assert rows[records[2][0]]['section_delivery']['status'] == 'AVAILABLE'
    assert rows[records[1][0]]['section_delivery']['missing_sections'] == ['financial_notes']


@pytest.mark.parametrize('exception', [httpx.RemoteProtocolError, httpx.ConnectError])
def test_one_transient_note_retry_is_counted_in_same_budget(exception):
    attempts = 0
    def hook(request):
        nonlocal attempts
        if request.url.params.get('eleId') == '3':
            attempts += 1
            if attempts == 1:
                raise exception('private transport error')
    result, requests = run_sections(response_hook=hook)
    assert len(requests) == result['metrics']['calls'] == 6
    assert attempts == 2
    assert result['metrics']['transport_retries'] == 1
    assert result['filings'][0]['section_delivery']['status'] == 'AVAILABLE'


def test_persistent_transport_failure_and_budget_cannot_be_bypassed():
    def hook(request):
        if request.url.params.get('eleId') == '3':
            raise httpx.RemoteProtocolError('private error')
    result, requests = run_sections(response_hook=hook, max_calls=5)
    assert len(requests) == result['metrics']['calls'] == 5
    assert result['filings'][0]['section_delivery']['status'] == 'PARTIAL'
    assert 'CALL_BUDGET_EXHAUSTED' in result['filings'][0]['section_delivery']['errors']
    result, requests = run_sections(response_hook=hook)
    assert len(requests) == 6
    assert 'HTTP_TRANSPORT_FAILURE' in result['filings'][0]['section_delivery']['errors']
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('status', [403, 429])
def test_http_denial_is_not_retried(status):
    result, requests = run_sections(response_hook=lambda request: httpx.Response(status)
                                   if request.url.params.get('eleId') == '3' else None)
    assert len(requests) == 5
    assert 'HTTP_STATUS_FAILURE' in result['filings'][0]['section_delivery']['errors']


def test_opt_in_requests_are_paced_without_changing_default(monkeypatch):
    sleeps = []
    async def observed_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(dart_public_filings.asyncio, 'sleep', observed_sleep)
    run_sections()
    assert len(sleeps) == 4 and all(0 < x <= 0.2 for x in sleeps)
    sleeps.clear()
    run_sections(include=False)
    assert sleeps == []


def test_post_transport_failure_is_never_retried():
    def fail(request):
        raise httpx.RemoteProtocolError('private')
    result, requests = run_sections(response_hook=fail)
    assert len(requests) == 1
    assert result['errors'] == ['HTTP_TRANSPORT_FAILURE']


def test_timeout_is_not_retried_and_preserves_missing_body():
    def fail(request):
        if request.url.params.get('eleId') == '3':
            raise httpx.ReadTimeout('private')
    result, requests = run_sections(response_hook=fail)
    assert len(requests) == 5
    assert result['filings'][0]['section_delivery']['missing_sections'] == ['financial_notes']
