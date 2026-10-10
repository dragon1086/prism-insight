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
    # tools/trend_exit_seller.py confirms TIER2 trailing at the session close; a stop the AI raises via
    # portfolio_adjustment is executed intraday by the hard stop (x 0.995). The prompt must say both and
    # must not tell the AI to sell on an intraday-only trailing breach.
    for key, text in prompts.items():
        assert ("종가(closing price)** 확인" in text) or ("confirmed on the **closing price**" in text), key
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
