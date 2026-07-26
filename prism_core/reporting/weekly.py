"""Pure composition and deterministic rendering of weekly KR/US read models."""

from __future__ import annotations

from prism_core.reporting.daily import render_daily_report
from prism_core.reporting.models import (
    DailyReport,
    ScenarioCase,
    ShadowStatus,
    WeeklyReport,
    WeeklyScenario,
)
from prism_core.strategies.contracts import Market


def build_weekly_report(
    *,
    kr_daily: DailyReport,
    us_daily: DailyReport,
    kr_scenario: WeeklyScenario,
    us_scenario: WeeklyScenario,
) -> WeeklyReport:
    """Compose pre-validated daily/scenario inputs without I/O or recomputation."""

    if kr_daily.market is not Market.KR:
        raise ValueError("KR daily input must have market KR")
    if us_daily.market is not Market.US:
        raise ValueError("US daily input must have market US")
    if kr_scenario.market is not Market.KR:
        raise ValueError("KR scenario input must have market KR")
    if us_scenario.market is not Market.US:
        raise ValueError("US scenario input must have market US")
    if kr_scenario.week != us_scenario.week:
        raise ValueError("KR and US scenarios must use the same ISO week")
    for label, daily, scenario in (
        ("KR", kr_daily, kr_scenario),
        ("US", us_daily, us_scenario),
    ):
        year, week, _ = daily.as_of_date.isocalendar()
        expected = f"{year:04d}-W{week:02d}"
        if scenario.week != expected:
            raise ValueError(f"{label} scenario week does not contain its daily as-of date")
        if scenario.as_of > scenario.created_at:
            raise ValueError(f"{label} scenario cannot be created before its as-of")
        if scenario.context_board.updated_at > scenario.context_board.fetched_at:
            raise ValueError(f"{label} context board cannot be fetched before its update")
    return WeeklyReport(
        week=kr_scenario.week,
        kr_daily=kr_daily,
        us_daily=us_daily,
        kr_scenario=kr_scenario,
        us_scenario=us_scenario,
        shadow_status=ShadowStatus(),
    )


def _render_case(label: str, scenario: ScenarioCase) -> list[str]:
    return [
        f"### {label} scenario",
        f"- Conditions: {'; '.join(scenario.conditions)}",
        f"- Transmission: {'; '.join(scenario.transmission)}",
        f"- Beneficiaries: {'; '.join(scenario.beneficiaries) or 'none identified'}",
        f"- Risks: {'; '.join(scenario.risks) or 'none identified'}",
        f"- Catalysts: {'; '.join(scenario.catalysts) or 'none identified'}",
        f"- Falsifiers: {'; '.join(scenario.falsifiers)}",
    ]


def _render_market_scenario(scenario: WeeklyScenario) -> list[str]:
    lines = [
        f"## {scenario.market.value} Weekly Scenario",
        f"- Scenario as of: {scenario.as_of.isoformat()}",
        f"- Context source: {scenario.context_board.url}",
        f"- Context updated at: {scenario.context_board.updated_at.isoformat()}",
        f"- Context fetched at: {scenario.context_board.fetched_at.isoformat()}",
        f"- Context quality: {scenario.context_board.freshness.value}",
        f"- Sources: {', '.join(scenario.source_urls)}",
        f"- Switches: {'; '.join(scenario.switches) or 'none identified'}",
        f"- Transmission channels: {'; '.join(scenario.transmission_channels) or 'none identified'}",
        "",
    ]
    lines.extend(_render_case("Base", scenario.base))
    lines.append("")
    lines.extend(_render_case("Bull", scenario.bull))
    lines.append("")
    lines.extend(_render_case("Bear", scenario.bear))
    lines.extend(
        (
            "",
            f"- Verified facts: {'; '.join(scenario.verified_facts) or 'none available'}",
            f"- Interpretations: {'; '.join(scenario.interpretations) or 'none available'}",
            f"- Counter-evidence: {'; '.join(scenario.counter_evidence) or 'none available'}",
            f"- Uncertainty: {'; '.join(scenario.uncertainties) or 'none declared'}",
            f"- Missing data: {'; '.join(scenario.missing_data) or 'none declared'}",
        )
    )
    return lines


def render_weekly_report(report: WeeklyReport) -> str:
    """Render both markets while preserving their independent clocks and provenance."""

    lines = [
        f"# Weekly KR/US Research Report — {report.week}",
        "",
        "## Independent Market Clocks",
        f"- KR evaluated at: {report.kr_daily.evaluated_at.isoformat()}",
        f"- US evaluated at: {report.us_daily.evaluated_at.isoformat()}",
        f"- Safety: {report.safety_notice}",
        "",
    ]
    lines.extend(_render_market_scenario(report.kr_scenario))
    lines.extend(("",))
    lines.extend(_render_market_scenario(report.us_scenario))
    lines.extend(
        (
            "",
            "## SHADOW Evaluation — Inert",
            "- SHADOW material is evaluation-only.",
            "- Score effect: NONE",
            "- Policy effect: NONE",
            "- Proposal effect: NONE",
            "",
            render_daily_report(report.kr_daily).rstrip(),
            "",
            render_daily_report(report.us_daily).rstrip(),
            "",
        )
    )
    return "\n".join(lines)
