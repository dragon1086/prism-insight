import importlib.util
from pathlib import Path


# Load prism-us trading_agents.py directly to avoid sys.path collision with KR cores/agents/trading_agents.py
_THIS_DIR = Path(__file__).resolve().parent
_TRADING_PATH = _THIS_DIR.parent / "cores" / "agents" / "trading_agents.py"
_spec = importlib.util.spec_from_file_location("us_trading_agents_under_test", _TRADING_PATH)
_us_trading = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_us_trading)

create_us_trading_scenario_agent = _us_trading.create_us_trading_scenario_agent


def test_us_trading_prompt_sideways_rules_ko():
    agent = create_us_trading_scenario_agent(language="ko")

    assert "6점 + 모멘텀 + 추가 확인 1개 → **진입**" in agent.instruction
    assert "횡보장에서는 명확한 부정 요소가 없다는 이유만으로 진입하지 않습니다" in agent.instruction
    assert "거래량 급증만으로 진입을 정당화하지 말 것" in agent.instruction


def test_us_trading_prompt_sideways_rules_en():
    agent = create_us_trading_scenario_agent(language="en")

    assert "6 points + momentum + 1 additional confirmation → **Entry**" in agent.instruction
    assert "In sideways markets, lack of a negative factor alone is NOT enough for entry" in agent.instruction
    assert "volume surge alone is not enough for entry" in agent.instruction


# --- Reverse DCF + Tagging cherry-pick (feat/reverse-dcf-and-tagging) -------

def test_us_reverse_dcf_section_ko():
    agent = create_us_trading_scenario_agent(language="ko")

    assert "## 리버스 DCF 점검 (필수)" in agent.instruction
    # US equity uses 8% discount rate (vs 9% KR)
    assert "할인율 8% 가정" in agent.instruction
    for band in ('"reasonable"', '"stretched"', '"unrealistic"', '"insufficient_data"'):
        assert band in agent.instruction, f"missing verdict band: {band}"


def test_us_reverse_dcf_section_en():
    agent = create_us_trading_scenario_agent(language="en")

    assert "## Reverse DCF Sanity Check (Mandatory)" in agent.instruction
    assert "discount rate = 8% (US equity)" in agent.instruction
    for band in ('"reasonable"', '"stretched"', '"unrealistic"', '"insufficient_data"'):
        assert band in agent.instruction


def test_us_tagging_section_ko():
    agent = create_us_trading_scenario_agent(language="ko")

    assert "## 데이터 출처 태깅 (필수)" in agent.instruction
    for tag in ("[actual]", "[inference]", "[assumption]", "[unavailable]"):
        assert tag in agent.instruction, f"missing tag: {tag}"
    assert 'too_many_assumptions' in agent.instruction
    assert "valuation_analysis, sector_outlook, rationale, rejection_reason" in agent.instruction


def test_us_tagging_section_en():
    agent = create_us_trading_scenario_agent(language="en")

    assert "## Data Provenance Tagging (Mandatory)" in agent.instruction
    for tag in ("[actual]", "[inference]", "[assumption]", "[unavailable]"):
        assert tag in agent.instruction
    assert 'too_many_assumptions' in agent.instruction
    assert "valuation_analysis, sector_outlook, rationale, and rejection_reason" in agent.instruction


def test_us_json_schema_has_new_keys_ko():
    agent = create_us_trading_scenario_agent(language="ko")

    assert '"reverse_dcf":' in agent.instruction
    assert '"data_quality_check":' in agent.instruction
    assert '"verdict":' in agent.instruction
    assert '"flag":' in agent.instruction


def test_us_json_schema_has_new_keys_en():
    agent = create_us_trading_scenario_agent(language="en")

    assert '"reverse_dcf":' in agent.instruction
    assert '"data_quality_check":' in agent.instruction
    assert '"verdict":' in agent.instruction
    assert '"flag":' in agent.instruction


def test_us_bull_rejection_extends_with_dcf_and_tagging_ko():
    agent = create_us_trading_scenario_agent(language="ko")

    assert '리버스 DCF 결과 "unrealistic"' in agent.instruction
    assert 'data_quality_check.flag = "too_many_assumptions"' in agent.instruction
    assert "허용되는 밸류에이션/데이터 품질 기반 미진입 표현" in agent.instruction


def test_us_bull_rejection_extends_with_dcf_and_tagging_en():
    agent = create_us_trading_scenario_agent(language="en")

    assert 'Reverse DCF verdict = "unrealistic"' in agent.instruction
    assert 'data_quality_check.flag = "too_many_assumptions"' in agent.instruction
    assert "Permitted valuation/data-quality No Entry expressions" in agent.instruction
