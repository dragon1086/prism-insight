"""Offline cohort evaluator boundaries; no Yahoo or SEC access."""
import hashlib
from datetime import datetime, timezone

import pytest

from tools.evaluate_us_inline_cohort import (
    baseline_parser,
    compare_baseline,
    compare_body,
    evaluate_case,
    frozen_cohort,
    provider_identity,
)

URL = 'https://cdn.yahoofinance.com/prod/sec-filings/0000000123/000119312526000001/issuer.htm'
CASE = {'ticker': 'XYZ', 'name': 'Synthetic', 'sector': 'testing'}


def test_agent_accession_prefix_does_not_need_to_equal_company_cik():
    assert provider_identity(URL) == {'cik': '0000000123', 'accession': '000119312526000001'}


@pytest.mark.parametrize('url', [URL.replace('https:', 'http:'), URL + '?other=1',
    URL.replace('cdn.yahoofinance.com', 'www.sec.gov'), URL.replace('/issuer.htm', '/../issuer.htm'),
    URL.replace('0000000123', '123'), URL.replace('000119312526000001', 'invalid'),
    URL.replace('cdn.yahoofinance.com', 'cdn.yahoofinance.com.evil.example')])
def test_invalid_or_non_provider_url_rejected(url):
    with pytest.raises(ValueError):
        provider_identity(url)


def test_compare_same_body_hash_and_no_raw_payload():
    body = b'<html><body>example</body></html>'
    result = compare_body(body, '123', old_parser=lambda text: 'in millions USD FY2026 $0M')
    assert result['source_sha256'] == hashlib.sha256(body).hexdigest()
    assert result['baseline']['assumed_millions_usd'] is True
    assert result['baseline']['fy_labels'] == ['FY2026']
    assert result['baseline']['zero_cells'] == 1
    assert result['new']['facts_count'] == 0
    assert 'example</body>' not in str(result)


def test_case_keeps_provider_first_periodic_not_official_latest():
    received = []
    def fetch(url):
        received.append(url)
        return b'<html/>'
    result = evaluate_case(CASE, [{'type': '8-K'}, {'type': '10-Q', 'date': '2026-04-29',
        'exhibits': {'10-Q': URL}}], fetch=fetch, old_parser=lambda text: '')
    assert received == [URL]
    assert result['provider_filing_date'] == '2026-04-29'
    assert result['identity_basis'] == 'PROVIDER_ONLY_NOT_OFFICIAL'
    assert result['latest_certified'] is False


def test_missing_source_stays_in_denominator_without_request():
    def fetch(url):
        pytest.fail('must not fetch')
    result = evaluate_case(CASE, [], fetch=fetch, old_parser=lambda text: '')
    assert result['status'] == 'FAILED'
    assert result['reason'] == 'PROVIDER_PERIODIC_MISSING'
    assert result['ticker'] == 'XYZ'


def test_fetch_exception_redacted_and_case_retained():
    def fetch(url):
        raise RuntimeError('unsafe upstream details')
    result = evaluate_case(CASE, [{'type': '10-K', 'date': '2026-04-29', 'exhibits': {'10-K': URL}}],
        fetch=fetch, old_parser=lambda text: '')
    assert result['status'] == 'FAILED'
    assert result['reason'] == 'PROVIDER_BODY_FAILURE'
    assert 'unsafe' not in str(result)


def test_baseline_helper_loading_does_not_execute_other_code(monkeypatch):
    from types import SimpleNamespace

    # CI can be shallow. Real fixed-commit A/B is recorded by the live evaluator;
    # this unit checks the loader without requiring hidden repository history.
    source = ('raise RuntimeError("must not import production module")\n'
              'def _clean_xbrl_label(value):\n    return value\n'
              'def _parse_10k_segment_revenue(value: str) -> str:\n    return ""\n')
    monkeypatch.setattr('tools.evaluate_us_inline_cohort.subprocess.run',
                        lambda *args, **kwargs: SimpleNamespace(stdout=source))
    assert baseline_parser()('<html/>') == ''


def test_expected_provider_cik_is_passed_to_exact_parser(monkeypatch):
    observed = []
    def parse(body, expected_cik):
        observed.append((body, expected_cik))
        return {'status': 'failed', 'facts': [], 'gaps': [{'reason': 'fixture_gap'}]}
    monkeypatch.setattr('tools.evaluate_us_inline_cohort.parse_inline_revenue', parse)
    result = compare_body(b'<html/>', '0000000123', old_parser=lambda text: '')
    assert observed == [(b'<html/>', '0000000123')]
    assert result['new']['gap_reasons'] == ['fixture_gap']


@pytest.mark.parametrize('form', ['20-F', '40-F'])
def test_foreign_periodic_forms_supported_without_event_replacement(form):
    result = evaluate_case(CASE, [{'type': '6-K'}, {'type': '8-K'},
        {'type': form, 'date': '2026-04-29', 'exhibits': {form: URL}}],
        fetch=lambda url: b'<html/>', old_parser=lambda text: '')
    assert result['provider_filing_type'] == form


@pytest.mark.parametrize('filed,reason', [('2026-09-20', 'PROVIDER_FUTURE_FILING'),
    ('2026-09-19', 'PROVIDER_SAME_DAY_PUBLICATION_UNRESOLVED')])
def test_frozen_date_only_publication_rejects_future_and_same_day(filed, reason):
    def fetch(url):
        pytest.fail('not available at frozen decision')
    result = evaluate_case(CASE, [{'type': '10-Q', 'date': filed, 'exhibits': {'10-Q': URL}}],
        fetch=fetch, old_parser=lambda text: '', decision_at=datetime(2026, 9, 20, 1, tzinfo=timezone.utc))
    assert result['status'] == 'FAILED' and result['reason'] == reason


def test_group_selects_complete_cohort_and_preserves_failure_denominator():
    manifest = {'US': {'development': [CASE], 'holdout': [CASE, {**CASE, 'ticker': 'ABC'}]}}
    cohort = frozen_cohort(manifest, 'holdout')
    results = [evaluate_case(case, [], fetch=lambda url: b'', old_parser=lambda text: '') for case in cohort]
    assert len(results) == 2 and all(r['status'] == 'FAILED' for r in results)
    with pytest.raises(ValueError, match='COHORT_GROUP_INVALID'):
        frozen_cohort(manifest, 'other')


def test_baseline_body_comparison_does_not_call_changed_body_stable():
    cases = [{'ticker': 'A', 'source_sha256': 'new'}, {'ticker': 'B', 'status': 'FAILED'}]
    compare_baseline(cases, {'cases': [{'ticker': 'A', 'source_sha256': 'old'},
                                      {'ticker': 'B', 'source_sha256': 'hash'}]})
    assert cases[0]['baseline_body_unchanged'] is False
    assert cases[1]['baseline_body_unchanged'] is None
