"""Fixed official-page shapes; no network, model, broker, or secrets."""
import json
from datetime import date, datetime, timezone

import pytest

from prism_core import us_official_macro_sources as macro

CPI = b'''<html><pre>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Friday, September 11, 2026 USDL-26-1496
CONSUMER PRICE INDEX - AUGUST 2026
The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.4 percent on a seasonally adjusted basis in August.
Over the last 12 months, the all items index increased 3.4 percent before seasonal adjustment.</pre>
<p>Table A. Percent changes</p>
<p>The Consumer Price Index for September 2026 is scheduled to be released on Wednesday, October 14, 2026.</p></html>'''
PCE = b'''<html><h1>Personal Income and Outlays, July 2026</h1>
<div class="field field--name-field-release-date">EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Wednesday, August 26, 2026</div>
<p>Personal income increased $115.1 billion (0.4 percent at a monthly rate) in July.</p>
<p>From the preceding month, the PCE price index for July increased 0.2 percent.</p>
<p>From the same month one year ago, the PCE price index increased 3.7 percent.</p>
<h2>Personal Income and Related Measures</h2>
<p>Next release: September 30, 2026, at 8:30 a.m. EDT</p><p>Personal Income and Outlays, August 2026</p></html>'''
INDEX = b'<a href="/news/2026/personal-income-and-outlays-july-2026">Personal Income and Outlays, July 2026</a>'
PCE_URL = 'https://www.bea.gov/news/2026/personal-income-and-outlays-july-2026'


def treasury(*rows):
    return ('<feed xmlns:m="urn:meta" xmlns:d="urn:data">' + ''.join(
        f'<m:properties><d:NEW_DATE>{day}T00:00:00</d:NEW_DATE><d:BC_2YEAR>{y2}</d:BC_2YEAR><d:BC_10YEAR>{y10}</d:BC_10YEAR></m:properties>'
        for day, y2, y10 in rows) + '</feed>').encode()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(macro.requests, 'get', blocked)


@pytest.fixture
def pages(monkeypatch):
    calls = []
    def fetch(self, url):
        calls.append(url)
        return {macro.CPI_URL: CPI, macro.BEA_INDEX: INDEX, PCE_URL: PCE,
                macro.TREASURY_URL.format(year=2026): treasury(('2026-09-22', 3.55, 4.11))}[url]
    monkeypatch.setattr(macro._Fetcher, '__call__', fetch)
    return calls


def test_release_dates_periods_calendar_and_definitions():
    cpi = macro._release(CPI, 'cpi', macro.CPI_URL, date(2026, 9, 23))
    assert cpi['published_date'] == '2026-09-11'
    assert cpi['observed_period'] == 'AUGUST 2026'
    assert 'before seasonal adjustment' in cpi['excerpt']
    assert cpi['next_release']['date'] == '2026-10-14'
    pce = macro._release(PCE, 'pce', PCE_URL, date(2026, 9, 23))
    assert pce['observed_period'] == 'July 2026'
    assert pce['next_release']['status'] == 'not_yet_released'
    assert pce['next_release']['date'] == '2026-09-30'
    assert 'same month one year ago' in pce['excerpt']


@pytest.mark.parametrize('raw,kind', [(CPI, 'cpi'), (PCE, 'pce')])
def test_future_and_changed_release_fail_closed(raw, kind):
    assert macro._release(raw, kind, PCE_URL, date(2026, 1, 1))['status'] == 'not_found'
    assert macro._release(b'<p>Layout changed. 0.2 percent</p>', kind, PCE_URL, date(2026, 9, 23))['status'] == 'not_found'


def test_same_day_embargo_not_assumed_released():
    assert macro._release(CPI, 'cpi', macro.CPI_URL, date(2026, 9, 11))['status'] == 'not_found'


def test_unreleased_requires_future_calendar_evidence():
    parsed = macro._release(PCE.replace(b'Next release:', b'Other:'), 'pce', PCE_URL, date(2026, 9, 23))
    assert 'next_release' not in parsed


def test_same_date_yields_no_future_stale_missing_or_nonfinite():
    raw = treasury(('2026-09-21', '3.5', ''), ('2026-09-22', '3.55', '4.11'),
                   ('2026-09-23', '4', '8'), ('2026-09-24', '2', '9'), ('2026-09-22', 'NaN', '4'))
    packet = macro._treasury(raw, 'url', date(2026, 9, 23))
    assert packet['observed_date'] == '2026-09-22'
    assert packet['spread_10y_minus_2y_pp'] == .56
    assert macro._treasury(raw, 'url', date(2026, 10, 10))['status'] == 'not_found'


def test_cache_success_only_and_date_guard(tmp_path, pages):
    first = macro.collect_us_official_macro_sources('20260923', tmp_path)
    assert len(pages) == 4
    second = macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert len(pages) == 4
    assert first['sources'] == second['sources']
    path = next(tmp_path.iterdir())
    bad = json.loads(path.read_text())
    bad['captured_at'] = '2999-01-01T00:00:00+00:00'
    path.write_text(json.dumps(bad))
    macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert len(pages) == 8


def test_failures_not_cached(tmp_path, monkeypatch):
    def unavailable(*args):
        raise ValueError('unavailable')
    monkeypatch.setattr(macro._Fetcher, '__call__', unavailable)
    packet = macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert all(v['status'] == 'not_found' for v in packet['sources'].values())
    assert list(tmp_path.iterdir()) == []
    text = macro.render_us_official_macro_sources(packet)
    assert '미발표를 뜻하지 않음' in text


def test_bad_cache_cannot_assert_future_releases(tmp_path, pages):
    macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    path = next(tmp_path.iterdir())
    bad = json.loads(path.read_text())
    bad['sources']['cpi']['published_date'] = '2026-10-01'
    bad['sources']['pce']['url'] = 'https://evil.invalid/'
    path.write_text(json.dumps(bad))
    macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert len(pages) == 7


@pytest.mark.parametrize('url', ['http://www.bea.gov/', 'https://www.bea.gov.evil.test/',
                                'https://user@www.bea.gov/', 'https://www.bea.gov:444/', 'file:///etc/passwd'])
def test_public_https_allowlist(url):
    with pytest.raises(ValueError):
        macro._Fetcher()(url)


def test_future_asof_does_not_fetch(pages):
    result = macro.collect_us_official_macro_sources(date(datetime.now(timezone.utc).year + 1, 1, 1))
    assert result['status'] == 'invalid_future_as_of'
    assert pages == []


def test_api_current_revision_not_historical_vintage():
    today = datetime.now(macro.ZoneInfo('America/New_York')).date()
    month = today.month - 1 or 12
    year = today.year if today.month > 1 else today.year - 1
    rows = [{'year': str(year), 'period': f'M{month:02}', 'value': '103.4'},
            {'year': str(year - 1), 'period': f'M{month:02}', 'value': '100'},
            {'year': str(year), 'period': 'M13', 'value': '999'},
            {'year': str(year), 'period': f'M{month:02}', 'value': '-'}]
    raw = json.dumps({'status': 'REQUEST_SUCCEEDED', 'Results': {'series': [
        {'seriesID': 'CUUR0000SA0', 'data': rows}]}}).encode()
    result = macro._cpi_api(raw, 'url', today)
    assert result['derived_yoy_percent'] == 3.4
    assert result['publication_date_status'] == 'unknown'
    assert 'published_date' not in result
    with pytest.raises(ValueError, match='vintage'):
        macro._cpi_api(raw, 'url', date(2020, 1, 1))


def test_redirect_cannot_escape_allowlist(monkeypatch):
    calls = []
    class Response:
        is_redirect = True
        def __init__(self):
            self.headers = {'Location': 'https://evil.invalid/steal'}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        return Response()
    monkeypatch.setattr(macro.requests, 'get', get)
    with pytest.raises(ValueError):
        macro._Fetcher()(macro.CPI_URL)
    assert calls == [macro.CPI_URL]


def test_response_size_and_request_count_bounded(monkeypatch):
    class Response:
        is_redirect = False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def raise_for_status(self):
            pass
        def iter_content(self, size):
            yield b'x' * (macro.MAX_BYTES + 1)
    monkeypatch.setattr(macro.requests, 'get', lambda *a, **kw: Response())
    with pytest.raises(ValueError, match='response boundary'):
        macro._Fetcher()(macro.CPI_URL)
    fetch = macro._Fetcher()
    fetch.calls = 9
    with pytest.raises(ValueError, match='request boundary'):
        fetch(macro.CPI_URL)


def test_same_day_release_requires_elapsed_embargo(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 11, 9, 0, tzinfo=macro.ZoneInfo('America/New_York'))
    monkeypatch.setattr(macro, 'datetime', Clock)
    assert macro._release(CPI, 'cpi', macro.CPI_URL, date(2026, 9, 11))['status'] == 'released'


def test_corrupt_cache_fails_open_to_fetch(tmp_path, pages):
    path = tmp_path / 'official_macro_v1_2026-09-23.json'
    path.write_text('invalid json')
    result = macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert result['sources']['pce']['status'] == 'released'
    assert len(pages) == 4


def test_fred_requires_same_date_finite_pair_and_labels_definition():
    today = datetime.now(macro.ZoneInfo('America/New_York')).date()
    yesterday = today - macro.timedelta(days=1)
    two_days = today - macro.timedelta(days=2)
    csv = f'observation_date,DGS2,DGS10\n{today},1,9\n{yesterday},,9\n{two_days},4.76,4.96\n{yesterday},NaN,9\n'
    packet = macro._fred_yields(csv.encode(), today)
    assert packet['observed_date'] == two_days.isoformat()
    assert packet['spread_10y_minus_2y_pp'] == .2
    assert 'constant maturity' in packet['definition']
    assert 'not historical vintage' in packet['availability_basis']
    with pytest.raises(ValueError, match='vintages'):
        macro._fred_yields(csv.encode(), date(2020, 1, 1))
    with pytest.raises(ValueError, match='columns'):
        macro._fred_yields(b'DATE,VALUE\n2026-09-22,9\n', today)


def test_bad_yield_cache_recomputed(tmp_path, pages):
    macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    path = next(tmp_path.iterdir())
    packet = json.loads(path.read_text())
    packet['sources']['treasury']['spread_10y_minus_2y_pp'] = 999
    path.write_text(json.dumps(packet))
    result = macro.collect_us_official_macro_sources('2026-09-23', tmp_path)
    assert result['sources']['treasury']['spread_10y_minus_2y_pp'] == .56
    assert len(pages) == 5


def test_future_observed_period_rejected():
    raw = PCE.replace(b'July 2026', b'July 2027')
    assert macro._release(raw, 'pce', PCE_URL, date(2026, 9, 23))['status'] == 'not_found'


def test_same_day_release_cache_reused_only_after_actual_embargo(monkeypatch):
    zone = macro.ZoneInfo('America/New_York')

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 11, 9, 0, tzinfo=zone)
            return value.astimezone(tz) if tz else value

    monkeypatch.setattr(macro, 'datetime', Clock)
    now = Clock.now(timezone.utc)
    item = {**macro._release(CPI, 'cpi', macro.CPI_URL, date(2026, 9, 11)),
            'captured_at': now.isoformat()}
    assert macro._valid_cached('cpi', item, date(2026, 9, 11), now)
    assert datetime.fromisoformat(item['release_at']).astimezone(zone).hour == 8
    assert datetime.fromisoformat(item['release_at']).astimezone(zone).minute == 30
    before = Clock(2026, 9, 11, 8, 29, tzinfo=zone)
    assert not macro._valid_cached('cpi', {**item, 'captured_at': before.isoformat()}, date(2026, 9, 11), now)
    assert not macro._valid_cached('cpi', item, date(2026, 9, 11), before)
    assert not macro._valid_cached('cpi', {**item, 'release_at': '2026-09-11T08:00:00-04:00'}, date(2026, 9, 11), now)


@pytest.mark.parametrize('period', ['October 2026', 'September 2026', 'August 2027'])
def test_future_observed_period_in_cache_rejected(period):
    now = datetime(2026, 9, 23, 14, tzinfo=timezone.utc)
    item = {**macro._release(CPI, 'cpi', macro.CPI_URL, date(2026, 9, 23)),
            'captured_at': now.isoformat(), 'observed_period': period}
    assert not macro._valid_cached('cpi', item, date(2026, 9, 23), now)


def test_observed_period_must_precede_its_publication_month():
    raw = CPI.replace(b'AUGUST 2026', b'SEPTEMBER 2026')
    assert macro._release(raw, 'cpi', macro.CPI_URL, date(2026, 10, 23))['status'] == 'not_found'


def test_duplicate_yield_date_conflicts_fail_closed_and_identical_deduplicate():
    asof = datetime.now(macro.ZoneInfo('America/New_York')).date()
    day = asof - macro.timedelta(days=1)
    conflicting = treasury((str(day), 3.5, 4.0), (str(day), 3.7, 4.2))
    assert macro._treasury(conflicting, 'url', asof)['status'] == 'not_found'
    identical = treasury((str(day), 3.5, 4.0), (str(day), 3.5, 4.0))
    assert macro._treasury(identical, 'url', asof)['yield_2y_percent'] == 3.5
    with pytest.raises(ValueError, match='conflict'):
        macro._fred_yields(f'observation_date,DGS2,DGS10\n{day},3.5,4\n{day},3.7,4.2\n'.encode(), asof)
    pair = macro._fred_yields(f'observation_date,DGS2,DGS10\n{day},3.5,4\n{day},3.5,4\n'.encode(), asof)
    assert pair['yield_10y_percent'] == 4.0
