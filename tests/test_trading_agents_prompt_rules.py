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


# --- Reverse DCF + Tagging cherry-pick (feat/reverse-dcf-and-tagging) -------

def test_kr_reverse_dcf_section_ko():
    agent = create_trading_scenario_agent(language="ko")

    assert "## 리버스 DCF 점검 (필수)" in agent.instruction
    # KR equity uses 9% discount rate
    assert "할인율 9% 가정" in agent.instruction
    # All four verdict bands must be defined
    for band in ('"reasonable"', '"stretched"', '"unrealistic"', '"insufficient_data"'):
        assert band in agent.instruction, f"missing verdict band: {band}"


def test_kr_reverse_dcf_section_en():
    agent = create_trading_scenario_agent(language="en")

    assert "## Reverse DCF Sanity Check (Mandatory)" in agent.instruction
    assert "discount rate = 9% (KR equity)" in agent.instruction
    for band in ('"reasonable"', '"stretched"', '"unrealistic"', '"insufficient_data"'):
        assert band in agent.instruction


def test_kr_tagging_section_ko():
    agent = create_trading_scenario_agent(language="ko")

    assert "## 데이터 출처 태깅 (필수)" in agent.instruction
    for tag in ("[actual]", "[inference]", "[assumption]", "[unavailable]"):
        assert tag in agent.instruction, f"missing tag: {tag}"
    assert 'too_many_assumptions' in agent.instruction
    # The four tagged fields must be named together so future edits don't drift
    assert "valuation_analysis, sector_outlook, rationale, rejection_reason" in agent.instruction


def test_kr_tagging_section_en():
    agent = create_trading_scenario_agent(language="en")

    assert "## Data Provenance Tagging (Mandatory)" in agent.instruction
    for tag in ("[actual]", "[inference]", "[assumption]", "[unavailable]"):
        assert tag in agent.instruction
    assert 'too_many_assumptions' in agent.instruction
    assert "valuation_analysis, sector_outlook, rationale, and rejection_reason" in agent.instruction


def test_kr_json_schema_has_new_keys_ko():
    agent = create_trading_scenario_agent(language="ko")

    # New top-level JSON keys must be in the schema example
    assert '"reverse_dcf":' in agent.instruction
    assert '"data_quality_check":' in agent.instruction
    # Sub-fields the downstream consumer may rely on
    assert '"verdict":' in agent.instruction
    assert '"flag":' in agent.instruction


def test_kr_json_schema_has_new_keys_en():
    agent = create_trading_scenario_agent(language="en")

    assert '"reverse_dcf":' in agent.instruction
    assert '"data_quality_check":' in agent.instruction
    assert '"verdict":' in agent.instruction
    assert '"flag":' in agent.instruction


def test_kr_bull_rejection_extends_with_dcf_and_tagging_ko():
    agent = create_trading_scenario_agent(language="ko")

    # Bull-market No-Entry justifications now include valuation/data-quality outs
    assert '리버스 DCF 결과 "unrealistic"' in agent.instruction
    assert 'data_quality_check.flag = "too_many_assumptions"' in agent.instruction
    # And those outs are scoped under the existing rejection-list section header
    assert "허용되는 밸류에이션/데이터 품질 기반 미진입 표현" in agent.instruction


def test_kr_bull_rejection_extends_with_dcf_and_tagging_en():
    agent = create_trading_scenario_agent(language="en")

    assert 'Reverse DCF verdict = "unrealistic"' in agent.instruction
    assert 'data_quality_check.flag = "too_many_assumptions"' in agent.instruction
    assert "Permitted valuation/data-quality No Entry expressions" in agent.instruction
