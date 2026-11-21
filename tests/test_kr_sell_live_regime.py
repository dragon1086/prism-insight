import json

import pytest

from cores.oneil_fallback import SellInputs, TRAIL_DROP_BULL, TRAIL_DROP_WEAK, evaluate_oneil_sell
from prism_core.sell_regime_context import kr_live_regime_block
from tests.test_stock_tracking_enhanced_pending_exit import _enhanced_agent

CONTEXT = {"index_summary": {"kospi_current": 3100.5, "kospi_20d_ma": 3150.2, "kospi_2w_change_pct": -2.4}}


async def _captured_prompt(monkeypatch, tmp_path, regime, context=CONTEXT, language="ko"):
    monkeypatch.setenv("PRISM_KR_CODEX_FAST_SELL", "0")
    monkeypatch.setenv("PRISM_KR_CODEX_FAST_TRADING", "0")
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "false")
    agent, connection, stock = _enhanced_agent(tmp_path / f"regime-{regime}-{language}.sqlite")
    agent.language = language
    agent._live_regime_cache = regime
    agent._live_market_context = context
    seen = []

    class LLM:
        async def generate_str(self, **kwargs):
            seen.append(kwargs["message"])
            return json.dumps({"should_sell": False, "sell_reason": "hold", "confidence": 6,
                               "analysis_summary": {}, "portfolio_adjustment": {"needed": False}})

    class DecisionAgent:
        async def attach_llm(self, _factory):
            return LLM()

    agent.sell_decision_agent = DecisionAgent()
    try:
        await agent._analyze_sell_decision(stock)
    finally:
        connection.close()
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
async def test_weak_system_regime_reaches_the_sell_prompt(monkeypatch, tmp_path):
    # 2026-09-28 samcns: system moderate_bear, but the AI judged a bull market on its own.
    prompt = await _captured_prompt(monkeypatch, tmp_path, "moderate_bear")
    assert "현재 시장 국면 (시스템 실시간 계산 — 우선 적용)" in prompt
    assert "보통 약세장" in prompt and "B) 약세장/횡보장 모드" in prompt
    assert "최고가 × 0.95" in prompt and "KOSPI 3100.5 / 20일선 3150.2" in prompt
    assert prompt.index("현재 시장 국면") < prompt.index("### 종목 기본 정보")


@pytest.mark.asyncio
async def test_bull_system_regime_keeps_the_wide_band(monkeypatch, tmp_path):
    prompt = await _captured_prompt(monkeypatch, tmp_path, "moderate_bull", language="en")
    assert "Current Market Regime (LIVE, system-computed — authoritative)" in prompt
    assert "A) bull-market mode" in prompt and "highest price since entry × 0.92" in prompt


@pytest.mark.asyncio
async def test_unavailable_regime_falls_back_to_step_zero(monkeypatch, tmp_path):
    prompt = await _captured_prompt(monkeypatch, tmp_path, None, context=None)
    # 2026-11-11 prompt diet (audit M4/X4, user decision): no AI self-judged regime; only the code fallback.
    assert "시스템 국면을 계산하지 못했습니다" in prompt and "국면을 직접 판단하지 마세요" in prompt
    assert "0단계" not in prompt
    # 2026-10-19: without a live regime the exit code trails at x0.90; the AI must not raise the stop to a trailing level.
    assert "최고가 × 0.90" in prompt and "trailing 수준으로 올리지 마세요" in prompt
    assert "× 0.92" not in prompt.split("### 종목 기본 정보")[0] and "× 0.95" not in prompt.split("### 종목 기본 정보")[0]


@pytest.mark.parametrize("regime", ["parabolic", "strong_bull", "moderate_bull", "sideways",
                                    "moderate_bear", "strong_bear"])
def test_prompt_band_never_tighter_than_the_deterministic_trail(regime):
    # The AI stop must sit on the same line the automatic TIER2 exit already enforces,
    # so fixing the regime input cannot create an earlier exit than the existing rule.
    block = kr_live_regime_block(regime, None, "ko")
    drop = TRAIL_DROP_BULL if "0.92" in block else TRAIL_DROP_WEAK
    peak, buy = 18490.0, 15780.0
    line = peak * drop
    above = SellInputs(buy_price=buy, current_price=line + 1, highest_price=peak, market_condition=regime,
                       regime_is_live=True)
    below = SellInputs(buy_price=buy, current_price=line * 0.99, highest_price=peak, market_condition=regime,
                       regime_is_live=True)
    assert evaluate_oneil_sell(above)[0] is False
    assert evaluate_oneil_sell(below)[0] is True and "TIER2_TRAIL" in evaluate_oneil_sell(below)[1]


def test_samcns_case_now_clears_the_adjustment_threshold():
    peak, buy, stop = 18490.0, 15780.0, 16376.0
    threshold = max(1.5, min(5.0, (peak - buy) / buy * 100 * 0.3))
    weak = peak * float(kr_live_regime_block("moderate_bear").split("최고가 × ")[1][:4])
    bull = peak * float(kr_live_regime_block("moderate_bull").split("최고가 × ")[1][:4])
    assert (bull / stop - 1) * 100 < threshold  # what the AI computed on 9/28 (no adjustment)
    assert (weak / stop - 1) * 100 >= threshold  # system regime -> stop raised to ~17,566
