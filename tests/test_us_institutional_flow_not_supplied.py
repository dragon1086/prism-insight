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
    # 2026-11-11 prompt diet (audit X1): the three placeholders are shortened; Step 3 item 2, Step 4 and the
    # compound reason still say the data is absent and never scores/fires.
    text = _buy("US", language)
    if language == "ko":
        assert text.count("자료가 없어 평가하지 않음") == 2 and text.count("기관 자료가 없어 미국은 발동하지 않음") == 1
        assert "'기관 순매수 미확인'처럼 쓰지 마십시오" in text
    else:
        assert text.count("not supplied for US stocks") == 2 and text.count("this never fires for US stocks") == 1
        assert "Do not mention it" in text


@pytest.mark.parametrize("language", ["ko", "en"])
def test_kr_prompt_keeps_its_institutional_conditions(language):
    text = _buy("KR", language)
    assert "일별 기관 순매매 자료가 제공되지 않아" not in text
    assert "Daily institutional flow is not supplied" not in text
