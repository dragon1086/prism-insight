"""Official-shape synthetic SEC metadata; never accesses the network."""
import asyncio
from datetime import date, datetime, timezone

import httpx
import pytest

from prism_core.sec_public_filings import collect_sec_public_filings

CIK = '0000000123'
NOW = datetime(2026, 9, 18, 18, tzinfo=timezone.utc)
UA = 'Prism test contact fixture@example.invalid'


def row(form='10-Q', report='2026-06-30', accepted='2026-08-01T16:00:00Z', seq=1):
    return {'accessionNumber': f'0000000123-26-{seq:06d}', 'form': form,
            'reportDate': report, 'filingDate': accepted[:10],
            'acceptanceDateTime': accepted, 'primaryDocument': 'report.htm'}


def columns(rows):
    return {key: [r[key] for r in rows] for key in row()}


def run(rows=None, *, mapping=None, change=None, older=None, **kwargs):
    requests = []
    submissions = {'cik': 123, 'tickers': ['XYZ'], 'name': 'Synthetic Issuer',
                   'filings': {'recent': columns(rows if rows is not None else [row()]), 'files': older or []}}
    def handler(request):
        requests.append(request)
        assert request.headers['User-Agent'] == UA
        assert request.headers['Accept-Encoding'] == 'identity'
        if request.url.path == '/files/company_tickers.json':
            payload = mapping if mapping is not None else {'0': {'cik_str': 123, 'ticker': 'XYZ', 'title': 'Synthetic Issuer'}}
        else:
            payload = submissions
        if change:
            changed = change(request, payload)
            if isinstance(changed, httpx.Response):
                return changed
            payload = changed
        return httpx.Response(200, json=payload)
    def factory(**options):
        assert options['follow_redirects'] is False and options['trust_env'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
    args = {'ticker': 'XYZ', 'decision_at': NOW, 'start_date': date(2025, 1, 1),
            'user_agent': UA, 'client_factory': factory}
    args.update(kwargs)
    return asyncio.run(collect_sec_public_filings(**args)), requests


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr('prism_core.sec_public_filings._REQUEST_INTERVAL', 0)


def test_latest_periodic_and_annual_and_events_keep_distinct_dates():
    result, requests = run([row(), row('10-K', '2025-12-27', '2026-02-06T20:00:00Z', 2),
                            row('8-K', '2026-09-15', '2026-09-16T19:00:00Z', 3)])
    assert len(requests) == 2
    assert result['status'] == 'COMPLETE_WITHIN_QUERY'
    assert result['primary']['form'] == '10-Q'
    assert result['primary']['report_date'] == '2026-06-30'
    assert result['primary']['acceptance_at'] == '2026-08-01T16:00:00+00:00'
    assert result['primary']['filing_date'] == '2026-08-01'
    assert result['primary']['period_start'] is None and result['primary']['scope'] is None
    assert result['annual_supplement']['report_date'] == '2025-12-27'
    assert result['events'][0]['form'] == '8-K'
    assert result['selection']['latest_confirmed'] is False


@pytest.mark.parametrize('form', ['20-F', '40-F'])
def test_foreign_annual_and_6k_not_periodic_replacement(form):
    result, _ = run([row(form, '2026-03-31'), row('6-K', '2026-08-30', '2026-09-01T20:00:00Z', 2)])
    assert result['primary']['form'] == form
    assert result['events'][0]['form'] == '6-K'


def test_future_same_day_acceptance_is_not_available():
    result, _ = run([row(accepted='2026-09-18T19:00:00Z')])
    assert result['primary'] is None
    assert result['excluded_future'] == 1


def test_later_filing_credit_cannot_make_future_report_period_available():
    filing = row(report='2026-09-20', accepted='2026-09-18T17:00:00Z')
    filing['filingDate'] = '2026-09-21'
    result, _ = run([filing])
    assert result['primary'] is None
    assert 'SEC_FILING_METADATA_INVALID' in result['gaps']


def test_amendment_never_replaces_same_period_base():
    result, _ = run([row(), row('10-Q/A', accepted='2026-08-04T20:00:00Z', seq=2)])
    assert result['primary'] is None
    assert result['amendments'][0]['form'] == '10-Q/A'
    assert 'AMENDMENT_SCOPE_UNVERIFIED' in result['gaps']


def test_old_annual_amendment_does_not_block_new_quarter():
    result, _ = run([row(), row('10-K', '2025-12-31', '2026-02-01T20:00:00Z', 2),
                    row('10-K/A', '2025-12-31', '2026-03-01T20:00:00Z', 3)])
    assert result['primary']['form'] == '10-Q'
    assert result['annual_supplement'] is None


@pytest.mark.parametrize('user_agent', [None, '', 'python-httpx', 'bot\r\nmalicious@example.invalid'])
def test_missing_identifying_user_agent_is_no_network(user_agent):
    result, requests = run(user_agent=user_agent)
    assert result['status'] == 'FAILED'
    assert result['gaps'] == ['SEC_USER_AGENT_REQUIRED']
    assert not requests


def test_exact_ticker_mapping_rejects_ambiguity():
    result, requests = run(mapping={'0': {'cik_str': 123, 'ticker': 'XYZ'},
                                    '1': {'cik_str': 456, 'ticker': 'XYZ'}})
    assert result['primary'] is None
    assert 'SEC_TICKER_AMBIGUOUS' in result['gaps']
    assert len(requests) == 1


def test_submissions_identity_must_confirm_ticker():
    def change(request, payload):
        if request.url.host == 'data.sec.gov':
            payload['tickers'] = ['OTHER']
        return payload
    result, _ = run(change=change)
    assert result['identity']['verified'] is False
    assert 'SEC_IDENTITY_MISMATCH' in result['gaps']


@pytest.mark.parametrize('status', [301, 403, 429, 500])
def test_http_failures_have_no_retry_or_alternate_host(status):
    result, requests = run(change=lambda r, p: httpx.Response(status, headers={'Location': 'https://other.example'}))
    assert len(requests) == 1
    assert result['metrics']['calls'] == 1
    assert result['gaps'] == [f'SEC_HTTP_{status}']


@pytest.mark.parametrize('field,value', [('acceptanceDateTime', '2026-08-01T16:00:00'),
    ('reportDate', ''), ('primaryDocument', '../other.htm'), ('accessionNumber', 'bad')])
def test_invalid_periodic_metadata_fails_closed(field, value):
    bad = row(); bad[field] = value
    result, _ = run([bad])
    assert result['primary'] is None
    assert 'SEC_FILING_METADATA_INVALID' in result['gaps']


def test_history_path_cannot_escape_or_switch_cik():
    result, requests = run(older=[{'name': '../secret.json', 'filingFrom': '2025-01-01', 'filingTo': '2025-12-31'}])
    assert result['primary'] is None
    assert 'SEC_HISTORY_INVALID' in result['gaps']
    assert len(requests) == 2


def test_historical_file_fetched_when_query_overlaps():
    old = row('10-K', '2025-12-31', '2026-02-01T20:00:00Z', 2)
    def change(request, payload):
        return columns([old]) if 'submissions-001' in request.url.path else payload
    result, requests = run(change=change, older=[{'name': CIK.join(['CIK', '-submissions-001.json']),
        'filingFrom': '2026-02-01', 'filingTo': '2026-02-01'}])
    assert len(requests) == 3
    assert result['annual_supplement']['report_date'] == '2025-12-31'


def test_budget_cannot_claim_complete_history():
    result, requests = run(max_calls=2, older=[{'name': f'CIK{CIK}-submissions-001.json',
        'filingFrom': '2025-01-01', 'filingTo': '2025-12-31'}])
    assert len(requests) == 2
    assert result['primary'] is None
    assert 'SEC_CALL_BUDGET_EXHAUSTED' in result['gaps']


def test_shared_metrics_object_and_bytes_limit():
    metrics = {}
    result, _ = run(_metrics=metrics, max_response_bytes=32)
    assert result['metrics'] is metrics
    assert metrics['calls'] == 1 and metrics['response_bytes'] > 32
    assert result['gaps'] == ['SEC_RESPONSE_BYTES_EXCEEDED']


def test_zero_padded_submission_cik_and_next_day_filing_credit():
    sample = row(accepted='2026-09-18T17:00:00Z')
    sample['filingDate'] = '2026-09-21'
    def change(request, payload):
        if request.url.host == 'data.sec.gov':
            payload['cik'] = CIK
        return payload
    result, _ = run([sample], change=change)
    assert result['primary']['filing_date'] == '2026-09-21'
    assert result['primary']['acceptance_at'] == '2026-09-18T17:00:00+00:00'


def test_duplicate_conflicting_accessions_fail_closed():
    second = row(report='2026-03-31')
    result, _ = run([row(), second])
    assert result['gaps'] == ['SEC_ACCESSION_CONFLICT']
    assert result['primary'] is None


def test_duplicate_period_does_not_choose_by_submission_recency():
    result, _ = run([row(), row(accepted='2026-08-04T17:00:00Z', seq=2)])
    assert result['primary'] is None
    assert 'PERIODIC_EDITION_AMBIGUOUS' in result['gaps']


def test_unpaired_newer_amendment_blocks_old_periodic_fallback():
    result, _ = run([row(report='2026-03-31'), row('10-Q/A', seq=2)])
    assert result['primary'] is None
    assert 'AMENDMENT_BASE_UNRESOLVED' in result['gaps']


def test_event_amendment_is_not_periodic_blocker():
    result, _ = run([row(), row('8-K/A', '2026-08-03', '2026-08-04T17:00:00Z', 2)])
    assert result['primary']['form'] == '10-Q'
    assert result['events'][0]['is_amendment'] is True


def test_aggregate_bytes_are_enforced():
    result, requests = run(max_total_bytes=120)
    assert len(requests) == 2
    assert result['gaps'] == ['SEC_RESPONSE_BYTES_EXCEEDED']


@pytest.mark.parametrize('kwargs', [{'max_calls': True}, {'timeout_seconds': float('nan')},
    {'ticker': 'xyz'}, {'decision_at': NOW.replace(tzinfo=None)}, {'_metrics': []}])
def test_invalid_policy_is_no_network(kwargs):
    result, requests = run(**kwargs)
    assert result['gaps'] == ['SEC_POLICY_INVALID']
    assert not requests


def test_external_cancellation_preserves_shared_metrics():
    async def exercise():
        metrics, ready = {}, asyncio.Event()
        async def handler(request):
            ready.set()
            await asyncio.Event().wait()
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
        task = asyncio.create_task(collect_sec_public_filings(ticker='XYZ', decision_at=NOW,
            start_date=date(2025, 1, 1), user_agent=UA, client_factory=factory, _metrics=metrics))
        await asyncio.wait_for(ready.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert metrics == {'calls': 1, 'response_bytes': 0}
    asyncio.run(exercise())


def test_rate_slots_are_shared_across_concurrent_collectors(monkeypatch):
    import prism_core.sec_public_filings as sec
    waits = []
    async def sleep(delay):
        waits.append(delay)
    monkeypatch.setattr(sec, '_REQUEST_INTERVAL', 0.2)
    monkeypatch.setattr(sec, '_next_request_at', 0)
    monkeypatch.setattr(sec.time, 'monotonic', lambda: 10.0)
    monkeypatch.setattr(sec.asyncio, 'sleep', sleep)
    async def exercise():
        await asyncio.gather(sec._rate_wait(), sec._rate_wait(), sec._rate_wait())
    asyncio.run(exercise())
    assert waits == pytest.approx([0, 0.2, 0.4])


def test_history_count_mismatch_cannot_certify_coverage():
    result, _ = run(older=[{'name': f'CIK{CIK}-submissions-001.json', 'filingFrom': '2025-01-01',
                           'filingTo': '2025-12-31', 'filingCount': 2}],
        change=lambda request, payload: columns([row()]) if 'submissions-001' in request.url.path else payload)
    assert result['gaps'] == ['SEC_HISTORY_COUNT_MISMATCH']
    assert result['coverage']['complete_within_query'] is False


def test_mismatched_cik_fails_even_with_matching_ticker():
    def change(request, payload):
        if request.url.host == 'data.sec.gov':
            payload['cik'] = 456
        return payload
    result, _ = run(change=change)
    assert result['gaps'] == ['SEC_IDENTITY_MISMATCH']


def test_column_length_mismatch_never_truncates_zip():
    def change(request, payload):
        if request.url.host == 'data.sec.gov':
            payload['filings']['recent']['form'].append('10-Q')
        return payload
    result, _ = run(change=change)
    assert result['gaps'] == ['SEC_SUBMISSIONS_STRUCTURE_INVALID']


def test_unsafe_provider_exception_is_redacted():
    def change(request, payload):
        raise RuntimeError('private provider payload')
    result, _ = run(change=change)
    assert result['gaps'] == ['SEC_ACQUISITION_FAILURE']
    assert 'private provider payload' not in str(result)


def test_encoded_response_is_rejected_without_decompression():
    result, requests = run(change=lambda r, p: httpx.Response(200, headers={'Content-Encoding': 'br'}))
    assert len(requests) == 1
    assert result['metrics']['response_bytes'] == 0
    assert result['gaps'] == ['SEC_RESPONSE_ENCODING_OR_URL_INVALID']


def test_timeout_preserves_count_without_partial_latest_claim():
    async def exercise():
        async def handler(request):
            await asyncio.Event().wait()
        def factory(**options):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
        return await collect_sec_public_filings(ticker='XYZ', decision_at=NOW,
            start_date=date(2025, 1, 1), user_agent=UA, client_factory=factory, timeout_seconds=0.01)
    result = asyncio.run(exercise())
    assert result['gaps'] == ['SEC_TOTAL_TIMEOUT']
    assert result['metrics']['calls'] == 1
    assert result['primary'] is None
