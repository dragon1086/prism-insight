"""Prompt-rule regression tests for the KR trading scenario agent.

These tests lock down the structural invariants of the strategist prompt so
that future edits cannot silently drop the CAN SLIM identity, the fundamental
gate, the market-regime matrix, the No-Entry justification rules, or the
prohibited expressions list.

Tests assert *prompt content*, not LLM output — they catch unintended prompt
drift, not modeling regressions.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cores.agents.trading_agents import create_trading_scenario_agent


# --- Identity & framework -------------------------------------------------

def test_identity_is_can_slim_only_ko():
    agent = create_trading_scenario_agent(language="ko")
    # CAN SLIM identity is the single source of truth — no "value investing" hedge
    assert "CAN SLIM 시스템 창시자" in agent.instruction
    assert "윌리엄 오닐" in agent.instruction
    # Must NOT carry the legacy "primarily value investing" framing
    assert "가치투자 원칙을 따르되" not in agent.instruction


def test_identity_is_can_slim_only_en():
    agent = create_trading_scenario_agent(language="en")
    assert "creator of the CAN SLIM system" in agent.instruction
    assert "NOT value-investing" in agent.instruction


def test_can_slim_framework_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    # All seven CAN SLIM elements must be referenced
    for element in ("C — 분기 실적", "A — 연간 실적", "N — New", "S — 수급",
                    "L — 리더", "I — 기관 매수", "M — 시장 추세"):
        assert element in agent.instruction, f"missing CAN SLIM element: {element}"


def test_can_slim_framework_present_en():
    agent = create_trading_scenario_agent(language="en")
    for element in ("C — Current quarter", "A — Annual earnings", "N — New",
                    "S — Supply/Demand", "L — Leader", "I — Institutional sponsorship",
                    "M — Market direction"):
        assert element in agent.instruction


# --- Fundamental gate -----------------------------------------------------

def test_fundamental_gate_four_checks_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "## 1단계 — 펀더멘털 게이트 (필수)" in agent.instruction
    for check in ("F1 수익성", "F2 재무 건전성", "F3 성장성", "F4 사업 명확성"):
        assert check in agent.instruction, f"missing fundamental check: {check}"


def test_fundamental_gate_four_checks_en():
    agent = create_trading_scenario_agent(language="en")
    assert "Step 1 — Fundamental Gate (mandatory)" in agent.instruction
    for check in ("F1 Profitability", "F2 Balance sheet", "F3 Growth", "F4 Business clarity"):
        assert check in agent.instruction


# --- Market-regime matrix (single source of truth) ------------------------

def test_market_regime_matrix_values_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "## 2단계 — 시장 체제별 진입 매트릭스" in agent.instruction
    # Each row must appear with exact min_score and R/R floor
    for row in (
        "| strong_bull   | 4 | 1.0 | -7% | 1개+ | 0 |",
        "| moderate_bull | 4 | 1.2 | -7% | 1개+ | 0 |",
        "| sideways      | 5 | 1.3 | -6% | 1개+ | 0 |",
        "| moderate_bear | 5 | 1.5 | -5% | 2개+ | 1 |",
        "| strong_bear   | 6 | 1.8 | -5% | 2개+ | 1 |",
    ):
        assert row in agent.instruction, f"missing matrix row: {row}"


def test_market_regime_matrix_values_en():
    agent = create_trading_scenario_agent(language="en")
    for row in (
        "| strong_bull   | 4 | 1.0 | -7% | 1+ | 0 |",
        "| moderate_bull | 4 | 1.2 | -7% | 1+ | 0 |",
        "| sideways      | 5 | 1.3 | -6% | 1+ | 0 |",
        "| moderate_bear | 5 | 1.5 | -5% | 2+ | 1 |",
        "| strong_bear   | 6 | 1.8 | -5% | 2+ | 1 |",
    ):
        assert row in agent.instruction, f"missing matrix row: {row}"


def test_min_score_schema_matches_matrix_ko():
    agent = create_trading_scenario_agent(language="ko")
    # The JSON schema's min_score line must match the matrix exactly
    assert "strong_bull:4, moderate_bull:4, sideways:5, moderate_bear:5, strong_bear:6" in agent.instruction


def test_min_score_schema_matches_matrix_en():
    agent = create_trading_scenario_agent(language="en")
    assert "strong_bull:4, moderate_bull:4, sideways:5, moderate_bear:5, strong_bear:6" in agent.instruction


# --- No-Entry justification ----------------------------------------------

def test_no_entry_standalone_reasons_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "**단독 사유 (한 가지만 충족해도 미진입):**" in agent.instruction
    # 4 standalone reasons + 1 compound; key phrases must be present
    # 2026-11-11 prompt diet (audit M1, user decision): the "-10% support" reason is gone — the stop rule
    # (tighter of matrix max stop / support) always yields a usable stop, so its premise was false.
    assert "손절 지지선이 -10% 이하" not in agent.instruction
    assert "PER ≥ 업종 평균 2.5배" in agent.instruction
    assert "펀더 게이트 미달 + 시장 체제가 sideways/bear" in agent.instruction
    assert 'severity = "high"' in agent.instruction
    assert "effective_score < min_score (초분할 진입 기준 부록이 있으면 그 최소 점수, 없으면 매트릭스의 현재 regime 값)" in agent.instruction


def test_no_entry_standalone_reasons_en():
    agent = create_trading_scenario_agent(language="en")
    assert "**Standalone (any one is sufficient):**" in agent.instruction
    assert "Stop loss support is at -10% or worse" not in agent.instruction  # 2026-11-11 prompt diet (M1)
    assert "PER ≥ 2.5× industry average" in agent.instruction
    assert "Fundamental Gate fail in sideways / bear regime" in agent.instruction
    assert 'severity risk event' in agent.instruction
    assert "effective_score < min_score" in agent.instruction


def test_prohibited_expressions_ko():
    agent = create_trading_scenario_agent(language="ko")
    # The prompt must explicitly forbid vague-hedge expressions as standalone reasons
    for forbidden in ("과열 우려", "변곡 신호", "추가 확인 필요",
                      "단기 조정 가능성", "관망이 안전"):
        assert forbidden in agent.instruction, f"prohibition missing for: {forbidden}"


def test_prohibited_expressions_en():
    agent = create_trading_scenario_agent(language="en")
    for forbidden in ("overheating concern", "inflection signal",
                      "needs more confirmation", "short-term correction risk",
                      "wait and see is safer"):
        assert forbidden in agent.instruction


# --- Decision rule and macro adjustment -----------------------------------

def test_decision_rule_uses_effective_score_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "effective_score ≥ min_score" in agent.instruction
    # Macro adjustment must be a separate field, not folded into buy_score
    assert "buy_score에 직접 합산하지 마십시오" in agent.instruction
    assert "effective_score = buy_score + macro_adjustment" in agent.instruction


def test_decision_rule_uses_effective_score_en():
    agent = create_trading_scenario_agent(language="en")
    assert "effective_score ≥ min_score" in agent.instruction
    assert "NOT folded into buy_score" in agent.instruction
    assert "effective_score = buy_score + macro_adjustment" in agent.instruction


# --- JSON schema must include the new gates -------------------------------

def test_json_schema_has_required_keys_ko():
    agent = create_trading_scenario_agent(language="ko")
    for key in ('"fundamental_check":', '"buy_score":', '"macro_adjustment":',
                '"effective_score":', '"min_score":', '"momentum_signal_count":',
                '"additional_confirmation_count":', '"decision":'):
        assert key in agent.instruction, f"missing schema key: {key}"


def test_json_schema_has_required_keys_en():
    agent = create_trading_scenario_agent(language="en")
    for key in ('"fundamental_check":', '"buy_score":', '"macro_adjustment":',
                '"effective_score":', '"min_score":', '"momentum_signal_count":',
                '"additional_confirmation_count":', '"decision":'):
        assert key in agent.instruction


# --- Sector constraint substitution still works ---------------------------

def test_sector_constraint_applied_ko():
    agent = create_trading_scenario_agent(language="ko",
                                           sector_names=["반도체", "바이오"])
    assert "반도체, 바이오" in agent.instruction
    # The unreplaced placeholder must NOT remain
    assert "{sector_constraint}" not in agent.instruction


def test_sector_constraint_applied_en():
    agent = create_trading_scenario_agent(language="en",
                                           sector_names=["Semiconductors", "Biotech"])
    assert "Semiconductors, Biotech" in agent.instruction
    assert "{sector_constraint}" not in agent.instruction


# --- Restored content: trigger detail / portfolio guide / perplexity / R/R / checklist ---

def test_macro_sector_leader_detail_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "**매크로 섹터 리더 트리거 분석 포인트:**" in agent.instruction
    assert "단기 모멘텀이 약해도 섹터 순풍에 의한 중기 상승 가능성" in agent.instruction


def test_macro_sector_leader_detail_present_en():
    agent = create_trading_scenario_agent(language="en")
    assert "**Macro Sector Leader trigger — analysis points:**" in agent.instruction
    assert "weigh the medium-term tailwind from the sector" in agent.instruction


# 2026-11-11 prompt diet (audit D13): the contrarian "analysis points" block restated the trigger line,
# so only the trigger line remains and it must still carry the temporary-vs-structural rule.
def test_contrarian_value_detail_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "**역발상 가치주 트리거 분석 포인트:**" not in agent.instruction
    assert "구조적(실적 악화/경쟁력 상실)이면 미진입" in agent.instruction


def test_contrarian_value_detail_present_en():
    agent = create_trading_scenario_agent(language="en")
    assert "**Contrarian Value Stock trigger — analysis points:**" not in agent.instruction
    assert "earnings deterioration / loss of competitive edge" in agent.instruction


# 2026-11-11 prompt diet (audit X6): the portfolio analysis guide duplicated the per-report user message
# (holdings count, slot usage, sector and period distribution), so it is gone from the shared instruction.
def test_portfolio_analysis_guide_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "## 포트폴리오 분석 가이드" not in agent.instruction
    assert "## 포트폴리오 제약" in agent.instruction
    assert "보유 종목 7개 이상" not in agent.instruction  # M7 (user decision): no LLM-only 7-holdings rule


def test_portfolio_analysis_guide_present_en():
    agent = create_trading_scenario_agent(language="en")
    assert "## Portfolio Analysis Guide" not in agent.instruction
    assert "## Portfolio Constraints" in agent.instruction
    assert "7+ holdings" not in agent.instruction


def test_perplexity_detail_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "동종업계 주요 경쟁사 비교" in agent.instruction
    assert "현재 날짜를 포함" in agent.instruction


def test_perplexity_detail_present_en():
    agent = create_trading_scenario_agent(language="en")
    assert "major peer competitors valuation comparison" in agent.instruction
    assert "Include the current date" in agent.instruction


def test_rr_formula_present_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "## 손익비 계산식 (참고)" in agent.instruction
    assert "expected_return_pct = (target_price - current_price)" in agent.instruction


def test_rr_formula_present_en():
    agent = create_trading_scenario_agent(language="en")
    assert "## R/R Calculation (reference)" in agent.instruction
    assert "expected_return_pct = (target_price - current_price)" in agent.instruction


def test_entry_checklist_passed_in_schema_ko():
    agent = create_trading_scenario_agent(language="ko")
    # Dashboard depends on this field — must remain in schema
    assert '"entry_checklist_passed":' in agent.instruction


def test_entry_checklist_passed_in_schema_en():
    agent = create_trading_scenario_agent(language="en")
    assert '"entry_checklist_passed":' in agent.instruction


# --- Anti-loophole: "ambiguous → No Entry" loophole removed ---------------

def test_ambiguous_setup_no_longer_auto_no_entry_ko():
    agent = create_trading_scenario_agent(language="ko")
    # The legacy "If unclear, choose No Entry" escape valve must be replaced by
    # the "name the specific uncertainty and still pick" rule
    assert "어떤 부분이 불확실한지 rationale에" in agent.instruction
    assert '"막연한 우려"는 미진입 사유로 인정되지 않습니다' in agent.instruction


def test_ambiguous_setup_no_longer_auto_no_entry_en():
    agent = create_trading_scenario_agent(language="en")
    assert "name the *specific* uncertainty in the rationale" in agent.instruction
    assert '"Vague concern" is not allowed as a No Entry reason' in agent.instruction


# --- v2.12.0: Parabolic regime + target_price fallback + DD kill switch ---

def test_parabolic_regime_row_in_matrix_ko():
    agent = create_trading_scenario_agent(language="ko")
    # v2.10.0: parabolic stop loosened to -7% (O'Neil rule) and momentum to 1+ for active buying
    assert "| parabolic     | 4 | 0.7 | -7% | 1개+ | 0 |" in agent.instruction


def test_parabolic_regime_row_in_matrix_en():
    agent = create_trading_scenario_agent(language="en")
    assert "| parabolic     | 4 | 0.7 | -7% | 1+ | 0 |" in agent.instruction


# 2026-10-19: the LLM no longer self-selects parabolic from KOSPI 90/30-day returns (inputs it never
# receives). The computed regime never outputs parabolic (cores/data_prefetch.py), the scenario regime is
# overwritten by the computed one (cores/regime_policy.py stamp_scenario_market_regime) and the buy gate
# uses the computed value, so the prompt says: copy the system value, never pick parabolic yourself.
def test_parabolic_is_copied_from_the_system_regime_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "KOSPI 90일 수익률" not in agent.instruction and "KOSPI 30일 수익률" not in agent.instruction
    assert "parabolic 행 적용 조건" not in agent.instruction
    assert "현재 시스템 국면 계산은 parabolic을 내지 않으므로 직접 parabolic을 고르지 마십시오" in agent.instruction


def test_parabolic_is_copied_from_the_system_regime_en():
    agent = create_trading_scenario_agent(language="en")
    assert "KOSPI 90-day return" not in agent.instruction and "KOSPI 30-day return" not in agent.instruction
    assert "Parabolic regime activation" not in agent.instruction
    assert "never select parabolic yourself" in agent.instruction


# 2026-10-19: distribution days come from the Market Pulse facts line the buy gate reads (cores/buy_gate.py
# _DIST_RE, cores/market_pulse.py), not index_summary (never sent to BUY) or a report "4-week" fallback.
def test_distribution_day_kill_switch_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "Distribution Day Kill Switch" in agent.instruction
    assert "분포일" in agent.instruction
    assert "Market Pulse … 분산일(distribution days" in agent.instruction
    assert "index_summary" not in agent.instruction and "4주 내" not in agent.instruction
    # Demotion mapping must be explicit
    assert "parabolic → strong_bull" in agent.instruction


def test_distribution_day_kill_switch_en():
    agent = create_trading_scenario_agent(language="en")
    assert "Distribution Day Kill Switch" in agent.instruction
    assert "distribution days" in agent.instruction
    assert "Market Pulse … 분산일(distribution days" in agent.instruction
    assert "index_summary" not in agent.instruction
    assert "parabolic → strong_bull" in agent.instruction


def test_target_price_fallback_rule_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "손익비 계산 전에 근거와 보유 기간" in agent.instruction
    assert "임의 상승률로 목표를 만들거나" in agent.instruction
    assert "15~30%" not in agent.instruction


def test_target_price_fallback_rule_en():
    agent = create_trading_scenario_agent(language="en")
    assert "holding horizon and exit model BEFORE R/R" in agent.instruction
    assert "Never invent a percentage target" in agent.instruction
    assert "15~30%" not in agent.instruction


# 2026-10-19: the parabolic sizing section is removed with the self-selected parabolic row; sizing is
# set by the system (constraint 4: per-account slot, micro-split, rebound pilot half slot).
def test_parabolic_position_sizing_section_removed_ko():
    agent = create_trading_scenario_agent(language="ko")
    assert "parabolic 포지션 운영" not in agent.instruction
    assert "비중은 시스템이 정합니다" in agent.instruction


def test_parabolic_position_sizing_section_removed_en():
    agent = create_trading_scenario_agent(language="en")
    assert "Parabolic position management" not in agent.instruction
    assert "The system sets the size" in agent.instruction


def test_parabolic_min_score_in_schema_ko():
    agent = create_trading_scenario_agent(language="ko")
    # JSON schema must list parabolic:4 alongside the existing regimes
    assert "parabolic:4" in agent.instruction


def test_parabolic_min_score_in_schema_en():
    agent = create_trading_scenario_agent(language="en")
    assert "parabolic:4" in agent.instruction
