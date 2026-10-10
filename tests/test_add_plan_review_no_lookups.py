"""The micro-split review block must not send the sell model on extra lookups (2026-10-10).

US micro-split sell reviews asked for Pulse/distribution days, sector strength and news that were not
in the prompt; the model made 18~26 tool calls instead of ~8 and hit the 180 s Codex limit.
"""
import pytest

from prism_core import add_plan_prompts, micro_split_live

BLOCK = {"allocation": "0.35", "legs": [{"kind": "initial", "allocation": "0.35", "price": 100.0}], "add_plan": None}


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("market", ["KR", "US"])
def test_review_block_hands_over_pulse_and_forbids_extra_lookups(market, language):
    text = add_plan_prompts.review_block(BLOCK, market=market, language=language, valid_for="2026-10-12",
                                         stop_loss=93.0, pulse=("UPTREND", 3, 25))
    if language == "ko":
        assert "시스템 Market Pulse: UPTREND, 분배일 3회(최근 25세션)" in text
        assert "추가로 조회하지 마십시오" in text and "업종 강도와 뉴스" not in text
    else:
        assert "System Market Pulse: UPTREND, distribution days 3 (last 25 sessions)" in text
        assert "Do not run extra DB, price or news lookups" in text and "sector strength and news" not in text


def test_review_block_without_pulse_has_no_pulse_line():
    text = add_plan_prompts.review_block(BLOCK, market="US", language="ko", valid_for="2026-10-12", pulse=None)
    assert "Market Pulse:" not in text.split("반영하십시오")[0].split("현재 증액 계획")[1]


def test_market_pulse_is_fail_open(monkeypatch):
    import sys
    broken = type(sys)("prism_root_regime_policy")
    broken.get_market_pulse_detail = lambda market: (_ for _ in ()).throw(RuntimeError("no network"))
    monkeypatch.setitem(sys.modules, "prism_root_regime_policy", broken)
    assert micro_split_live._market_pulse("US") is None
