from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SIGN_COMMENT = "I have read the CLA Document and I hereby sign the CLA"


def test_cla_covers_dual_licensing_and_limited_successors() -> None:
    cla = (ROOT / "CLA.md").read_text(encoding="utf-8")

    required_terms = (
        "retain ownership",
        "commercial or proprietary",
        "sublicense and relicense",
        "previously submitted",
        "Project Successor",
        "substantially all of the Project",
    )
    for term in required_terms:
        assert term in cla

    assert "IP corporation" not in cla


def test_contribution_guides_require_the_same_signature_comment() -> None:
    for filename in ("CONTRIBUTING.md", "CONTRIBUTING_ko.md"):
        guide = (ROOT / filename).read_text(encoding="utf-8")
        assert "CLA.md" in guide
        assert SIGN_COMMENT in guide


def test_cla_workflow_is_pinned_and_minimally_scoped() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "cla.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(workflow_text, Loader=yaml.BaseLoader)

    assert workflow["on"]["issue_comment"]["types"] == ["created"]
    assert workflow["on"]["pull_request_target"]["types"] == [
        "opened",
        "closed",
        "synchronize",
    ]
    assert workflow["permissions"] == {
        "actions": "write",
        "contents": "write",
        "pull-requests": "write",
        "statuses": "write",
    }

    steps = workflow["jobs"]["cla"]["steps"]
    action_step = next(step for step in steps if "uses" in step)
    action_ref = action_step["uses"]
    assert action_ref.startswith("contributor-assistant/github-action@")
    assert len(action_ref.rsplit("@", 1)[1]) == 40
    assert action_step["with"]["path-to-document"].endswith("/blob/main/CLA.md")
    assert action_step["with"]["path-to-signatures"] == "signatures/cla.json"
    assert action_step["with"]["branch"] == "cla-signatures"
    assert SIGN_COMMENT in workflow_text
