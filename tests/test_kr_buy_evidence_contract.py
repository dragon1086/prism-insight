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
    expected = {
        "ko": ("16f2eea5fe4f69f6635064967ae222c00ce8f8b9d6d0b18191c1a378ddf0de8b", "96246810a487039fed91b8b44d68f1a8d9c99c3384f00a3a13400c9b7a45156e"),
        "en": ("4554e6bf8ac11ae69963d5eb6e9aac75025ba541cd64874a128d3fc6cd6d062c", "165a98f70fb92ff5d5c8d52577a7703172df08d1e37825866cebec7e4a4a2d56"),
    }
    assert hashlib.sha256(text.split(heading)[0].encode()).hexdigest() == expected[language][0]
    assert hashlib.sha256(text[text.index(json_heading):].encode()).hexdigest() == expected[language][1]


def test_sell_factory_matches_reviewed_volume_prompt():
    # Intentional ko/en volume interpretation and KR completed-session guidance update.
    # 2026-09-28 reviewed: only appends the wording-only Korean rationale style rule.
    # 2026-10-01 reviewed: Core-1 / tier-1 wording matches the executed intraday hard stop
    # (stop_loss x 0.995, -7% on the live price); trailing stop stays closing-price based.
    source = SOURCE.read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "create_sell_decision_agent")
    assert hashlib.sha256(ast.get_source_segment(source, node).encode()).hexdigest() == "8793bcb5667adc0b78b1d135f58792919897e7a22feea2ec569c0929a488666e"
