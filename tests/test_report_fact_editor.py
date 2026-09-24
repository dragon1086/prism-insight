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


@pytest.mark.parametrize('source,output,allowed', [
    ('+951,112', '951,112', True), ('951,112', '+951,112', True),
    ('-951,112', '951,112', False), ('951,112', '-951,112', False),
    ('951,112', '951112', False), ('9.10', '9.1', False),
])
def test_summary_positive_sign_equivalence_preserves_value_guards(source, output, allowed):
    reports, payload = fixture()
    reports['shared_reference'] += ' 순매수 ' + source + '주'
    payload['summary'] += ' 순매수 ' + output + '주입니다.'
    if allowed:
        assert output in editor._validate_and_apply(reports, payload)[1]
    else:
        with pytest.raises(editor.ReportFactEditorError, match='unsupported'):
            editor._validate_and_apply(reports, payload)


@pytest.mark.parametrize('source,allowed', [('2026-08-27', True), ('-27', False),
                                          ('2026-13-27', False), ('2026-02-31', False)])
def test_valid_iso_date_separator_is_not_a_negative_financial_amount(source, allowed):
    reports, payload = fixture()
    reports['shared_reference'] += source
    payload['summary'] += ' 27일 관측입니다.'
    if allowed:
        assert '27일' in editor._validate_and_apply(reports, payload)[1]
    else:
        with pytest.raises(editor.ReportFactEditorError, match='unsupported'):
            editor._validate_and_apply(reports, payload)


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


def test_exact_error_paragraph_in_strategy_cannot_survive_a_body_only_edit():
    reports, payload = fixture()
    reports['investment_strategy'] += '\n\n' + payload['edits'][0]['original']
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError, match='immutable strategy'):
        editor._validate_and_apply(reports, payload)
    assert reports == before


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


def test_session_correction_rejects_position_amount_instruction_bypass():
    reports, payload = session_fixture()
    original = '9월 24일 개인 매수 금액을 100만원으로 제한하세요.'
    replacement = '9월 24일은 휴장이므로 다음 거래일 개인 매수 금액을 100만원 이상으로 늘리세요.'
    reports['news_analysis'] = original
    payload['edits'][0].update(original=original, replacement=replacement)
    with pytest.raises(editor.ReportFactEditorError):
        editor._validate_and_apply(reports, payload, {'calendar': 'XKRX', 'is_session': False})


@pytest.mark.parametrize('side', ['original', 'replacement', 'paragraph'])
@pytest.mark.parametrize('instruction', [' 금액을 늘리세요.', ' 금액을 제한하세요.',
                                        ' 규모를 축소해야 합니다.', ' 규모를 확대하세요.'])
def test_flow_observation_never_unprotects_adjacent_action(side, instruction):
    reports, payload = session_fixture()
    if side == 'paragraph':
        reports['news_analysis'] += instruction
    else:
        payload['edits'][0][side] += instruction
        if side == 'original':
            reports['news_analysis'] += instruction
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError):
        editor._validate_and_apply(reports, payload, {'calendar': 'XKRX', 'is_session': False})
    assert reports == before


@pytest.mark.parametrize('suffix', ['금액', '규모', '추이', '동향', '여부'])
def test_only_exact_continuation_observation_is_masked(suffix):
    assert editor._has_decision('개인 매수 ' + suffix, session_timing=True)
    assert not editor._has_decision('기관 매도 지속 여부를 확인해야 합니다.', session_timing=True)


def test_structured_conflicts_are_atomic_immutable_and_targets_follow_base_order():
    reports, payload = fixture()
    reports.update({section: '기본 분석' for section in editor.REPAIRABLE_SECTIONS if section not in reports})
    payload['unresolved'] = [
        {'section': 'market_index_analysis', 'issue': '120일을 연간으로 설명했습니다.'},
        {'section': 'company_status', 'issue': '분기 비율의 기준이 다릅니다.'},
        {'section': 'company_status', 'issue': '비교 기간도 다릅니다.'},
        {'section': 'investor_trading_analysis', 'issue': '시작일이 다릅니다.'},
    ]
    for conflict in payload['unresolved']:
        conflict['evidence_section'] = 'shared_reference'
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    error = caught.value
    assert reports == before  # Valid ordinary edits must not be applied either.
    for (section, issue), item in zip(error.conflicts[:4], payload['unresolved']):
        assert section == item['section'] and issue.startswith(item['issue'])
    assert error.conflicts[4][0] == 'company_overview'
    assert error.targets == ('investor_trading_analysis', 'company_status', 'company_overview', 'market_index_analysis')
    assert error.evidence_sections == ('shared_reference',) * 5
    payload['unresolved'][0]['issue'] = 'mutated'
    assert error.conflicts[0][1] != 'mutated'
    with pytest.raises(AttributeError):
        error.conflicts = ()
    with pytest.raises(TypeError):
        error.conflicts[0] = ('company_status', 'changed')


@pytest.mark.parametrize('section', editor.REPAIRABLE_SECTIONS)
def test_each_exact_base_section_can_be_reported_for_repair(section):
    reports, payload = fixture()
    payload['edits'] = []
    reports.setdefault(section, '기본 분석')
    payload['unresolved'] = [{'section': section, 'issue': '공식 근거와 기간이 다릅니다.',
                              'evidence_section': 'shared_reference'}]
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    assert caught.value.targets == (section,)


@pytest.mark.parametrize('unresolved', [
    ['company_status: legacy string'], None, {},
    [{'section': 'unknown', 'issue': 'conflict'}],
    [{'section': 'dart_deep_analysis', 'issue': 'conflict'}],
    [{'section': 'investment_strategy', 'issue': 'conflict'}],
    [{'section': 'shared_reference', 'issue': 'conflict'}],
    [{'section': 'peer_comparison', 'issue': 'conflict'}],
    [{'section': 'price_volume_analysis', 'issue': 'conflict'}],
    [{'section': 'news_analysis', 'issue': 'absent from report'}],
    [{'section': 'company_status', 'issue': ''}],
    [{'section': 'company_status', 'issue': '   '}],
    [{'section': 'company_status', 'issue': 123}],
    [{'section': ['company_status'], 'issue': 'conflict'}],
    [{'section': 'company_status', 'issue': 'x' * 2001}],
    [{'section': 'company_status', 'issue': 'conflict', 'extra': True}],
    [{'section': 'company_status'}],
    [{'section': 'company_status', 'issue': 'conflict'}] * 9,
])
def test_invalid_or_nonrepairable_conflicts_remain_generic_failure(unresolved):
    reports, payload = fixture()
    if isinstance(unresolved, list):
        unresolved = [dict(item, evidence_section='shared_reference') if isinstance(item, dict) else item
                      for item in unresolved]
    payload['unresolved'] = unresolved
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError
    assert reports == before


@pytest.mark.parametrize('invalid', ['schema', 'summary', 'summary_literal', 'number', 'decision', 'edit_schema'])
def test_ordinary_guard_errors_cannot_become_retryable_conflicts(invalid):
    reports, payload = fixture()
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'conflict',
                              'evidence_section': 'shared_reference'}]
    if invalid == 'schema':
        payload['extra'] = True
    elif invalid == 'summary':
        payload['summary'] = 'too short'
    elif invalid == 'summary_literal':
        payload['summary'] += '99999배'
    elif invalid == 'number':
        payload['edits'][0]['replacement'] = payload['edits'][0]['replacement'].replace('123', '124')
    elif invalid == 'decision':
        payload['edits'][0]['replacement'] += ' 매수해야 합니다.'
    else:
        payload['edits'][0]['extra'] = True
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError


def test_structured_conflict_is_still_one_backend_call_without_retry(monkeypatch):
    reports, payload = fixture()
    payload['unresolved'] = [{'section': 'company_status', 'issue': '공식 자료와 충돌합니다.',
                              'evidence_section': 'dart_deep_analysis'}]
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    with pytest.raises(editor.ReportFactConflictError):
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert len(calls) == 1
    assert '"issue"' in calls[0][0].instructions
    assert not calls[0][0].mcp_servers


def test_conflict_limits_are_inclusive_and_constructor_rejects_invalid_targets():
    reports, payload = fixture()
    payload['edits'] = []
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'x' * 2000,
                              'evidence_section': 'shared_reference'}] * 8
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    assert len(caught.value.conflicts) == 8
    with pytest.raises(editor.ReportFactEditorError) as invalid:
        editor.ReportFactConflictError((('investment_strategy', 'conflict'),))
    assert type(invalid.value) is editor.ReportFactEditorError


@pytest.mark.parametrize('source,value', [
    ('unknown', 'text'), ('company_status', 'text'), ('price_volume_analysis', 'text'),
    ('shared_reference', ''), ('shared_reference', '  '),
    ('dart_deep_analysis', None), ('peer_comparison', []),
])
def test_conflict_requires_nonempty_allowed_evidence_pointer(source, value):
    reports, payload = fixture()
    if source in editor.CONFLICT_EVIDENCE_SECTIONS:
        reports[source] = value
    # Keep the existing reports text contract; absence is tested by removal.
    if not isinstance(value, str):
        del reports[source]
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'conflict', 'evidence_section': source}]
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError


def test_conflict_missing_evidence_pointer_is_not_retryable():
    reports, payload = fixture()
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'conflict'}]
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError


def test_base_and_dependent_strategy_conflicts_require_fresh_synthesis_not_strategy_patch():
    reports, payload = fixture()
    payload['unresolved'] = [
        {'section': 'investment_strategy', 'issue': '기본 섹션의 비교 기준 혼동이 반복됩니다.',
         'evidence_section': 'peer_comparison'},
        {'section': 'company_overview', 'issue': '배수의 기간 기준이 다릅니다.',
         'evidence_section': 'shared_reference'},
    ]
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    assert caught.value.targets == ('company_status', 'company_overview')
    assert caught.value.conflicts[0][0] == 'investment_strategy'
    assert caught.value.evidence_sections == ('peer_comparison', 'shared_reference', 'shared_reference')
    assert reports == before
    assert 'investment_strategy' not in editor.REPAIRABLE_SECTIONS
    assert 'investment_strategy' not in editor.EDITABLE_SECTIONS


@pytest.mark.parametrize('other', ['unknown', 'dart_deep_analysis', 'price_volume_analysis'])
def test_base_conflict_never_unprotects_other_readonly_targets(other):
    reports, payload = fixture()
    reports.setdefault(other, '보호된 내용')
    payload['unresolved'] = [
        {'section': 'company_status', 'issue': '충돌', 'evidence_section': 'shared_reference'},
        {'section': other, 'issue': '충돌', 'evidence_section': 'shared_reference'},
    ]
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError


def test_dependent_strategy_conflict_keeps_evidence_and_edit_guards():
    reports, payload = fixture()
    payload['unresolved'] = [
        {'section': 'company_status', 'issue': '충돌', 'evidence_section': 'shared_reference'},
        {'section': 'investment_strategy', 'issue': '충돌', 'evidence_section': 'missing'},
    ]
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError
    payload['unresolved'][1]['evidence_section'] = 'shared_reference'
    payload['edits'].append({'section': 'investment_strategy',
                             'original': reports['investment_strategy'],
                             'replacement': '전략을 바꿉니다.', 'reason': 'comparison_basis'})
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError


def test_safe_edits_with_other_conflict_carry_all_unapplied_sections_as_proposals():
    reports, payload = fixture()
    reports['news_analysis'] = '기존 뉴스 설명'
    payload['unresolved'] = [{'section': 'news_analysis', 'issue': '잔액 기준 충돌',
                              'evidence_section': 'dart_deep_analysis'}]
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    error = caught.value
    assert error.targets == ('company_status', 'company_overview', 'news_analysis')
    assert reports == before
    proposals = dict(error.conflicts)
    assert proposals['company_status'].startswith('Untrusted editorial proposals')
    assert payload['edits'][0]['original'] in proposals['company_status']
    assert payload['edits'][0]['replacement'] in proposals['company_status']
    assert payload['edits'][1]['replacement'] in proposals['company_status']
    assert error.evidence_sections == ('dart_deep_analysis', 'shared_reference', 'shared_reference')


def test_oversized_unapplied_edit_proposal_fails_without_clipping_or_mutation():
    reports, payload = fixture()
    reports['company_status'] = '사실 설명입니다. ' * 150
    payload['edits'] = [{'section': 'company_status', 'original': reports['company_status'],
                         'replacement': '범위를 구분합니다. ' * 150, 'reason': 'availability_scope'}]
    payload['unresolved'] = [{'section': 'company_overview', 'issue': '충돌',
                              'evidence_section': 'dart_deep_analysis'}]
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError, match='no clipping') as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError
    assert reports == before


def test_factual_mix_word_still_protects_entire_paragraph_and_prompt_explains_it(monkeypatch):
    reports, payload = fixture()
    reports['company_status'] += ' 고사양 제품 매출 비중이 큽니다.'
    payload['edits'] = [payload['edits'][1]]
    with pytest.raises(editor.ReportFactEditorError, match='decision paragraphs'):
        editor._validate_and_apply(reports, payload)
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    with pytest.raises(editor.ReportFactEditorError):
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    instruction = calls[0][0].instructions
    assert '빈 줄로 구분된 문단 전체' in instruction
    assert '매출 비중처럼 사실 설명인 비중' in instruction


@pytest.mark.parametrize('case', ['missing_source', 'total_cap'])
def test_unapplied_proposals_do_not_bypass_source_or_total_conflict_limit(case):
    reports, payload = fixture()
    if case == 'missing_source':
        reports['shared_reference'] = ''
        reports['dart_deep_analysis'] = ''
        payload['unresolved'] = [{'section': 'company_status', 'issue': '충돌',
                                  'evidence_section': 'peer_comparison'}]
    else:
        payload['unresolved'] = [{'section': 'company_status', 'issue': '충돌',
                                  'evidence_section': 'shared_reference'}] * 8
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError
    assert reports == before


def test_existing_conflict_keeps_its_issue_and_same_target_unapplied_proposals():
    reports, payload = fixture()
    payload['edits'] = payload['edits'][:2]
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'Quarterly ratio basis conflict',
                              'evidence_section': 'dart_deep_analysis'}]
    before = copy.deepcopy(reports)
    with pytest.raises(editor.ReportFactConflictError) as caught:
        editor._validate_and_apply(reports, payload)
    error = caught.value
    assert error.targets == ('company_status',)
    assert len(error.conflicts) == 1
    assert error.conflicts[0][1].startswith('Quarterly ratio basis conflict\n\nUntrusted editorial proposals')
    assert payload['edits'][1]['replacement'] in error.conflicts[0][1]
    assert error.evidence_sections == ('dart_deep_analysis',)
    assert reports == before


def test_same_target_merged_issue_limit_includes_original_conflict():
    reports, payload = fixture()
    payload['edits'] = payload['edits'][:1]
    payload['unresolved'] = [{'section': 'company_status', 'issue': 'x' * 1800,
                              'evidence_section': 'dart_deep_analysis'}]
    with pytest.raises(editor.ReportFactEditorError, match='no clipping') as caught:
        editor._validate_and_apply(reports, payload)
    assert type(caught.value) is editor.ReportFactEditorError
