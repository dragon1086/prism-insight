"""No network/model requests: atomic report edits and single-call boundary."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from cores import report_fact_editor as editor


def fixture():
    reports = {
        'company_status': '관계기업 투자이익 123백만원이 영업이익 개선을 이끌었습니다. https://example.com/source\n\n'
                          '예상 PER 12.3배는 업종 PER 15.0배보다 낮아 저평가입니다.',
        'company_overview': '경쟁사 재무 비교 자료는 전혀 없습니다.',
        'dart_deep_analysis': '영업이익 아래 투자이익 123백만원은 세전이익에 반영됩니다.',
        'peer_comparison': '예상 PER 12.3배. 업종 PER 15.0배의 기간은 불명확합니다.',
        'shared_reference': '코드 계산 고정',
        'investment_strategy': '매수는 기존 조건을 따릅니다.',
    }
    edits = [
        {'section': 'company_status', 'original': reports['company_status'].split('\n\n')[0],
         'replacement': '관계기업 투자이익 123백만원은 영업이익이 아닌 세전이익에 기여했습니다. https://example.com/source',
         'reason': 'profit_attribution'},
        {'section': 'company_status', 'original': reports['company_status'].split('\n\n')[1],
         'replacement': '예상 PER 12.3배와 업종 PER 15.0배는 기간 기준이 달라 저평가를 단정할 수 없습니다.',
         'reason': 'comparison_basis'},
        {'section': 'company_overview', 'original': reports['company_overview'],
         'replacement': '경쟁사 재무 비교 자료는 후속 장에 있지만 동일 기준의 사업 경쟁력은 별도 확인이 필요합니다.',
         'reason': 'availability_scope'},
    ]
    summary = ('투자이익은 영업이익이 아닌 세전이익에 기여했습니다. 회사 예상 배수와 업종 배수의 기준은 다릅니다. '
               '재무 비교 자료와 사업 경쟁력 입증은 구분해야 합니다. 원문에서 확인된 조건과 실제 이행 범위를 '
               '구분해 현금흐름과 재무건전성을 살펴볼 필요가 있습니다. 기존 전략의 기준을 유지하며 '
               '비교 대상과 기간을 통일한 뒤 판단해야 합니다. 제공된 자료가 뒷받침하지 않는 우위는 단정하지 않습니다.')
    return reports, {'summary': summary, 'edits': edits, 'unresolved': []}


def install_backend(monkeypatch, text):
    import report_model_config
    from cores import report_generation
    calls = []

    class Backend:
        async def run(self, spec, message):
            calls.append((spec, message))
            return SimpleNamespace(text=text, usage={'input_tokens': 20, 'output_tokens': 30})

    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: Backend())
    monkeypatch.setattr(report_model_config, 'DART_REPORT_MODEL', 'gpt-6-astra')
    monkeypatch.setattr(report_model_config, 'DART_REPORT_EFFORT', 'low')
    return calls


def test_three_generic_corrections_atomic_immutable_sources_and_one_call(monkeypatch):
    reports, payload = fixture()
    before = copy.deepcopy(reports)
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    patched, summary, receipt = asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert reports == before
    assert patched['company_status'] != reports['company_status']
    assert patched['company_overview'] != reports['company_overview']
    for key in reports.keys() - editor.EDITABLE_SECTIONS:
        assert patched[key] == reports[key]
    assert summary == payload['summary']
    assert receipt['edits_count'] == 3
    assert receipt['model'] == 'gpt-6-astra'
    assert len(calls) == 1
    spec, message = calls[0]
    assert spec.params.max_iterations == 1
    assert spec.params.reasoning_effort == 'low'
    assert not spec.mcp_servers
    assert json.loads(message)['sections'] == reports


@pytest.mark.parametrize('case', ['number', 'url', 'duplicate', 'unknown', 'immutable', 'strategy',
                                  'heading', 'table', 'fence', 'evidence', 'unresolved', 'overlap',
                                  'summary_number', 'summary_internal', 'schema', 'new_heading'])
def test_reject_entire_transaction_without_mutation(case):
    reports, payload = fixture()
    item = payload['edits'][0]
    if case == 'number':
        item['replacement'] = item['replacement'].replace('123', '124')
    elif case == 'url':
        item['replacement'] = item['replacement'].replace('example.com', 'evil.com')
    elif case == 'duplicate':
        reports['company_status'] += '\n\n' + item['original']
    elif case == 'unknown':
        item['section'] = 'unknown'
    elif case == 'immutable':
        item['section'] = 'dart_deep_analysis'
    elif case in {'strategy', 'heading', 'table', 'fence', 'evidence'}:
        prefix = {'strategy': '매수 조건: ', 'heading': '## ', 'table': '| ',
                  'fence': '```\n\n', 'evidence': 'CE-record: '}[case]
        reports['company_status'] = prefix + reports['company_status']
    elif case == 'unresolved':
        payload['unresolved'] = ['protected section conflict']
    elif case == 'overlap':
        payload['edits'].append(dict(item))
    elif case == 'summary_number':
        payload['summary'] += '9999배입니다.'
    elif case == 'summary_internal':
        payload['summary'] += ' BAR_FINALITY_UNKNOWN'
    elif case == 'schema':
        item['extra'] = True
    elif case == 'new_heading':
        item['replacement'] = '## ' + item['replacement']
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError):
        editor._validate_and_apply(reports, payload)
    assert reports == before


@pytest.mark.parametrize('response', ['bad JSON', '{} trailing', '{"summary":1,"summary":2}', '[]'])
def test_malformed_response_no_retry(monkeypatch, response):
    reports, _ = fixture()
    before = copy.deepcopy(reports)
    calls = install_backend(monkeypatch, response)
    with pytest.raises(editor.ReportFactEditorError):
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert len(calls) == 1
    assert reports == before


def test_whole_json_fence_and_no_edits_supported():
    reports, payload = fixture()
    payload['edits'] = []
    decoded = editor._decode('```json\n' + json.dumps(payload) + '\n```')
    patched, _ = editor._validate_and_apply(reports, decoded)
    assert patched == reports and patched is not reports


def test_input_cap_fails_before_call(monkeypatch):
    reports, payload = fixture()
    reports['dart_deep_analysis'] = '한' * 140000
    calls = install_backend(monkeypatch, json.dumps(payload))
    with pytest.raises(editor.ReportFactEditorError, match='capacity'):
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert not calls


def test_newly_created_target_does_not_change_original_offset_application():
    reports, payload = fixture()
    reports['company_status'] = '첫 문장입니다.\n\n둘째 문장입니다.'
    payload['edits'] = [
        {'section': 'company_status', 'original': '첫 문장입니다.',
         'replacement': '둘째 문장입니다.', 'reason': 'availability_scope'},
        {'section': 'company_status', 'original': '둘째 문장입니다.',
         'replacement': '마지막 문장입니다.', 'reason': 'availability_scope'},
    ]
    patched, _ = editor._validate_and_apply(reports, payload)
    assert patched['company_status'] == '둘째 문장입니다.\n\n마지막 문장입니다.'


def test_tilde_fence_with_blank_lines_remains_protected():
    reports, payload = fixture()
    reports['company_status'] = '~~~text\n\n' + reports['company_status'] + '\n\n~~~'
    with pytest.raises(editor.ReportFactEditorError, match='fenced'):
        editor._validate_and_apply(reports, payload)


@pytest.mark.parametrize('boundary', ['\n \n', '\n\t\n', '\r\n\r\n', '\r\n \t\r\n'])
@pytest.mark.parametrize('side', ['original', 'replacement'])
def test_whitespace_and_crlf_paragraph_boundaries_are_rejected(boundary, side):
    reports, payload = fixture()
    item = payload['edits'][0]
    previous = item[side]
    item[side] = previous.replace(' ', boundary, 1)
    if side == 'original':
        reports[item['section']] = reports[item['section']].replace(previous, item[side])
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError, match='structure'):
        editor._validate_and_apply(reports, payload)
    assert reports == before


def session_fixture():
    reports, payload = fixture()
    reports['news_analysis'] = ('단기적으로는 9월 24일 실제 거래량과 확정 종가, 기관 매도 지속 여부, '
                                '경쟁사 대비 상대수익률을 확인해야 합니다.')
    payload['edits'] = [{'section': 'news_analysis', 'original': reports['news_analysis'],
                         'replacement': ('9월 24일은 휴장이므로 다음 거래일의 실제 거래량과 확정 종가, '
                                         '기관 매도 지속 여부, 경쟁사 대비 상대수익률을 확인해야 합니다.'),
                         'reason': 'session_timing'}]
    return reports, payload


def test_verified_closed_date_allows_observation_only_correction():
    reports, payload = session_fixture()
    calendar = {'reference_date': '2026-09-24', 'calendar': 'XKRX', 'is_session': False}
    patched, _ = editor._validate_and_apply(reports, payload, calendar)
    assert '휴장' in patched['news_analysis']
    assert '기관 매도 지속 여부' in patched['news_analysis']
    assert patched['investment_strategy'] == reports['investment_strategy']


@pytest.mark.parametrize('calendar', [None, {}, {'calendar': 'XKRX', 'is_session': None},
                                     {'calendar': 'XKRX', 'is_session': True},
                                     {'calendar': 'XNYS', 'is_session': False}])
def test_session_correction_requires_verified_closed_calendar(calendar):
    reports, payload = session_fixture()
    with pytest.raises(editor.ReportFactEditorError, match='verified closed'):
        editor._validate_and_apply(reports, payload, calendar)


@pytest.mark.parametrize('reason', ['profit_attribution', 'comparison_basis', 'availability_scope'])
def test_news_never_allows_other_edit_reasons(reason):
    reports, payload = session_fixture()
    payload['edits'][0]['reason'] = reason
    with pytest.raises(editor.ReportFactEditorError, match='immutable'):
        editor._validate_and_apply(reports, payload, {'calendar': 'XKRX', 'is_session': False})


@pytest.mark.parametrize('side', ['original', 'replacement'])
@pytest.mark.parametrize('decision', [' 개인은 매도해야 합니다.', ' 손절을 실행합니다.', ' 기관 매도하라.'])
def test_session_correction_never_masks_actual_decisions(side, decision):
    reports, payload = session_fixture()
    payload['edits'][0][side] += decision
    if side == 'original':
        reports['news_analysis'] += decision
    with pytest.raises(editor.ReportFactEditorError):
        editor._validate_and_apply(reports, payload, {'calendar': 'XKRX', 'is_session': False})


def test_existing_local_calendar_recognizes_closed_and_open_dates():
    assert editor._calendar_context('20260924') == {
        'reference_date': '2026-09-24', 'calendar': 'XKRX', 'is_session': False}
    assert editor._calendar_context('2026-09-23')['is_session'] is True


@pytest.mark.parametrize('date', ['invalid', '20260230', '2026-9-24', None])
def test_invalid_calendar_date_remains_unavailable(date):
    assert editor._calendar_context(date)['is_session'] is None


def test_calendar_failure_is_unknown_not_closed(monkeypatch):
    import pandas_market_calendars as mcal

    def unavailable(*args, **kwargs):
        raise RuntimeError('local calendar unavailable')

    monkeypatch.setattr(mcal, 'get_calendar', unavailable)
    assert editor._calendar_context('20260924')['is_session'] is None
