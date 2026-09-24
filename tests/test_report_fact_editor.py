"""No network/model requests: atomic report edits and single-call boundary."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from cores import report_fact_editor as editor


@pytest.fixture(autouse=True)
def no_review_artifacts(monkeypatch):
    import cores.report_review_diagnostics as diagnostics
    monkeypatch.setattr(diagnostics, 'record_report_review_failure', lambda **kwargs: None)


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
    ('951,112', '951112', True), ('9.10', '9.1', True),
    ('-78308', '-78,308', True), ('-184922', '-184,922', True),
    ('-133186', '-133,186', True), ('-396951', '-396,951', True),
    ('-14561', '-14,561', True), ('9.104', '9.10', False),
    ('-78308', '78,308', False), ('-78308', '-78,309', False),
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


@pytest.mark.parametrize('literal', ['-27', '-08'])
def test_iso_date_does_not_authorize_negative_financial_values(literal):
    reports, payload = fixture()
    reports['shared_reference'] += '기준일 2026-08-27'
    payload['summary'] += ' 영업이익률 ' + literal + '%입니다.'
    with pytest.raises(editor.ReportFactEditorError, match='unsupported'):
        editor._validate_and_apply(reports, payload)


@pytest.mark.parametrize('rendered', ['2026.08.26', '2026.8.26', '2026-8-26',
                                    '2026/08/26', '2026년 8월 26일'])
def test_full_valid_date_presentation_does_not_invent_financial_decimals(rendered):
    assert not (editor._numeric_literals(rendered) - editor._numeric_literals('2026-08-26'))


@pytest.mark.parametrize('text', ['2026.08', '2026.13.26', '2026.02.30', '2026.08/26',
                                '-2026.08.26', '12026.08.26'])
def test_partial_invalid_or_signed_date_is_not_normalized(text):
    assert editor._numeric_literals(text) - editor._numeric_literals('2026-08-26')


def test_dotted_date_does_not_authorize_decimal_amount_or_wrong_day():
    assert editor._numeric_literals('2026.08') - editor._numeric_literals('2026.08.26')
    assert editor._numeric_literals('27일') - editor._numeric_literals('2026.08.26')


@pytest.mark.parametrize('output,allowed', [
    ('순매수 수량 -1,219,730주', True), ('1,219,730주 순매도', True),
    ('1,219,730주 순매수', False), ('1,219,730', False), ('-1,219,731주', False),
])
def test_explicit_korean_share_quantity_preserves_net_direction(output, allowed):
    source = '121만 9,730주 순매도'
    assert bool(editor._unsupported_numeric_literals(output, source)) is not allowed


def test_explicit_net_share_list_requires_each_additional_sign_and_unit():
    source = '-750389, 121만 9,730주 순매도'
    assert not editor._unsupported_numeric_literals('순매수 수량 -750,389주 및 -1,219,730주', source)
    assert editor._unsupported_numeric_literals('순매수 수량 -750,389주 및 -1,219,730원', source)
    assert editor._unsupported_numeric_literals('순매수 수량 -750,389주 및 +1,219,730주', source)


def test_known_plain_share_direction_cannot_be_bypassed_by_its_unsigned_token():
    assert editor._unsupported_numeric_literals('순매수 수량 +100주', '100주 순매도')
    assert editor._unsupported_numeric_literals('100주 순매도', '순매수 수량 +100주')


@pytest.mark.parametrize('source', ['121만 9,730원 순매도', '121만 19,730주 순매도',
                                    '121만 9,730주', '1만주 및 2만주 순매도'])
def test_share_direction_is_not_inferred_from_currency_missing_or_coordinated_text(source):
    target = '-10,000' if source.startswith('1만주') else '-1,219,730'
    assert editor._unsupported_numeric_literals(target, source)


@pytest.mark.parametrize('output', ['-1,219,730원', '-1,219,730%', '121원', '9,730원',
                                   '순매도 -1,219,730주'])
def test_composite_share_alias_cannot_authorize_money_percent_fragments_or_ambiguous_sign(output):
    assert editor._unsupported_numeric_literals(output, '121만 9,730주 순매도')


@pytest.mark.parametrize('source', ['약 121만 9,730주 순매도', '121만 9,730주 순매도 이상',
                                   '(약) 121만 9,730주 순매도', '[약] 121만 9,730주 순매도',
                                   '약 **121만 9,730주 순매도**', '121만 9,730주 순매도** 이상',
                                   '120만~121만 9,730주 순매도', '121.5만 9,730주 순매도'])
def test_approximate_or_range_share_counts_are_not_exact_aliases(source):
    assert editor._unsupported_numeric_literals('순매수 수량 -1,219,730주', source)


def install_backend(monkeypatch, text):
    import report_model_config
    from cores import report_generation
    calls = []

    class Backend:
        async def run(self, spec, message):
            calls.append((spec, message))
            try:
                decoded = editor._decode(text)
                structured = dict(decoded, status='READY') if isinstance(decoded, dict) else decoded
            except editor.ReportFactEditorError:
                structured = None
            return SimpleNamespace(text=text, structured=structured,
                                   usage={'input_tokens': 20, 'output_tokens': 30})

    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: Backend())
    monkeypatch.setattr(report_model_config, 'DART_REPORT_MODEL', 'gpt-6-astra')
    monkeypatch.setattr(report_model_config, 'DART_REPORT_EFFORT', 'low')
    return calls


@pytest.mark.parametrize('stage', ['assessment', 'final'])
@pytest.mark.parametrize('is_session', [False, True, None])
def test_frozen_calendar_is_used_without_second_lookup(monkeypatch, stage, is_session):
    reports, payload = fixture()
    payload['edits'] = []
    if stage == 'assessment':
        payload['summary'] = None
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    def unexpected_lookup(day):
        raise AssertionError('Calendar must be reused from report start')
    monkeypatch.setattr(editor, '_calendar_context', unexpected_lookup)
    frozen = {'reference_date': '2026-09-25', 'calendar': 'XKRX', 'is_session': is_session}
    function = editor.assess_report_facts if stage == 'assessment' else editor.edit_and_summarize
    asyncio.run(function(reports, '회사', '123456', '20260925', calendar_context=frozen))
    assert json.loads(calls[0][1])['calendar_context'] == frozen


@pytest.mark.parametrize('field,value', [('reference_date', '2026-09-23'), ('calendar', 'NYSE'),
                                        ('is_session', 0), ('is_session', 'false')])
def test_invalid_shared_calendar_is_not_trusted(monkeypatch, field, value):
    reports, payload = fixture()
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    frozen = {'reference_date': '2026-09-25', 'calendar': 'XKRX', 'is_session': False}
    frozen[field] = value
    with pytest.raises(editor.ReportFactEditorError):
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260925', calendar_context=frozen))
    assert not calls


def test_review_reads_canonical_ce_once_but_preserves_published_handoff(monkeypatch):
    from prism_core.competitive_evidence import attach_competitive_evidence
    reports, payload = fixture()
    reports['news_analysis'] = '뉴스입니다.\n\n### Competitive Evidence\n사업 근거는 미확인입니다.'
    reports, _ = attach_competitive_evidence(reports, 'KR', '123456', '20260924')
    payload['edits'] = []
    calls = install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    patched, _, _ = asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    view = json.loads(calls[0][1])['sections']
    assert 'Competitive Evidence Handoff' not in view['company_overview']
    assert view['news_analysis'] == reports['news_analysis']
    assert patched == reports


def test_independent_overview_edit_is_not_ambiguous_with_hidden_ce_copy(monkeypatch):
    from prism_core.competitive_evidence import attach_competitive_evidence
    reports, payload = fixture()
    reports['news_analysis'] = '뉴스입니다.\n\n### Competitive Evidence\n' + reports['company_overview']
    reports, receipt = attach_competitive_evidence(reports, 'KR', '123456', '20260924')
    payload['edits'] = [payload['edits'][2]]
    install_backend(monkeypatch, json.dumps(payload, ensure_ascii=False))
    patched, _, _ = asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert patched['company_overview'].startswith(payload['edits'][0]['replacement'])
    assert patched['news_analysis'] == reports['news_analysis']
    assert patched['company_overview'].count(receipt['evidence_id']) == 1
    assert patched['company_overview'].count(payload['edits'][0]['original']) == 1


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


def test_structured_final_schema_is_requested(monkeypatch):
    reports, payload = fixture()
    calls = install_backend(monkeypatch, json.dumps(payload))
    asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert calls[0][0].output_schema is not None


def test_factual_mix_word_still_protects_entire_paragraph():
    reports, payload = fixture()
    reports['company_status'] += ' 고사양 제품 매출 비중이 큽니다.'
    payload['edits'] = [payload['edits'][1]]
    with pytest.raises(editor.ReportFactEditorError, match='decision paragraphs'):
        editor._validate_and_apply(reports, payload)


@pytest.mark.parametrize('issue', [None, '', '   ', 'x' * 2001])
def test_protocol_invalid_issue_cannot_trigger_regeneration(issue):
    from cores.report_review_protocol import validate_review_envelope
    reports, _ = fixture()
    payload = {'status': 'CONFLICTS', 'summary': None, 'edits': [], 'unresolved': [
        {'section': 'company_status', 'issue': issue, 'evidence_section': 'shared_reference', 'kind': 'contradiction', 'source_roles': []}]}
    with pytest.raises(editor.ReportFactEditorError) as caught:
        validate_review_envelope(reports, payload)
    assert not isinstance(caught.value, editor.ReportFactConflictError)


@pytest.mark.parametrize('bad', ['summary', 'edits'])
def test_mixed_conflict_envelope_never_applies_edits_or_generates_summary(bad):
    from cores.report_review_protocol import validate_review_envelope
    reports, legacy = fixture()
    before = copy.deepcopy(reports)
    payload = {'status': 'CONFLICTS', 'summary': None, 'edits': [], 'unresolved': [
        {'section': 'company_status', 'issue': 'conflict', 'evidence_section': 'shared_reference', 'kind': 'contradiction', 'source_roles': []}]}
    payload[bad] = legacy[bad]
    with pytest.raises(editor.ReportFactEditorError) as caught:
        validate_review_envelope(reports, payload)
    assert caught.value.code == 'INVALID_ENVELOPE'
    assert reports == before


def test_assessment_and_final_use_structured_result_not_text(monkeypatch):
    from cores import report_generation
    reports, legacy = fixture()
    calls = []
    async def run(spec, message):
        calls.append(spec)
        payload = {'status': 'READY', 'summary': None, 'edits': [], 'unresolved': []}
        if spec.name == 'report_final_fact_editor':
            payload.update(summary=legacy['summary'])
        return SimpleNamespace(structured=spec.output_schema.model_validate(payload), text='INVALID JSON', usage={})
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    assert asyncio.run(editor.assess_report_facts(reports, '회사', '123456', '20260924'))[1] is None
    assert asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))[1] == legacy['summary']
    assert [spec.name for spec in calls] == ['report_fact_assessor', 'report_final_fact_editor']
    assert all(spec.mcp_servers == () and spec.params.max_iterations == 1 for spec in calls)


def test_missing_structured_result_never_falls_back_to_valid_text(monkeypatch):
    from cores import report_generation
    reports, payload = fixture()
    async def run(*args):
        return SimpleNamespace(structured=None, text=json.dumps(dict(payload, status='READY')), usage={})
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    with pytest.raises(editor.ReportFactEditorError) as caught:
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert caught.value.code == 'STRUCTURED_OUTPUT_MISSING'


def test_real_review_boundary_reports_conflicts_without_summary_guards(monkeypatch):
    from cores import report_generation
    reports, _ = fixture()
    calls = []
    async def run(spec, message):
        calls.append(spec)
        return SimpleNamespace(structured=spec.output_schema.model_validate({
            'status': 'CONFLICTS', 'summary': None, 'edits': [], 'unresolved': [
                {'section': 'investment_strategy', 'issue': 'period conflict',
                 'evidence_section': 'company_status', 'kind': 'contradiction', 'source_roles': []}]}), text='', usage={})
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    with pytest.raises(editor.ReportFactConflictError) as caught:
        asyncio.run(editor.edit_and_summarize(reports, '회사', '123456', '20260924'))
    assert caught.value.strategy_only and len(calls) == 1


def test_failure_diagnostic_receives_structured_payload_but_exception_is_safe(monkeypatch):
    from cores import report_generation, report_review_diagnostics as diagnostics
    reports, _ = fixture()
    payload = {'status': 'CONFLICTS', 'summary': None, 'edits': [], 'unresolved': [
        {'section': 'shared_reference', 'issue': 'PRIVATE_SOURCE_ISSUE', 'evidence_section': 'company_status', 'kind': 'contradiction', 'source_roles': []}]}
    captured = []
    def record(**kwargs):
        captured.append(kwargs)
        return 'opaque-id'
    async def run(*args):
        return SimpleNamespace(structured=payload, text='', usage={}, response_id='response-id')
    monkeypatch.setattr(diagnostics, 'record_report_review_failure', record)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    with pytest.raises(editor.ReportSourceConflictError) as caught:
        asyncio.run(editor.assess_report_facts(reports, '회사', '123456', '20260924'))
    assert caught.value.diagnostic_id == 'opaque-id'
    assert 'PRIVATE_SOURCE_ISSUE' not in str(caught.value)
    assert captured[0]['response'] == payload and captured[0]['sections'] == reports
    assert captured[0]['stage'] == 'assessment'


@pytest.mark.parametrize('case,code', [
    ('summary_short', 'SUMMARY_LENGTH'), ('summary_number', 'SUMMARY_NUMBER'),
    ('summary_url', 'SUMMARY_URL'), ('summary_internal', 'SUMMARY_INTERNAL'),
    ('edit_number', 'EDIT_NUMBER'), ('edit_url', 'EDIT_URL'),
    ('edit_decision', 'EDIT_DECISION'), ('edit_structure', 'EDIT_STRUCTURE'),
])
def test_ready_guard_errors_have_specific_safe_codes(case, code):
    reports, payload = fixture()
    if case == 'summary_short': payload['summary'] = 'short'
    elif case == 'summary_number': payload['summary'] += ' 999999배'
    elif case == 'summary_url': payload['summary'] += ' https://unsupported.test/'
    elif case == 'summary_internal': payload['summary'] += ' PRIVATE_INTERNAL_FIELD'
    elif case == 'edit_number': payload['edits'][0]['replacement'] = payload['edits'][0]['replacement'].replace('123', '124')
    elif case == 'edit_url': payload['edits'][0]['replacement'] = payload['edits'][0]['replacement'].replace('example.com', 'unsupported.test')
    elif case == 'edit_decision': payload['edits'][0]['replacement'] += ' 매수해야 합니다.'
    else: payload['edits'][0]['replacement'] = '## ' + payload['edits'][0]['replacement']
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert caught.value.code == code


def test_numeric_inventory_uses_exact_decimal_without_context_rounding():
    from decimal import Decimal, localcontext
    value = '1234567890123456789012345678901234567890.123456789012345678901'
    grouped = '1,234,567,890,123,456,789,012,345,678,901,234,567,890.1234567890123456789010'
    with localcontext() as context:
        context.prec = 6
        expected = {Decimal(value)}
        assert editor._numeric_literals(value) == expected
        assert editor._numeric_literals(grouped) == expected
        assert editor._numeric_literals(value[:-1] + '2') != expected


def test_ready_edits_still_require_identical_numeric_spelling():
    reports, payload = fixture()
    payload['edits'][0]['replacement'] = payload['edits'][0]['replacement'].replace('123', '123.0')
    with pytest.raises(editor.ReportFactEditorError) as caught:
        editor._validate_and_apply(reports, payload)
    assert caught.value.code == 'EDIT_NUMBER'


def test_numeric_inventory_does_not_authorize_new_arithmetic():
    assert editor._numeric_literals('합계 19.204') - editor._numeric_literals('9.104 및 10.1')
