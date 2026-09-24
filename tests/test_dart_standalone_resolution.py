"""No standalone substitution on access failure; official absence is required."""
import asyncio
import pytest

from prism_core import kr_official_report_inputs as adapter
from prism_core.dart_public_filings import _consolidated_not_applicable


def test_actual_standalone_only_disclosure_is_recognized():
    body='<html><body><p>2. 연결재무제표</p><p>당사는 보고서 작성기준일 현재 해당사항이 없습니다.</p></body></html>'
    assert _consolidated_not_applicable(body)


def test_real_collector_emits_explicit_absence_provenance():
    from test_dart_section_collection import run_sections
    body='<html><body><p>2. 연결재무제표</p><p>당사는 보고서 작성기준일 현재 해당사항이 없습니다.</p></body></html>'
    result, _ = run_sections(financial=body)
    row=result['filings'][0]
    proof=row['scope_absence_evidence']
    assert proof['reason']=='OFFICIAL_NOT_APPLICABLE'
    assert proof['receipt_id']==row['receipt_id']
    assert proof['url'].startswith('https://dart.fss.or.kr/report/viewer.do?')
    assert len(proof['sha256'])==64
    assert row['body_status']=='unavailable'
    assert 'CONSOLIDATED_NOT_APPLICABLE' in row['errors']


@pytest.mark.parametrize('body',[
    '<p>접속 오류</p>', '<p>2. 연결재무제표</p>',
    '<p>2. 연결재무제표</p><p>소송은 해당사항이 없습니다.</p><p>자산 100 부채 30</p>',
    '<p>4. 재무제표</p><p>해당사항 없음</p>',
    '<p>2. 연결재무제표 해당사항이 없습니다.</p><table><tr><td>100</td></tr></table>',
])
def test_missing_ambiguous_or_unrelated_text_is_not_absence(body):
    assert not _consolidated_not_applicable(body)


@pytest.mark.parametrize('proof,match,expected_calls,admitted',[
    (True,True,2,True), (False,True,1,False), (True,False,2,False),
])
def test_fallback_requires_official_absence_and_same_issuer_receipt(monkeypatch,proof,match,expected_calls,admitted):
    calls=[]
    async def collect(ticker,company,date,scope,progress,*,source_sink):
        calls.append(scope)
        progress['dart_calls']=4
        progress['dart_response_bytes']=100
        if scope=='consolidated':
            progress['filing_selection']={
                'identity':{'corp_code':'12345678','ticker_verified_from_company_profile':True},
                'selection':{'primary_id':None,'best_known_candidate_id':'20260814001644'},
                'scope_absence_evidence':({'20260814001644':{
                    'reason':'OFFICIAL_NOT_APPLICABLE','scope':'consolidated',
                    'receipt_id':'20260814001644','url':'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260814001644',
                    'sha256':'a'*64}} if proof else {})}
        else:
            progress['filing_selection']={
                'identity':{'corp_code':'12345678' if match else '87654321','ticker_verified_from_company_profile':True},
                'selection':{'primary_id':'20260814001644'}}
            progress['sources']=[{'filing':{'scope':'standalone'}}]
            source_sink.append({'scope':'standalone','marker':'verified-source'})
    monkeypatch.setattr(adapter,'collect_latest',collect)
    monkeypatch.setattr(adapter,'_render',lambda p,c:{'section_contexts':{},'public_receipt':'receipt' if p['sources'] else '', 'shared_reference':'','diagnostics':p})
    monkeypatch.setattr(adapter,'build_dart_chapter_inputs',lambda sources,**kw:{'ready':bool(sources),'contexts':{}})
    packet=asyncio.run(adapter.collect_kr_official_report_inputs('252990','검증회사','20260924'))
    assert len(calls)==expected_calls
    assert packet['dart_chapter_inputs']['ready'] is admitted
    if admitted:
        assert '별도재무제표' in packet['public_receipt']
        assert packet['diagnostics']['dart_calls']==8
    else:
        assert '별도재무제표' not in packet['public_receipt']
