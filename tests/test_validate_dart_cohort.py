import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

from tools.validate_dart_cohort import main, run_cohort

MANIFEST = {"version": 1, "decision_at": "2026-09-18T15:30:00+09:00",
            "start_date": "2025-01-01", "scope": "consolidated",
            "groups": {"development": [
                {"name": "회사A", "ticker": "000001", "sector": "test"},
                {"name": "회사B", "ticker": "000002", "sector": "test"}]}}


def factory(body):
    def handler(request):
        if request.url.path.endswith('selectPopup.ax'):
            return httpx.Response(200, text='<table><tr><th>종목코드</th><td>000001</td></tr></table>')
        query = parse_qs(request.content.decode()).get('textCrpNm', [''])[0]
        if query.isdigit():
            return httpx.Response(200, text='<table><tbody id="tbody"></tbody></table>')
        return httpx.Response(200, text=body)
    return lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def markup(name, corp):
    return f'<table><tbody id="tbody"><tr><td><a onclick="openCorpInfoNew(\'{corp}\')">{name}</a></td></tr></tbody></table>'


def test_cases_retained_with_exact_identity_and_failed_match():
    seen = []

    async def collector(**kwargs):
        seen.append(kwargs)
        return {"status": "PARTIAL", "selection": {"primary_id": None},
                "filings": [{"preview": "not saved"}], "metrics": {"calls": 1}}

    result = asyncio.run(run_cohort(MANIFEST, "development", collector=collector,
                                   client_factory=factory(markup("회사A", "12345678"))))
    assert len(result["cases"]) == 2 and len(seen) == 1
    assert result["cases"][0]["identity"]["corp_code"] == "12345678"
    assert result["cases"][0]["receipt"]["status"] == "PARTIAL"
    assert result["cases"][1]["status"] == "IDENTITY_UNRESOLVED"
    assert "not saved" not in json.dumps(result)


def test_duplicate_identity_and_name_substring_not_accepted():
    async def forbidden(**kwargs):
        pytest.fail("ambiguous identity must not collect")

    body = markup("회사A", "12345678") + markup("회사A", "87654321") + markup("회사B지주", "12345678")
    result = asyncio.run(run_cohort(MANIFEST, "development", collector=forbidden, client_factory=factory(body)))
    assert all(row["status"] == "IDENTITY_UNRESOLVED" for row in result["cases"])


def test_exception_keeps_case_and_hides_provider_text():
    async def fail(**kwargs):
        raise RuntimeError("private source error")

    result = asyncio.run(run_cohort(MANIFEST, "development", collector=fail,
                                   client_factory=factory(markup("회사A", "12345678"))))
    assert len(result["cases"]) == 2 and result["cases"][0]["status"] == "CASE_FAILED"
    assert "private" not in json.dumps(result)


def test_cli_off_does_not_read_manifest(tmp_path, capsys):
    assert main(["--manifest", str(tmp_path / "missing"), "--group", "development",
                 "--out", str(tmp_path / "out")]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "LIVE_ACK_REQUIRED"


def test_cli_existing_output_no_read_or_network(tmp_path, capsys):
    path = tmp_path / "existing"
    path.write_text("keep")
    assert main(["--live", "--group", "development", "--out", str(path)]) == 2
    assert path.read_text() == "keep"
    assert json.loads(capsys.readouterr().out)["reason"] == "OUTPUT_EXISTS"


def test_invalid_manifest_before_http():
    def forbidden(**kwargs):
        pytest.fail("invalid manifest before network")

    with pytest.raises(ValueError):
        asyncio.run(run_cohort({**MANIFEST, "decision_at": "2026-09-18"}, "development", client_factory=forbidden))


def test_ticker_fallback_verified_by_official_profile_not_hardcoded_alias():
    called = []

    def handler(request):
        called.append(request.url.path)
        if request.url.path.endswith('selectPopup.ax'):
            return httpx.Response(200, text='<table><tr><th>종목코드</th><td>000001</td></tr></table>')
        query = parse_qs(request.content.decode())['textCrpNm'][0]
        return httpx.Response(200, text=markup('다른 공식 법인명', '12345678') if query == '000001'
                              else '<table><tbody id="tbody"></tbody></table>')

    async def collector(**kwargs):
        assert kwargs['corp_code'] == '12345678'
        return {'status': 'COMPLETE_WITHIN_QUERY', 'selection': {'primary_id': '1'}}

    manifest = {**MANIFEST, 'groups': {'development': [MANIFEST['groups']['development'][0]]}}
    result = asyncio.run(run_cohort(manifest, 'development', collector=collector,
                                   client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)))
    row = result['cases'][0]
    assert row['identity']['ticker_verified_from_company_profile']
    assert row['identity']['resolution'] == 'OFFICIAL_TICKER_QUERY'
    assert row['status'] == 'COMPLETE_WITHIN_QUERY' and len(called) == 3


def test_unique_name_but_wrong_ticker_is_not_accepted():
    async def forbidden(**kwargs):
        pytest.fail('wrong stock mapping')

    result = asyncio.run(run_cohort(MANIFEST, 'development', collector=forbidden,
                                   client_factory=factory(markup('회사B', '12345678'))))
    assert all(row['status'] == 'IDENTITY_UNRESOLVED' for row in result['cases'])
    assert result['cases'][1]['identity']['reason'] == 'PROFILE_TICKER_MISMATCH'


def test_transport_outage_is_not_company_failure_or_empty_evidence():
    def handler(request):
        raise httpx.RemoteProtocolError('private diagnostic')

    result = asyncio.run(run_cohort(MANIFEST, 'development', client_factory=lambda **kw:
                                   httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)))
    assert result['summary']['case_count'] == 2
    assert all(row['reason'] == 'IDENTITY_TRANSPORT_FAILURE' for row in result['cases'])
    assert all('receipt' not in row for row in result['cases'])
    assert 'private' not in json.dumps(result)
