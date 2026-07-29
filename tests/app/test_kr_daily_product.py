from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from prism_app.kr_daily_product import (
    CandidateAnalysisResult,
    KRDailyProduct,
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
from prism_core.data.contracts import SecurityId
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


@pytest.mark.asyncio
async def test_daily_product_unions_stable_identities_without_truncation_and_analyzes_both_strategies() -> None:
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
            assert supplement == "approved-snapshot"
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
            snapshot="approved-snapshot",
            temporary_image_deleted=False,
        ),
    )

    assert result.reconciliation is final_reconciliation
    assert result.counts.raw_assertions == 122
    assert result.counts.raw_assertions_by_source == {"CORE": 120, "SUPPLEMENT": 2}
    assert result.counts.unique_identities == 121
    assert result.counts.analyzed_swing == 121
    assert result.counts.analyzed_trend == 121
    assert result.counts.truncated == 0
    assert len(analyzed) == 121
    channels = Counter(channel for item in analyzed for channel in item.channels)
    assert channels[CandidateChannel.CORE_PRISM] == 120
    assert channels[CandidateChannel.SUPPLEMENTAL_LEADERSHIP] == 2


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
    )

    assert result.supplement_outcome is StockEasyImportOutcome.UNAVAILABLE
    assert result.supplement_error_code == "APPROVED_UI_EXPORT_NOT_CONFIRMED"
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
