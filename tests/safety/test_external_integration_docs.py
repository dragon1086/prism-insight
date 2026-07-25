from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / ".hermes/plans/2026-07-23_204700-prism-current-to-target-transformation.md"


def _read(relative_path: str | Path) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_product_scope_separates_fixture_live_runtime_and_operated_readiness() -> None:
    product_scope = _read("docs/PRODUCT_SCOPE_AND_STRATEGY.md")

    assert "검증 계층과 operated readiness" in product_scope
    for state in ("Foundation/tests", "Live integration", "Runtime wiring", "Operated readiness"):
        assert state in product_scope
    assert "fixture 결과로 대체" in product_scope
    assert "KIS/FMP 시장데이터의 실제 read-only integration/smoke도 승인" in product_scope
    assert "실제 계좌 조회, 잔고·보유종목, 주문·취소·정정" in product_scope
    assert "그 밖의 실제 endpoint 호출" in product_scope
    assert "별도 source-specific capability/approval" in product_scope


def test_product_scope_documents_one_fmp_key_and_incremental_current_prism_integration() -> None:
    product_scope = _read("docs/PRODUCT_SCOPE_AND_STRATEGY.md")

    assert "하나의 동일한 API key" in product_scope
    assert "`FMP_API_KEY` 또는 호환 별칭 `FINANCIAL_MODELING_PREP_API_KEY`" in product_scope
    assert "서로 다른 두 자격증명을 요구하지 않는다" in product_scope
    assert "서로 다른 값은 구성 충돌로 fail closed" in product_scope
    assert "runtime wiring이나 operated readiness를 뜻하지 않는다" in product_scope
    assert "현재 PRISM 표면과 점진적 통합" in product_scope
    assert "전체 신규 로드맵이 끝날 때까지 통합을 미루지 않으며" in product_scope
    assert "별도 표면은 기존 표면으로 목표를 달성할 수 없다는 근거가 있을 때만" in product_scope
    assert "불필요해진 레거시 경로를 마지막에" in product_scope


def test_guides_require_bounded_vertical_integration_before_legacy_retirement() -> None:
    rules = _read("AGENTS.md")
    architecture = _read("CLAUDE.md")
    plan = PLAN.read_text(encoding="utf-8")

    assert "Do not build a second user-facing product" in rules
    assert "integrate it into one narrow current caller" in rules
    assert "incremental vertical checkpoints" in architecture
    assert "current PRISM caller behind an explicit seam" in architecture
    assert "점진적 수직 통합 checkpoint" in plan
    assert "obsolete path 삭제는 마지막 단계" in plan


def test_repository_rules_require_live_evidence_without_expanding_broker_scope() -> None:
    rules = _read("AGENTS.md")

    assert "### External integration verification" in rules
    assert "Actual KIS and FMP **market-data-only** live integration/smoke is approved" in rules
    assert "A passing fixture suite proves only the first state" in rules
    assert (
        "must not read account identifiers or call balance, holdings, order, cancel, "
        "replace, broker-paper, or live-trading APIs"
    ) in rules


def test_plan_requires_actual_kis_fmp_and_external_transport_smoke() -> None:
    plan = PLAN.read_text(encoding="utf-8")

    assert "tests/integration/test_kis_live_market_data.py" in plan
    assert "tests/integration/test_fmp_live_market_data.py" in plan
    assert "actual FMP endpoint capability/entitlement" in plan
    assert "provider_live: source-specific capability/approval을 받은 실제 LLM transport" in plan
    for live_test in (
        "tests/integration/test_kr_official_live.py",
        "tests/integration/test_sec_edgar_live.py",
        "tests/integration/test_macro_official_live.py",
    ):
        assert live_test in plan
    assert "source-specific capability/approval" in plan
    assert "provider_live:" in plan
    assert "broker_live: nonexistent until separately approved" in plan


def test_developer_and_agent_guides_preserve_live_verification_boundaries() -> None:
    architecture = _read("CLAUDE.md")
    agent_contract = _read("docs/CLAUDE_AGENTS.md")

    assert "fixture-tested foundation, actual-endpoint live integration" in architecture
    assert "KIS/FMP market-data-only live smoke is approved" in architecture
    assert "excludes account, holdings, balance, order, cancel, replace" in architecture
    assert "corresponding adapter must also have separately recorded bounded live-integration evidence" in agent_contract
    assert "stored immutable snapshots rather than performing uncontrolled live lookups" in agent_contract


def test_fixture_only_legacy_migration_is_not_operated_readiness() -> None:
    migration = _read("docs/LEGACY_DB_MIGRATION.md")

    assert "operated migration readiness가 아닙니다" in migration
    assert "실제 source를 read-only로 inventory" in migration
    assert "fixture 결과로 대체하지 않고 `not operated`" in migration
