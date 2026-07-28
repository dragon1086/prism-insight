from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_app.daily_pipeline import PersistedDailyAnalysis, StrategyAnalysis
from prism_app.query_service import StrategyEvaluationView
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import (
    QualityDecision,
    QualityDisposition,
    QualitySkipRecord,
)
from prism_core.feedback.repository import StoredProposal
from prism_core.feedback.retrieval import EvaluationLessonSet
from prism_core.llm.trade_plan import ProposedDecision, TradePlanProposal
from prism_core.policy.proposal_validator import ProposalValidationStatus
from prism_core.reporting.daily import build_daily_report, render_daily_report
from prism_core.reporting.models import LeadingSector
from prism_core.reporting.leadership_tracking import (
    LeadershipRepository,
    MarketTrackingSnapshot,
)
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion
from tests.app.test_daily_pipeline import _leadership_snapshot
from tests.llm.test_trade_plan_schema import valid_proposal_payload
from tests.reporting.test_leadership_tracking import valid_snapshot_payload


AS_OF = datetime(2020, 7, 26, 1, 0, tzinfo=timezone.utc)
DATA_SNAPSHOT_ID = UUID("11111111-1111-1111-1111-111111111111")
PROJECT_ROOT = Path(__file__).parents[2]


def _proposal(strategy_id: StrategyId, version: StrategyVersion) -> TradePlanProposal:
    payload = valid_proposal_payload()
    payload["strategy_id"] = strategy_id
    payload["strategy_version"] = version.value
    payload["market"] = Market.KR
    payload["feature_provenance"]["data_snapshot_id"] = DATA_SNAPSHOT_ID
    payload["decision"] = ProposedDecision.NO_ENTRY
    return TradePlanProposal.model_validate(payload)


def _evaluation(
    strategy_id: StrategyId,
    version: StrategyVersion,
) -> StrategyEvaluationView:
    proposal = _proposal(strategy_id, version)
    stored = StoredProposal(
        proposal_record_id=f"proposal-{strategy_id.value}",
        proposal_key=f"key-{strategy_id.value}",
        revision=0,
        strategy_id=strategy_id,
        strategy_version=version,
        proposed_decision=proposal.decision,
        raw_output="fixture raw output",
        normalized_proposal_json=proposal.model_dump_json(),
        validation_status=ProposalValidationStatus.ACCEPTED,
        model_provider="fixture-provider",
        model_id="fixture-model",
        model_version="fixture-model-v1",
        prompt_version="fixture-prompt-v1",
        sampling_version="fixture-sampling-v1",
        sampling={"temperature": "0"},
        validator_version="fixture-validator-v1",
        policy_version="fixture-policy-v1",
        data_snapshot_id=str(DATA_SNAPSHOT_ID),
        feature_snapshot_id=str(proposal.feature_provenance.feature_snapshot_id),
        available_at=AS_OF,
        dispositions=(),
    )
    return StrategyEvaluationView(
        proposals=(stored,),
        shadow_evaluation=EvaluationLessonSet(strategy_id, version, AS_OF, ()),
    )


def _complete_scenario(decision: str) -> dict[str, object]:
    return {
        "regime": {
            "probabilities": {
                "strong_bull": "0.10", "moderate_bull": "0.45", "sideways": "0.30",
                "moderate_bear": "0.10", "strong_bear": "0.05",
            },
            "confidence": "0.70",
            "drivers": ["quant regime evidence"],
        },
        "bull_path": ["bull-evidence"],
        "base_path": ["quant regime evidence"],
        "bear_path": ["bear-evidence"],
        "current_action": decision,
        "triggers": [{
            "feature_name": "swing_v1.regime_compatibility", "operator": "GTE",
            "comparison_value": "0.60", "upper_value": None,
            "observed_value": "0.55",
            "observed_result": "false", "valid_until": "2020-07-27T01:00:00Z",
            "evidence_ids": ["trigger-evidence"],
        }],
        "failure_transition": ["breadth deteriorates"],
        "falsifiers": ["breadth deteriorates"],
        "uncertainty": {
            "level": "0.30", "known_unknowns": ["next session breadth"],
            "assumptions": ["provider data remains fresh"],
        },
        "next_review_at": "2020-07-27T01:00:00Z",
    }


def _analysis() -> PersistedDailyAnalysis:
    swing = StrategyVersion("swing-v1.0.0")
    trend = StrategyVersion("trend-v1.0.0")
    return PersistedDailyAnalysis(
        job_key="daily:KR:2020-07-26:daily-close",
        run_id="run-1",
        market=Market.KR,
        as_of_date=date(2020, 7, 26),
        run_type="daily-close",
        evaluated_at=AS_OF,
        data_snapshot_id=DATA_SNAPSHOT_ID,
        leadership_snapshot_id="filled-by-test",
        leadership_report_id="filled-by-test",
        quality_decision=QualityDecision(QualityDisposition.ACCEPT, (), (), ()),
        quality_skip=None,
        source_payload={"provider": "fixture-provider", "snapshot": "complete"},
        strategies=(
            StrategyAnalysis(
                StrategyId.SWING_V1,
                swing,
                {
                    "decision": "NO_ENTRY", "summary": "Short-horizon setup is selective.",
                    "scenario_state": "NO_ENTRY", "scenario_complete": True,
                    "scenario_reasons": (), "scenario": _complete_scenario("NO_ENTRY"),
                },
                ("swing-analysis-evidence",),
            ),
            StrategyAnalysis(
                StrategyId.TREND_V1,
                trend,
                {
                    "decision": "WATCH", "summary": "Medium-term durability is under review.",
                    "scenario_state": "WATCH", "scenario_complete": True,
                    "scenario_reasons": (), "scenario": _complete_scenario("WATCH"),
                },
                ("trend-analysis-evidence",),
            ),
        ),
    )


def test_daily_report_reads_persisted_leadership_and_keeps_strategy_versions_separate(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        ingested = LeadershipRepository(connection).ingest(_leadership_snapshot())
        leadership = LeadershipRepository(connection).read(ingested.snapshot_id)
        analysis = _analysis()
        analysis = PersistedDailyAnalysis(
            **{
                **analysis.__dict__,
                "leadership_snapshot_id": ingested.snapshot_id,
                "leadership_report_id": ingested.report_id,
            }
        )

        report = build_daily_report(
            analysis=analysis,
            leadership=leadership,
            strategy_evaluations=(
                _evaluation(StrategyId.SWING_V1, analysis.strategies[0].strategy_version),
                _evaluation(StrategyId.TREND_V1, analysis.strategies[1].strategy_version),
            ),
            leading_sectors=(
                LeadingSector(
                    market=Market.KR,
                    name="Semiconductors",
                    evidence_refs=("sector-evidence-1",),
                ),
            ),
        )

    assert report.market is Market.KR
    assert report.as_of_date == date(2020, 7, 26)
    assert report.leadership_as_of == AS_OF
    assert report.sources[0].provider == "hermes_agent_report"
    assert report.sources[1].provider == "fixture-provider"
    assert report.leadership_quality.status is DataQualityStatus.FRESH
    assert report.analysis_quality.disposition is QualityDisposition.ACCEPT
    assert report.market_regime.value == "MODERATE_BULL"
    assert [sector.name for sector in report.leading_sectors] == ["Semiconductors"]
    assert tuple(item.strategy_id for item in report.strategies) == (
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    )
    assert tuple(item.strategy_version.value for item in report.strategies) == (
        "swing-v1.0.0",
        "trend-v1.0.0",
    )
    assert report.strategies[0].proposals[0].bull_evidence_ids
    assert report.strategies[0].proposals[0].bear_evidence_ids
    assert report.strategies[0].proposals[0].counter_evidence_ids
    assert report.strategies[0].proposals[0].falsifiers
    assert report.strategies[0].proposals[0].uncertainty.known_unknowns
    assert report.strategies[0].scenario_state.value == "NO_ENTRY"
    assert report.strategies[0].scenario_complete is True
    assert report.strategies[0].scenario is not None
    assert report.strategies[0].scenario.triggers[0].observed_value == "0.55"
    assert report.strategies[0].scenario.next_review_at.isoformat().startswith("2020-07-27")
    assert report.shadow_status.evaluation_only is True
    assert report.shadow_status.score_effect is False
    assert report.shadow_status.policy_effect is False
    assert report.shadow_status.proposal_effect is False


def test_daily_renderer_delegates_leadership_markdown_and_labels_shadow_inert(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(_leadership_snapshot())
        leadership = repository.read(ingested.snapshot_id)
        base = _analysis()
        analysis = PersistedDailyAnalysis(
            **{
                **base.__dict__,
                "leadership_snapshot_id": ingested.snapshot_id,
                "leadership_report_id": ingested.report_id,
            }
        )
        report = build_daily_report(
            analysis=analysis,
            leadership=leadership,
            strategy_evaluations=tuple(
                _evaluation(item.strategy_id, item.strategy_version)
                for item in analysis.strategies
            ),
            leading_sectors=(),
        )

    rendered = render_daily_report(report)
    assert leadership.rendered_markdown.strip() in rendered
    assert "SWING_V1 (swing-v1.0.0)" in rendered
    assert "TREND_V1 (trend-v1.0.0)" in rendered
    assert "Scenario state: NO_ENTRY" in rendered
    assert "Scenario complete: True" in rendered
    assert "Next review: 2020-07-27T01:00:00+00:00" in rendered
    assert "SHADOW Evaluation — Inert" in rendered
    assert "evaluation-only" in rendered
    assert "Research report only; no execution authority." in rendered
    assert "Leading sectors: none identified" in rendered


def test_daily_builder_rejects_cross_snapshot_or_strategy_version_mix(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(_leadership_snapshot())
        leadership = repository.read(ingested.snapshot_id)
        analysis = _analysis()

    with pytest.raises(ValueError, match="leadership snapshot"):
        build_daily_report(
            analysis=analysis,
            leadership=leadership,
            strategy_evaluations=(),
            leading_sectors=(),
        )

    corrected = PersistedDailyAnalysis(
        **{
            **analysis.__dict__,
            "leadership_snapshot_id": ingested.snapshot_id,
            "leadership_report_id": ingested.report_id,
        }
    )
    wrong = _evaluation(StrategyId.SWING_V1, StrategyVersion("wrong-v9"))
    with pytest.raises(ValueError, match="strategy identity"):
        build_daily_report(
            analysis=corrected,
            leadership=leadership,
            strategy_evaluations=(wrong,),
            leading_sectors=(),
        )


def test_report_contract_rejects_executable_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LeadingSector.model_validate(
            {
                "market": "KR",
                "name": "Semiconductors",
                "evidence_refs": ["sector-evidence"],
                "quantity": 1,
            }
        )


def test_reporting_package_exports_shared_read_model_api():
    import prism_core.reporting as reporting

    assert reporting.DailyReport is not None
    assert reporting.WeeklyReport is not None
    assert reporting.build_daily_report is build_daily_report
    assert reporting.render_daily_report is render_daily_report
    assert reporting.Market is Market
    assert reporting.LeadershipMarket is not Market


def test_accept_daily_report_requires_both_approved_strategy_families(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(_leadership_snapshot())
        leadership = repository.read(ingested.snapshot_id)

    base = _analysis()
    swing_only = PersistedDailyAnalysis(
        **{
            **base.__dict__,
            "leadership_snapshot_id": ingested.snapshot_id,
            "leadership_report_id": ingested.report_id,
            "strategies": (base.strategies[0],),
        }
    )
    evaluation = _evaluation(
        StrategyId.SWING_V1,
        swing_only.strategies[0].strategy_version,
    )

    with pytest.raises(ValueError, match="SWING_V1 and TREND_V1"):
        build_daily_report(
            analysis=swing_only,
            leadership=leadership,
            strategy_evaluations=(evaluation,),
            leading_sectors=(),
        )


def test_non_accept_daily_report_is_explicitly_skipped_without_strategy_output(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(_leadership_snapshot())
        leadership = repository.read(ingested.snapshot_id)

    base = _analysis()
    decision = QualityDecision(
        QualityDisposition.REPORT_ONLY,
        ("core evidence is incomplete",),
        ("evidence",),
        (),
    )
    skipped = replace(
        base,
        leadership_snapshot_id=ingested.snapshot_id,
        leadership_report_id=ingested.report_id,
        quality_decision=decision,
        quality_skip=QualitySkipRecord(
            request_id=base.job_key,
            snapshot_id=base.data_snapshot_id,
            evaluated_at=base.evaluated_at,
            disposition=decision.disposition,
            reasons=decision.reasons,
            missing_fields=decision.missing_fields,
            stale_fields=decision.stale_fields,
        ),
        strategies=(),
    )

    report = build_daily_report(
        analysis=skipped,
        leadership=leadership,
        strategy_evaluations=(),
        leading_sectors=(),
    )

    assert report.analysis_quality.skipped is True
    assert report.analysis_quality.disposition is QualityDisposition.REPORT_ONLY
    assert report.strategies == ()


def test_daily_report_json_roundtrip_preserves_public_read_contract(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(_leadership_snapshot())
        leadership = repository.read(ingested.snapshot_id)

    base = _analysis()
    analysis = replace(
        base,
        leadership_snapshot_id=ingested.snapshot_id,
        leadership_report_id=ingested.report_id,
    )
    report = build_daily_report(
        analysis=analysis,
        leadership=leadership,
        strategy_evaluations=tuple(
            _evaluation(item.strategy_id, item.strategy_version)
            for item in analysis.strategies
        ),
        leading_sectors=(),
    )

    assert type(report).model_validate_json(report.model_dump_json()) == report


def test_unusable_leadership_is_visibly_suppressed_in_daily_report(tmp_path: Path):
    payload = valid_snapshot_payload()
    payload.update(
        {
            "quality": "PARTIAL",
            "quality_reasons": ["core leadership evidence is incomplete"],
            "core_evidence_usable": False,
            "leader_universe_complete": False,
        }
    )
    snapshot = MarketTrackingSnapshot.model_validate_json(json.dumps(payload))
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        ingested = repository.ingest(snapshot)
        leadership = repository.read(ingested.snapshot_id)

    base = _analysis()
    analysis = replace(
        base,
        leadership_snapshot_id=ingested.snapshot_id,
        leadership_report_id=ingested.report_id,
    )
    report = build_daily_report(
        analysis=analysis,
        leadership=leadership,
        strategy_evaluations=tuple(
            _evaluation(item.strategy_id, item.strategy_version)
            for item in analysis.strategies
        ),
        leading_sectors=(),
    )

    assert report.leading_stocks == ()
    assert report.leadership_quality.core_evidence_usable is False
    assert (
        "Leading stocks: suppressed because core leadership evidence is unusable"
        in render_daily_report(report)
    )


def test_reporting_read_model_tests_are_explicitly_enforced_in_ci():
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Run daily and weekly report read-model tests" in workflow
    assert "python -m pytest tests/reporting -q" in workflow
