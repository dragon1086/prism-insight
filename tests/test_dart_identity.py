import asyncio
import json
from datetime import date, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from prism_core import dart_identity as module

START = date(2025, 1, 1)
CUTOFF = datetime.fromisoformat('2026-09-18T15:30:00+09:00')


def catalog(*companies):
    anchors = ''.join(f'<a onclick="openCorpInfoNew(\'{code}\')">{name}</a>' for name, code in companies)
    return f'<table><tbody id="tbody"><tr><td>{anchors}</td></tr></tbody></table>'


def profile(ticker='000001'):
    return f'<table><tr><th>종목코드</th><td>{ticker}</td></tr></table>'


def run(handler, **overrides):
    options = {'name': '회사A', 'ticker': '000001', 'start_date': START, 'decision_at': CUTOFF}
    options.update(overrides)
    def factory(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)
    return asyncio.run(module.resolve_dart_identity(**options, client_factory=factory))


def test_name_requires_profile_and_preserves_provenance_and_metrics():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.host == 'dart.fss.or.kr'
        assert request.headers['accept-encoding'] == 'identity'
        body = profile() if request.url.path.endswith('selectPopup.ax') else catalog(('회사A', '12345678'))
        return httpx.Response(200, text=body)
    result = run(handler)
    assert result['corp_code'] == '12345678'
    assert result['ticker_verified_from_company_profile'] is True
    assert result['metrics']['calls'] == len(seen) == 2
    assert result['metrics']['response_bytes'] == sum(x['response_bytes'] for x in result['lookups'])
    assert all(len(x['response_sha256']) == 64 for x in result['lookups'])
    assert datetime.fromisoformat(result['observed_at']).utcoffset() is not None


def test_ticker_fallback_and_korean_cutoff_date():
    seen = []
    def handler(request):
        fields = parse_qs(request.content.decode())
        seen.append(fields)
        if request.url.path.endswith('selectPopup.ax'):
            return httpx.Response(200, text=profile())
        return httpx.Response(200, text=catalog(('공식명', '12345678'))
                              if fields['textCrpNm'] == ['000001'] else catalog())
    result = run(handler, decision_at=datetime.fromisoformat('2026-09-18T23:00:00+00:00'))
    assert result['resolution'] == 'OFFICIAL_TICKER_QUERY'
    assert result['official_display_names'] == ['공식명']
    assert result['metrics']['calls'] == 3
    assert seen[0]['endDate'] == ['20260919']


@pytest.mark.parametrize('ticker', ['000002', '000001 000002', '', '000001 000001'])
def test_wrong_missing_or_duplicate_profile_ticker(ticker):
    result = run(lambda r: httpx.Response(200, text=profile(ticker) if r.url.path.endswith('selectPopup.ax')
                                        else catalog(('회사A', '12345678'))))
    assert result['reason'] == 'PROFILE_TICKER_MISMATCH'
    assert result['corp_code'] is None


@pytest.mark.parametrize('fallback,reason', [
    (catalog(), 'IDENTITY_AMBIGUOUS_OR_MISSING'),
    (catalog(('회사A', '12345678'), ('회사A', '87654321')), 'IDENTITY_AMBIGUOUS_OR_MISSING'),
    (catalog(('회사A', '11111111')), 'NAME_TICKER_IDENTITY_CONFLICT'),
])
def test_ambiguous_or_conflicting_mapping(fallback, reason):
    def handler(request):
        query = parse_qs(request.content.decode())['textCrpNm'][0]
        return httpx.Response(200, text=fallback if query == '000001'
                              else catalog(('회사A', '12345678'), ('회사A', '87654321')))
    result = run(handler)
    assert result['reason'] == reason and result['metrics']['calls'] == 2


@pytest.mark.parametrize('overrides', [
    {'name': ''}, {'name': 'x' * 81}, {'ticker': '１２３４５６'}, {'ticker': '123'},
    {'decision_at': datetime(2026, 9, 18, tzinfo=None)}, {'start_date': date(2027, 1, 1)},  # noqa: DTZ001
    {'start_date': '2025-01-01'}, {'start_date': datetime(2025, 1, 1, tzinfo=None)},  # noqa: DTZ001
])
def test_invalid_input_does_not_fetch(overrides):
    result = run(lambda r: pytest.fail('must not fetch'), **overrides)
    assert result['reason'] == 'IDENTITY_INPUT_INVALID'
    assert result['metrics'] == {'calls': 0, 'response_bytes': 0}


@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'Location': 'https://private.example/secret'}),
    httpx.Response(403, text='private secret'),
    httpx.Response(200, headers={'Content-Encoding': 'br'}, content=b''),
])
def test_redirect_status_encoding_rejected_without_followup(response):
    result = run(lambda r: response)
    assert result['reason'] == 'IDENTITY_RESPONSE_FAILURE'
    assert result['metrics']['calls'] == 1
    assert 'private' not in json.dumps(result)


def test_response_limit():
    result = run(lambda r: httpx.Response(200, content=b'x' * (module._MAX_RESPONSE_BYTES + 1)))
    assert result['reason'] == 'IDENTITY_RESPONSE_LIMIT'
    assert result['lookups'] == []


@pytest.mark.parametrize('limit,reason', [('_MAX_CALLS', 'IDENTITY_CALL_LIMIT'),
                                       ('_MAX_TOTAL_BYTES', 'IDENTITY_RESPONSE_LIMIT')])
def test_aggregate_limits(monkeypatch, limit, reason):
    monkeypatch.setattr(module, limit, 1)
    result = run(lambda r: httpx.Response(200, text=catalog(('회사A', '12345678'))))
    assert result['reason'] == reason
    assert result['metrics']['calls'] == 1


@pytest.mark.parametrize('error,reason', [(httpx.RemoteProtocolError('secret'), 'IDENTITY_TRANSPORT_FAILURE'),
                                       (httpx.ReadTimeout('secret'), 'IDENTITY_TIMEOUT'),
                                       (RuntimeError('secret'), 'IDENTITY_LOOKUP_FAILED')])
def test_failures_are_sanitized(error, reason):
    def handler(request):
        raise error
    result = run(handler)
    assert result['reason'] == reason
    assert 'secret' not in json.dumps(result)


def test_overall_timeout(monkeypatch):
    monkeypatch.setattr(module, '_TIMEOUT_SECONDS', 0.01)
    async def handler(request):
        await asyncio.sleep(1)
    result = run(handler)
    assert result['reason'] == 'IDENTITY_TIMEOUT'


def test_cancellation_propagates():
    async def handler(request):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        run(handler)


def test_cancellation_preserves_live_metrics_sink():
    metrics = {'calls': 999, 'response_bytes': 999}
    body = catalog(('회사A', '12345678'))
    async def handler(request):
        if request.url.path.endswith('selectPopup.ax'):
            raise asyncio.CancelledError()
        return httpx.Response(200, text=body)
    with pytest.raises(asyncio.CancelledError):
        run(handler, _metrics=metrics)
    assert metrics == {'calls': 2, 'response_bytes': len(body.encode())}


def test_result_uses_same_metrics_sink():
    metrics = {}
    result = run(lambda r: httpx.Response(200, text=catalog()), _metrics=metrics)
    assert result['metrics'] is metrics
    assert metrics['calls'] == 2


@pytest.mark.parametrize('sink', [[], 0, 'invalid'])
def test_non_dict_metrics_sink_rejected(sink):
    result = run(lambda r: pytest.fail('must not fetch'), _metrics=sink)
    assert result['reason'] == 'IDENTITY_INPUT_INVALID'


def test_extreme_timezone_conversion_is_sanitized():
    result = run(lambda r: pytest.fail('must not fetch'),
                 decision_at=datetime.fromisoformat('9999-12-31T23:59:59+00:00'))
    assert result['reason'] == 'IDENTITY_INPUT_INVALID'
    assert result['metrics']['calls'] == 0


@pytest.mark.parametrize('body', [b'', b'\xff'])
def test_invalid_html_or_encoding_is_sanitized(body):
    result = run(lambda r: httpx.Response(200, content=body))
    assert result['reason'] == 'IDENTITY_LOOKUP_FAILED'


@pytest.mark.parametrize('rows,expected', [
    ('<tr><th>업종명</th><td>지주회사</td></tr>', '지주회사'),
    ('', None),
    ('<tr><th>업종명</th><td>국내은행</td></tr><tr><th>업종명</th><td>지주회사</td></tr>', None),
])
def test_official_industry_name_is_read_only_when_unique(rows, expected):
    def handler(request):
        body = (profile()[:-8] + rows + '</table>' if request.url.path.endswith('selectPopup.ax')
                else catalog(('회사A', '12345678')))
        return httpx.Response(200, text=body)
    result = run(handler)
    assert result['ticker_verified_from_company_profile'] is True
    assert result.get('industry_name') == expected


@pytest.mark.parametrize('rows,expected', [
    # Observed markup: buttons share the cell with the official name.
    ('<tr><th scope="row"><label>회사이름</label></th><td> 롯데위탁관리부동산투자회사 주식회사 '
     '<button class="btnRss">rss</button> </td></tr>', '롯데위탁관리부동산투자회사 주식회사'),
    ('<tr><th>회사이름</th><td> SK(주) <button>정보 더보기</button> <button>rss</button> </td></tr>', 'SK(주)'),
    ('', None),
])
def test_official_legal_name_excludes_cell_buttons(rows, expected):
    def handler(request):
        body = (profile()[:-8] + rows + '</table>' if request.url.path.endswith('selectPopup.ax')
                else catalog(('회사A', '12345678')))
        return httpx.Response(200, text=body)
    result = run(handler)
    assert result['ticker_verified_from_company_profile'] is True
    assert result.get('legal_name') == expected
