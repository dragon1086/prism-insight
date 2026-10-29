"""SELL prompts must agree with the deterministic exit code (2026-10-10 contradiction sweep).

Each check pins one contradiction that shipped before: the prompt said one thing while the
code (cores/oneil_fallback.py, tools/trend_exit_seller.py, prism_core/sell_regime_context.py, add_plan prompts)\ndid another.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sell_instructions():
    from cores.agents import trading_agents as kr
    us = _load("prism-us/cores/agents/trading_agents.py", "us_trading_agents_consistency")
    out = {}
    for market, make in (("KR", kr.create_sell_decision_agent), ("US", us.create_us_sell_decision_agent)):
        for lang in ("ko", "en"):
            out[f"{market}-{lang}"] = make(language=lang).instruction
    return out


@pytest.fixture(scope="module")
def prompts():
    return _sell_instructions()


def test_trailing_close_confirmed_but_raised_stop_runs_intraday(prompts):
    # tools/trend_exit_seller.py confirms TIER2 trailing in its pre-close window (KR 15:10~15:25, US 15:50~15:59 ET); a stop the AI raises via
    # portfolio_adjustment is executed intraday by the hard stop (x 0.995). The prompt must say both and
    # must not tell the AI to sell on an intraday-only trailing breach.
    for key, text in prompts.items():
        assert ("장 마감 직전 자동 점검" in text) or ("pre-close automatic check" in text), key
        # the AI sell run is before the close (KR 14:46, US 14:30 ET): it never has today's close
        assert ("당일 종가는 아직 없습니다" in text) or ("today's close does not exist yet" in text), key
        assert ("장중 하드스탑이 그대로 실행" in text) or ("executed by the intraday hard stop above" in text), key
        assert "trailing stop > 현재가이면 should_sell" not in text, key
        assert "If trailing stop > current price, set should_sell" not in text, key


def test_no_grace_exception_to_the_automatic_stop(prompts):
    # The -7% / stop_loss exits are automatic intraday; a one-day grace rule can never apply.
    for key, text in prompts.items():
        assert "7.1%" not in text, key
        assert "유일한 예외" not in text and "ONLY exception" not in text, key


def test_sells_all_or_nothing_but_micro_split_adds_exist(prompts):
    # Micro-split holdings get an add-plan block in the same prompt; "no split trading" contradicted it.
    for key, text in prompts.items():
        assert "분할매매가 불가능" not in text and "does NOT support split trading" not in text, key
        assert ("초분할" in text) or ("micro-split" in text), key


def test_step0_defers_to_the_system_regime(prompts):
    for key, text in prompts.items():
        assert ("시스템이 계산한 시장 국면" in text) or ("system-computed market regime" in text), key


def test_trailing_bands_match_the_exit_code(prompts):
    from cores.oneil_fallback import TRAIL_DROP_BULL, TRAIL_DROP_WEAK
    for key, text in prompts.items():
        assert f"× {TRAIL_DROP_BULL:.2f}" in text and f"× {TRAIL_DROP_WEAK:.2f}" in text, key


@pytest.mark.parametrize("path,needles", [
    ("stock_tracking_enhanced_agent.py", ("진입일 봉 고가는 매수 전에 찍혔을 수 있어 넣지 않", "Leave out the entry-day high")),
    ("prism-us/us_stock_tracking_agent.py", ("Leave out the entry-day high",)),
])
def test_first_tracking_peak_excludes_the_pre_buy_entry_day_high(path, needles):
    # 2026-10-13 RF머트리얼즈: bought 14:00 at 50,000, the AI took the entry-day high 52,500 (pre-buy) as the
    # post-entry peak, switched the trailing stop on and sold 70 minutes later. The stored peak was 50,000.
    src = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in src
    assert "verify actual peak since entry" not in src and "진입일 이후 실제 최고가" not in src


def test_trailing_bands_are_the_exact_code_values(prompts):
    # 2026-10-19 audit: the prompts gave ranges (-3~5%, -8~10%) while the code trails at fixed x0.95 / x0.92,
    # and a -3% stop the AI raises runs as an intraday hard stop. With no live regime the code trails at x0.90.
    from cores.oneil_fallback import TRAIL_DROP_UNKNOWN
    for key, text in prompts.items():
        assert "-3~5%" not in text and "-8~10%" not in text, key
        assert f"× {TRAIL_DROP_UNKNOWN:.2f}" in text, key


def test_stop_raise_rules_match_the_code_in_every_prompt(prompts):
    # Ratchet + dynamic threshold (stock_tracking_enhanced_agent.py: max(1.5, min(5.0, gain x 0.3))), in ko AND en.
    for key, text in prompts.items():
        assert "1.5~5%" in text, key
        assert "기본 3%" not in text and "default 3%" not in text, key
        assert "🔒" in text, key
        assert ("이 값으로만 계산" in text) or ("from this value only" in text), key


def test_system_50_day_exit_is_named_and_reentry_is_not_a_reason(prompts):
    # TIER1.5_MA50 (losing position below the 50-day MA) is run by tools/trend_exit_seller.py, not the AI.
    # Re-entry v3 only watches stop-outs, so "re-entry cost" is not a reason to hold (user decision 10/19).
    for key, text in prompts.items():
        assert ("50일선×0.995" in text) or ("50-day MA × 0.995" in text), key
        # tools/trend_exit_seller.py ma50_exit_armed: bought below the line -> exempt until a close above it (10/29).
        assert ("50일선 아래에서 매수한 종목은" in text) or ("bought below its 50-day MA is exempt" in text), key
        assert "재진입 비용" not in text and "re-entry cost" not in text, key
        assert ("20일 평균 이상 거래량" in text) or ("volume above its 20-day average" in text), key


def test_regime_blocks_carry_the_code_band():
    from cores.oneil_fallback import TRAIL_DROP_BULL, TRAIL_DROP_UNKNOWN, TRAIL_DROP_WEAK
    from prism_core.sell_regime_context import kr_live_regime_block, trailing_band_line
    for lang in ("ko", "en"):
        assert f"× {TRAIL_DROP_UNKNOWN:.2f}" in kr_live_regime_block(None, language=lang)
        assert f"× {TRAIL_DROP_BULL:.2f}" in trailing_band_line("strong_bull", lang)
        assert f"× {TRAIL_DROP_WEAK:.2f}" in trailing_band_line("sideways", lang)
    src = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text(encoding="utf-8")
    assert "trailing_band_line(" in src


@pytest.mark.parametrize("path", ["stock_tracking_enhanced_agent.py", "prism-us/us_stock_tracking_agent.py"])
def test_pre_ai_stop_check_uses_the_hard_stop_rule(path):
    # The batch pre-check sold at stop_loss with no 0.5% buffer while the prompt and hard stop use x 0.995.
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "evaluate_tier1_hardstop(SellInputs(" in src
    assert "if stop_loss > 0 and current_price <= stop_loss:\n                loss_pct" not in src
    assert "Mechanical stop-loss triggered" in src or "기계적 손절 조건 도달" in src


def test_kr_en_user_message_has_the_threshold_line():
    src = (ROOT / "stock_tracking_enhanced_agent.py").read_text(encoding="utf-8")
    assert "- Trailing Stop Adjustment Threshold: {trailing_stop_threshold_pct:.1f}%" in src


def test_runner_block_drops_the_removed_target_exit_rule():
    src = (ROOT / "prism_core/runner_hold.py").read_text(encoding="utf-8")
    assert "도달 즉시 전량 매도" not in src and "'exit at the target'" not in src


def test_kr_ai_fallback_uses_oneil_rules_not_target_or_time_sells():
    # 2026-10-19 audit F11: on an empty/unparsable AI reply the KR fallback sold on target, +10% and 15/30/60/90-day
    # holds. It must use the same O'Neil rules as the base tracker (target is a hold, trailing protects).
    import asyncio
    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent
    agent = object.__new__(EnhancedStockTrackingAgent)
    agent._live_regime_cache = "strong_bull"
    base = {"ticker": "005930", "buy_price": 100.0, "buy_date": "2026-06-01 10:00:00", "target_price": 110.0,
            "stop_loss": 93.0, "scenario": '{"investment_period": "Short-term", "highest_price": 120.0}'}
    sell, reason = asyncio.run(agent._fallback_sell_decision({**base, "current_price": 115.0}))
    assert sell is False and reason.startswith("HOLD"), reason   # target hit, +15%, 140 days: hold
    sell, reason = asyncio.run(agent._fallback_sell_decision({**base, "current_price": 109.0}))
    assert sell is True and reason.startswith("TIER2_TRAIL"), reason  # 120 x 0.92 x 0.995 = 109.85


def test_no_opportunity_cost_time_sell(prompts):
    # User decision 10/19 (audit F3-1, US #17): "poor performance after a long hold -> sell on opportunity cost"
    # contradicted "time is not a sell trigger"; US had already dropped it.
    for key, text in prompts.items():
        assert "기회비용 관점에서 전량 매도" not in text and "opportunity cost view" not in text, key


def test_no_discretionary_sharp_decline_or_market_shock_sell(prompts):
    # User decision 10/19 (audit F3-2): never the deciding reason in 62 replayed or 18 logged AI sells;
    # sells fire only via the stop / trailing / trend-weakness tiers.
    for key, text in prompts.items():
        assert "-5% 이상" not in text and "(-5%+)" not in text, key
        assert "방어적 전량 매도" not in text and "defensive full exit" not in text, key


def test_prompts_defer_to_the_system_computed_facts(prompts):
    # prism_core/sell_facts.py states the trailing line, raise eligibility and tier-3 conditions (2026-10-19).
    for key, text in prompts.items():
        assert ("'시스템 계산 사실' 블록" in text) or ("'System-computed facts' block" in text), key
