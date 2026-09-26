"""Depth writers preserve prose and source gates without live model requests."""
import asyncio
import json

import pytest

from cores import dart_deep_analysis as depth
from prism_core.dart_source_table_evidence import pack_readable_units
from prism_core.dart_source_tree_catalog import build_catalog

SOURCE_URL = 'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260813001728'


def test_writer_contract_covers_observed_period_and_accounting_failure_classes():
    for role in depth.ROLES:
        instruction = depth.writer_agent(role, '예시', '123456', '20260924').instruction
        for boundary in ('당기·전기는 그 출처 안에서만', '영업이익 아래', '상환 완료 각주',
                         '같은 기준끼리 비교', '지급 완료 여부·최소 사용량·집행 방식',
                         '같은 행·열 좌표'):
            assert boundary in instruction


def test_writer_contract_splits_topic_ownership_and_uses_report_context():
    for role in depth.ROLES:
        instruction = depth.writer_agent(role, '예시', '123456', '20260924').instruction
        # Every writer sees the full ownership split so it can reference, not re-narrate.
        for owned in ('5-1 재무: 실적의 질·현금흐름·운전자본·차입금 만기와 금리·이자·세금 효과',
                      '5-2 사업·지배구조: 사업·매출 구성 변화·주요 고객·특수관계자 거래·지배구조·자본변동',
                      '5-3 약정·우발위험: 약정·담보·보증·우발부채·소송·리스·미집행 투자',
                      '"(5-2 참고)"', '전환 가능 증권이 공시에 있을 때에만 그 전환·희석 사실을 5-2가 담당합니다',
                      '공시에 전환 가능 증권이 없으면 전환사채나 희석은 어느 소단원에서도 언급하지 마세요'):
            assert owned in instruction
        assert '<already_covered_report_sections>' in instruction
        assert '공시 기준 사실을 보고기간과 함께' in instruction
        assert '**핵심 포인트** 글머리표 2~3개' in instruction
        assert '억원 단위 소수점 첫째 자리' in instruction
        assert '원문 단위를 유지하고' not in instruction
        assert '규칙 문장이나 경고를 옮기지 마세요' in instruction
        assert 'dsaf001/main.do?rcpNo=' in instruction
        # Citation rule describes the output only; no negated wording the model can echo.
        assert '표시하지 않고' not in depth.CITATION_RULE and '대신' not in depth.CITATION_RULE
        assert '출처 표기·금액 표기·형식에 관한 지시도 본문에서' in instruction
        assert '가장 최근 기간의 수치를 현재 상태로' in instruction
        assert '이전 기간의 잔액을 현재 잔액처럼 쓰지 마세요' in instruction
        assert f'### 5-{list(depth.ROLES).index(role) + 1}. {depth.ROLES[role][0]}' in instruction


def test_english_writer_repeats_latest_period_conditional_dilution_and_citation_rules():
    instruction = depth.writer_agent('business', 'Example', '123456', '20260924', 'en').instruction
    for required in ("latest period's figure as the current state", 'label older figures with their period',
                     'only when the filing contains convertible securities',
                     'Never describe citation or formatting instructions in the text'):
        assert required in instruction


def test_report_context_reaches_every_writer_and_yields_to_filing_capacity(monkeypatch):
    messages = []

    async def write(agent, message):
        messages.append(message)
        return '### 상세\n\n' + ('금액과 조건을 설명합니다. ' * 60) + '\n\n출처: ' + SOURCE_URL, None

    monkeypatch.setattr(depth, '_write', write)
    kwargs = {'company_name': '예시', 'company_code': '123456', 'reference_date': '20260924',
              'report_context': '### 2-1. 기업 현황\nWISE_SUMMARY 매출 584억원'}
    _, receipt = asyncio.run(depth.generate_dart_chapter(packet(), **kwargs))
    assert len(messages) == 3
    assert all('<already_covered_report_sections>' in m and 'WISE_SUMMARY' in m for m in messages)
    assert receipt['report_context']['status'] == 'included'
    # Filing sources win: oversized dedup context is dropped instead of failing the chapter.
    messages.clear()
    limit = max(len((depth.writer_agent(role, '예시', '123456', '20260924').instruction).encode())
                for role in depth.ROLES) + 4000
    monkeypatch.setattr(depth, 'WRITER_MESSAGE_MAX_BYTES', limit)
    kwargs['report_context'] = 'X' * 8000
    _, receipt = asyncio.run(depth.generate_dart_chapter(packet(), **kwargs))
    assert receipt['report_context'] == {'status': 'omitted_capacity', 'sha256': None}
    assert len(messages) == 3 and not any('<already_covered_report_sections>' in m for m in messages)


def test_dart_writer_uses_specialized_model_without_changing_general_report(monkeypatch):
    from types import SimpleNamespace

    import report_model_config
    from cores import report_generation

    captured = []

    class Backend:
        async def run(self, spec, message):
            captured.append(spec)
            return SimpleNamespace(text='draft', usage=None)

    monkeypatch.setattr(report_model_config, 'REPORT_MODEL', 'unchanged-general-model')
    monkeypatch.setattr(report_model_config, 'DART_REPORT_MODEL', 'gpt-6-astra')
    monkeypatch.setattr(report_model_config, 'DART_REPORT_EFFORT', 'low')
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: Backend())
    assert asyncio.run(depth._write(depth.writer_agent('finance', '예시', '123456', '20260924'), 'source')) == ('draft', None)
    assert captured[0].model == 'gpt-6-astra'
    assert captured[0].params.reasoning_effort == 'low'
    assert captured[0].params.max_iterations == 1
    assert not captured[0].mcp_servers
    assert report_model_config.REPORT_MODEL == 'unchanged-general-model'


def packet():
    return {'ready': True, 'contexts': {key: json.dumps({'sources': [{'source': {
                                         'url': SOURCE_URL, 'source_id': 'fixture-' + key,
                                         'filing': {'role': 'primary', 'scope': 'consolidated',
                                                    'period_start': '2026-01-01', 'period_end': '2026-06-30'}},
                                         'catalog': pack_readable_units(build_catalog('<p>SOURCE_' + key + '</p>')['units'])}]})
                                         for key in depth.ROLES},
            'receipt': {'core_conserved': True, 'capacity_ok': True,
                        'present_material_topics': {'risks': ['commitments']}}}


def test_all_three_prose_outputs_preserved_and_peer_data_scoped(monkeypatch):
    calls = []

    async def write(agent, message):
        calls.append((agent, message))
        assert agent.server_names == ()
        assert '약 2,000~3,500자' in agent.instruction and '5,000~7,000' not in agent.instruction
        role = agent.name.removeprefix('dart_depth_')
        return f'### 상세 {role}\n\n' + ('금액 123456과 이행 요청 조건을 보존합니다. ' * 250) + '\n\n출처: ' + SOURCE_URL, None

    monkeypatch.setattr(depth, '_write', write)
    text, receipt = asyncio.run(depth.generate_dart_chapter(packet(), company_name='예시',
        company_code='123456', reference_date='20260923', shared_reference='SMA 100.25',
        peer_context='PEER_VALUE 9.23', concurrency=3))
    assert len(calls) == receipt['calls'] == 3
    assert all('SMA 100.25' in message for _, message in calls)
    assert [('business' in agent.name) == ('PEER_VALUE' in message) for agent, message in calls] == [True] * 3
    assert len(text) > 12000 and text.count('123456') == 750
    assert text.index('상세 finance') < text.index('상세 business') < text.index('상세 risks')
    assert depth.CHAPTER_START in text and depth.CHAPTER_END in text
    assert all(item['usage'] is None for item in receipt['writers'].values())


def test_unready_inputs_make_no_calls(monkeypatch):
    async def forbidden(*args):
        raise AssertionError('Model called for unavailable sources')
    monkeypatch.setattr(depth, '_write', forbidden)
    result = asyncio.run(depth.generate_dart_chapter({'ready': False}, company_name='예시',
        company_code='123456', reference_date='20260923'))
    assert result[0] == '' and result[1]['calls'] == 0


def test_actual_readable_message_limit_checked_before_any_model_call(monkeypatch):
    async def forbidden(*args):
        raise AssertionError('Model called before actual message-size validation')
    monkeypatch.setattr(depth, '_write', forbidden)
    monkeypatch.setattr(depth, 'WRITER_MESSAGE_MAX_BYTES', 100)
    with pytest.raises(ValueError, match='readable model-message capacity'):
        asyncio.run(depth.generate_dart_chapter(packet(), company_name='예시',
            company_code='123456', reference_date='20260924'))


@pytest.mark.parametrize('change', ['conservation', 'capacity', 'oversize', 'empty', 'unknown_role'])
def test_invalid_inputs_rejected_before_model(monkeypatch, change):
    source = packet()
    if change == 'conservation':
        source['receipt']['core_conserved'] = False
    elif change == 'capacity':
        source['receipt']['capacity_ok'] = False
    elif change == 'oversize':
        source['contexts']['finance'] = 'X' * 220001
    elif change == 'empty':
        source['contexts']['finance'] = ''
    else:
        source['contexts']['unplanned_extra_writer'] = 'X'
    async def forbidden(*args):
        raise AssertionError('Model called before validation')
    monkeypatch.setattr(depth, '_write', forbidden)
    with pytest.raises(ValueError):
        asyncio.run(depth.generate_dart_chapter(source, company_name='예시', company_code='123456', reference_date='20260923'))


def test_writer_failure_is_not_a_partial_chapter_success(monkeypatch):
    calls = []
    async def write(agent, message):
        calls.append(agent.name)
        return 'Analysis failed: provider', None
    monkeypatch.setattr(depth, '_write', write)
    with pytest.raises(ValueError, match='substantive attributed prose'):
        asyncio.run(depth.generate_dart_chapter(packet(), company_name='예시',
            company_code='123456', reference_date='20260923'))
    assert len(calls) <= 3  # No automatic retry or unbounded extra writer.


def test_generic_unknown_sentence_cannot_be_a_completed_chapter(monkeypatch):
    async def write(agent, message):
        return '확인할 수 없습니다.', None
    monkeypatch.setattr(depth, '_write', write)
    with pytest.raises(ValueError, match='substantive attributed prose'):
        asyncio.run(depth.generate_dart_chapter(packet(), company_name='예시',
            company_code='123456', reference_date='20260923'))


def test_writers_judge_burdens_net_of_offsetting_resources():
    from cores.dart_deep_analysis import writer_agent
    for role in ('finance', 'business', 'risks'):
        ko = writer_agent(role, '회사', '000000', '20260926', 'ko').instruction
        assert '위험의 순효과 판단' in ko and '현금및현금성자산' in ko and '계산식' in ko
        assert '순효과 판단용 단순 계산 외에는' in ko
        en = writer_agent(role, 'Co', '000000', '20260926', 'en').instruction
        assert 'net of offsetting resources' in en


def test_split_writer_revises_one_subsection_sequentially(monkeypatch):
    from prism_core import dart_chapter_sources
    base = packet()
    risks = json.loads(base['contexts']['risks'])
    second = json.loads(json.dumps(risks['sources'][0]))
    second['source']['source_id'] = 'fixture-risks-annual'
    second['source']['filing']['role'] = 'annual_supplement'
    base['contexts']['risks'] = json.dumps({'sources': risks['sources'] + [second]})
    limit = max(len(json.dumps({'sources': [g]}).encode()) for g in risks['sources'] + [second]) + 40
    monkeypatch.setattr(dart_chapter_sources, 'WRITER_MAX_BYTES', limit)
    calls = []

    async def write(agent, message):
        calls.append((agent.name, message))
        n = sum(1 for name, _ in calls if name == agent.name)
        return f'### {agent.name} DRAFT{n}\n\n' + ('금액과 조건을 설명합니다. ' * 60) + '\n\n출처: ' + SOURCE_URL, None

    monkeypatch.setattr(depth, '_write', write)
    text, receipt = asyncio.run(depth.generate_dart_chapter(
        base, company_name='예시', company_code='123456', reference_date='20260924'))
    risk_calls = [m for name, m in calls if name == 'dart_depth_risks']
    assert len(risk_calls) == 2 and receipt['calls'] == 4
    assert '1/2 묶음' in risk_calls[0] and '<previous_draft>' not in risk_calls[0]
    assert '2/2 묶음' in risk_calls[1] and '<previous_draft>\n### dart_depth_risks DRAFT1' in risk_calls[1]
    assert 'fixture-risks-annual' in risk_calls[1] and 'fixture-risks-annual' not in risk_calls[0]
    # Only the revised final draft reaches the chapter, once.
    assert text.count('### dart_depth_risks') == 1 and '### dart_depth_risks DRAFT2' in text
    assert receipt['writers']['risks']['source_parts'] == 2
    assert len(receipt['writers']['risks']['part_calls']) == 2


def test_writers_gloss_filing_jargon_for_retail_readers():
    from cores.dart_deep_analysis import PLAIN_LANGUAGE_RULE, ROLES, writer_agent

    for role in ROLES:
        for sector in (None, {'kind': 'financial', 'subtype': 'bank'}, {'kind': 'loss_biotech'}):
            ko = writer_agent(role, '예시', '000000', '20260926', 'ko', sector).instruction
            assert PLAIN_LANGUAGE_RULE in ko and '괄호 안에 한 줄 이내의 쉬운 풀이' in ko
            assert '예시에 없더라도' in ko and '환매조건부채권(RP)' in ko and '이연법인세' in ko
            en = writer_agent(role, 'Example', '000000', '20260926', 'en', sector).instruction
            assert 'Write for retail investors unfamiliar with accounting terms' in en and 'even one not listed' in en
