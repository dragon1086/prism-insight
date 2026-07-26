from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.reporting.leadership_tracking import MarketRegime
from prism_core.reporting.models import (
    AnalysisQuality,
    ContextBoard,
    DailyReport,
    LeadershipQuality,
    ReportSource,
    ScenarioCase,
    ShadowStatus,
    WeeklyScenario,
)
from prism_core.reporting.weekly import build_weekly_report, render_weekly_report
from prism_core.strategies.contracts import Market


KR_AS_OF = datetime(2026, 7, 24, 6, 30, tzinfo=timezone.utc)
US_AS_OF = datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc)


def _daily(market: Market, as_of: datetime) -> DailyReport:
    return DailyReport(
        market=market,
        as_of_date=date(2026, 7, 24),
        evaluated_at=as_of,
        leadership_as_of=as_of,
        data_snapshot_id=f"data-{market.value}",
        leadership_snapshot_id=f"leadership-{market.value}",
        sources=(ReportSource(provider="fixture", source_urls=(), evidence_refs=("ev-1",)),),
        leadership_quality=LeadershipQuality(
            status=DataQualityStatus.FRESH,
            core_evidence_usable=True,
            leader_universe_complete=True,
            reasons=(),
        ),
        analysis_quality=AnalysisQuality(
            disposition=QualityDisposition.ACCEPT,
            reasons=(),
            missing_fields=(),
            stale_fields=(),
            skipped=False,
        ),
        market_regime=MarketRegime.MODERATE_BULL,
        market_summary=f"{market.value} regime summary",
        leading_sectors=(),
        leading_stocks=(),
        leadership_changes=(),
        strategies=(),
        shadow_status=ShadowStatus(),
        leadership_markdown=f"# {market.value} Leadership Evidence\n",
    )


def _case(label: str) -> ScenarioCase:
    return ScenarioCase(
        conditions=(f"{label} condition",),
        transmission=(f"{label} transmission",),
        beneficiaries=(f"{label} beneficiary",),
        risks=(f"{label} risk",),
        catalysts=(f"{label} catalyst",),
        falsifiers=(f"{label} falsifier",),
    )


def _scenario(market: Market, as_of: datetime) -> WeeklyScenario:
    return WeeklyScenario(
        market=market,
        week="2026-W30",
        as_of=as_of,
        created_at=as_of,
        context_board=ContextBoard(
            url=f"https://agentnews.md/finance{'-ko' if market is Market.KR else ''}.md",
            updated_at=as_of,
            fetched_at=as_of,
            freshness=DataQualityStatus.FRESH,
            content_hash="a" * 64,
        ),
        switches=("rates",),
        transmission_channels=("discount rate",),
        base=_case("base"),
        bull=_case("bull"),
        bear=_case("bear"),
        variables_to_watch=(),
        event_calendar=(),
        verified_facts=("verified fact",),
        interpretations=("bounded interpretation",),
        counter_evidence=("counter evidence",),
        uncertainties=("known uncertainty",),
        missing_data=(),
        source_urls=("https://example.com/source",),
    )


def test_weekly_report_composes_one_kr_and_one_us_daily_without_collapsing_clocks():
    report = build_weekly_report(
        kr_daily=_daily(Market.KR, KR_AS_OF),
        us_daily=_daily(Market.US, US_AS_OF),
        kr_scenario=_scenario(Market.KR, KR_AS_OF),
        us_scenario=_scenario(Market.US, US_AS_OF),
    )

    assert report.week == "2026-W30"
    assert report.kr_daily.evaluated_at == KR_AS_OF
    assert report.us_daily.evaluated_at == US_AS_OF
    assert report.kr_scenario.context_board.freshness is DataQualityStatus.FRESH
    assert report.us_scenario.context_board.freshness is DataQualityStatus.FRESH
    assert report.kr_scenario.bull.falsifiers == ("bull falsifier",)
    assert report.us_scenario.bear.risks == ("bear risk",)
    assert report.kr_scenario.counter_evidence == ("counter evidence",)
    assert report.shadow_status.evaluation_only is True


def test_weekly_renderer_shows_market_regimes_scenarios_uncertainty_and_sources():
    report = build_weekly_report(
        kr_daily=_daily(Market.KR, KR_AS_OF),
        us_daily=_daily(Market.US, US_AS_OF),
        kr_scenario=_scenario(Market.KR, KR_AS_OF),
        us_scenario=_scenario(Market.US, US_AS_OF),
    )

    rendered = render_weekly_report(report)

    assert "# Weekly KR/US Research Report — 2026-W30" in rendered
    assert "KR evaluated at: 2026-07-24T06:30:00+00:00" in rendered
    assert "US evaluated at: 2026-07-24T20:00:00+00:00" in rendered
    assert "Context quality: FRESH" in rendered
    assert "Bull scenario" in rendered
    assert "Bear scenario" in rendered
    assert "Counter-evidence: counter evidence" in rendered
    assert "Uncertainty: known uncertainty" in rendered
    assert "Falsifiers: bull falsifier" in rendered
    assert "SHADOW Evaluation — Inert" in rendered
    assert "Research report only; no execution authority." in rendered
    assert "# KR Daily Research Report" in rendered
    assert "# US Daily Research Report" in rendered


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (
            {
                "kr_daily": _daily(Market.US, KR_AS_OF),
                "us_daily": _daily(Market.US, US_AS_OF),
                "kr_scenario": _scenario(Market.KR, KR_AS_OF),
                "us_scenario": _scenario(Market.US, US_AS_OF),
            },
            "KR daily",
        ),
        (
            {
                "kr_daily": _daily(Market.KR, KR_AS_OF),
                "us_daily": _daily(Market.US, US_AS_OF),
                "kr_scenario": _scenario(Market.US, KR_AS_OF),
                "us_scenario": _scenario(Market.US, US_AS_OF),
            },
            "KR scenario",
        ),
    ],
)
def test_weekly_builder_rejects_cross_market_composition(kwargs: dict, message: str):
    with pytest.raises(ValueError, match=message):
        build_weekly_report(**kwargs)
