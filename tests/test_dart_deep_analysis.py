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
        assert '3,000자 요약 제한은 이 장에 적용되지 않습니다' in agent.instruction
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
