"""Public issuer evidence collection is bounded, dated and source-preserving."""
from datetime import date

import pytest

from prism_core import us_official_company_sources as sources

SEED = [{'type': '10-Q', 'date': '2026-07-23', 'exhibits': {
    '10-Q': 'https://cdn.yahoofinance.com/prod/sec-filings/0001022079/000102207926000070/dgx.htm'}}]
RELEASE = '''<html><body><h1>Company Reports Second Quarter Financial Results</h1>
<p>July 23, 2026: financial results for the quarter ended June 30, 2026.</p>
<h2>Full Year 2026 Guidance</h2><p>We expect revenues of $11.95 billion to $12.05 billion.</p>
<table><tr><th>2026 Guidance (USD billions except EPS)</th><th>Prior</th><th>Updated</th></tr>
<tr><td>Revenue</td><td>11.7–11.8</td><td>11.95–12.05</td></tr>
<tr><td>Adjusted diluted EPS</td><td>$10.85–$11.05</td><td>$11.05–$11.25</td></tr></table>
<h2>Segment revenues</h2><p>Three months ended June 30, in millions except percentages.</p>
<table><tr><th>Revenue by segment</th><th>2026</th><th>2025</th></tr>
<tr><td>Diagnostics</td><td>3,040</td><td>2,759</td></tr>
<tr><td>Insurer mix</td><td>33%</td><td>35%</td></tr></table></body></html>'''


@pytest.fixture(autouse=True)
def reset_cache():
    sources._CACHE.clear()


def metadata():
    return {'filings': {'recent': {
        'form': ['8-K', '8-K', '10-Q', '8-K'],
        'filingDate': ['2026-07-23', '2026-08-01', '2026-07-23', '2027-01-01'],
        'reportDate': ['2026-07-23', '2026-08-01', '2026-06-30', '2027-01-01'],
        'items': ['2.02,9.01', '5.02', '', '2.02'],
        'accessionNumber': ['0001022079-26-000067', '0001022079-26-000080',
                            '0001022079-26-000070', '0001022079-27-000001'],
        'primaryDocument': ['earnings.htm', 'appointment.htm', 'quarter.htm', 'future.htm'],
    }}}


def setup_transport(monkeypatch, *, failure=False):
    import json
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if failure:
            raise OSError('blocked')
        if 'submissions/' in url:
            return json.dumps(metadata()).encode(), url
        if url.endswith('earnings.htm'):
            return b'<html><a href="ex991.htm">Exhibit 99.1 earnings release</a></html>', url
        return RELEASE.encode(), url

    monkeypatch.setattr(sources, '_fetch', fetch)
    return calls


def test_latest_earnings_not_latest_non_earnings_8k_and_no_future(monkeypatch):
    calls = setup_transport(monkeypatch)
    packet = sources.collect_official_company_sources('DGX', date(2026, 9, 23), filings=SEED)
    assert packet['status'] == 'complete'
    assert any('ex991.htm' in url for url in calls)
    assert not any('appointment' in url or 'future' in url for url in calls)
    text = sources.render_official_company_sources(packet)
    assert '33%' in text and '35%' in text and '$33M' not in text
    assert '2026 Guidance' in text and 'Prior' in text and 'Updated' in text
    assert 'USD billions except EPS' in text and 'Three months ended June 30' in text
    assert 'not normalized' in text and 'not analyst consensus' in text
    assert len(calls) <= sources.MAX_REQUESTS


def test_failures_not_cached_and_successes_cached(monkeypatch):
    calls = setup_transport(monkeypatch, failure=True)
    a = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert a['status'] != 'complete'
    before = len(calls)
    sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert len(calls) > before
    calls = setup_transport(monkeypatch)
    sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    before = len(calls)
    sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert len(calls) == before


def test_render_subset_omits_whole_tables_not_cells(monkeypatch):
    setup_transport(monkeypatch)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    text = sources.render_official_company_sources(packet, sections=('segments',), max_chars=1500)
    assert len(text) <= 1500
    assert 'Context subset' in text and 'Omission here does not mean' in text
    assert '2026 Guidance (USD billions except EPS)' not in text
    for source in packet['sources']:
        for excerpt in source['segments']:
            assert excerpt in text or excerpt[:100] not in text


def test_disclaimer_and_future_release_notice_are_not_guidance():
    html = '<html><p>We will report financial results next month.</p><p>Forward-looking statements, including guidance, involve risks.</p></html>'
    extracted = sources._extract_sections(html)
    assert not extracted['guidance']
    assert not extracted['financials']


def test_accounting_guidance_is_not_company_forecast():
    document = '''<p>In September 2025 the FASB issued a standard so the guidance is neutral.</p>
    <h2>3. Earnings per share</h2><table><tr><th>2026</th><th>2025</th></tr>
    <tr><td>Net income</td><td>334</td><td>296</td></tr></table>'''
    extracted = sources._extract_sections(document)
    assert not extracted['guidance']
    assert extracted['financials']


def test_xml_declaration_nested_layout_and_colspans_are_preserved():
    document = '''<?xml version="1.0" encoding="UTF-8"?><html><body><table><tr><td>
    <table><tr><th colspan="2">Net revenues, three months ended June 30</th></tr>
    <tr><th>2026 (USD millions)</th><th>2025 (USD millions)</th></tr>
    <tr><td>33</td><td>35</td></tr></table></td></tr></table></body></html>'''
    extracted = sources._extract_sections(document)
    text = '\n'.join(extracted['financials'])
    assert '[c1-2] Net revenues' in text and '[c2] 35' in text
    assert '2026 (USD millions)' in text and '2025 (USD millions)' in text


def test_rowspan_preserves_column_positions():
    document = '''<table><tr><th rowspan="2">Net revenues (USD millions)</th><th>2026</th></tr>
    <tr><td>33</td></tr></table>'''
    text = '\n'.join(sources._extract_sections(document)['financials'])
    assert '[c1; rowspan=2]' in text and '[c2] 33' in text


@pytest.mark.parametrize('span', ['101', '0', '-1', 'abc', '2.5'])
def test_invalid_spans_reject_whole_table_instead_of_repairing(span):
    document = f'<table><tr><th colspan="{span}">Net revenues</th><th>2026</th></tr><tr><td>33</td><td>35</td></tr></table>'
    result = sources._extract_sections(document)
    assert not result['financials']
    assert 'unsupported_table_span' in result['unparsed_reasons']


def test_overlapping_rowspan_colspan_rejects_whole_table():
    document = '<table><tr><th>Net revenues</th><th rowspan="2">2026</th></tr><tr><td colspan="2">33</td></tr></table>'
    result = sources._extract_sections(document)
    assert not result['financials']
    assert 'unsupported_table_span' in result['unparsed_reasons']


def test_oversized_tables_are_not_cut_into_misleading_facts():
    document = '<table><tr><th>Net revenues, USD millions, 2026</th></tr>' + '<tr><td>12345</td></tr>' * 1000 + '</table>'
    assert not sources._extract_sections(document)['financials']


def test_seed_ignores_third_party_ownership_filings():
    filings = SEED + [{'type': 'SC 13G', 'exhibits': {'SC 13G':
        'https://cdn.yahoofinance.com/prod/sec-filings/0000000123/123/foo.htm'}}]
    assert sources._cik_from_filings(filings) == 1022079


def test_empty_excerpts_not_cached(monkeypatch):
    import json
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        body = json.dumps(metadata()).encode() if '/submissions/' in url else b'<html>No usable evidence</html>'
        return body, url

    monkeypatch.setattr(sources, '_fetch', fetch)
    for _ in range(2):
        packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
        assert packet['status'] == 'missing'
    assert sum('/submissions/' in url for url in calls) == 2


def test_private_dns_rejected_before_connection(monkeypatch):
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='non_public'):
        sources._fetch('https://data.sec.gov/submissions/CIK0001022079.json')


def test_ticker_mismatch_is_not_attributed_to_requested_company(monkeypatch):
    import json
    payload = {**metadata(), 'tickers': ['OTHER']}
    monkeypatch.setattr(sources, '_fetch', lambda url, **kwargs: (json.dumps(payload).encode(), url))
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert not packet['sources'] and packet['status'] == 'missing'


def test_missing_metadata_never_triggers_unbounded_provider_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected network')

    monkeypatch.setattr(sources, '_fetch', forbidden)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23')
    assert packet['issues'] == ['missing_or_ambiguous_seeded_cik']


def test_request_budget_is_enforced(monkeypatch):
    calls = setup_transport(monkeypatch)
    monkeypatch.setattr(sources, 'MAX_REQUESTS', 2)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert packet['status'] != 'complete'
    assert len(calls) == 2


def test_blocked_submissions_reuses_known_periodic_filing_not_generic_8k(monkeypatch):
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if '/submissions/' in url:
            raise OSError('http_403')
        return RELEASE.encode(), url

    monkeypatch.setattr(sources, '_fetch', fetch)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED)
    assert packet['status'] == 'partial'
    assert len(packet['sources']) == 1 and packet['sources'][0]['filing_type'] == '10-Q'
    assert packet['sources'][0]['publication_date_basis'].startswith('provider')
    assert packet['issues'] == ['sec_submissions_unavailable_or_invalid']
    assert not sources._CACHE


def test_historical_reference_excludes_unreleased_filings(monkeypatch):
    calls = setup_transport(monkeypatch)
    packet = sources.collect_official_company_sources('DGX', '2026-07-01', filings=SEED)
    assert packet['status'] != 'complete'
    assert not any(url.endswith(('earnings.htm', 'quarter.htm')) for url in calls)


@pytest.mark.parametrize('url', ['http://www.sec.gov/a', 'https://127.0.0.1/a',
    'https://www.sec.gov.evil.test/a', 'https://user:secret@www.sec.gov/a',
    'https://www.sec.gov:444/a', 'https://cdn.yahoofinance.com/not-a-filing'])
def test_untrusted_urls_rejected(url):
    with pytest.raises(ValueError):
        sources._validate_url(url)


def test_user_agent_reuses_only_existing_public_config_field(monkeypatch):
    from io import BytesIO

    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    monkeypatch.delenv('SEC_EDGAR_USER_AGENT', raising=False)
    content = b'mcp:\n  servers:\n    sec_edgar:\n      env:\n        SEC_EDGAR_USER_AGENT: Fixture Research contact@fixture-corp.invalid\n'
    monkeypatch.setattr(sources.Path, 'open', lambda *a, **k: BytesIO(content))
    assert sources._configured_user_agent('fake-config.yaml') == 'Fixture Research contact@fixture-corp.invalid'


def test_user_agent_environment_precedence(monkeypatch):
    monkeypatch.setenv('SEC_EDGAR_USER_AGENT', 'EDGAR Research edgar@fixture-corp.invalid')
    monkeypatch.setenv('SEC_USER_AGENT', 'Primary Research primary@fixture-corp.invalid')
    assert sources._configured_user_agent('not-read.yaml').startswith('Primary Research')
    monkeypatch.delenv('SEC_USER_AGENT')
    assert sources._configured_user_agent('not-read.yaml').startswith('EDGAR Research')


@pytest.mark.parametrize('value', ['Your Name your_email@example.com', 'x' * 513,
    'Research\r\nInjected: value', 'placeholder contact', 'short'])
def test_invalid_user_agents_are_not_sent(monkeypatch, value):
    from io import BytesIO

    monkeypatch.setenv('SEC_USER_AGENT', value)
    monkeypatch.setenv('SEC_EDGAR_USER_AGENT', value)
    monkeypatch.setattr(sources.Path, 'open', lambda *a, **k: BytesIO(b'mcp: {}'))
    assert sources._configured_user_agent('fake-config.yaml').endswith('github.com/dragon1086/prism-insight')


def test_same_company_newsroom_discovery_after_sec_block_is_bounded_and_dated(monkeypatch):
    website = 'https://www.issuer.test'
    newsroom = 'https://newsroom.issuer.test/'
    listing = newsroom + 'press-releases'
    release = newsroom + '2026-07-23-Company-Reports-Second-Quarter-Financial-Results'
    pages = {
        website: f'<a href="{newsroom}">Newsroom</a><a href="https://evil.test/">News Releases</a>',
        newsroom: f'<a href="{newsroom}index.php?s=1">News Releases</a><a href="{listing}">News Releases</a><a href="{listing}">All Releases</a>',
        listing: f'<a href="{listing}?o=10">2</a><a href="{newsroom}2026-10-01-future">Company Reports Third Quarter Financial Results</a>',
        listing + '?o=10': f'<a href="{release}">Company Reports Second Quarter Financial Results</a>',
        release: RELEASE,
    }
    calls = []

    def fetch(url, **kwargs):
        calls.append(url)
        if url.startswith(('https://data.sec.gov', 'https://www.sec.gov')):
            raise OSError('http_403')
        if url.startswith('https://cdn.yahoofinance.com'):
            return RELEASE.encode(), url
        return pages[url].encode(), url

    monkeypatch.setattr(sources, '_fetch', fetch)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED, company_website=website)
    assert packet['status'] == 'complete' and len(calls) == 8
    assert packet['sources'][0]['filing_type'] == 'Issuer release'
    assert packet['sources'][0]['publication_date'] == '2026-07-23'
    assert packet['sources'][0]['guidance']
    assert not any('evil.test' in url or 'future' in url or 'index.php' in url for url in calls)
    text = sources.render_official_company_sources(packet)
    assert 'Issuer release — published 2026-07-23' in text
    before = len(calls)
    sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED, company_website=website)
    assert len(calls) == before


def test_release_without_publication_date_or_upcoming_notice_is_not_evidence():
    document = RELEASE.replace('Reports Second', 'to Release Second')
    calls = []

    def fetch(url):
        calls.append(url)
        return document.encode(), url

    assert sources._discover_company_release('https://issuer.test/2026-07-23-notice', date(2026, 9, 23), fetch, ('issuer.test',)) is None
    assert sources._release_date(sources.html.fromstring(RELEASE), 'https://issuer.test/undated') is None


def test_same_day_future_publication_timestamp_is_not_released(monkeypatch):
    from datetime import datetime, timezone

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 23, 12, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    monkeypatch.setattr(sources, 'datetime', Clock)
    url = 'https://issuer.test/2026-09-23-results'
    def result(timestamp):
        document = RELEASE.replace('<body>', f'<body><meta property="article:published_time" content="{timestamp}">')
        return sources._discover_company_release(url, date(2026, 9, 23), lambda target: (document.encode(), target), ('issuer.test',))

    assert result('2026-09-23T23:59:00Z') is None
    published = result('2026-09-23T08:30:00Z')
    assert published['publication_at'] == '2026-09-23T08:30:00+00:00'
    assert published['publication_time_status'] == 'verified_aware_timestamp'
    date_only = result('2026-09-23')
    assert date_only['publication_time_status'] == 'unknown_date_only'


def test_company_scope_does_not_allow_vendor_or_lookalike_hosts():
    assert sources._company_domains('https://www.issuer.test') == ('issuer.test',)
    sources._validate_url('https://newsroom.issuer.test/releases?o=10', ('issuer.test',))
    for url in ('https://issuer.test.evil.test/releases', 'https://vendor.test/issuer'):
        with pytest.raises(ValueError):
            sources._validate_url(url, ('issuer.test',))


@pytest.mark.parametrize('website', [{}, 'https://[invalid', 'not a URL'])
def test_invalid_optional_company_website_does_not_break_sec_collection(monkeypatch, website):
    setup_transport(monkeypatch)
    packet = sources.collect_official_company_sources('DGX', '2026-09-23', filings=SEED, company_website=website)
    assert packet['status'] == 'complete'


def test_sec_contact_header_not_sent_to_company_websites(monkeypatch):
    captured = []
    monkeypatch.setattr(sources, '_configured_user_agent', lambda: 'SEC-only configured fixture contact')
    monkeypatch.setattr(sources.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, headers):
            captured.append(headers['User-Agent'])

        def getresponse(self):
            return type('Response', (), {'status': 200, 'getheader': lambda *a: 'identity', 'read1': lambda *a: b''})()

        def close(self):
            pass

    monkeypatch.setattr(sources.http.client, 'HTTPSConnection', Connection)
    sources._fetch('https://newsroom.issuer.test/', company_domains=('issuer.test',))
    sources._fetch('https://data.sec.gov/submissions/CIK0001022079.json')
    assert 'SEC-only' not in captured[0] and captured[1] == 'SEC-only configured fixture contact'


def _release_site(dates):
    """Listing page linking to one results release per date (newest-first link priority)."""
    base = 'https://issuer.test/'
    links = ''.join(f'<a href="{base}{d}-results">Company Reports Quarter Financial Results</a>' for d in dates)
    pages = {base: links}
    for d in dates:
        pages[f'{base}{d}-results'] = RELEASE.replace('July 23, 2026', d)
    return base, pages


def test_stale_issuer_release_older_than_400_days_is_not_current_evidence():
    base, pages = _release_site(['2022-05-05'])
    fetch = lambda url: (pages[url].encode(), url)
    assert sources._discover_company_release(base, date(2026, 9, 25), fetch, ('issuer.test',)) is None


def test_most_recent_qualifying_issuer_release_wins():
    base, pages = _release_site(['2025-12-01', '2025-11-02', '2022-05-05'])
    fetch = lambda url: (pages[url].encode(), url)
    found = sources._discover_company_release(base, date(2026, 9, 25), fetch, ('issuer.test',))
    assert found['publication_date'] == '2025-12-01'
    base, pages = _release_site(['2026-08-06'])
    fetch = lambda url: (pages[url].encode(), url)
    assert sources._discover_company_release(base, date(2026, 9, 25), fetch, ('issuer.test',))['publication_date'] == '2026-08-06'
