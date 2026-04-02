import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cores.agents.trading_agents import create_trading_scenario_agent


def test_kr_trading_prompt_sideways_rules_ko():
    agent = create_trading_scenario_agent(language="ko")

    assert "6점 + 모멘텀 + 추가 확인 1개 → **진입**" in agent.instruction
    assert "횡보장에서는 명확한 부정 요소가 없다는 이유만으로 진입하지 않습니다" in agent.instruction
    assert "거래량 급증만으로 진입을 정당화하지 말 것" in agent.instruction


def test_kr_trading_prompt_sideways_rules_en():
    agent = create_trading_scenario_agent(language="en")

    assert "6 points + momentum + 1 additional confirmation → **Entry**" in agent.instruction
    assert "In sideways markets, lack of a negative factor alone is NOT enough for entry" in agent.instruction
    assert "volume surge alone is not enough for entry" in agent.instruction
