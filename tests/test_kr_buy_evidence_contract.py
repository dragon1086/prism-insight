"""KR buy evidence instructions; AST stubs avoid SDK, model, and network calls."""

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "cores/agents/trading_agents.py"


@pytest.fixture(params=["ko", "en"])
def prompt(request):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    from prism_core.sector_names import KR_SECTOR_NAMES
    namespace = {"Agent": SimpleNamespace, "buy_scenario_prompt_contract": lambda _: "", "KR_SECTOR_NAMES": KR_SECTOR_NAMES}
    exec(compile(tree, str(SOURCE), "exec"), namespace)
    return request.param, namespace["create_trading_scenario_agent"](request.param).instruction


def test_missingness_is_provenance_not_invented_gate(prompt):
    _, text = prompt
    for marker in ("NOT_IN_INPUT", "NOT_REQUESTED", "SOURCE_UNAVAILABLE", "INCOMPARABLE"):
        assert marker in text
    assert "UNKNOWN" in text
    assert "fundamental_check" in text and "rationale" in text


def test_whole_input_checked_before_one_material_supplement(prompt):
    language, text = prompt
    for marker in ("quarterly EPS", "annual EPS", "industry_leadership", "price_RS"):
        assert marker in text
    for marker in (("전체 보고서", "주입된 팩트", "이미 반환된 MCP", "통합 질의 최대 1회", "차트 이미지", "연결/별도")
                   if language == "ko" else
                   ("whole report", "injected facts", "already returned MCP", "at most ONE consolidated query", "chart images", "consolidated/separate")):
        assert marker in text


def test_time_alone_cannot_finalize_today_bar(prompt):
    language, text = prompt
    assert "BAR_FINALITY_UNKNOWN" in text
    assert "today's data is settled" not in text
    assert "당일 데이터가 확정됩니다" not in text
    assert ("시각만으로" if language == "ko" else "time alone") in text


def test_decision_rules_and_json_schema_are_byte_preserved(prompt):
    language, text = prompt
    # Reviewed 2026-09-27 decision_inputs appendix is peeled off before the original hashes.
    from prism_core.decision_input_features import prompt_contract, prompt_facts_enabled
    from messaging.korean_trading_message import korean_rationale_style_contract
    # Reviewed 2026-09-28 wording-only Korean rationale style appendix is peeled off first.
    style = korean_rationale_style_contract(language)
    if style:
        assert text.count(style) == 1 and text.endswith(style)
        text = text[:-len(style)]
    appendix = prompt_contract("en" if language == "en" else "ko", market="KR")
    if prompt_facts_enabled():
        assert text.count(appendix) == 1 and text.endswith(appendix)
        text = text[:-len(appendix)]
    assert appendix not in text
    heading = "## 도구 사용" if language == "ko" else "## Tool Usage"
    json_heading = "## JSON 응답 형식" if language == "ko" else "## JSON Response Format"
    # Refreshed stale hashes against 9203306b; volume changes preserve these bytes.
    # 2026-09-27 reviewed O'Neil overhead-free breakout target (2a); JSON schema hash unchanged.
    # 2026-09-29 reviewed: +1 line only (today's open-bar high is not a major resistance); JSON unchanged.
    # 2026-09-30 reviewed: tail hash only — the appended KR flow contract now says to repeat the
    # raw-share caveat only when a corporate action is flagged or unknown. Decision rules and JSON unchanged.
    # 2026-10-01 reviewed: tail hash only — sell_triggers hard stop / -7% lines now describe the actual
    # intraday hard stop (live price <= stop_loss x 0.995, no close wait). Decision rules unchanged.
    # 2026-10-02 reviewed: rules hash only — the F4 table row now passes an identified business model and
    # revenue drivers without sourced structural decline (an unconfirmed edge is not a fail). JSON unchanged.
    # 2026-10-03 reviewed: rules hash only — buy_score rubric aligned with the Step 1.5/1.6 gates (a gated
    # no-entry scores at most 4 with no positive macro bonus; R/R stays out of the score). JSON unchanged.
    # 2026-10-03 reviewed: rules hash only — rubric consistency (zero-momentum 3~4 is a no-entry zone; the
    # Step 1 bull-regime compensation path scores by the bands, capped at 6). JSON unchanged.
    # 2026-10-10 reviewed: rules hash only — Step 1.5 T1/T2 judged at the current price from the facts block
    # (as the final gate does); the exception is the current price back above the 50-day MA. JSON unchanged.
    # 2026-10-10 reviewed: rules + JSON — text aligned with executed rules (distribution-day step-down ladder,
    # min_score source: micro-split appendix else matrix with the system regime floor). No rule change.
    expected = {
        "ko": ("6f47e5079640519e674e710dd16f54136f0294524fa1b8f3a70d8cfe5c47cc38", "b3d868ed9d55e5b9c39d717204f52252e7c0a8549996117f08ccaa026b32a290"),
        "en": ("9c5f26f07a2bf5e3d66cb1e19406deac5397086deb2688591609290781a9f69d", "42167974db3b0e53d09409c02f9df89ae798fd7dce0b7b50b7b922fdfb266c37"),
    }
    assert hashlib.sha256(text.split(heading)[0].encode()).hexdigest() == expected[language][0]
    assert hashlib.sha256(text[text.index(json_heading):].encode()).hexdigest() == expected[language][1]


def test_sell_factory_matches_reviewed_volume_prompt():
    # Intentional ko/en volume interpretation and KR completed-session guidance update.
    # 2026-09-28 reviewed: only appends the wording-only Korean rationale style rule.
    # 2026-10-01 reviewed: Core-1 / tier-1 wording matches the executed intraday hard stop
    # (stop_loss x 0.995, -7% on the live price); trailing stop stays closing-price based.
    # 2026-10-08 reviewed: Core-0 counts only control/delisting tender offers confirmed by an
    # official filing (Official filing check block); buybacks, debt tenders and mini-tenders are
    # not corporate events (MRVL sold on a 0.06% press-release mini-tender, 2026-10-07).
    # 2026-10-10 reviewed (contradiction sweep, tests/test_sell_prompt_consistency.py): sells are all-or-nothing
    # but micro-split adds exist; Step 0 defers to the system regime; the system trailing stop stays
    # close-confirmed while a raised stop_loss runs on the intraday hard stop; the dead -5~-7% grace
    # exception and -7.1% wording removed. Trailing bands and discretionary-sell wording unchanged.
    # 2026-10-10 reviewed: "close" wording matches operations — the AI sell run is before the close
    # (14:46 KST) and the trailing stop is confirmed by the pre-close trend-exit check (15:10~15:25 KST).
    source = SOURCE.read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "create_sell_decision_agent")
    assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == "00f948f9bf409f84cc07040705cf12a3f325f452076a757b1e8f158038a54bbf"
