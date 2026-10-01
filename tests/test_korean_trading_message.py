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


def test_internal_terms_do_not_leak_and_particles_follow_the_label():
    # 2026-09-28 한화솔루션 hold message (raw model rationale before rendering).
    raw = ('분석 의견: 보고서 2-1·2-2 및 5장 대조 결과 F1·F2·F4는 통과하지만, 실제 ROE는 F3에 미달합니다.\n'
           'KIS 확정 5세션 합계는 -428,847주입니다. 최신 MCP 장중 관측가격 33,500원을 사용했으며 '
           'BAR_FINALITY_UNKNOWN입니다. 주입 팩트상 T1·T2는 해당하지 않고 OHLCV를 확인했습니다. '
           'KR_FLOW_EVIDENCE_V1로 판단했고 MA20로 지지를 봅니다. F2와 T1가 핵심입니다.')
    text = render_korean_trading_message(raw)
    for code in ('F1', 'F2', 'F3', 'F4', 'T1', 'T2', 'KIS', 'MCP', 'BAR_FINALITY_UNKNOWN', 'OHLCV',
                 'KR_FLOW_EVIDENCE_V1', 'MA20'):
        assert code not in text
    assert '사업 모델·경쟁력 기준은 통과' in text and '기준는' not in text
    assert '하락 추세 차단 조건은 해당하지' in text and '조건는' not in text
    assert '성장성 기준에 미달' in text
    assert '증권사 확정 5세션' in text and '최신 시세 조회 장중 관측가격 33,500원' in text
    assert '마감 확정 여부 미확인입니다' in text and '일봉 시세를 확인' in text
    assert '수급 근거 자료로 판단' in text and '20일 이동평균선으로 지지' in text
    assert '재무 건전성 기준과 ' in text and '이동평균선 이탈 조건이 핵심' in text
    for fact in ('2-1·2-2', '-428,847주', '33,500원', 'ROE'):
        assert fact in text
    assert render_korean_trading_message(text) == text


@pytest.mark.parametrize('raw,expected', [
    ('수익성 기준는 충족', '수익성 기준은 충족'),       # consonant ending
    ('필수 재무·사업 기준 4개은 모두', '필수 재무·사업 기준 4개는 모두'),  # vowel ending
    ('20일 이동평균선로부터', '20일 이동평균선으로부터'),
    ('시세 조회으로 확인', '시세 조회로 확인'),
    ('조건은행', '조건은행'),                          # particle-like syllable inside a word
])
def test_particle_repair_is_narrow(raw, expected):
    assert render_korean_trading_message(raw) == expected


def test_rationale_style_rule_is_in_korean_trading_prompts():
    from cores.agents.trading_agents import create_sell_decision_agent, create_trading_scenario_agent
    from messaging.korean_trading_message import korean_rationale_style_contract

    rule = korean_rationale_style_contract('ko')
    assert '조사는 바로 앞 단어의 받침에 맞추십시오' in rule and korean_rationale_style_contract('en') == ''
    assert rule in create_sell_decision_agent('ko').instruction
    assert rule in create_trading_scenario_agent('ko').instruction
    assert '판단 근거 문장 작성 규칙' not in create_sell_decision_agent('en').instruction


def test_hold_reason_shows_the_ai_blocking_rule():
    from messaging.korean_trading_message import hold_reason_display, render_korean_trading_message

    scenario = {"rejection_reason": "주요 지지선이 10% 이상 아래에 있어 진입 금지 규칙에 해당합니다. MA20 대비 +24%입니다."}
    shown = hold_reason_display("AI 판단: Skip / 점수 부족 (7/8)", scenario)
    assert shown == "AI 판단: Skip — 주요 지지선이 10% 이상 아래에 있어 진입 금지 규칙에 해당합니다. / 점수 부족 (7/8)"
    assert "보류 사유: AI 판단: 미진입 — 주요 지지선" in render_korean_trading_message(f"보류 사유: {shown}")
    # US labels render in Korean; no rejection reason keeps the original parts.
    assert hold_reason_display("AI judgment: no_entry / Insufficient score (6/8)", {}) == \
        "AI 판단: no_entry / 점수 부족 (6/8)"
    assert hold_reason_display("점수 부족 (6/8)", {"rejection_reason": "R/R 0.45로 기준 미달"}) == \
        "점수 부족 (6/8) / AI 차단 사유: R/R 0.45로 기준 미달"
    long = hold_reason_display("AI 판단: Skip", {"rejection_reason": "가" * 300}, limit=20)
    assert long.endswith("…") and len(long) < 50


def test_prompt_plumbing_words_are_rendered_for_readers():
    raw = ("15:00 주입 자료의 거래량 255,691주는 평균의 2.08배입니다. 20일선 괴리율은 당일 포함·제외 계산에 따라 "
           "판정이 달라집니다. 주입된 팩트를 확인했습니다.")
    out = render_korean_trading_message(raw)
    assert "주입" not in out and "포함·제외 계산" not in out
    assert "15:00 기준 자료의 거래량" in out and "당일 봉 포함 여부에 따라" in out and "제공된 자료를" in out
    from messaging.korean_trading_message import korean_rationale_style_contract
    assert "주입 자료" in korean_rationale_style_contract("ko")


def test_hold_message_codes_render_in_korean():
    # 2026-10-02 US afternoon LITE/MU hold messages.
    raw = ("보류 사유: AI 판단: no_entry — effective_score 3점이 moderate_bull 최소 4점에 미달합니다. / 점수 부족 (2/4)\n"
           "보류 사유: AI 판단: no_entry — R/R floor 미달: moderate_bull의 손익비 하한은 1.2입니다.\n"
           "시장이 sideways는 아닙니다.\n"
           "📊 경험 기반 점수조정: -1점 (Same stock past average loss -5.9%, Technology sector avg profit 2.1%, "
           "Trigger 'Closing Strength Top' actual trade win rate low 30% (n=12), Recent stop-out 3.5h ago (-6.1%) — churn guard)")
    out = render_korean_trading_message(raw)
    for code in ("effective_score", "moderate_bull", "R/R floor", "sideways", "Same stock", "churn guard", "win rate"):
        assert code not in out, (code, out)
    assert "유효 점수 3점이 보통 강세장 최소 4점에 미달" in out
    assert "손익비 하한 미달: 보통 강세장의 손익비 하한은 1.2" in out
    assert "횡보장은" in out  # particle re-picked for the vowel-final label
    assert "같은 종목 과거 평균 손실 -5.9%" in out and "Technology 업종 과거 평균 수익 2.1%" in out
    assert "'Closing Strength Top' 트리거 실제 승률 낮음 30%(표본 12건)" in out
    assert "최근 손절 3.5시간 전(-6.1%) — 잦은 재진입 방지" in out
    assert "moderate_bullish" in render_korean_trading_message("moderate_bullish")  # whole codes only
