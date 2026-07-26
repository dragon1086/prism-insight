"""Strict immutable contracts for shared daily and weekly report read models."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.llm.trade_plan import (
    MissingOrStaleData,
    ProposedDecision,
    StrategyVersionValue,
    UncertaintyDeclaration,
)
from prism_core.reporting.leadership_tracking import (
    LeadershipChangeItem,
    LeadershipSecurity,
    MarketRegime,
)
from prism_core.strategies.contracts import Market, StrategyId

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ReportModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ReportSource(ReportModel):
    provider: NonEmptyStr
    source_urls: tuple[NonEmptyStr, ...]
    evidence_refs: tuple[NonEmptyStr, ...]


class LeadershipQuality(ReportModel):
    status: DataQualityStatus
    core_evidence_usable: bool
    leader_universe_complete: bool
    reasons: tuple[NonEmptyStr, ...]


class AnalysisQuality(ReportModel):
    disposition: QualityDisposition
    reasons: tuple[NonEmptyStr, ...]
    missing_fields: tuple[NonEmptyStr, ...]
    stale_fields: tuple[NonEmptyStr, ...]
    skipped: bool


class LeadingSector(ReportModel):
    market: Market
    name: NonEmptyStr
    evidence_refs: tuple[NonEmptyStr, ...]


class ProposalReadModel(ReportModel):
    proposal_record_id: NonEmptyStr
    proposed_decision: ProposedDecision
    bull_evidence_ids: tuple[NonEmptyStr, ...]
    bear_evidence_ids: tuple[NonEmptyStr, ...]
    counter_evidence_ids: tuple[NonEmptyStr, ...]
    falsifiers: tuple[NonEmptyStr, ...]
    uncertainty: UncertaintyDeclaration
    missing_or_stale_data: tuple[MissingOrStaleData, ...]


class StrategyReportSection(ReportModel):
    strategy_id: StrategyId
    strategy_version: StrategyVersionValue
    proposed_decision: ProposedDecision | None
    summary: str | None
    analysis_evidence_refs: tuple[NonEmptyStr, ...]
    proposals: tuple[ProposalReadModel, ...]
    shadow_lesson_ids: tuple[NonEmptyStr, ...]


class ShadowStatus(ReportModel):
    label: Literal["SHADOW"] = "SHADOW"
    evaluation_only: Literal[True] = True
    score_effect: Literal[False] = False
    policy_effect: Literal[False] = False
    proposal_effect: Literal[False] = False


class DailyReport(ReportModel):
    report_kind: Literal["DAILY"] = "DAILY"
    market: Market
    as_of_date: date
    evaluated_at: AwareDatetime
    leadership_as_of: AwareDatetime
    data_snapshot_id: NonEmptyStr
    leadership_snapshot_id: NonEmptyStr
    sources: tuple[ReportSource, ...] = Field(min_length=1)
    leadership_quality: LeadershipQuality
    analysis_quality: AnalysisQuality
    market_regime: MarketRegime
    market_summary: NonEmptyStr
    leading_sectors: tuple[LeadingSector, ...]
    leading_stocks: tuple[LeadershipSecurity, ...]
    leadership_changes: tuple[LeadershipChangeItem, ...]
    strategies: tuple[StrategyReportSection, ...]
    shadow_status: ShadowStatus
    leadership_markdown: NonEmptyStr
    safety_notice: Literal["Research report only; no execution authority."] = (
        "Research report only; no execution authority."
    )


class ScenarioCase(ReportModel):
    conditions: tuple[NonEmptyStr, ...] = Field(min_length=1)
    transmission: tuple[NonEmptyStr, ...] = Field(min_length=1)
    beneficiaries: tuple[NonEmptyStr, ...]
    risks: tuple[NonEmptyStr, ...]
    catalysts: tuple[NonEmptyStr, ...]
    falsifiers: tuple[NonEmptyStr, ...] = Field(min_length=1)


class ContextBoard(ReportModel):
    url: NonEmptyStr
    updated_at: AwareDatetime
    fetched_at: AwareDatetime
    freshness: DataQualityStatus
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class VariableToWatch(ReportModel):
    name: NonEmptyStr
    current_value: str | None
    direction: Literal["UP", "DOWN", "FLAT", "MIXED", "UNKNOWN"]
    threshold: str | None
    source_url: NonEmptyStr
    next_check_at: AwareDatetime | None


class CalendarEvent(ReportModel):
    event: NonEmptyStr
    expected_at: AwareDatetime
    affected_markets: tuple[Market, ...] = Field(min_length=1)


class WeeklyScenario(ReportModel):
    market: Market
    week: Annotated[str, Field(pattern=r"^\d{4}-W\d{2}$")]
    as_of: AwareDatetime
    created_at: AwareDatetime
    context_board: ContextBoard
    switches: tuple[NonEmptyStr, ...]
    transmission_channels: tuple[NonEmptyStr, ...]
    base: ScenarioCase
    bull: ScenarioCase
    bear: ScenarioCase
    variables_to_watch: tuple[VariableToWatch, ...]
    event_calendar: tuple[CalendarEvent, ...]
    verified_facts: tuple[NonEmptyStr, ...]
    interpretations: tuple[NonEmptyStr, ...]
    counter_evidence: tuple[NonEmptyStr, ...]
    uncertainties: tuple[NonEmptyStr, ...]
    missing_data: tuple[NonEmptyStr, ...]
    source_urls: tuple[NonEmptyStr, ...] = Field(min_length=1)


class WeeklyReport(ReportModel):
    report_kind: Literal["WEEKLY"] = "WEEKLY"
    week: Annotated[str, Field(pattern=r"^\d{4}-W\d{2}$")]
    kr_daily: DailyReport
    us_daily: DailyReport
    kr_scenario: WeeklyScenario
    us_scenario: WeeklyScenario
    shadow_status: ShadowStatus
    safety_notice: Literal["Research report only; no execution authority."] = (
        "Research report only; no execution authority."
    )
