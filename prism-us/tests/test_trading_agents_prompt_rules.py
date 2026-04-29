"""Prompt-rule regression tests for the US trading scenario agent.

Mirror of tests/test_trading_agents_prompt_rules.py but for the US strategist.
"""

from cores.agents.trading_agents import create_us_trading_scenario_agent


# --- Identity & framework -------------------------------------------------

def test_us_identity_is_can_slim_only_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "CAN SLIM 시스템 창시자" in agent.instruction
    assert "윌리엄 오닐" in agent.instruction
    assert "가치투자 원칙을 따르되" not in agent.instruction


def test_us_identity_is_can_slim_only_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "creator of the CAN SLIM system" in agent.instruction
    assert "NOT value-investing" in agent.instruction


def test_us_can_slim_framework_present_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    for element in ("C — 분기 실적", "A — 연간 실적", "N — New", "S — 수급",
                    "L — 리더", "I — 기관 매수", "M — 시장 추세"):
        assert element in agent.instruction


def test_us_can_slim_framework_present_en():
    agent = create_us_trading_scenario_agent(language="en")
    for element in ("C — Current quarter", "A — Annual earnings", "N — New",
                    "S — Supply/Demand", "L — Leader", "I — Institutional sponsorship",
                    "M — Market direction"):
        assert element in agent.instruction


# --- US-specific market regime (S&P 500 + VIX) ----------------------------

def test_us_regime_uses_sp500_vix_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "S&P 500 (^GSPC)" in agent.instruction
    assert "VIX < 18" in agent.instruction
    assert "VIX > 25" in agent.instruction


def test_us_regime_uses_sp500_vix_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "S&P 500 (^GSPC)" in agent.instruction
    assert "VIX < 18" in agent.instruction
    assert "VIX > 25" in agent.instruction


# --- Fundamental gate -----------------------------------------------------

def test_us_fundamental_gate_four_checks_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "## 1단계 — 펀더멘털 게이트 (필수)" in agent.instruction
    for check in ("F1 수익성", "F2 재무 건전성", "F3 성장성", "F4 사업 명확성"):
        assert check in agent.instruction


def test_us_fundamental_gate_four_checks_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "Step 1 — Fundamental Gate (mandatory)" in agent.instruction
    for check in ("F1 Profitability", "F2 Balance sheet", "F3 Growth", "F4 Business clarity"):
        assert check in agent.instruction


# --- Market-regime matrix (must match KR exactly) -------------------------

def test_us_market_regime_matrix_values_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    for row in (
        "| strong_bull   | 4 | 1.0 | -7% | 1개+ | 0 |",
        "| moderate_bull | 4 | 1.2 | -7% | 1개+ | 0 |",
        "| sideways      | 5 | 1.3 | -6% | 1개+ | 0 |",
        "| moderate_bear | 5 | 1.5 | -5% | 2개+ | 1 |",
        "| strong_bear   | 6 | 1.8 | -5% | 2개+ | 1 |",
    ):
        assert row in agent.instruction


def test_us_market_regime_matrix_values_en():
    agent = create_us_trading_scenario_agent(language="en")
    for row in (
        "| strong_bull   | 4 | 1.0 | -7% | 1+ | 0 |",
        "| moderate_bull | 4 | 1.2 | -7% | 1+ | 0 |",
        "| sideways      | 5 | 1.3 | -6% | 1+ | 0 |",
        "| moderate_bear | 5 | 1.5 | -5% | 2+ | 1 |",
        "| strong_bear   | 6 | 1.8 | -5% | 2+ | 1 |",
    ):
        assert row in agent.instruction


def test_us_min_score_schema_matches_matrix_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "strong_bull:4, moderate_bull:4, sideways:5, moderate_bear:5, strong_bear:6" in agent.instruction


def test_us_min_score_schema_matches_matrix_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "strong_bull:4, moderate_bull:4, sideways:5, moderate_bear:5, strong_bear:6" in agent.instruction


# --- No-Entry justification ----------------------------------------------

def test_us_no_entry_standalone_reasons_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "**단독 사유 (한 가지만 충족해도 미진입):**" in agent.instruction
    assert "P/E ≥ 업종 평균 2.5배" in agent.instruction
    assert "펀더 게이트 미달 + 시장 체제가 sideways/bear" in agent.instruction
    assert 'severity = "high"' in agent.instruction


def test_us_no_entry_standalone_reasons_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "**Standalone (any one is sufficient):**" in agent.instruction
    assert "PE ≥ 2.5× industry average" in agent.instruction
    assert "Fundamental Gate fail in sideways / bear regime" in agent.instruction


def test_us_prohibited_expressions_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    for forbidden in ("과열 우려", "변곡 신호", "추가 확인 필요",
                      "단기 조정 가능성", "관망이 안전"):
        assert forbidden in agent.instruction


def test_us_prohibited_expressions_en():
    agent = create_us_trading_scenario_agent(language="en")
    for forbidden in ("overheating concern", "inflection signal",
                      "needs more confirmation", "short-term correction risk",
                      "wait and see is safer"):
        assert forbidden in agent.instruction


# --- Decision rule and macro adjustment -----------------------------------

def test_us_decision_rule_uses_effective_score_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "effective_score ≥ min_score" in agent.instruction
    assert "buy_score에 직접 합산하지 마십시오" in agent.instruction


def test_us_decision_rule_uses_effective_score_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "effective_score ≥ min_score" in agent.instruction
    assert "NOT folded into buy_score" in agent.instruction


# --- JSON schema must include the new gates -------------------------------

def test_us_json_schema_has_required_keys_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    for key in ('"fundamental_check":', '"buy_score":', '"macro_adjustment":',
                '"effective_score":', '"min_score":', '"momentum_signal_count":',
                '"additional_confirmation_count":', '"decision":'):
        assert key in agent.instruction


def test_us_json_schema_has_required_keys_en():
    agent = create_us_trading_scenario_agent(language="en")
    for key in ('"fundamental_check":', '"buy_score":', '"macro_adjustment":',
                '"effective_score":', '"min_score":', '"momentum_signal_count":',
                '"additional_confirmation_count":', '"decision":'):
        assert key in agent.instruction


# --- US uses GICS sectors and yahoo_finance / us_stock_holdings -----------

def test_us_uses_gics_sectors_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "GICS 섹터명" in agent.instruction


def test_us_uses_gics_sectors_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "GICS sector name" in agent.instruction


def test_us_references_yahoo_finance_and_us_holdings_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "yahoo_finance-get_historical_stock_prices" in agent.instruction
    assert "us_stock_holdings" in agent.instruction


def test_us_references_yahoo_finance_and_us_holdings_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "yahoo_finance-get_historical_stock_prices" in agent.instruction
    assert "us_stock_holdings" in agent.instruction


def test_us_sector_constraint_applied_ko():
    agent = create_us_trading_scenario_agent(language="ko",
                                              sector_names=["Technology", "Healthcare"])
    assert "Technology, Healthcare" in agent.instruction
    assert "{sector_constraint}" not in agent.instruction


def test_us_sector_constraint_applied_en():
    agent = create_us_trading_scenario_agent(language="en",
                                              sector_names=["Technology", "Healthcare"])
    assert "Technology, Healthcare" in agent.instruction
    assert "{sector_constraint}" not in agent.instruction


# --- Anti-loophole: "ambiguous → No Entry" loophole removed ---------------

def test_us_ambiguous_setup_no_longer_auto_no_entry_ko():
    agent = create_us_trading_scenario_agent(language="ko")
    assert "어떤 부분이 불확실한지 rationale에" in agent.instruction
    assert '"막연한 우려"는 미진입 사유로 인정되지 않습니다' in agent.instruction


def test_us_ambiguous_setup_no_longer_auto_no_entry_en():
    agent = create_us_trading_scenario_agent(language="en")
    assert "name the *specific* uncertainty in the rationale" in agent.instruction
    assert '"Vague concern" is not allowed as a No Entry reason' in agent.instruction
