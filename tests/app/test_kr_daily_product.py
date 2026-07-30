from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from prism_app import kr_daily_product as daily_product_module
from prism_app.kr_daily_product import (
    CandidateAnalysisResult,
    KRDailyProduct,
    candidate_readback_status,
    context_call_evidence,
    daily_dashboard_projection,
    daily_product_status,
    genuine_completed_count,
    render_daily_composition,
    resolve_runtime_as_of,
    write_dashboard_projection,
)
from prism_app.stockeasy_snapshot_import import (
    StockEasyImportOutcome,
    StockEasyImportResult,
)
from prism_core.candidates import (
    CandidateChannel,
    CandidateSnapshot,
    CandidateStatus,
    reconcile_candidates,
)
from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.strategies.contracts import Market, StrategyId


AS_OF = datetime(2026, 7, 29, 15, 40, tzinfo=timezone.utc)


def _candidate(
    number: int,
    *,
    channel: CandidateChannel = CandidateChannel.CORE_PRISM,
    source: str = "CORE",
    trigger: str = "volume",
) -> CandidateSnapshot:
    symbol = f"{number:06d}"
    return CandidateSnapshot(
        market=Market.KR,
        security_id=SecurityId(value=UUID(int=number + 1)),
        provider=source,
        provider_symbol=symbol,
        display_name=f"Candidate {number}",
        channel=channel,
        source_id=f"{source}:{trigger}",
        source_snapshot_id=f"{source}:snapshot",
        observed_at=AS_OF,
        available_at=AS_OF,
        ingested_at=AS_OF,
        as_of=AS_OF,
        trigger_ids=(trigger,),
        raw_scores={"score": Decimal(number)},
        evidence_ids=(f"evidence:{source}:{number}",),
        status=CandidateStatus.ELIGIBLE,
    )


def test_replayed_readback_is_not_counted_as_a_fresh_completed_scenario() -> None:
    candidate = _candidate(9)
    result = SimpleNamespace(
        analyses=(
            CandidateAnalysisResult(
                security_id=candidate.security_id,
                provider_symbol=candidate.provider_symbol,
                job_key="daily:KR:replay",
                status="PERSISTED_READBACK_VERIFIED",
                strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
                report_markdown="replayed",
                fresh_invocation=False,
            ),
        ),
    )

    assert genuine_completed_count(result) == 0


def test_candidate_analysis_rejects_strategy_result_from_another_snapshot() -> None:
    candidate = _candidate(9)

    with pytest.raises(ValueError, match="same data snapshot"):
        CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=candidate.provider_symbol,
            job_key="daily:KR:mismatch",
            status="PERSISTED_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1,),
            report_markdown="persisted",
            data_snapshot_id="snapshot-current",
            strategy_results={
                "SWING_V1": {
                    "data_snapshot_id": "snapshot-other",
                    "scenario_state": "NO_ENTRY",
                }
            },
        )


def test_candidate_readback_status_preserves_invalid_and_policy_rejected_states() -> None:
    complete = {
        "SWING_V1": {"scenario_state": "WATCH", "scenario_complete": True},
        "TREND_V1": {"scenario_state": "NO_ENTRY", "scenario_complete": True},
    }
    invalid = {
        **complete,
        "TREND_V1": {
            "scenario_state": "INVALID_PROPOSAL",
            "scenario_complete": False,
        },
    }
    rejected = {
        **complete,
        "SWING_V1": {
            "scenario_state": "POLICY_REJECTED",
            "scenario_complete": False,
        },
    }

    assert candidate_readback_status("PERSISTED_READBACK_VERIFIED", complete) == (
        "PERSISTED_READBACK_VERIFIED"
    )
    assert candidate_readback_status("PERSISTED_READBACK_VERIFIED", invalid) == (
        "ANALYSIS_INCOMPLETE_READBACK_VERIFIED"
    )
    assert candidate_readback_status("PERSISTED_READBACK_VERIFIED", rejected) == (
        "POLICY_REJECTED_READBACK_VERIFIED"
    )


def test_daily_product_treats_persisted_policy_rejection_as_terminal_success() -> None:
    candidate = _candidate(9)
    result = SimpleNamespace(
        analyses=(
            CandidateAnalysisResult(
                security_id=candidate.security_id,
                provider_symbol=candidate.provider_symbol,
                job_key="daily:KR:policy-rejected",
                status="POLICY_REJECTED_READBACK_VERIFIED",
                strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
                report_markdown="persisted",
                data_snapshot_id="snapshot-current",
                strategy_results={
                    "SWING_V1": {
                        "data_snapshot_id": "snapshot-current",
                        "scenario_state": "WATCH",
                        "scenario_complete": True,
                    },
                    "TREND_V1": {
                        "data_snapshot_id": "snapshot-current",
                        "scenario_state": "POLICY_REJECTED",
                        "scenario_complete": False,
                    },
                },
            ),
        ),
        failures=(),
    )

    assert daily_product_status(result) == "COMPLETED_WITH_POLICY_REJECTIONS"
    assert daily_product_module.candidate_analysis_state(result.analyses[0]) == (
        "POLICY_REJECTED / WATCH"
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ("COMPLETED", 0),
        ("COMPLETED_WITH_POLICY_REJECTIONS", 0),
        ("REPORT_ONLY", 0),
        ("IDEMPOTENT_REPLAY", 0),
        ("ANALYSIS_INCOMPLETE", 2),
        ("PRODUCT_CAPABILITY_UNAVAILABLE", 2),
    ),
)
def test_daily_product_exit_code_distinguishes_terminal_outputs_from_failures(
    status: str, expected: int
) -> None:
    assert daily_product_module.daily_product_exit_code(status) == expected


@pytest.mark.asyncio
async def test_daily_product_retains_uncapped_union_while_analyzing_only_overlap() -> None:
    core = tuple(_candidate(index) for index in range(120))
    duplicate = _candidate(
        7,
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source="SUPPLEMENT",
        trigger="momentum",
    )
    supplemental = (
        duplicate,
        _candidate(
            500,
            channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
            source="SUPPLEMENT",
            trigger="high_52_week",
        ),
    )
    final_reconciliation = reconcile_candidates((*core, *supplemental))
    context = SimpleNamespace(timing=SimpleNamespace(as_of=AS_OF))
    selection = SimpleNamespace(
        snapshots=core,
        reconciliation=reconcile_candidates(core),
    )
    approved_snapshot = SimpleNamespace(
        observations=(),
        candidate_nominations=(),
        timing=SimpleNamespace(
            observed_at=AS_OF,
            available_at=AS_OF,
            ingested_at=AS_OF,
        ),
        image_hash=None,
        provider="STOCKEASY_SANITIZED_UI_EXPORT",
        capture_method=SimpleNamespace(value="APPROVED_UI"),
        content_hash="a" * 64,
        permission_record_id="permission:test",
        source_scope_id="scope:test",
        source_snapshot_id="snapshot:test",
        quality=SimpleNamespace(value="FRESH"),
        issues=(),
    )

    class ContextComposer:
        async def compose(self):
            return context

    class CandidateSource:
        def discover(self, *, trigger_time, market_context):
            assert trigger_time == "daily-close"
            assert market_context is context
            return selection

    class SupplementComposer:
        def compose(self, *, core_candidates, supplement, authoritative_values):
            assert tuple(core_candidates) == core
            assert supplement is approved_snapshot
            assert authoritative_values == {}
            return SimpleNamespace(
                supplemental_candidates=supplemental,
                reconciliation=final_reconciliation,
                authority_conflicts=(),
            )

    analyzed = []

    async def analyze(candidate):
        analyzed.append(candidate)
        return CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=candidate.provider_symbols[0],
            job_key=f"daily:KR:{candidate.provider_symbols[0]}",
            status="PERSISTED_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
            report_markdown=f"## {candidate.provider_symbols[0]}",
        )

    product = KRDailyProduct(
        context_composer=ContextComposer(),
        candidate_source=CandidateSource(),
        supplement_composer=SupplementComposer(),
        candidate_analyzer=analyze,
    )
    result = await product.run(
        trigger_time="daily-close",
        supplement_import=StockEasyImportResult.model_construct(
            outcome=StockEasyImportOutcome.IMPORTED,
            snapshot=approved_snapshot,
            temporary_image_deleted=False,
        ),
    )

    assert result.reconciliation is final_reconciliation
    assert result.counts.raw_assertions == 122
    assert result.counts.raw_assertions_by_source == {"CORE": 120, "SUPPLEMENT": 2}
    assert result.counts.unique_identities == 121
    assert result.observation_universe.identity_count == 121
    assert len(result.analysis_cohort.observation_only_members) == 120
    assert result.counts.analyzed_swing == 1
    assert result.counts.analyzed_trend == 1
    assert result.counts.truncated == 0
    assert len(analyzed) == 1
    channels = Counter(channel for item in analyzed for channel in item.channels)
    assert channels[CandidateChannel.CORE_PRISM] == 1
    assert channels[CandidateChannel.SUPPLEMENTAL_LEADERSHIP] == 1


@pytest.mark.asyncio
async def test_fresh_supplement_fans_out_only_six_cross_confirmed_candidates() -> None:
    core = tuple(_candidate(index) for index in range(22))
    supplemental = tuple(
        _candidate(
            index,
            channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
            source="SUPPLEMENT",
            trigger="momentum",
        )
        for index in range(6)
    )
    context = SimpleNamespace(timing=SimpleNamespace(as_of=AS_OF))
    selection = SimpleNamespace(
        snapshots=core,
        reconciliation=reconcile_candidates(core),
    )
    approved_snapshot = SimpleNamespace(
        quality=SimpleNamespace(value="FRESH"),
        observations=(),
        candidate_nominations=(),
        timing=SimpleNamespace(
            observed_at=AS_OF,
            available_at=AS_OF,
            ingested_at=AS_OF,
        ),
        image_hash=None,
        provider="STOCKEASY_SANITIZED_UI_EXPORT",
        capture_method=SimpleNamespace(value="APPROVED_UI"),
        content_hash="b" * 64,
        permission_record_id="permission:test",
        source_scope_id="scope:test",
        source_snapshot_id="snapshot:test",
        issues=(),
    )

    class ContextComposer:
        async def compose(self):
            return context

    class CandidateSource:
        def discover(self, *, trigger_time, market_context):
            return selection

    class SupplementComposer:
        def compose(self, *, core_candidates, supplement, authoritative_values):
            return SimpleNamespace(
                supplemental_candidates=supplemental,
                reconciliation=reconcile_candidates((*core, *supplemental)),
            )

    analyzed = []

    async def analyze(candidate):
        analyzed.append(candidate.security_id)
        return CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=candidate.provider_symbols[0],
            job_key=f"daily:KR:{candidate.provider_symbols[0]}",
            status="PERSISTED_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
            report_markdown=f"## {candidate.provider_symbols[0]}",
        )

    result = await KRDailyProduct(
        context_composer=ContextComposer(),
        candidate_source=CandidateSource(),
        supplement_composer=SupplementComposer(),
        candidate_analyzer=analyze,
    ).run(
        trigger_time="daily-close",
        supplement_import=StockEasyImportResult.model_construct(
            outcome=StockEasyImportOutcome.IMPORTED,
            snapshot=approved_snapshot,
            temporary_image_deleted=False,
        ),
    )

    assert len(analyzed) == 6
    assert result.observation_universe.identity_count == 22
    assert len(result.analysis_cohort.analysis_members) == 6
    assert len(result.analysis_cohort.observation_only_members) == 16
    projection = daily_dashboard_projection(result)
    skipped = [
        item for item in projection["candidates"] if item["analysis_status"] == "NOT_ANALYZED"
    ]
    assert len(skipped) == 16
    assert all(item["selection_reasons"] for item in skipped)
    rendered = render_daily_composition("# Existing\n", result)
    assert rendered.count("- 상태: `NOT_ANALYZED`") == 16
    assert "CORE_ONLY_SUPPLEMENT_REQUIRED" in rendered


@pytest.mark.asyncio
async def test_stale_core_context_fails_closed_before_candidate_analysis() -> None:
    core = (_candidate(1),)
    selection = SimpleNamespace(
        snapshots=core,
        reconciliation=reconcile_candidates(core),
    )

    class ContextComposer:
        async def compose(self):
            return SimpleNamespace(
                timing=SimpleNamespace(as_of=AS_OF),
                quality=DataQualityStatus.STALE,
            )

    class CandidateSource:
        def discover(self, *, trigger_time, market_context):
            return selection

    async def analyze(candidate):
        raise AssertionError("stale core context must not fan out to analysis")

    result = await KRDailyProduct(
        context_composer=ContextComposer(),
        candidate_source=CandidateSource(),
        supplement_composer=SimpleNamespace(),
        candidate_analyzer=analyze,
    ).run(
        trigger_time="daily-close",
        supplement_import=StockEasyImportResult(
            outcome=StockEasyImportOutcome.UNAVAILABLE,
            error_code="APPROVED_UI_EXPORT_NOT_CONFIRMED",
            temporary_image_deleted=False,
        ),
    )

    assert result.analyses == ()
    assert result.failures == ()
    assert result.analysis_cohort.observation_only_members[0].selection_reasons[0].value == (
        "CORE_INELIGIBLE"
    )


@pytest.mark.asyncio
async def test_daily_product_drops_imported_supplement_when_composition_fails() -> None:
    core = (_candidate(1),)
    selection = SimpleNamespace(
        snapshots=core,
        reconciliation=reconcile_candidates(core),
    )
    snapshot = SimpleNamespace()

    class ContextComposer:
        async def compose(self):
            return SimpleNamespace(timing=SimpleNamespace(as_of=AS_OF))

    class CandidateSource:
        def discover(self, *, trigger_time, market_context):
            return selection

    class SupplementComposer:
        def compose(self, **_kwargs):
            raise RuntimeError("private composition detail must not escape")

    async def analyze(candidate):
        return CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=candidate.provider_symbols[0],
            job_key="daily:KR:000001",
            status="REPORT_ONLY_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
            report_markdown="## 000001",
        )

    result = await KRDailyProduct(
        context_composer=ContextComposer(),
        candidate_source=CandidateSource(),
        supplement_composer=SupplementComposer(),
        candidate_analyzer=analyze,
    ).run(
        trigger_time="daily-close",
        supplement_import=StockEasyImportResult.model_construct(
            outcome=StockEasyImportOutcome.IMPORTED,
            snapshot=snapshot,
            temporary_image_deleted=False,
        ),
        supplement_snapshot_argument_supplied=True,
    )

    assert result.supplement_outcome is StockEasyImportOutcome.REJECTED
    assert result.supplement_error_code == "SUPPLEMENT_COMPOSITION_FAILED"
    assert result.supplement_capability is not None
    assert result.supplement_capability["status"] == "STOCKEASY_REJECTED"
    assert result.reconciliation is selection.reconciliation
    assert result.counts.raw_assertions == 1
    assert result.counts.analyzed_swing == 1
    assert result.counts.analyzed_trend == 1
    assert "private composition detail" not in repr(result)


@pytest.mark.asyncio
async def test_daily_product_isolates_candidate_failure_and_preserves_unavailable_supplement_counts() -> None:
    candidates = (_candidate(1), _candidate(2, trigger="high_52_week"))
    invalid = {"market": "KR", "provider_symbol": "not-a-symbol"}
    reconciliation = reconcile_candidates((*candidates, invalid))
    selection = SimpleNamespace(snapshots=candidates, reconciliation=reconciliation)

    class ContextComposer:
        async def compose(self):
            return SimpleNamespace(timing=SimpleNamespace(as_of=AS_OF))

    class CandidateSource:
        def discover(self, *, trigger_time, market_context):
            return selection

    class SupplementComposer:
        def compose(self, **_kwargs):
            raise AssertionError("UNAVAILABLE supplement must not be composed")

    async def analyze(candidate):
        if candidate.provider_symbols[0] == "000002":
            raise RuntimeError("raw provider detail must not escape")
        return CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=candidate.provider_symbols[0],
            job_key="daily:KR:000001",
            status="REPORT_ONLY_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
            report_markdown="## 000001",
        )

    result = await KRDailyProduct(
        context_composer=ContextComposer(),
        candidate_source=CandidateSource(),
        supplement_composer=SupplementComposer(),
        candidate_analyzer=analyze,
    ).run(
        trigger_time="daily-close",
        supplement_import=StockEasyImportResult(
            outcome=StockEasyImportOutcome.UNAVAILABLE,
            error_code="APPROVED_UI_EXPORT_NOT_CONFIRMED",
            temporary_image_deleted=False,
        ),
        supplement_snapshot_argument_supplied=True,
    )

    assert result.supplement_outcome is StockEasyImportOutcome.UNAVAILABLE
    assert result.supplement_error_code == "APPROVED_UI_EXPORT_NOT_CONFIRMED"
    assert result.supplement_capability is not None
    assert result.supplement_capability["status"] == "STOCKEASY_UNAVAILABLE"
    assert result.supplement_capability["snapshot_argument_supplied"] is True
    assert {
        item["requirement"] for item in result.supplement_capability["requirements"]
    } == {
        "SECURITIES",
        "MARKET_OVERVIEW",
        "LEADING_SECURITIES",
        "LEADING_SECTORS",
    }
    assert result.counts.raw_assertions == 3
    assert result.counts.invalid_records == 1
    assert result.counts.raw_assertions_by_source == {
        "CORE": 2,
        "INVALID_RECORD": 1,
    }
    assert result.counts.raw_assertions_by_trigger == {
        "INVALID_RECORD": 1,
        "high_52_week": 1,
        "volume": 1,
    }
    assert result.counts.analysis_failures == 1
    assert result.counts.analyzed_swing == 1
    assert result.counts.analyzed_trend == 1
    assert genuine_completed_count(result) == 0
    assert result.failures[0].failure_type == "RuntimeError"
    assert "raw provider detail" not in repr(result.failures)

    rendered = render_daily_composition("# 기존 PRISM 리포트\n", result)
    assert rendered.startswith("# 기존 PRISM 리포트")
    assert "CORE_PRISM" in rendered
    assert "APPROVED_UI_EXPORT_NOT_CONFIRMED" in rendered
    assert "APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED" in rendered
    assert "SECURITIES" in rendered
    assert "LEADING_SECTORS" in rendered
    assert "ANALYSIS_INCOMPLETE" in rendered
    assert "raw provider detail" not in rendered
    assert "NO_ENTRY" not in rendered

    rerendered = render_daily_composition(rendered, result)
    assert rerendered.count("<!-- PRISM_KR_DAILY_COMPOSITION_START -->") == 1
    assert rerendered.count("<!-- PRISM_KR_DAILY_COMPOSITION_END -->") == 1


def test_report_only_and_incomplete_scenarios_are_never_rendered_as_empty_or_no_entry() -> None:
    candidate = _candidate(9)
    reconciliation = reconcile_candidates((candidate,))
    result = SimpleNamespace(
        reconciliation=reconciliation,
        supplement_outcome=StockEasyImportOutcome.UNAVAILABLE,
        supplement_error_code="APPROVED_UI_EXPORT_NOT_CONFIRMED",
        analyses=(
            CandidateAnalysisResult(
                security_id=candidate.security_id,
                provider_symbol=candidate.provider_symbol,
                job_key="daily:KR:report-only",
                status="REPORT_ONLY_READBACK_VERIFIED",
                strategy_ids=(),
                report_markdown="## Persisted report-only evidence",
                issues=("FUNDAMENTAL_DATA_UNAVAILABLE",),
            ),
        ),
        failures=(),
        counts=SimpleNamespace(
            raw_assertions=1,
            raw_assertions_by_source={"CORE": 1},
            raw_assertions_by_channel={"CORE_PRISM": 1},
            raw_assertions_by_trigger={"volume": 1},
            unique_identities=1,
            excluded_identities=0,
            invalid_records=0,
            data_unavailable=0,
            analyzed_swing=0,
            analyzed_trend=0,
            analysis_failures=0,
            truncated=0,
        ),
    )

    rendered = render_daily_composition("# Existing\n", result)

    assert daily_product_status(result) == "REPORT_ONLY"
    assert "- 분석 상태: `ANALYSIS_INCOMPLETE`" in rendered
    assert "- 전략: `ANALYSIS_INCOMPLETE`" in rendered
    assert "FUNDAMENTAL_DATA_UNAVAILABLE" in rendered
    assert "NO_ENTRY" not in rendered
    assert "- 전략: ``" not in rendered
    assert "- 후보 원천: `CORE" in rendered


def test_report_renders_connected_stockeasy_evidence_without_unavailable_prerequisite() -> None:
    result = SimpleNamespace(
        reconciliation=reconcile_candidates(()),
        supplement_outcome=StockEasyImportOutcome.IMPORTED,
        supplement_error_code=None,
        supplement_capability={
            "status": "CONNECTED",
            "site_status": "SITE_AVAILABLE",
            "site_status_as_of": AS_OF.isoformat(),
            "site_status_basis": "OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT",
            "site_currently_verified": False,
            "ingestion_status": "IMPORTED",
            "authority_crosscheck_status": "NOT_PERFORMED",
            "supplemental_numeric_values_used_for_strategy": False,
            "requirements": [
                {"requirement": "MARKET_OVERVIEW", "status": "IMPORTED"},
                {"requirement": "LEADING_SECTORS", "status": "IMPORTED"},
            ],
            "observations": [
                {
                    "kind": "LEADING_GROUP",
                    "scope": "GROUP",
                    "group_id": "SHIPBUILDING",
                    "provider_symbol": None,
                    "value": "+6.4",
                    "unit": "PERCENT_VISIBLE",
                }
            ],
        },
        analyses=(),
        failures=(),
        counts=SimpleNamespace(
            raw_assertions=0,
            raw_assertions_by_source={},
            raw_assertions_by_channel={},
            raw_assertions_by_trigger={},
            unique_identities=0,
            excluded_identities=0,
            invalid_records=0,
            data_unavailable=0,
            analyzed_swing=0,
            analyzed_trend=0,
            analysis_failures=0,
            truncated=0,
        ),
    )

    rendered = render_daily_composition("# Existing\n", result)

    assert "보조 근거 기능 상태: `CONNECTED`" in rendered
    assert "사이트/수집 상태: `SITE_AVAILABLE` / `IMPORTED`" in rendered
    assert f"사이트 상태 기준 시각: `{AS_OF.isoformat()}`" in rendered
    assert "현재 시점 재검증: `False`" in rendered
    assert "KIS/KRX 값 대조: `NOT_PERFORMED`" in rendered
    assert "전략 수치 입력 사용: `False`" in rendered
    assert "LEADING_GROUP GROUP SHIPBUILDING=+6.4 PERCENT_VISIBLE" in rendered
    assert "APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED" not in rendered


def test_replayed_readback_has_an_explicit_non_success_status() -> None:
    candidate = _candidate(9)
    result = SimpleNamespace(
        analyses=(
            CandidateAnalysisResult(
                security_id=candidate.security_id,
                provider_symbol=candidate.provider_symbol,
                job_key="daily:KR:replay",
                status="PERSISTED_READBACK_VERIFIED",
                strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
                report_markdown="replayed",
                fresh_invocation=False,
            ),
        ),
        failures=(),
    )

    assert daily_product_status(result) == "IDEMPOTENT_REPLAY"


def test_replayed_policy_rejection_remains_labeled_as_replay() -> None:
    candidate = _candidate(9)
    result = SimpleNamespace(
        analyses=(
            CandidateAnalysisResult(
                security_id=candidate.security_id,
                provider_symbol=candidate.provider_symbol,
                job_key="daily:KR:replayed-policy-rejection",
                status="POLICY_REJECTED_READBACK_VERIFIED",
                strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
                report_markdown="replayed",
                data_snapshot_id="snapshot-replayed",
                fresh_invocation=False,
                strategy_results={
                    "SWING_V1": {
                        "data_snapshot_id": "snapshot-replayed",
                        "scenario_state": "WATCH",
                        "scenario_complete": True,
                    },
                    "TREND_V1": {
                        "data_snapshot_id": "snapshot-replayed",
                        "scenario_state": "POLICY_REJECTED",
                        "scenario_complete": False,
                    },
                },
            ),
        ),
        failures=(),
    )

    assert daily_product_status(result) == "IDEMPOTENT_REPLAY"


def test_dashboard_projection_preserves_funnel_channels_and_same_candidate_identities(
    tmp_path,
) -> None:
    core = _candidate(7)
    supplemental = _candidate(
        500,
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source="SUPPLEMENT",
        trigger="high_52_week",
    )
    reconciliation = reconcile_candidates((core, supplemental))
    analyses = tuple(
        CandidateAnalysisResult(
            security_id=item.security_id,
            provider_symbol=item.provider_symbols[0],
            job_key=f"daily:KR:{item.provider_symbols[0]}",
            status="PERSISTED_READBACK_VERIFIED",
            strategy_ids=(StrategyId.SWING_V1, StrategyId.TREND_V1),
            report_markdown="persisted",
            data_snapshot_id=f"snapshot:{item.provider_symbols[0]}",
            strategy_results={
                "SWING_V1": {
                    "data_snapshot_id": f"snapshot:{item.provider_symbols[0]}",
                    "scenario_state": "NO_ENTRY",
                    "decision": "NO_ENTRY",
                    "scenario_reasons": ["entry threshold veto"],
                    "hard_vetoes": ["shadow_score_v1:swing_v1.min_quant_score"],
                    "quant_score": {
                        "score_version": "SHADOW_SCORE_V1.SWING_V1",
                        "total_score": "61.000000",
                        "threshold_version": "SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1",
                    },
                },
                "TREND_V1": {
                    "data_snapshot_id": f"snapshot:{item.provider_symbols[0]}",
                    "scenario_state": "WATCH",
                    "decision": "WATCH",
                    "scenario_reasons": ["trigger not confirmed"],
                    "quant_score": {
                        "score_version": "SHADOW_SCORE_V1.TREND_V1",
                        "total_score": "71.000000",
                        "threshold_version": "SHADOW_ENTRY_THRESHOLDS_V1.TREND_V1",
                    },
                },
            },
        )
        for item in reconciliation.included
    )
    result = SimpleNamespace(
        reconciliation=reconciliation,
        supplement_outcome=StockEasyImportOutcome.IMPORTED,
        supplement_error_code=None,
        analyses=analyses,
        failures=(),
        counts=SimpleNamespace(
            raw_assertions=2,
            raw_assertions_by_source={"CORE": 1, "SUPPLEMENT": 1},
            raw_assertions_by_channel={
                "CORE_PRISM": 1,
                "SUPPLEMENTAL_LEADERSHIP": 1,
            },
            raw_assertions_by_trigger={"high_52_week": 1, "volume": 1},
            unique_identities=2,
            excluded_identities=0,
            invalid_records=0,
            data_unavailable=0,
            analyzed_swing=2,
            analyzed_trend=2,
            analysis_failures=0,
            truncated=0,
        ),
    )

    projection = daily_dashboard_projection(result)
    dashboard = tmp_path / "dashboard.json"
    dashboard.write_text(
        json.dumps({"schema_version": "prism_dashboard_v1", "research": {}}),
        encoding="utf-8",
    )
    write_dashboard_projection(dashboard, projection)
    persisted = json.loads(dashboard.read_text(encoding="utf-8"))[
        "kr_daily_composition"
    ]

    assert persisted == projection
    assert persisted["counts"]["truncated"] == 0
    assert {item["security_id"] for item in persisted["candidates"]} == {
        str(item.security_id.value) for item in reconciliation.included
    }
    assert {
        channel
        for item in persisted["candidates"]
        for channel in item["channels"]
    } == {"CORE_PRISM", "SUPPLEMENTAL_LEADERSHIP"}
    assert {
        source["provider"]
        for item in persisted["candidates"]
        for source in item["sources"]
    } == {"CORE", "SUPPLEMENT"}
    assert all(
        source["available_at"] == AS_OF.isoformat()
        for item in persisted["candidates"]
        for source in item["sources"]
    )
    assert all(item["data_snapshot_id"] for item in persisted["candidates"])
    assert all(
        set(item["strategy_results"]) == {"SWING_V1", "TREND_V1"}
        for item in persisted["candidates"]
    )
    assert persisted["candidates"][0]["strategy_results"]["SWING_V1"][
        "quant_score"
    ]["threshold_version"] == "SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1"
    rendered = render_daily_composition("", result)
    assert "결정론적 진입 거부" in rendered
    assert "shadow_score_v1:swing_v1.min_quant_score" in rendered


def test_context_call_evidence_is_timestamped_and_sanitized() -> None:
    context = SimpleNamespace(
        timing=SimpleNamespace(
            session_date=AS_OF.date(),
            session_state=SimpleNamespace(value="COMPLETE"),
        ),
        source_clocks=(
            SimpleNamespace(
                source="KIS",
                observed_at=AS_OF,
                available_at=AS_OF,
                ingested_at=AS_OF,
                quality=SimpleNamespace(value="FRESH"),
            ),
        ),
    )
    agentnews = SimpleNamespace(
        attempts=(
            SimpleNamespace(
                url="https://agentnews.md/finance-ko.md",
                status_code=200,
                fetched_at=AS_OF,
                latency_ms=17,
                content_hash="a" * 64,
                outcome="RESPONSE_RECEIVED",
            ),
        ),
        used_last_known_good=False,
    )

    evidence = context_call_evidence(
        context=context,
        kis_evidence=(
            {
                "endpoint": "/uapi/domestic-stock/v1/quotations/volume-rank",
                "status_code": 200,
                "received_at": AS_OF.isoformat(),
                "raw_payload_hash": "b" * 64,
                "authorization": "must-not-escape",
            },
        ),
        agentnews_result=agentnews,
    )

    encoded = json.dumps(evidence)
    assert evidence["session_authority"] == "KRX_EXCHANGE_CALENDAR"
    assert evidence["kis"][0]["status_code"] == 200
    assert evidence["agentnews"][0]["received_at"] == AS_OF.isoformat()
    assert evidence["source_clocks"][0]["quality"] == "FRESH"
    assert "authorization" not in encoded
    assert "must-not-escape" not in encoded


def test_runtime_as_of_uses_post_fetch_clock_and_rejects_future_requested_boundary() -> None:
    requested = datetime(2026, 7, 29, 15, 40, tzinfo=timezone.utc)
    post_fetch = datetime(2026, 7, 29, 15, 41, tzinfo=timezone.utc)

    assert resolve_runtime_as_of(requested=requested, now=post_fetch) == post_fetch
    with pytest.raises(ValueError, match="future"):
        resolve_runtime_as_of(requested=post_fetch, now=requested)


def test_live_candidate_analysis_defers_decision_clock_until_after_fundamental_prefetch() -> None:
    requested = datetime(2026, 7, 29, 15, 40, tzinfo=timezone.utc)
    post_context_fetch = datetime(2026, 7, 29, 15, 41, tzinfo=timezone.utc)

    assert (
        daily_product_module.live_candidate_analysis_as_of(
            requested=requested,
            now=post_context_fetch,
        )
        is None
    )


def test_kr_daily_defaults_to_live_verified_chatgpt_oauth_model() -> None:
    args = daily_product_module._parser().parse_args(
        [
            "--as-of",
            AS_OF.isoformat(),
            "--research-db",
            "research.sqlite",
            "--paper-db",
            "paper.sqlite",
            "--ops-db",
            "ops.sqlite",
            "--report-output",
            "report.md",
            "--dashboard-output",
            "dashboard.json",
        ]
    )

    assert args.model == "gpt-5.4-mini"
    assert args.model_version == "gpt-5.4-mini"


def test_kr_daily_accepts_snapshot_only_with_explicit_permission_record_path() -> None:
    args = daily_product_module._parser().parse_args(
        [
            "--as-of",
            AS_OF.isoformat(),
            "--research-db",
            "research.sqlite",
            "--paper-db",
            "paper.sqlite",
            "--ops-db",
            "ops.sqlite",
            "--report-output",
            "report.md",
            "--dashboard-output",
            "dashboard.json",
            "--stockeasy-snapshot",
            "stockeasy_sanitized_snapshot_v1.json",
            "--stockeasy-permission-record",
            "stockeasy_permission_record_v1.json",
        ]
    )

    assert args.stockeasy_snapshot.name == "stockeasy_sanitized_snapshot_v1.json"
    assert args.stockeasy_permission_record.name == "stockeasy_permission_record_v1.json"


def test_runtime_rejects_one_sided_stockeasy_contract_fail_soft() -> None:
    result = daily_product_module.resolve_stockeasy_import(
        snapshot_path=Path("stockeasy_sanitized_snapshot_v1.json"),
        permission_path=None,
        imported_at=AS_OF,
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "SNAPSHOT_AND_PERMISSION_RECORD_REQUIRED"
