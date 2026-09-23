"""Depth writers preserve prose and source gates without live model requests."""
import asyncio
import json

import pytest

from cores import dart_deep_analysis as depth

SOURCE_URL = 'https://dart.fss.or.kr/report/viewer.do?rcpNo=20260813001728'


def packet():
    return {'ready': True, 'contexts': {key: json.dumps({'sources': [{'source': {'url': SOURCE_URL},
                                         'catalog': 'SOURCE_' + key}]}) for key in depth.ROLES},
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
