import asyncio
import copy

import pytest

from test_us_evidence_pipeline_contract import isolated_imports_and_effects  # noqa: F401
from cores.agents.report_agent import ReportAgent
from prism_core.kr_report_context import (
    apply_kr_report_context, reference_context, synthesis_reference_context,
    market_cache_key, render_calendar_reference,
)


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_local_calendar_fact_reaches_each_writer_and_synthesis_unchanged(language):
    from cores.report_calendar import calendar_context
    calendar = calendar_context('20260925')
    assert calendar == {'reference_date': '2026-09-25', 'calendar': 'XKRX', 'is_session': False}
    packet = {'report_calendar_context': calendar}
    expected = render_calendar_reference(calendar, language)
    before = copy.deepcopy(packet)
    for section in ('price_volume_analysis', 'investor_trading_analysis', 'company_status',
                    'company_overview', 'news_analysis', 'market_index_analysis'):
        agent = apply_kr_report_context(ReportAgent(section, 'BASE'), section, packet, language)
        assert expected in agent.instruction
    assert expected in synthesis_reference_context(packet, language)
    assert expected in reference_context(packet, language, market_only=True)
    assert packet == before


@pytest.mark.parametrize('calendar', [None, {}, {'calendar': 'XKRX', 'reference_date': '2026-09-25', 'is_session': None},
                                     {'calendar': 'XNYS', 'reference_date': '2026-09-25', 'is_session': False},
                                     {'calendar': 'XKRX', 'reference_date': 'invalid', 'is_session': False}])
def test_unknown_calendar_is_never_claimed_closed(calendar):
    text = render_calendar_reference(calendar)
    assert '미확인' in text
    assert ', 휴장으로 확인.' not in text


def test_calendar_rules_never_shift_foreign_news_or_invent_next_session():
    text = render_calendar_reference({'calendar': 'XKRX', 'reference_date': '2026-09-25', 'is_session': False})
    assert '해외시장 거래·공시·뉴스의 사건 날짜를 변경하지' in text
    assert '다음 거래일 날짜를 추정하지' in text
    assert '2026-09-28' not in text


def test_calendar_changes_cache_key_but_company_fields_do_not():
    packet = {'report_calendar_context': {'calendar': 'XKRX', 'reference_date': '2026-09-25', 'is_session': False}}
    key = market_cache_key(packet, '20260925', 'ko')
    packet['company_status'] = 'OTHER TICKER'
    assert market_cache_key(packet, '20260925', 'ko') == key
    packet['report_calendar_context']['is_session'] = None
    assert market_cache_key(packet, '20260925', 'ko') != key


@pytest.mark.parametrize('market', [False, True])
def test_latest_ma_alignment_is_not_evidence_that_all_slopes_rise(market):
    text = reference_context({}, market_only=market)
    assert '최신값의 크기·배열만으로' in text
    assert '기울기가 모두 상승' in text


def test_existing_local_calendar_recognizes_closed_and_open_dates():
    from cores.report_calendar import calendar_context
    assert calendar_context('20260924') == {
        'reference_date': '2026-09-24', 'calendar': 'XKRX', 'is_session': False}
    assert calendar_context('2026-09-23')['is_session'] is True


@pytest.mark.parametrize('date', ['invalid', '20260230', '2026-9-24', None])
def test_invalid_calendar_date_remains_unavailable(date):
    from cores.report_calendar import calendar_context
    assert calendar_context(date)['is_session'] is None


def test_calendar_failure_is_unknown_not_closed(monkeypatch):
    import pandas_market_calendars as mcal
    from cores.report_calendar import calendar_context

    def unavailable(*args, **kwargs):
        raise RuntimeError('local calendar unavailable')

    monkeypatch.setattr(mcal, 'get_calendar', unavailable)
    assert calendar_context('20260924')['is_session'] is None


def test_actual_orchestrator_collects_local_calendar_before_agent_creation(monkeypatch):
    from cores import analysis
    import cores.data_prefetch as prefetch
    import prism_core.kr_official_report_inputs as official
    import prism_core.report_research_prefetch as research

    monkeypatch.setattr(prefetch, 'prefetch_kr_analysis_data', lambda *args: {})
    async def no_external_inputs(*args): return {}
    monkeypatch.setattr(official, 'collect_kr_official_report_inputs', no_external_inputs)
    monkeypatch.setattr(research, 'prefetch_report_research', no_external_inputs)

    class AgentBoundaryReached(RuntimeError):
        pass

    def agent_boundary(*args, **kwargs):
        packet = kwargs['prefetched_data']
        assert packet['report_calendar_context'] == {
            'reference_date': '2026-09-25', 'calendar': 'XKRX', 'is_session': False}
        assert '2026-09-25, 휴장으로 확인.' in reference_context(packet)
        raise AgentBoundaryReached('calendar collected before model preparation')
    monkeypatch.setattr(analysis, 'get_agent_directory', agent_boundary)
    with pytest.raises(AgentBoundaryReached):
        asyncio.run(analysis.analyze_stock('252990', 'Example', '20260925'))
