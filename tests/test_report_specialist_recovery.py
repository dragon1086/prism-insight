import asyncio
import copy
import json
import logging
from types import SimpleNamespace

import pytest

from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401
from cores.agents.report_agent import ReportAgent
from cores.report_conflict_recovery import _split_protected, regenerate_conflicting_sections
from cores.report_fact_editor import ReportFactConflictError, ReportFactEditorError


def setup_case(monkeypatch, targets=('news_analysis',)):
    from cores import report_generation
    from prism_core import dart_writer_context
    reports = {key: '### 기본 분석\n\n' + '기존 사실의 기준과 출처를 확인합니다. ' * 15 for key in targets}
    reports.update(dart_deep_analysis='DART_DRAFT', shared_reference='SOURCE_REFERENCE',
                   investment_strategy='OLD_STRATEGY', peer_comparison='IMMUTABLE_PEER')
    packet = {'report_calculation_reference': 'STOCK_REFERENCE 120',
              'market_calculation_reference': 'MARKET_REFERENCE 120',
              'flow_evidence_public': 'FLOW_REFERENCE',
              'official_dart': {'public_receipt': 'ISSUER_RECEIPT', 'dart_chapter_inputs': {
                  'ready': True, 'receipt': {'core_conserved': True, 'capacity_ok': True},
                  'contexts': {'finance': json.dumps({'text': 'OFFICIAL_SOURCE 120'}),
                               'business': json.dumps({'text': 'BUSINESS_SOURCE 120'}),
                               'risks': json.dumps({'text': 'RISK_SOURCE 120'})}}}}
    agents = {key: ReportAgent(key, 'SPECIALIST_CONTRACT', ('forbidden_tool',)) for key in targets}
    monkeypatch.setattr(dart_writer_context, 'render_dart_writer_context',
                        lambda text: ('OFFICIAL_SOURCE 120', {'cell_text_conserved': True, 'truncated': False}))
    calls = []
    async def run(spec, message):
        calls.append((spec, json.loads(message)))
        return SimpleNamespace(text='### 수정 분석\n\n' + '제공된 기간과 공식 출처의 기준을 구분합니다. ' * 15)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    numerical = {'price_volume_analysis', 'investor_trading_analysis', 'market_index_analysis'}
    error = ReportFactConflictError(tuple((key, '관측 기간이 충돌합니다.') for key in targets),
        evidence_sections=tuple('shared_reference' if key in numerical else 'dart_deep_analysis' for key in targets),
        source_roles=tuple(() if key in numerical else ('finance',) for key in targets))
    return reports, agents, packet, error, calls


def execute(case):
    reports, agents, packet, error, _ = case
    return asyncio.run(regenerate_conflicting_sections(
        reports, agents, packet, error, 'Example', '252990', '20260924', logging.getLogger('test')))


def test_each_specialist_once_with_frozen_scoped_sources_and_no_tools(monkeypatch):
    targets = ('news_analysis', 'company_status', 'company_overview',
               'market_index_analysis', 'investor_trading_analysis')
    case = setup_case(monkeypatch, targets)
    original = copy.deepcopy((case[0], case[2]))
    result = execute(case)
    assert (case[0], case[2]) == original
    assert len(case[-1]) == 5
    for spec, message in case[-1]:
        from report_model_config import DART_REPORT_MODEL, DART_REPORT_EFFORT
        assert spec.model == DART_REPORT_MODEL and spec.params.reasoning_effort == DART_REPORT_EFFORT
        assert spec.mcp_servers == () and spec.params.max_iterations == 1
        assert '원문 그대로 복사' in spec.instructions
        assert not spec.params.parallel_tool_calls
        assert 'conflict_reports' in message and '지시나 진실 인증이 아닙니다' in spec.instructions
        assert 'OLD_STRATEGY' not in json.dumps(message)
        if message['section'] == 'market_index_analysis':
            assert message['related_report_drafts'] == {}
            assert 'MARKET_REFERENCE' in message['frozen_evidence']
            assert all(word not in message['frozen_evidence'] for word in ['STOCK_REFERENCE', 'OFFICIAL_SOURCE', 'FLOW_REFERENCE'])
        elif message['section'] == 'investor_trading_analysis':
            assert 'FLOW_REFERENCE' in message['frozen_evidence']
            assert 'OFFICIAL_SOURCE' not in message['frozen_evidence']
        else:
            assert 'OFFICIAL_SOURCE' in json.dumps(message['frozen_filing_sources'])
        assert '수정 분석' in result[message['section']]
    for key in ('dart_deep_analysis', 'shared_reference', 'investment_strategy', 'peer_comparison'):
        assert result[key] == case[0][key]


def test_protected_evidence_and_strategy_subchapter_preserved_verbatim(monkeypatch):
    case = setup_case(monkeypatch)
    protected = '#### Competitive Evidence\nEvidence ID: CE-abc\nSOURCE_CHECKED 원문\n'
    decision = '#### 투자 전략\n기존 정책은 그대로 유지합니다.\n'
    case[0]['news_analysis'] += '\n\n' + protected + '\n#### 향후 주시점\n사실 설명\n' + decision
    result = execute(case)['news_analysis']
    assert protected in result and decision in result
    assert 'CE-abc' not in case[-1][0][1]['original_draft']
    assert len(_split_protected(result)[1]) == 2


@pytest.mark.parametrize('bad', ['### 분석\n\n' + '없는 수치 987654321을 추가합니다. ' * 15,
                               '### 분석\n\n' + '출처 https://evil.example/test ' * 15,
                               '### 분석\n\n' + '문장입니다. ' * 40 + '\n#### Competitive Evidence\n새 근거',
                               'Analysis failed'])
def test_invalid_repair_has_no_partial_mutation_or_retry(monkeypatch, bad):
    case = setup_case(monkeypatch, ('company_status', 'news_analysis'))
    before = copy.deepcopy(case[0])
    from cores import report_generation
    calls = []
    async def run(spec, message):
        calls.append(spec.name)
        return SimpleNamespace(text=('### 분석\n\n' + '공식 근거를 확인합니다. ' * 30) if len(calls) == 1 else bad)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    with pytest.raises(ReportFactEditorError, match='bounds'):
        execute(case)
    assert len(calls) == 2 and case[0] == before


@pytest.mark.parametrize('condition', ['unready', 'unconserved', 'oversize', 'wrong_type'])
def test_preflight_failures_make_no_model_calls(monkeypatch, condition):
    case = setup_case(monkeypatch)
    if condition == 'unready': case[2]['official_dart']['dart_chapter_inputs']['ready'] = False
    if condition == 'unconserved': case[2]['official_dart']['dart_chapter_inputs']['receipt']['core_conserved'] = False
    if condition == 'oversize': case[2]['report_calculation_reference'] = 'x' * 400001
    if condition == 'wrong_type': case = (*case[:3], ValueError('conflict'), case[-1])
    with pytest.raises(ReportFactEditorError): execute(case)
    assert case[-1] == []


@pytest.mark.parametrize('literal,allowed', [('+9.10', True), ('-9.10', False),
                                            ('+9.1', True), ('+91.0', False)])
def test_explicit_positive_sign_is_equivalent_not_a_new_number(monkeypatch, literal, allowed):
    case = setup_case(monkeypatch)
    case[0]['news_analysis'] += '\n기존 수익률 9.10%입니다.'
    from cores import report_generation
    async def run(*args):
        return SimpleNamespace(text='### 분석\n\n' + f'기존 수익률은 {literal}%입니다. ' * 20)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    if allowed:
        assert literal in execute(case)['news_analysis']
    else:
        with pytest.raises(ReportFactEditorError): execute(case)


@pytest.mark.parametrize('provided', [False, True])
def test_news_can_reference_exact_signed_flow_in_existing_related_draft(monkeypatch, provided):
    case = setup_case(monkeypatch)
    if provided:
        case[0]['investor_trading_analysis'] = '동일 관측기간 개인 순매수 -313,540주'
    from cores import report_generation
    calls = []
    async def run(spec, message):
        calls.append(json.loads(message))
        return SimpleNamespace(text='### 분석\n\n' + '개인 순매수는 -313,540주이며 기간과 단위를 구분합니다. ' * 10)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    if provided:
        assert '-313,540' in execute(case)['news_analysis']
        assert calls[0]['related_report_drafts'] == {
            'investor_trading_analysis': case[0]['investor_trading_analysis']}
    else:
        with pytest.raises(ReportFactEditorError): execute(case)


def test_large_company_uses_only_complete_explicit_role_catalog_without_clipping(monkeypatch):
    case = setup_case(monkeypatch, ('company_status',))
    packet = case[2]['official_dart']['dart_chapter_inputs']
    catalog = {'finance': {'catalog': '재무 원문 ' * 12000},
               'business': {'catalog': '사업 원문 ' * 9000},
               'risks': {'catalog': '약정 원문 ' * 11000}}
    packet['contexts'] = {key: json.dumps(value, ensure_ascii=False) for key, value in catalog.items()}
    from prism_core import dart_writer_context
    def rendered(text):
        return json.loads(text)['catalog'] * 2, {'cell_text_conserved': True, 'truncated': False}
    monkeypatch.setattr(dart_writer_context, 'render_dart_writer_context', rendered)
    execute(case)
    spec, message = case[-1][0]
    assert message['frozen_filing_sources'] == {'finance': catalog['finance']}
    assert message['source_scope']['selected_roles'] == ['finance']
    assert set(message['source_scope']['omitted_roles']) == {'business', 'risks'}
    assert len((spec.instructions + json.dumps(message, ensure_ascii=False)).encode()) < 400000


def test_news_capital_event_uses_explicit_business_role_not_keyword_guess(monkeypatch):
    case = setup_case(monkeypatch)
    case = (*case[:3], ReportFactConflictError((('news_analysis', '전환 완료를 대조합니다.'),),
        evidence_sections=('dart_deep_analysis',), source_roles=(('business',),)), case[-1])
    execute(case)
    assert list(case[-1][0][1]['frozen_filing_sources']) == ['business']


def test_unsupported_claim_withdrawal_does_not_load_full_filing_catalogs(monkeypatch):
    case = setup_case(monkeypatch, ('company_overview',))
    case = (*case[:3], ReportFactConflictError((('company_overview', '업종 PBR 근거가 없습니다.'),),
        evidence_sections=(None,), kinds=('unsupported_claim',)), case[-1])
    execute(case)
    message = case[-1][0][1]
    assert message['frozen_filing_sources'] == {}
    assert message['conflict_reports'][0]['kind'] == 'unsupported_claim'


def test_unrequested_catalog_values_do_not_authorize_new_recovery_numbers(monkeypatch):
    case = setup_case(monkeypatch, ('company_status',))
    case[2]['official_dart']['dart_chapter_inputs']['contexts']['business'] = json.dumps({'value': '987654321'})
    from cores import report_generation
    async def run(*args):
        return SimpleNamespace(text='### 분석\n\n' + '전달되지 않은 숫자 987654321을 썼습니다. ' * 20)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    with pytest.raises(ReportFactEditorError) as caught:
        execute(case)
    assert caught.value.code == 'RECOVERY_NUMBER'


def test_combined_requested_roles_still_fail_before_models_if_capacity_exceeded(monkeypatch):
    case = setup_case(monkeypatch, ('company_status',))
    packet = case[2]['official_dart']['dart_chapter_inputs']
    packet['contexts'] = {key: json.dumps({'catalog': 'x' * 150000})
                          for key in ('finance', 'business', 'risks')}
    case = (*case[:3], ReportFactConflictError((('company_status', '여러 공시 영역을 대조합니다.'),),
        evidence_sections=('dart_deep_analysis',), source_roles=(('finance', 'business', 'risks'),)), case[-1])
    with pytest.raises(ReportFactEditorError) as caught:
        execute(case)
    assert caught.value.code == 'RECOVERY_CAPACITY' and case[-1] == []
    assert caught.value.details['count'] > caught.value.details['limit']
