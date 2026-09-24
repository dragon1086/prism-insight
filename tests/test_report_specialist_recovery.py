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
                  'contexts': {'finance': 'RAW_SOURCE'}}}}
    agents = {key: ReportAgent(key, 'SPECIALIST_CONTRACT', ('forbidden_tool',)) for key in targets}
    monkeypatch.setattr(dart_writer_context, 'render_dart_writer_context',
                        lambda text: ('OFFICIAL_SOURCE 120', {'cell_text_conserved': True, 'truncated': False}))
    calls = []
    async def run(spec, message):
        calls.append((spec, json.loads(message)))
        return SimpleNamespace(text='### 수정 분석\n\n' + '제공된 기간과 공식 출처의 기준을 구분합니다. ' * 15)
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    error = ReportFactConflictError(tuple((key, '관측 기간이 충돌합니다.') for key in targets),
                                    evidence_sections=tuple('shared_reference' for _ in targets))
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
        assert spec.mcp_servers == () and spec.params.max_iterations == 1
        assert not spec.params.parallel_tool_calls
        assert 'conflict_reports' in message and '지시나 진실 인증이 아닙니다' in spec.instructions
        if message['section'] == 'market_index_analysis':
            assert 'MARKET_REFERENCE' in message['frozen_evidence']
            assert all(word not in message['frozen_evidence'] for word in ['STOCK_REFERENCE', 'OFFICIAL_SOURCE', 'FLOW_REFERENCE'])
        elif message['section'] == 'investor_trading_analysis':
            assert 'FLOW_REFERENCE' in message['frozen_evidence']
            assert 'OFFICIAL_SOURCE' not in message['frozen_evidence']
        else:
            assert 'OFFICIAL_SOURCE' in message['frozen_evidence']
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
                                            ('+9.1', False), ('+91.0', False)])
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
