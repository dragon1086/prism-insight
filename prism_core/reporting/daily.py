"""Pure construction and deterministic rendering of the daily report read model."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Mapping, Protocol
from uuid import UUID

from prism_core.data.quality import QualityDecision, QualityDisposition, QualitySkipRecord
from prism_core.feedback.repository import StoredProposal
from prism_core.feedback.retrieval import EvaluationLessonSet
from prism_core.llm.trade_plan import (
    ProposedDecision,
    ScoreComponentName,
    TradePlanProposal,
)
from prism_core.reporting.leadership_tracking import (
    PROVIDER as LEADERSHIP_PROVIDER,
    StoredLeadershipRun,
)
from prism_core.reporting.models import (
    AnalysisQuality,
    DailyReport,
    LeadingSector,
    LeadershipQuality,
    ProposalReadModel,
    ProductScenarioReadModel,
    ReportSource,
    ShadowStatus,
    StrategyReportSection,
)
from prism_core.reporting.scenario_completeness import (
    ProductScenarioState,
    sanitize_scenario_reasons,
)
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


class StrategyAnalysisPort(Protocol):
    @property
    def strategy_id(self) -> StrategyId: ...

    @property
    def strategy_version(self) -> StrategyVersion: ...

    @property
    def output_payload(self) -> Mapping[str, object]: ...

    @property
    def evidence_refs(self) -> tuple[str, ...]: ...


class PersistedDailyAnalysisPort(Protocol):
    @property
    def leadership_snapshot_id(self) -> str: ...

    @property
    def leadership_report_id(self) -> str: ...

    @property
    def market(self) -> Market: ...

    @property
    def as_of_date(self) -> date: ...

    @property
    def evaluated_at(self) -> datetime: ...

    @property
    def data_snapshot_id(self) -> UUID: ...

    @property
    def source_payload(self) -> Mapping[str, object]: ...

    @property
    def strategies(self) -> tuple[StrategyAnalysisPort, ...]: ...

    @property
    def quality_decision(self) -> QualityDecision: ...

    @property
    def quality_skip(self) -> QualitySkipRecord | None: ...


class StrategyEvaluationPort(Protocol):
    @property
    def proposals(self) -> tuple[StoredProposal, ...]: ...

    @property
    def shadow_evaluation(self) -> EvaluationLessonSet: ...


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _strategy_identity(evaluation: StrategyEvaluationPort) -> tuple[StrategyId, str]:
    shadow = evaluation.shadow_evaluation
    return shadow.strategy_id, shadow.strategy_version.value


def _parse_current_proposals(
    *,
    evaluation: StrategyEvaluationPort,
    strategy_id: StrategyId,
    strategy_version: StrategyVersion,
    market: Market,
    data_snapshot_id: UUID,
) -> tuple[ProposalReadModel, ...]:
    result: list[ProposalReadModel] = []
    for stored in evaluation.proposals:
        if stored.normalized_proposal_json is None:
            continue
        proposal = TradePlanProposal.model_validate_json(stored.normalized_proposal_json)
        if (
            proposal.strategy_id is not strategy_id
            or proposal.strategy_version != strategy_version
            or proposal.market is not market
        ):
            raise ValueError("stored proposal has mismatched exact strategy identity")
        if proposal.feature_provenance.data_snapshot_id != data_snapshot_id:
            continue
        counter_component_ids = (
            evidence_id
            for component in proposal.score_breakdown
            if component.name is ScoreComponentName.COUNTER_EVIDENCE_UNCERTAINTY
            for evidence_id in component.evidence_ids
        )
        result.append(
            ProposalReadModel(
                proposal_record_id=stored.proposal_record_id,
                proposed_decision=proposal.decision,
                bull_evidence_ids=proposal.bull_evidence_ids,
                bear_evidence_ids=proposal.bear_evidence_ids,
                counter_evidence_ids=_unique(
                    (*proposal.bear_evidence_ids, *counter_component_ids)
                ),
                falsifiers=proposal.regime.falsifiers,
                uncertainty=proposal.uncertainty,
                missing_or_stale_data=proposal.missing_or_stale_data,
            )
        )
    return tuple(sorted(result, key=lambda item: item.proposal_record_id))


def build_daily_report(
    *,
    analysis: PersistedDailyAnalysisPort,
    leadership: StoredLeadershipRun,
    strategy_evaluations: tuple[StrategyEvaluationPort, ...],
    leading_sectors: tuple[LeadingSector, ...],
) -> DailyReport:
    """Adapt Task 20 persisted/query outputs without persistence or external effects."""

    if analysis.leadership_snapshot_id != leadership.snapshot_id:
        raise ValueError("leadership snapshot identity does not match persisted analysis")
    if analysis.leadership_report_id != leadership.report_id:
        raise ValueError("leadership report identity does not match persisted analysis")
    if analysis.market.value != leadership.snapshot.market.value:
        raise ValueError("leadership market does not match persisted analysis")
    if any(sector.market is not analysis.market for sector in leading_sectors):
        raise ValueError("leading sector market does not match persisted analysis")

    analyses_by_identity = {
        (item.strategy_id, item.strategy_version.value): item
        for item in analysis.strategies
    }
    evaluations_by_identity = {
        _strategy_identity(item): item for item in strategy_evaluations
    }
    if len(evaluations_by_identity) != len(strategy_evaluations):
        raise ValueError("strategy evaluation identities must be unique")
    if set(analyses_by_identity) != set(evaluations_by_identity):
        raise ValueError("strategy identity/version inputs do not match exactly")

    if analysis.quality_decision.disposition is not QualityDisposition.ACCEPT:
        if analysis.strategies or strategy_evaluations:
            raise ValueError("non-ACCEPT analysis cannot expose strategy evaluations")
    elif analysis.quality_skip is not None:
        raise ValueError("ACCEPT analysis cannot carry a quality skip")
    elif {identity[0] for identity in analyses_by_identity} != {
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    }:
        raise ValueError("ACCEPT report requires separate SWING_V1 and TREND_V1 sections")

    strategy_sections: list[StrategyReportSection] = []
    for identity in sorted(analyses_by_identity, key=lambda item: item[0].value):
        item = analyses_by_identity[identity]
        evaluation = evaluations_by_identity[identity]
        shadow = evaluation.shadow_evaluation
        if shadow.as_of > analysis.evaluated_at:
            raise ValueError("SHADOW evaluation includes information after report as-of")
        for lesson in shadow.lessons:
            if (
                lesson.strategy_id is not item.strategy_id
                or lesson.strategy_version != item.strategy_version
                or lesson.influence.score_delta != 0
                or lesson.influence.policy_effect
                or lesson.influence.proposal_effect
            ):
                raise ValueError("SHADOW material must remain exact-strategy and inert")
        output = item.output_payload
        decision_value = output.get("decision") if hasattr(output, "get") else None
        try:
            decision = None if decision_value is None else ProposedDecision(decision_value)
        except ValueError:
            decision = None
        summary_value = output.get("summary") if hasattr(output, "get") else None
        summary = summary_value if isinstance(summary_value, str) and summary_value.strip() else None
        state_value = output.get("scenario_state") if hasattr(output, "get") else None
        try:
            scenario_state = ProductScenarioState(state_value)
        except (TypeError, ValueError):
            scenario_state = ProductScenarioState.ANALYSIS_INCOMPLETE
        scenario_complete = output.get("scenario_complete") is True
        reason_values = output.get("scenario_reasons", ()) if hasattr(output, "get") else ()
        scenario_reasons = sanitize_scenario_reasons(
            reason_values if isinstance(reason_values, (tuple, list)) else ()
        )
        scenario_value = output.get("scenario") if hasattr(output, "get") else None
        try:
            scenario = (
                ProductScenarioReadModel.model_validate(scenario_value)
                if scenario_complete
                else None
            )
        except (TypeError, ValueError):
            scenario = None
            scenario_complete = False
            scenario_state = ProductScenarioState.ANALYSIS_INCOMPLETE
            scenario_reasons = sanitize_scenario_reasons(
                (*scenario_reasons, "persisted scenario failed report-contract validation")
            )
        if scenario_complete and (
            scenario is None
            or decision is None
            or scenario.current_action is not decision
            or scenario_state.value != decision.value
        ):
            scenario = None
            decision = None
            scenario_complete = False
            scenario_state = ProductScenarioState.ANALYSIS_INCOMPLETE
            scenario_reasons = sanitize_scenario_reasons(
                (*scenario_reasons, "scenario state and decision identity mismatch")
            )
        if not scenario_complete:
            decision = None
        strategy_sections.append(
            StrategyReportSection(
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
                proposed_decision=decision,
                scenario_state=scenario_state,
                scenario_complete=scenario_complete,
                scenario_reasons=scenario_reasons,
                scenario=scenario,
                summary=summary,
                analysis_evidence_refs=item.evidence_refs,
                proposals=_parse_current_proposals(
                    evaluation=evaluation,
                    strategy_id=item.strategy_id,
                    strategy_version=item.strategy_version,
                    market=analysis.market,
                    data_snapshot_id=analysis.data_snapshot_id,
                ),
                shadow_lesson_ids=tuple(lesson.lesson_id for lesson in shadow.lessons),
            )
        )

    application_provider = analysis.source_payload.get("provider", "application_snapshot")
    if not isinstance(application_provider, str) or not application_provider.strip():
        application_provider = "application_snapshot"
    application_evidence = _unique(
        evidence
        for item in analysis.strategies
        for evidence in item.evidence_refs
    )
    leadership_source = leadership.snapshot.source
    sources = (
        ReportSource(
            provider=LEADERSHIP_PROVIDER,
            source_urls=tuple(str(url) for url in leadership_source.source_urls),
            evidence_refs=leadership_source.evidence_refs,
        ),
        ReportSource(
            provider=application_provider,
            source_urls=(),
            evidence_refs=application_evidence,
        ),
    )
    decision = analysis.quality_decision
    snapshot = leadership.snapshot
    return DailyReport(
        market=analysis.market,
        as_of_date=analysis.as_of_date,
        evaluated_at=analysis.evaluated_at,
        leadership_as_of=snapshot.as_of,
        data_snapshot_id=str(analysis.data_snapshot_id),
        leadership_snapshot_id=leadership.snapshot_id,
        sources=sources,
        leadership_quality=LeadershipQuality(
            status=snapshot.quality,
            core_evidence_usable=snapshot.core_evidence_usable,
            leader_universe_complete=snapshot.leader_universe_complete,
            reasons=snapshot.quality_reasons,
        ),
        analysis_quality=AnalysisQuality(
            disposition=decision.disposition,
            reasons=decision.reasons,
            missing_fields=decision.missing_fields,
            stale_fields=decision.stale_fields,
            skipped=analysis.quality_skip is not None,
        ),
        market_regime=snapshot.market_state.regime,
        market_summary=snapshot.market_state.summary,
        leading_sectors=tuple(sorted(leading_sectors, key=lambda item: item.name)),
        leading_stocks=snapshot.leaders if snapshot.core_evidence_usable else (),
        leadership_changes=leadership.changes,
        strategies=tuple(strategy_sections),
        shadow_status=ShadowStatus(),
        leadership_markdown=leadership.rendered_markdown,
    )


def render_daily_report(report: DailyReport) -> str:
    """Render the read model while delegating leadership detail to its stored renderer."""

    lines = [
        f"# {report.market.value} Daily Research Report",
        "",
        "## Report Context",
        f"- As of date: {report.as_of_date.isoformat()}",
        f"- Evaluated at: {report.evaluated_at.isoformat()}",
        f"- Leadership as of: {report.leadership_as_of.isoformat()}",
        f"- Analysis quality: {report.analysis_quality.disposition.value}",
        f"- Leadership quality: {report.leadership_quality.status.value}",
        "- Leading stocks: "
        + (
            "available from the stored leadership readback"
            if report.leadership_quality.core_evidence_usable
            else "suppressed because core leadership evidence is unusable"
        ),
        f"- Safety: {report.safety_notice}",
        "",
        "## Leading Sectors",
    ]
    if report.leading_sectors:
        lines.extend(
            f"- {sector.name} (evidence: {', '.join(sector.evidence_refs)})"
            for sector in report.leading_sectors
        )
    else:
        lines.append("- Leading sectors: none identified")
    lines.extend(("", "## Strategy Differences"))
    if report.strategies:
        for section in report.strategies:
            lines.append(
                f"### {section.strategy_id.value} ({section.strategy_version.value})"
            )
            lines.append(
                f"- Proposal status: {section.proposed_decision.value if section.proposed_decision else 'NOT_AVAILABLE'}"
            )
            lines.append(f"- Scenario state: {section.scenario_state.value}")
            lines.append(f"- Scenario complete: {section.scenario_complete}")
            if section.scenario_reasons:
                lines.append(f"- Scenario reasons: {'; '.join(section.scenario_reasons)}")
            if section.scenario is not None:
                lines.append(f"- Next review: {section.scenario.next_review_at.isoformat()}")
                lines.append(
                    f"- Current action: {section.scenario.current_action.value}"
                )
            if section.summary:
                lines.append(f"- Summary: {section.summary}")
            for proposal in section.proposals:
                lines.extend(
                    (
                        f"- Bull evidence: {', '.join(proposal.bull_evidence_ids)}",
                        f"- Bear evidence: {', '.join(proposal.bear_evidence_ids)}",
                        f"- Counter-evidence: {', '.join(proposal.counter_evidence_ids)}",
                        f"- Falsifiers: {'; '.join(proposal.falsifiers)}",
                        f"- Uncertainty: {proposal.uncertainty.level}",
                    )
                )
    else:
        lines.append("- No strategy evaluation due to the recorded quality disposition")
    lines.extend(
        (
            "",
            "## SHADOW Evaluation — Inert",
            "- SHADOW material is evaluation-only.",
            "- Score effect: NONE",
            "- Policy effect: NONE",
            "- Proposal effect: NONE",
            "",
            report.leadership_markdown.rstrip(),
            "",
        )
    )
    return "\n".join(lines)
