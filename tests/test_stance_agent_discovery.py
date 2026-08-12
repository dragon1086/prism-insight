import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "examples" / "dashboard" / "public" / ".well-known" / "stance.json"


def test_agent_discovery_contract_is_complete_and_public_safe():
    contract = json.loads(DISCOVERY.read_text(encoding="utf-8"))

    assert contract["schema"] == "stance-discovery/1"
    assert contract["protocol"] == "stance/1"
    assert contract["public_api"].startswith("https://")
    assert contract["registration"]["path"] == "/strategies"
    assert contract["declaration"]["path"] == "/stances"
    assert contract["status_paths"]["portfolio"] == "/portfolio"
    assert any("Do not migrate" in rule for rule in contract["agent_rules"])
    assert any("Never expose" in rule for rule in contract["agent_rules"])

    serialized = json.dumps(contract).lower()
    assert "stance_registration_token" not in serialized
    assert "stk_" not in serialized


def test_dashboard_leads_with_agent_onboarding_before_manual_form():
    page = (
        ROOT / "examples" / "dashboard" / "components" / "stance-leaderboard-page.tsx"
    ).read_text(encoding="utf-8")

    assert page.index("<StanceAgentConnectCard") < page.index("<StanceRegistrationCard")


def test_stance_tab_renders_independently_from_portfolio_data():
    page = (ROOT / "examples" / "dashboard" / "app" / "page.tsx").read_text(
        encoding="utf-8"
    )

    stance_branch = page.index('if (activeTab === "stance")')
    data_error_gate = page.index("if (dataError)")
    loading_gate = page.index("if (!data)")

    assert stance_branch < data_error_gate < loading_gate


def test_copyable_agent_prompt_contains_operational_safety_rules():
    card = (
        ROOT
        / "examples"
        / "dashboard"
        / "components"
        / "stance-agent-connect-card.tsx"
    ).read_text(encoding="utf-8")

    assert "navigator.clipboard.writeText(prompt)" in card
    assert 'const DISCOVERY_PATH = "/.well-known/stance.json"' in card
    assert "Never expose the issued API key" in card
    assert "mode-0600" in card
    assert "fail-open" in card
    assert "last_seq + 1" in card


def test_onboarding_explains_where_to_paste_and_when_results_appear():
    card = (
        ROOT
        / "examples"
        / "dashboard"
        / "components"
        / "stance-agent-connect-card.tsx"
    ).read_text(encoding="utf-8")

    assert "에이전트 채팅에 붙여넣기" in card
    assert "파일·터미널 접근 모드" in card
    assert "등록 · 키 보관 · 코드 연동 · 테스트" in card
    assert "예선 명단에 바로 표시" in card
    assert "63거래일과 자산의 1% 이상을 투자했던 포지션 청산 20건" in card
    assert "그전에도 기록과 성과는 보입니다" in card


def test_agent_onboarding_plans_multiple_strategies_before_registration():
    card = (
        ROOT
        / "examples"
        / "dashboard"
        / "components"
        / "stance-agent-connect-card.tsx"
    ).read_text(encoding="utf-8")
    contract = json.loads(DISCOVERY.read_text(encoding="utf-8"))

    assert "독립 전략을 모두 식별" in card
    assert "등록 계획" in card
    assert "명시적으로 승인" in card
    assert "각 전략마다 별도 API 키" in card
    assert any("multiple independent strategies" in rule for rule in contract["agent_rules"])
    assert contract["registration"]["profile_fields"]["tagline"]["required"] is False


def test_dashboard_marks_participant_links_as_untrusted_content():
    page = (
        ROOT
        / "examples"
        / "dashboard"
        / "components"
        / "stance-leaderboard-page.tsx"
    ).read_text(encoding="utf-8")

    assert 'rel="nofollow ugc noopener noreferrer"' in page
    assert "참여자가 직접 작성한 공개 정보" in page
