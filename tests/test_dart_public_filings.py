"""Synthetic DART markup; no issuer report content or live network."""
import asyncio
import json
import time
from datetime import date, datetime, timezone

import httpx
import pytest

from prism_core.dart_public_filings import (
    collect_dart_periodic_filings,
    parse_catalog_page,
    parse_cover_metadata,
    parse_viewer_nodes,
)

CORP = "01343665"
RECORDS = [
    ("20260814001631", "반기", "2026.06", "2026-08-14"),
    ("20260515000862", "분기", "2026.03", "2026-05-15"),
    ("20260318001224", "사업", "2025.12", "2026-03-18"),
    ("20251114000001", "분기", "2025.09", "2025-11-14"),
    ("20250814000001", "반기", "2025.06", "2025-08-14"),
    ("20250515000001", "분기", "2025.03", "2025-05-15"),
    ("20250318000001", "사업", "2024.12", "2025-03-18"),
]


def catalog(records=RECORDS, total=None):
    rows = "".join(
        f'<tr><td>{i+1}</td><td><a onclick="openCorpInfoNew(\'{CORP}\')">샘플</a></td>'
        f'<td><a href="/dsaf001/main.do?rcpNo={rid}">{kind}보고서 ({period})</a></td>'
        f'<td>샘플</td><td>{submitted.replace("-", ".")}</td><td></td></tr>'
        for i, (rid, kind, period, submitted) in enumerate(records))
    return f'<table><tbody id="tbody">{rows}</tbody></table><div class="pageInfo">[1/1] [총 {len(records) if total is None else total}건]</div>'


def main(rid):
    script = f"openCorpInfoNew('{CORP}'); function viewDoc() {{ var url='/report/viewer.do'; }}\n"
    for i, title in enumerate(("반기보고서", "2. 연결재무제표", "4. 재무제표")):
        for key, value in {'text': title, 'rcpNo': rid, 'dcmNo': "12345678", 'eleId': str(i), 'offset': "0", 'length': "100", 'dtd': "dart4.xsd"}.items():
            script += f'node{i}["{key}"] = {json.dumps(value, ensure_ascii=False)};\n'
    return f'<script>{script}</script>'


def cover(record):
    _, kind, period, submitted = record
    year, month = map(int, period.split('.'))
    day = 31 if month in (3, 12) else 30
    return (f'<h1>{kind} 보 고 서</h1><table><tr><td>사업연도</td><td>{year}년 01월 01일</td><td>부터</td></tr>'
            f'<tr><td></td><td>{year}년 {month:02}월 {day}일</td><td>까지</td></tr>'
            f'<tr><td>한국거래소 귀중</td><td>{submitted}</td></tr>'
            '<tr><td>회사명 :</td><td>주식회사 샘플</td></tr></table>')


BODY = '<h2>연결재무상태표</h2><table><tr><td>자산총계</td><td>1,000</td></tr><tr><td>부채총계</td><td>300</td></tr></table>'


def run(mutator=None, **kwargs):
    requests = []
    clients = []
    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = catalog()
        elif request.url.path.endswith("main.do"):
            body = main(request.url.params['rcpNo'])
        else:
            record = next(r for r in RECORDS if r[0] == request.url.params['rcpNo'])
            body = cover(record) if request.url.params['eleId'] == '0' else BODY
        if mutator:
            body = mutator(request, body)
        return httpx.Response(200, text=body)
    def factory(**options):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
        clients.append(client)
        return client
    result = asyncio.run(collect_dart_periodic_filings(
        corp_code=CORP, decision_at=datetime(2026, 9, 18, 6, 30, tzinfo=timezone.utc),
        start_date=date(2025, 1, 1), scope='consolidated', client_factory=factory, **kwargs))
    assert all(c.is_closed for c in clients)
    return result, requests


def test_seven_filings_real_httpx_path():
    result, requests = run()
    assert result['status'] == 'COMPLETE_WITHIN_QUERY'
    assert result['selection']['primary_id'] == RECORDS[0][0]
    assert result['selection']['annual_supplement_id'] == RECORDS[2][0]
    assert result['selection']['latest_confirmed'] is False
    assert result['coverage']['global_complete'] is False
    assert result['metrics']['calls'] == 22
    assert 'finalReport' not in requests[0].content.decode()
    assert requests[0].content.decode().count('publicType=') == 3
    assert result['filings'][0]['sections']['financial_statements']['preview_not_complete'] is True


@pytest.mark.parametrize('mutation', [
    lambda h: h.replace(CORP, '00000001'),
    lambda h: h.replace('/dsaf001/main.do?', 'https://evil.example/dsaf001/main.do?'),
    lambda h: h.replace('[총 7건]', '[총 8건]'),
    lambda h: h.replace('class="pageInfo"', 'class="other"'),
    lambda h: h.replace('<td>6</td>', '<td>7</td>'),
])
def test_bad_catalog(mutation):
    with pytest.raises(ValueError):
        parse_catalog_page(mutation(catalog()), CORP)


@pytest.mark.parametrize('mutation', [
    lambda h: h.replace('"12345678"', 'getId()'),
    lambda h: h.replace('"dart4.xsd"', '"other.xsd"'),
    lambda h: h.replace('"offset"] = "0"', '"offset"] = "-1"'),
    lambda h: h.replace('/report/viewer.do', '/wrong'),
    lambda h: h.replace(CORP, '00000001'),
])
def test_bad_nodes(mutation):
    with pytest.raises(ValueError):
        parse_viewer_nodes(mutation(main(RECORDS[0][0])), RECORDS[0][0], CORP)


def test_cover_exact_period_not_inferred():
    result = parse_cover_metadata(cover(RECORDS[0]).replace('01월 01일', '02월 03일'))
    assert result['period_start'] == date(2026, 2, 3)
    assert result['period_end'] == date(2026, 6, 30)
    assert result['legal_name'] == '주식회사 샘플'


@pytest.mark.parametrize('case', ['newest', 'pages', 'correction', 'same_day'])
def test_acquisition_gaps_never_fallback(case):
    def mutate(request, body):
        if case == 'pages' and request.method == 'POST':
            return body.replace('[총 7건]', '[총 8건]')
        if case == 'correction' and request.method == 'POST':
            return body.replace('반기보고서', '[기재정정]반기보고서', 1)
        if case == 'same_day' and request.method == 'POST':
            return body.replace('2026.08.14', '2026.09.18')
        if case == 'newest' and request.url.params.get('rcpNo') == RECORDS[0][0] and request.url.path.endswith('viewer.do'):
            return '<p>자료 없음</p>'
        return body
    result, _ = run(mutate)
    assert result['status'] in {'PARTIAL', 'FAILED'}
    for key in ('primary_id', 'latest_candidate_id', 'annual_supplement_id'):
        assert result['selection'][key] is None
    assert result['selection']['blocked_by']


def test_old_annual_failure_keeps_newest():
    result, _ = run(lambda r, b: '<p>오류</p>' if r.url.params.get('rcpNo') == RECORDS[2][0] and r.url.path.endswith('viewer.do') else b)
    assert result['selection']['primary_id'] == RECORDS[0][0]
    assert result['selection']['annual_supplement_id'] is None


def test_budget_and_unfetched_newest():
    result, requests = run(max_calls=1)
    assert len(requests) == 1
    assert result['selection']['primary_id'] is None
    assert result['status'] == 'PARTIAL'


@pytest.mark.parametrize('kwargs', [{'max_pages': 0}, {'max_calls': 65}, {'timeout_seconds': 181}, {'max_filings': True}])
def test_invalid_bounds_before_network(kwargs):
    with pytest.raises(ValueError):
        run(**kwargs)


@pytest.mark.parametrize('body', [
    '<h2>연결재무상태표</h2><table><tr><td>자산 부채</td></tr></table>',
    '<h2>연결재무상태표</h2><table><tr><td>가격</td><td>100</td></tr></table>',
    '<h2>재무상태표</h2><table><tr><td>자산 부채</td><td>100</td></tr></table>',
    '<h2>연결재무상태표</h2><p>해당사항 없음</p>',
])
def test_scope_requires_actual_financial_body(body):
    result, _ = run(lambda r, b: body if r.url.path.endswith('viewer.do') and r.url.params['eleId'] == '1' else b)
    assert result['selection']['primary_id'] is None
    assert all(not f['scope_verified'] for f in result['filings'])


@pytest.mark.parametrize('mutation', [
    lambda h: h.replace('2026년 01월 01일', ''),
    lambda h: h.replace('2026년 06월 30일', '2026년 02월 30일'),
    lambda h: h.replace('회사명 :', '다른 항목'),
    lambda h: h.replace('</table>', '<tr><td>회사명 :</td><td>중복</td></tr></table>'),
])
def test_ambiguous_cover_rejected(mutation):
    with pytest.raises(ValueError):
        parse_cover_metadata(mutation(cover(RECORDS[0])))


def test_node_variables_may_be_reused_but_identities_may_not():
    text = main(RECORDS[0][0]).replace('node2[', 'node0[').replace('node1[', 'node0[')
    assert len(parse_viewer_nodes(text, RECORDS[0][0], CORP)) == 3
    with pytest.raises(ValueError, match='DUPLICATE'):
        parse_viewer_nodes(text.replace('"eleId"] = "1"', '"eleId"] = "0"'), RECORDS[0][0], CORP)


def test_actual_viewer_function_nested_conditional():
    text = main(RECORDS[0][0]).replace(
        "var url='/report/viewer.do';",
        """var params=''; params+='?rcpNo='+rcpNo;
        if(!isNullTrim(fixKeyword)){ params += '&keyword=' + fixKeyword; }
        document.getElementById("ifrm").src = "/report/viewer.do" + params;""")
    assert len(parse_viewer_nodes(text, RECORDS[0][0], CORP)) == 3
    with pytest.raises(ValueError, match='VIEWER_FUNCTION'):
        parse_viewer_nodes(text.replace('"/report/viewer.do"', '"https://evil.example/report/viewer.do"'), RECORDS[0][0], CORP)


def test_explicit_empty_not_error_page():
    text = '<table><tbody id="tbody"><tr><td>조회 결과가 없습니다.</td></tr></tbody></table><div class="pageInfo">[1/1] [총 0건]</div>'
    assert parse_catalog_page(text, CORP)['rows'] == []
    with pytest.raises(ValueError):
        parse_catalog_page('<p>접속이 차단되었습니다</p>', CORP)
    result, _ = run(lambda r, b: text if r.method == 'POST' else b)
    assert result['status'] == 'EMPTY'


def test_filing_limit_keeps_known_newer_without_certifying_global_latest():
    result, requests = run(max_filings=1)
    assert len(requests) == 4
    assert len(result['filings']) == 7
    assert result['selection']['primary_id'] == RECORDS[0][0]
    assert result['status'] == 'PARTIAL'
    assert result['selection']['latest_confirmed'] is False


def test_catalog_outside_query_fails_closed():
    result, requests = run(lambda r, b: b.replace('2026.08.14', '2026.09.19') if r.method == 'POST' else b)
    assert result['status'] == 'FAILED'
    assert len(requests) == 1


async def collect_with_transport(handler, **kwargs):
    clients = []
    def factory(**options):
        assert options == {'timeout': 15, 'follow_redirects': False, 'trust_env': False}
        c = httpx.AsyncClient(transport=httpx.MockTransport(handler), **options)
        clients.append(c)
        return c
    try:
        return await collect_dart_periodic_filings(
            corp_code=CORP, decision_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
            start_date=date(2025, 1, 1), scope='consolidated', client_factory=factory, **kwargs)
    finally:
        assert all(c.is_closed for c in clients)


@pytest.mark.parametrize('status', [301, 403, 429, 500])
def test_non_200_is_not_empty(status):
    result = asyncio.run(collect_with_transport(lambda r: httpx.Response(status, headers={'location': 'https://evil.example'})))
    assert result['status'] == 'FAILED'
    assert result['metrics']['calls'] == 1
    assert result['errors'] == ['HTTP_STATUS_FAILURE']


def test_transport_error_has_no_raw_exception():
    def handler(request):
        raise httpx.ReadTimeout('secret credentials', request=request)
    result = asyncio.run(collect_with_transport(handler))
    assert result['errors'] == ['HTTP_TRANSPORT_FAILURE']
    assert 'secret' not in json.dumps(result)


def test_compression_rejected_before_stream_read():
    class Unreadable(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError('must not consume compressed body')
            yield b''
    result = asyncio.run(collect_with_transport(
        lambda r: httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=Unreadable())))
    assert result['errors'] == ['HTTP_ENCODING_REJECTED']
    assert result['metrics']['response_bytes'] == 0


def test_stream_size_limit():
    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'x' * (2 * 1024 * 1024)
            yield b'x'
    result = asyncio.run(collect_with_transport(lambda r: httpx.Response(200, stream=Oversized())))
    assert result['errors'] == ['RESPONSE_BYTES_EXCEEDED']


def test_timeout_and_external_cancellation_close_client():
    async def handler(request):
        await asyncio.sleep(10)
        return httpx.Response(200, text=catalog())
    result = asyncio.run(collect_with_transport(handler, timeout_seconds=0.001))
    assert result['errors'] == ['TOTAL_TIMEOUT']
    async def cancel():
        task = asyncio.create_task(collect_with_transport(handler))
        await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancel())


def multipage(number, total=101):
    start = (number - 1) * 100 + 1
    rows = ''.join(
        f'<tr><td>{i}</td><td><a onclick="openCorpInfoNew(\'{CORP}\')">샘플</a></td>'
        f'<td><a href="/dsaf001/main.do?rcpNo=20260814{i:06}">반기보고서 (2026.06)</a></td>'
        '<td>샘플</td><td>2026.08.14</td><td></td></tr>'
        for i in range(start, min(number * 100, total) + 1))
    return f'<table><tbody id="tbody">{rows}</tbody></table><div class="pageInfo">[{number}/2] [총 {total}건]</div>'


def test_page_cap_withholds_all_ids_even_if_known_candidate():
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, text=multipage(1))
        rid = request.url.params['rcpNo']
        if request.url.path.endswith('main.do'):
            body = main(rid)
        else:
            body = cover(RECORDS[0]) if request.url.params['eleId'] == '0' else BODY
        return httpx.Response(200, text=body)
    result = asyncio.run(collect_with_transport(handler, max_pages=1, max_filings=1))
    assert result['coverage']['seen_count'] == 100
    assert result['coverage']['complete_within_query'] is False
    assert result['selection']['primary_id'] is None
    assert result['selection']['annual_supplement_id'] is None
    assert result['selection']['latest_candidate_id'] is None
    assert 'PAGE_COVERAGE_UNCONFIRMED' in result['selection']['blocked_by']


@pytest.mark.parametrize('second', [multipage(1), multipage(2, total=102)])
def test_repeated_or_changed_pagination_preserves_first_page_as_unresolved(second):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(200, text=multipage(1) if count == 1 else second)
    result = asyncio.run(collect_with_transport(handler))
    assert len(result['filings']) == 100
    assert result['status'] == 'PARTIAL'
    assert result['selection']['primary_id'] is None
    assert result['errors'] == ['CATALOG_PAGE_INCONSISTENT']


def test_cumulative_bytes_budget_preserves_acquired_rows():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if request.method == 'POST':
            body = catalog()
        elif request.url.path.endswith('main.do'):
            body = main(request.url.params['rcpNo'])
        else:
            rec = next(r for r in RECORDS if r[0] == request.url.params['rcpNo'])
            body = cover(rec) if request.url.params['eleId'] == '0' else BODY
        # Valid markup padding makes each response just below individual cap.
        return httpx.Response(200, content=(body + '<!--' + ' ' * 1_900_000 + '-->').encode())
    result = asyncio.run(collect_with_transport(handler))
    assert result['status'] == 'PARTIAL'
    assert len(result['filings']) == 7
    assert any('RESPONSE_BYTES_EXCEEDED' in r['errors'] for r in result['filings'])
    assert calls <= 28


@pytest.mark.parametrize('close_failure', [False, True])
def test_unexpected_client_errors_do_not_escape(close_failure):
    class Broken:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            raise RuntimeError('secret close')

    def factory(**options):
        if close_failure:
            return Broken()
        raise RuntimeError('secret factory')
    result = asyncio.run(collect_dart_periodic_filings(
        corp_code=CORP, decision_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        start_date=date(2025, 1, 1), scope='consolidated', client_factory=factory))
    assert result['errors'] == ['ACQUISITION_FAILURE']
    assert 'secret' not in json.dumps(result)


def test_unhashable_scope_invalid_policy():
    with pytest.raises(ValueError, match='INVALID_ACQUISITION_POLICY'):
        asyncio.run(collect_dart_periodic_filings(
            corp_code=CORP, decision_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
            start_date=date(2025, 1, 1), scope=[]))


@pytest.mark.parametrize('body', [
    '<tr><td>조회할 권한이 없습니다.</td></tr>',
    '<tr><td>조회 결과가 없습니다.</td><td>unexpected</td></tr>',
    '<tr><td><a href="/dsaf001/main.do?rcpNo=20260814001631">조회 결과가 없습니다.</a></td></tr>',
])
def test_empty_requires_exact_normal_placeholder(body):
    html = f'<table><tbody id="tbody">{body}</tbody></table><div class="pageInfo">[1/1] [총 0건]</div>'
    with pytest.raises(ValueError):
        parse_catalog_page(html, CORP)


def test_zero_count_cannot_hide_real_rows():
    html = catalog().replace('[총 7건]', '[총 0건]').replace('</tbody>', '<tr><td>조회 결과가 없습니다.</td></tr></tbody>')
    with pytest.raises(ValueError):
        parse_catalog_page(html, CORP)


def test_zero_count_rejects_inconsistent_page_information():
    html = '<table><tbody id="tbody"><tr><td>조회 결과가 없습니다.</td></tr></tbody></table><div class="pageInfo">[1/2] [총 0건]</div>'
    with pytest.raises(ValueError):
        parse_catalog_page(html, CORP)


def test_observed_zero_without_pagination():
    html = '<table><tbody id="tbody"><tr><td class="no_data end" colspan="6" align="center">조회 결과가 없습니다.</td></tr></tbody></table>'
    assert parse_catalog_page(html, CORP) == {
        'rows': [], 'page': 1, 'total_pages': 1, 'total_count': 0,
        'empty_basis': 'CANONICAL_NO_DATA_PLACEHOLDER'}
    for mutated in (
        html.replace('조회 결과가 없습니다.', '조회할 권한이 없습니다.'),
        html.replace('colspan="6"', 'colspan="5"'),
        html.replace('</tbody>', '<tr><td>other</td></tr></tbody>'),
        html.replace('조회 결과가 없습니다.', '<a>조회 결과가 없습니다.</a>'),
    ):
        with pytest.raises(ValueError):
            parse_catalog_page(mutated, CORP)


def test_viewer_function_parser_is_bounded():
    text = main(RECORDS[0][0]).replace("function viewDoc() { var url='/report/viewer.do'; }", 'function x(){' * 3000)
    started = time.monotonic()
    with pytest.raises(ValueError):
        parse_viewer_nodes(text, RECORDS[0][0], CORP)
    assert time.monotonic() - started < 0.25


@pytest.mark.parametrize('mutate', [
    lambda text: text.replace('node0["text"]', '/* node0["text"]').replace('</script>', '*/</script>'),
    lambda text: text.replace('</script>', "node0.offset = '999';</script>"),
    lambda text: text.replace('</script>', 'node0[field] = "999";</script>'),
    lambda text: text.replace('</script>', 'node0["offset"] += "999";</script>'),
    lambda text: text.replace('</script>', 'function viewDoc() {}</script>'),
])
def test_viewer_nodes_reject_commented_or_unsupported_writes(mutate):
    with pytest.raises(ValueError):
        parse_viewer_nodes(mutate(main(RECORDS[0][0])), RECORDS[0][0], CORP)


@pytest.mark.parametrize('payload', [
    'function viewDoc(' * 8000,
    'node0[' * 8000,
    'node0["text"] = "x" ' * 8000,
], ids=['signature', 'bracket', 'unterminated_assignment'])
def test_all_viewer_scans_have_bounded_runtime(payload):
    text = main(RECORDS[0][0]).replace('</script>', payload + '</script>')
    started = time.monotonic()
    with pytest.raises(ValueError):
        parse_viewer_nodes(text, RECORDS[0][0], CORP)
    assert time.monotonic() - started < 0.4


@pytest.mark.parametrize('operator', ['&&=', '||=', '??=', '%=', '**=', '&=', '|=', '^=', '<<=', '>>=', '>>>='])
def test_all_compound_node_writes_rejected(operator):
    text = main(RECORDS[0][0]).replace('</script>', f'node0["offset"] {operator} "999";</script>')
    with pytest.raises(ValueError):
        parse_viewer_nodes(text, RECORDS[0][0], CORP)


def test_template_literal_cannot_supply_nodes():
    text = main(RECORDS[0][0]).replace('node0["text"]', 'const stale = ' + chr(96) + 'node0["text"]').replace('</script>', chr(96) + ';</script>')
    with pytest.raises(ValueError):
        parse_viewer_nodes(text, RECORDS[0][0], CORP)


def test_three_digit_cover_day_cannot_be_truncated():
    with pytest.raises(ValueError):
        parse_cover_metadata(cover(RECORDS[0]).replace('06월 30일', '06월 300일'))
