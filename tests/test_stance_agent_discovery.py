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
