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
    assert "patent license You granted under this Section" in cla
    assert "patent licenses granted to You under this Agreement" not in cla


def test_korean_cla_matches_the_authoritative_version() -> None:
    english = (ROOT / "CLA.md").read_text(encoding="utf-8")
    korean = (ROOT / "CLA_ko.md").read_text(encoding="utf-8")

    assert "[한국어 번역](CLA_ko.md)" in english
    assert "[영문 원본](CLA.md)" in korean
    assert "불일치하는 경우 영문 원본이 우선" in korean
    assert "버전 1.0" in korean

    required_korean_terms = (
        "저작권을 계속 보유",
        "상업 또는 독점 라이선스 조건",
        "재라이선스",
        "이전에 제출한 기여물",
        "프로젝트 승계인",
        "프로젝트의 전부 또는 실질적 전부",
        "해당 피소 당사자에게 부여한 특허 라이선스",
        "대한민국 법률",
        "서울중앙지방법원",
    )
    for term in required_korean_terms:
        assert term in korean


def test_contribution_guides_require_the_same_signature_comment() -> None:
    for filename in ("CONTRIBUTING.md", "CONTRIBUTING_ko.md"):
        guide = (ROOT / filename).read_text(encoding="utf-8")
        assert "CLA.md" in guide
        assert SIGN_COMMENT in guide

    korean_guide = (ROOT / "CONTRIBUTING_ko.md").read_text(encoding="utf-8")
    assert "CLA_ko.md" in korean_guide
    assert "정식 계약 문안은 영문 원본" in korean_guide


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
    unsigned_comment = action_step["with"]["custom-notsigned-prcomment"]
    assert "$pathToCLADocument" not in unsigned_comment
    assert "/blob/main/CLA.md" in unsigned_comment
    assert "/blob/main/CLA_ko.md" in unsigned_comment
