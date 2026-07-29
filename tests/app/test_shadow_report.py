from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from prism_app.daily_pipeline import (
    DailyRunRequest,
    PersistedDailyAnalysis,
    SQLiteAppRunRepository,
    StrategyAnalysis,
)
from prism_app.shadow_report import (
    append_shadow_section,
    read_persisted_shadow,
    render_shadow_report,
)
from prism_core.data.quality import QualityDecision, QualityDisposition
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


NOW = datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000901")


def _analysis(job_key: str) -> PersistedDailyAnalysis:
    return PersistedDailyAnalysis(
        job_key=job_key,
        run_id=SQLiteAppRunRepository.run_id_for(job_key),
        market=Market.KR,
        as_of_date=date(2026, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
        data_snapshot_id=SNAPSHOT_ID,
        leadership_snapshot_id="leadership-1",
        leadership_report_id="report-1",
        quality_decision=QualityDecision(
            disposition=QualityDisposition.ACCEPT,
            reasons=(),
            missing_fields=(),
            stale_fields=(),
        ),
        quality_skip=None,
        source_payload={
            "provider": "KIS",
            "stock_symbol": "005930",
            "provider_snapshot_id": str(SNAPSHOT_ID),
            "evidence_level": "LIVE_READ_ONLY",
            "collected_at": "2026-07-26T10:00:05+00:00",
            "observed_at": "2026-07-24T06:30:00+00:00",
            "available_at": "2026-07-24T06:31:00+00:00",
            "latest_completed_session": "2026-07-24",
            "price_basis": "RAW",
            "price_adjustment_semantics": "RAW_UNADJUSTED",
            "corporate_action_count": 0,
            "corporate_action_coverage_status": "UNVERIFIED",
            "corporate_action_coverage_scope": "UNVERIFIED",
            "corporate_action_covered_symbols": [],
            "incomplete_session_filter": "SCENARIO_PACK",
            "source_as_of": {
                "KIS": {
                    "source": "KIS daily market data",
                    "as_of": "2026-07-24T06:31:00+00:00",
                    "quality": "FRESH",
                },
                "AgentNews": {
                    "source": "https://agentnews.md/finance-ko.md",
                    "as_of": "2026-07-24T18:40:00+00:00",
                    "quality": "FRESH",
                },
            },
        },
        strategies=(
            StrategyAnalysis(
                strategy_id=StrategyId.SWING_V1,
                strategy_version=StrategyVersion("swing-v1.0.0"),
                output_payload={
                    "status": "ACCEPTED",
                    "decision": "NO_ENTRY",
                    "scenario_state": "NO_ENTRY",
                    "scenario_complete": True,
                    "scenario_reasons": (),
                    "quant_score": {
                        "score_version": "SHADOW_SCORE_V1.SWING_V1",
                        "total_score": "67.500000",
                        "components": {
                            "swing_v1.momentum_state_score": "75.000000",
                            "swing_v1.regime_state_score": "60.000000",
                        },
                    },
                    "scenario": {
                        "market_judgment": {"drivers": ["breadth weak"]},
                        "sector_judgment": {
                            "status": "ASSESSED",
                            "score_components": [{"rationale": "sector lag"}],
                        },
                        "security_judgment": {
                            "llm_score": "42",
                            "score_breakdown": [{"rationale": "weak momentum"}],
                        },
                        "bull_path": {
                            "summary": "leadership broadens",
                            "conditions": ["breadth improves"],
                            "confirmations": ["volume confirms"],
                            "falsifiers": ["breadth reverses"],
                            "next_event": "next close",
                            "valid_until": "2026-07-27T10:00:00+00:00",
                            "evidence_ids": ["ev-bull-1"],
                        },
                        "base_path": {
                            "summary": "setup remains unconfirmed",
                            "conditions": ["structure holds"],
                            "confirmations": ["entry trigger confirms"],
                            "falsifiers": ["structure breaks"],
                            "next_event": "next close",
                            "valid_until": "2026-07-27T10:00:00+00:00",
                            "evidence_ids": ["ev-base-1"],
                        },
                        "bear_path": {
                            "summary": "risk evidence dominates",
                            "conditions": ["support fails"],
                            "confirmations": ["momentum weakens"],
                            "falsifiers": ["support recovers"],
                            "next_event": "next close",
                            "valid_until": "2026-07-27T10:00:00+00:00",
                            "evidence_ids": ["ev-risk-1"],
                        },
                        "entry_triggers": [
                            {
                                "feature_name": "momentum_5d",
                                "operator": "GREATER_THAN_OR_EQUAL",
                                "comparison_value": "68123",
                                "observed_value": "67999",
                                "observed_result": "false",
                            }
                        ],
                        "avoid_triggers": [
                            {"feature_name": "momentum_5d", "observed_result": "false"}
                        ],
                        "stop_candidates": [{"price": "65000", "basis": "STRUCTURE"}],
                        "target_candidates": [{"price": "74000", "basis": "STRUCTURE"}],
                        "risk_multiplier_candidate": {
                            "value": "0.75",
                            "rationale": "uncertainty warrants reduced risk",
                            "evidence_ids": ["ev-risk-1"],
                        },
                        "reentry_candidates": [
                            {
                                "conditions": ["trend recovery confirms"],
                                "rationale": "wait for a distinct recovery signal",
                                "evidence_ids": ["ev-price-1"],
                            }
                        ],
                        "pyramiding_candidates": [
                            {
                                "conditions": ["position is profitable"],
                                "requires_profitable_position": True,
                                "rationale": "policy retains final authority",
                                "evidence_ids": ["ev-price-1"],
                            }
                        ],
                        "counter_evidence": ["ev-risk-1"],
                        "falsifiers": ["breadth recovers"],
                        "uncertainty": {"level": "0.3", "known_unknowns": ["flow"]},
                        "next_review_at": "2026-07-27T10:00:00+00:00",
                        "field_dispositions": [
                            {
                                "field_path": "entry_predicates[0].evaluation",
                                "action": "RECALCULATE",
                                "resolved_value": "false",
                            }
                        ],
                    },
                    "shadow_only": True,
                    "prompt_version": "swing.prompt.v1",
                    "validator_version": "validator.v1",
                },
                evidence_refs=("kis:005930:2026-07-25",),
            ),
        ),
    )


def test_daily_request_makes_snapshot_drift_a_distinct_visible_invocation() -> None:
    first = DailyRunRequest(
        market=Market.KR,
        as_of_date=date(2026, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
        invocation_id="a" * 64,
    )
    second = DailyRunRequest(
        market=Market.KR,
        as_of_date=date(2026, 7, 26),
        run_type="daily-close",
        evaluated_at=NOW,
        invocation_id="b" * 64,
    )

    assert first.job_key != second.job_key
    assert first.base_job_key == second.base_job_key
    assert first.job_key.endswith(":" + "a" * 64)


def test_shadow_report_appends_to_existing_markdown_once() -> None:
    rendered = render_shadow_report(_analysis("daily:KR:2026-07-26:daily-close"))
    existing = "# 기존 PRISM 리포트\n\n기존 내용입니다.\n"

    once = append_shadow_section(existing, rendered)
    twice = append_shadow_section(once, rendered)

    assert once == twice
    assert once.startswith(existing)
    assert once.count("<!-- PRISM_PHASE1_SHADOW_START -->") == 1
    assert "Phase 1 SHADOW" in once
    assert "주문 또는 계좌 작업을 수행하지 않습니다" in once
    assert str(SNAPSHOT_ID) in once
    assert "SWING_V1" in once
    assert "SHADOW_SCORE_V1.SWING_V1" in once
    assert "swing_v1.momentum_state_score" in once
    assert "67.500000" in once
    assert "NO_ENTRY" in once
    assert "실제 수집 시각: `2026-07-26T10:00:05+00:00`" in once
    assert "최신 완료 거래일: `2026-07-24`" in once
    assert "가격 기준/조정 의미: `RAW` / `RAW_UNADJUSTED`" in once
    assert "기업행위 검증: `UNVERIFIED` (건수 `0`)" in once
    assert "기업행위 검증 범위: `UNVERIFIED`" in once
    assert "미완료 세션 필터: `SCENARIO_PACK`" in once
    assert "KIS daily market data" in once
    assert "AgentNews" in once
    assert "FRESH" in once
    assert "시장 판정" in once and "breadth weak" in once
    assert "섹터 판정" in once and "sector lag" in once
    assert "종목 판정" in once and "weak momentum" in once
    assert "상승/기본/하락 경로" in once and "leadership broadens" in once
    assert "setup remains unconfirmed" in once and "risk evidence dominates" in once
    assert "진입/회피 조건" in once and "momentum_5d" in once
    assert "손절/목표 후보" in once and "65000" in once and "74000" in once
    assert "리스크 배수/재진입/피라미딩 후보" in once
    assert "0.75" in once and "trend recovery confirms" in once
    assert "position is profitable" in once
    assert "반대 근거/무효화" in once and "ev-risk-1" in once
    assert "필드 판정" in once and "RECALCULATE" in once
    assert "불확실성/다음 검토" in once and "2026-07-27T10:00:00+00:00" in once

    ordered_sections = (
        "### 1. 원천·기준시점·호출 증거와 데이터 품질",
        "### 2. KR 시장 국면·수급·주도/약세 그룹",
        "### 3. 후보 요약",
        "### 4. 종목별 SWING/TREND 카드",
        "### 5. 조건부 진입·회피와 무효화·축소/청산",
        "### 6. 데이터 공백·충돌과 가격 수준 공개 여부",
        "### 7. 이전 실행 대비 변화",
        "### 8. 다음 이벤트와 검토 시각",
        "### 9. 감사 상세",
    )
    positions = [once.index(section) for section in ordered_sections]
    assert positions == sorted(positions)
    assert "<details>" in once and "<summary>감사 상세 펼치기</summary>" in once
    assert "top support" not in once.lower()
    assert "상위 지지 근거" in once and "상위 반대 근거" in once
    assert "종목 | 채널 | 트리거" in once
    assert "005930" in once and "DATA_MISSING" in once


def test_shadow_report_suppresses_exact_levels_when_quality_is_degraded() -> None:
    analysis = _analysis("daily:KR:2026-07-26:degraded")
    degraded = replace(
        analysis,
        quality_decision=QualityDecision(
            disposition=QualityDisposition.REPORT_ONLY,
            reasons=("price: STALE",),
            missing_fields=(),
            stale_fields=("price",),
        ),
        source_payload={**analysis.source_payload, "quality_disposition": "REPORT_ONLY"},
    )

    rendered = render_shadow_report(degraded)

    assert "정확한 가격 수준을 공개하지 않습니다" in rendered
    assert "65000" not in rendered
    assert "74000" not in rendered
    assert "68123" not in rendered
    assert "67999" not in rendered
    assert "NO_ENTRY" in rendered
    assert "시나리오 상태: `NO_ENTRY`" in rendered


def test_shadow_report_renders_invalid_proposal_without_inventing_no_entry() -> None:
    original = _analysis("daily:KR:2026-07-26:invalid")
    invalid = StrategyAnalysis(
        strategy_id=StrategyId.SWING_V1,
        strategy_version=StrategyVersion("swing-v1.0.0"),
        output_payload={
            "status": "REJECTED",
            "decision": None,
            "scenario_state": "INVALID_PROPOSAL",
            "scenario_complete": False,
            "scenario_reasons": ("missing_or_stale_data: declared critical input",),
            "shadow_only": True,
            "prompt_version": "swing.prompt.v1",
            "validator_version": "validator.v1",
        },
        evidence_refs=("kis:005930:2026-07-25",),
    )
    analysis = PersistedDailyAnalysis(
        job_key=original.job_key,
        run_id=original.run_id,
        market=original.market,
        as_of_date=original.as_of_date,
        run_type=original.run_type,
        evaluated_at=original.evaluated_at,
        data_snapshot_id=original.data_snapshot_id,
        leadership_snapshot_id=original.leadership_snapshot_id,
        leadership_report_id=original.leadership_report_id,
        quality_decision=original.quality_decision,
        quality_skip=original.quality_skip,
        source_payload=original.source_payload,
        strategies=(invalid,),
    )

    rendered = render_shadow_report(analysis)

    assert "시나리오 상태: `INVALID_PROPOSAL`" in rendered
    assert "시나리오 완결: `False`" in rendered
    assert "declared critical input" in rendered
    assert "제안: `NO_ENTRY`" not in rendered


def test_persisted_shadow_readback_uses_ops_store_without_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "ops.sqlite"
    job_key = "daily:KR:2026-07-26:daily-close"
    with open_database(db_path) as connection:
        migrate_database(connection, DatabaseKind.OPS)
        SQLiteAppRunRepository(connection).save(_analysis(job_key))
        before = connection.total_changes

    readback = read_persisted_shadow(db_path, job_key=job_key)

    assert readback.analysis.job_key == job_key
    assert readback.analysis.data_snapshot_id == SNAPSHOT_ID
    assert "Phase 1 SHADOW" in readback.markdown
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM job_runs WHERE status = 'ANALYSIS_PERSISTED'"
        ).fetchone()[0] == 1
    assert before > 0
