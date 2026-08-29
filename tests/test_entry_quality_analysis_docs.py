from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_analysis_harness_locks_data_and_promotion_boundaries() -> None:
    harness = (ROOT / "docs" / "ENTRY_QUALITY_DATA_ANALYSIS_HARNESS.md").read_text(
        encoding="utf-8"
    )

    for required in (
        "decision_id",
        "position_id",
        "SUBMITTED_ONLY",
        "CONFIRMED",
        "MISSING",
        "Holm",
        "Holdout",
        "winner removal",
        "사용자의 명시적 승인",
        "Hypothesis",
        "Preregister",
        "Offline replay",
        "Rule SHADOW",
        "Limited LIVE",
    ):
        assert required in harness


def test_project_skill_and_agents_route_to_deterministic_packet() -> None:
    skill = (ROOT / "skills" / "prism-entry-quality-analysis" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "tools/build_entry_quality_evidence_packet.py" in skill
    assert "do not replace it with improvised SQL" in skill
    assert "automatic" in skill.lower()
    assert "매수품질 데이터 분석해줘" in agents
    assert "skills/prism-entry-quality-analysis/SKILL.md" in agents
