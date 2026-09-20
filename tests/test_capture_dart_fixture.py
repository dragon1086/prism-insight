"""S1 capture/replay admission, with no provider or report-model calls."""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from test_dart_public_filings import BODY, RECORDS, catalog, cover
from test_dart_public_filings import main as main_page

from prism_core import dart_identity, dart_public_filings
from prism_core.dart_report_evidence import collect_latest

INPUTS = {'version': 1, 'ticker': '327260', 'name': '샘플',
          'decision_at': '2026-09-18T15:30:00+09:00', 'scope': 'consolidated'}
NOTES = ('<h2>3. 연결재무제표 주석</h2><h3>38. 약정사항</h3>'
         '<p>차입금 약정의 위반은 없습니다.</p><p>다만 후속 조건에 따라 상환 부담이 발생할 수 있습니다.</p>')


def test_actual_report_adapter_accepts_diagnostic_factory_without_policy_change(monkeypatch):
    sentinel = object()
    seen = []
    async def identity(*args, **kwargs):
        seen.append(('identity', kwargs.get('client_factory')))
        return {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE', 'corp_code': '12345678',
                'ticker_verified_from_company_profile': True}
    async def filings(**kwargs):
        seen.append(('filings', kwargs.get('client_factory')))
        assert kwargs['include_section_bodies'] is True
        assert kwargs['max_calls'] == 28 and kwargs['timeout_seconds'] == 55
        return {'metrics': {'calls': 0, 'response_bytes': 0},
                'selection': {'primary_id': None, 'reasons': ['FIXTURE_NO_SELECTION']},
                'coverage': {}, 'observed_at': '2026-09-20T00:00:00+00:00',
                'limitations': [], 'errors': []}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', filings)
    progress = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(collect_latest('000001', 'Example', datetime(2026, 9, 18, tzinfo=timezone.utc),
                              'consolidated', progress, client_factory=sentinel))
    assert seen == [('identity', sentinel), ('filings', sentinel)]
    assert progress['sources'] == []


def provider(request):
    records = [RECORDS[0], RECORDS[2]]
    if request.url.path.endswith('selectPopup.ax'):
        body = '<table><tr><th>종목코드</th><td>327260</td></tr></table>'
    elif request.method == 'POST':
        body = catalog(records)
    else:
        rid = request.url.params.get('rcpNo')
        row = next(r for r in records if r[0] == rid)
        if request.url.path.endswith('main.do'):
            fields = {'text': '3. 연결재무제표 주석', 'rcpNo': rid, 'dcmNo': '12345678',
                      'eleId': '3', 'offset': '100', 'length': '1000', 'dtd': 'dart4.xsd'}
            script = ''.join(f'node3["{k}"] = {json.dumps(v, ensure_ascii=False)};\n' for k, v in fields.items())
            body = main_page(rid).replace('</script>', script + '</script>')
        elif request.url.params['eleId'] == '0':
            body = cover(row)
        elif request.url.params['eleId'] == '3':
            body = NOTES
        else:
            body = '<h2>2. 연결재무제표</h2>' + BODY
    return httpx.Response(200, text=body)


def test_actual_capture_replay_matches_report_summary_without_network(monkeypatch, tmp_path):
    from tools.capture_dart_fixture import capture, replay

    path = tmp_path / 'case'
    result = asyncio.run(capture(path, INPUTS, transport_factory=lambda: httpx.MockTransport(provider)))
    assert result['capture_complete']
    assert result['summary']['identity']['ticker_verified']
    assert result['summary']['selection']['primary_id'] == RECORDS[0][0]
    assert result['summary']['candidate_sources']
    assert NOTES not in json.dumps(result)
    assert '다만 후속' not in json.dumps(result, ensure_ascii=False)
    monkeypatch.setattr(httpx, 'AsyncHTTPTransport', lambda *a, **k: pytest.fail('no network in replay'))
    output = asyncio.run(replay(path))
    assert output['summary_equal']
    assert output['network_calls'] == 0


def test_semantic_rejection_still_preserves_complete_source(tmp_path):
    from tools.capture_dart_fixture import capture, replay

    wrong_scope = '<h2>5. 재무제표 주석</h2><p>별도 범위의 원문입니다.</p>'
    def altered(request):
        if request.url.params.get('eleId') == '3':
            return httpx.Response(200, text=wrong_scope)
        return provider(request)
    path = tmp_path / 'wrong-scope'
    result = asyncio.run(capture(path, INPUTS, transport_factory=lambda: httpx.MockTransport(altered)))
    assert result['capture_complete']
    assert any('SECTION_SCOPE_MISMATCH' in r['errors'] for r in result['summary']['body_delivery'].values())
    assert any(wrong_scope.encode() == file.read_bytes() for file in path.glob('response-*.bin'))
    assert asyncio.run(replay(path))['summary_equal']


def test_capture_requires_explicit_live_and_valid_inputs(tmp_path, capsys):
    from tools.capture_dart_fixture import main

    args = ['capture', '--fixture-dir', str(tmp_path / 'unused'), '--ticker', '327260', '--name', '샘플',
            '--decision-at', INPUTS['decision_at']]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'LIVE_ACK_REQUIRED'
    assert not (tmp_path / 'unused').exists()
    assert main([*args, '--live', '--decision-at', '2026-09-18']) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'INVALID_CAPTURE_INPUTS'
    assert not (tmp_path / 'unused').exists()


def test_code_fingerprint_includes_geometry_selection_and_summary_dependencies():
    from tools.capture_dart_fixture import _hashes

    required = {'prism_core/dart_viewer_tree.py', 'prism_core/filing_html_tables.py', 'prism_core/filing_catalog.py',
                'prism_core/filing_selection.py', 'prism_core/filing_structure.py',
                'prism_core/material_filing_selection.py', 'prism_core/filing_materiality.py',
                'prism_core/filing_table_projection.py', 'prism_core/report_research_prefetch.py',
                'tools/evaluate_general_filing_reports.py'}
    hashes = _hashes()
    assert required <= hashes.keys()
    assert all(len(value) == 64 for value in hashes.values())


@pytest.mark.parametrize('streamed', [False, True])
def test_adapter_caught_local_capture_limit_remains_explicit(tmp_path, streamed):
    from tests.test_dart_fixture_transport import Stream
    from tools.capture_dart_fixture import capture
    from tools.dart_fixture_transport import MAX_BODY, FixtureError, FixtureReplay

    body = b'x' * (MAX_BODY + 1)
    def oversized(request):
        if request.url.params.get('eleId') == '3':
            return httpx.Response(200, stream=Stream([body])) if streamed else httpx.Response(200, content=body)
        return provider(request)
    path = tmp_path / 'limit'
    result = asyncio.run(capture(path, INPUTS, transport_factory=lambda: httpx.MockTransport(oversized)))
    assert result['capture_complete'] is False
    assert result['capture_failure_codes'] == ['CAPTURE_BODY_LIMIT']
    assert 'ACQUISITION_INVALID_RESPONSE' in result['summary']['collector_errors']
    manifest = json.loads((path / 'manifest.json').read_text())
    assert manifest['summary']['capture_failure_codes'] == ['CAPTURE_BODY_LIMIT']
    assert (path / 'INCOMPLETE').exists()
    assert list(path.glob('response-*.bin'))  # Preserve earlier complete responses.
    with pytest.raises(FixtureError):
        asyncio.run(FixtureReplay.load(path))


def test_top_level_cancellation_never_finalizes_success(monkeypatch, tmp_path):
    import tools.capture_dart_fixture as tool
    from tools.dart_fixture_transport import FixtureError, FixtureReplay

    async def slow(*args):
        await asyncio.sleep(10)
    monkeypatch.setattr(tool, '_run', slow)
    path = tmp_path / 'cancelled'
    async def cancel():
        task = asyncio.create_task(tool.capture(path, INPUTS, transport_factory=lambda: httpx.MockTransport(provider)))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(FixtureError):
            await FixtureReplay.load(path)
    asyncio.run(cancel())


@pytest.mark.parametrize('repeat', [False, True])
def test_cancellation_during_finalization_is_joined_and_never_complete(monkeypatch, tmp_path, repeat):
    import threading

    import tools.capture_dart_fixture as tool
    import tools.dart_fixture_transport as transport

    entered, release = threading.Event(), threading.Event()
    path = tmp_path / 'finalizing'
    original = transport.os.unlink
    def delayed_unlink(target, *args, **kwargs):
        if str(target) == str(path / transport.MARKER):
            entered.set()
            release.wait(2)
        return original(target, *args, **kwargs)
    async def quick(*args):
        return {'fixture_summary': True}
    monkeypatch.setattr(tool, '_run', quick)
    monkeypatch.setattr(transport.os, 'unlink', delayed_unlink)
    async def cancel():
        task = asyncio.create_task(tool.capture(path, INPUTS))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            if repeat:
                await asyncio.sleep(.01)
                task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (path / transport.MARKER).exists()
        with pytest.raises(transport.FixtureError):
            await transport.FixtureReplay.load(path)
    asyncio.run(cancel())
