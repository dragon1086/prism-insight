"""US has no daily institutional flow; the three KR-derived conditions must say so.

September 2026: 13 of 72 US BUY rationales wrote "institutional buying unconfirmed"
(KR: 2 of 77). The conditions never scored or fired in the US, so the prompt now
states the data is absent and must not be cited. Decisions are unchanged.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _buy(market, language):
    path = ROOT / ("prism-us" if market == "US" else "") / "cores/agents/trading_agents.py"
    spec = importlib.util.spec_from_file_location(f"inst_{market}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = "create_us_" if market == "US" else "create_"
    return getattr(module, prefix + "trading_scenario_agent")(language=language).instruction


@pytest.mark.parametrize("language", ["ko", "en"])
def test_us_buy_prompt_marks_all_three_institutional_conditions_as_not_supplied(language):
    text = _buy("US", language)
    note = "일별 기관 순매매 자료가 제공되지 않아" if language == "ko" else "Daily institutional flow is not supplied"
    never = "이 조건은 발동하지 않습니다" if language == "ko" else "this condition never fires"
    assert text.count(note) == 2 and text.count(never) == 1
    assert ("언급하지 마십시오" if language == "ko" else "Do not mention it") in text


@pytest.mark.parametrize("language", ["ko", "en"])
def test_kr_prompt_keeps_its_institutional_conditions(language):
    text = _buy("KR", language)
    assert "일별 기관 순매매 자료가 제공되지 않아" not in text
    assert "Daily institutional flow is not supplied" not in text
