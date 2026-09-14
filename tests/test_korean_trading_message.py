import pytest

from messaging.korean_trading_message import render_korean_trading_message


def test_evidence_codes_and_decision_are_readable_without_changing_facts():
    raw = ('매수 Score: 6/10\n결정: Skip\n보류 사유: AI 판단: Skip / 점수 부족 (6/8)\n'
           'EPS 전년 대비 +80%는 SOURCE_REPORTED_YOY이며 기본·희석 구분은 '
           'NOT_IN_INPUT·NOT_REQUESTED입니다. F4를 통과했고 INCOMPARABLE은 유지합니다. '
           'MA20·MA50·MA60 위로 T1·T2에 해당하지 않습니다. ATR20 5.9%, 손절폭 4.33%.')
    text = render_korean_trading_message(raw)
    for code in ['SOURCE_REPORTED_YOY', 'NOT_IN_INPUT', 'NOT_REQUESTED', 'INCOMPARABLE', 'Skip', 'F4', 'T1', 'T2', 'MA20', 'ATR20']:
        assert code not in text
    for fact in ['6/10', '6/8', '+80%', '5.9%', '4.33%', '해당하지 않습니다']:
        assert fact in text
    assert '추가 조회' in text and '제공된 자료' in text
    assert '직접 검산' in text
    assert '기준을 통과' in text
    assert '결정: 미진입' in text


def test_link_and_identifiers_are_preserved_and_render_is_idempotent():
    url = 'https://example.com/NOT_IN_INPUT/MA20.pdf?tag=SOURCE_REPORTED_YOY'
    raw = f'[회사 IR]({url})\nNOT_IN_INPUT. X_NOT_IN_INPUT_X untouched. [exit-event: abc-123]'
    text = render_korean_trading_message(raw)
    assert url in text and 'X_NOT_IN_INPUT_X' in text and '[exit-event: abc-123]' in text
    assert render_korean_trading_message(text) == text


def test_history_states_and_profit_factor_remain_distinct():
    text = render_korean_trading_message('NO_HISTORY / SOURCE_UNAVAILABLE\n실제 매매: 47건, 승률 49%, PF 1.79 (n=148)')
    assert '매도 기록 없음' in text and '자료 없음으로 단정할 수 없음' in text
    assert '전략 원장 거래: 47건' in text
    assert '누적 이익/손실 비율 1.79' in text and '표본 148건' in text


@pytest.mark.parametrize('text', ['portfolio', 'Hello world', '', '005930 삼성전자 147,800원'])
def test_unrelated_text_is_unchanged(text):
    assert render_korean_trading_message(text) == text


@pytest.mark.asyncio
@pytest.mark.parametrize('source_file', ['stock_tracking_agent.py', 'prism-us/us_stock_tracking_agent.py'])
@pytest.mark.parametrize('language', ['ko', 'en'])
async def test_real_delivery_boundary_renders_without_mutating_scenario(source_file, language, monkeypatch):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    source = Path(__file__).resolve().parents[1] / source_file
    method = next(node for node in ast.walk(ast.parse(source.read_text()))
                  if isinstance(node, ast.AsyncFunctionDef) and node.name == 'send_telegram_message')
    ns = {'logger': MagicMock(), 'require_execution_runtime': lambda agent: None}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
    monkeypatch.setattr('portfolio_broadcast.should_send_portfolio', lambda *args, **kwargs: False)
    scenario = {'decision': 'Skip', 'rationale': 'SOURCE_REPORTED_YOY / NOT_REQUESTED'}
    original = dict(scenario)
    agent = SimpleNamespace(message_queue=['결정: Skip\n' + scenario['rationale']], _msg_types=['analysis'])
    agent._clear_message_queue = lambda: None
    assert await ns['send_telegram_message'](agent, None, language=language) is True
    if language == 'ko':
        assert '미진입' in agent.last_batch_messages[0][1]
        assert 'NOT_REQUESTED' not in agent.last_batch_messages[0][1]
    else:
        assert 'NOT_REQUESTED' in agent.last_batch_messages[0][1]
    assert scenario == original
