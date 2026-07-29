from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from prism_app.kr_candidate_source import KRCandidateSource, LegacyCandidateBatch
from prism_core.candidates import CandidateStatus
from prism_core.data.contracts import DataQualityStatus
from prism_core.market import (
    DeterministicMetric,
    GroupLeadership,
    KRMarketContext,
    MarketContextTiming,
    RegimeAssessment,
    RegimeFeature,
    SessionState,
    SourceClock,
    SourceRole,
    classify_kr_regime,
)


KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 7, 29, 16, 0, tzinfo=KST)


def _unknown_context() -> KRMarketContext:
    return KRMarketContext(
        timing=MarketContextTiming(
            session_date=date(2026, 7, 29),
            session_state=SessionState.COMPLETE,
            as_of=AS_OF,
            ingested_at=AS_OF,
        ),
        source_clocks=(
            SourceClock(
                source="KIS",
                role=SourceRole.PRIMARY,
                observed_at=datetime(2026, 7, 29, 15, 31, tzinfo=KST),
                available_at=datetime(2026, 7, 29, 15, 32, tzinfo=KST),
                ingested_at=AS_OF,
                quality=DataQualityStatus.FRESH,
                evidence_ids=("kis:context",),
            ),
        ),
        index_state=(),
        breadth=(
            DeterministicMetric(
                name="volume_rank_returned_security_count",
                value=Decimal("30"),
                unit="count",
                source="KIS",
                evidence_ids=("kis:context",),
            ),
        ),
        investor_flows=(),
        macro_indicators=(),
        group_leadership=(),
        regime=RegimeAssessment.unknown(
            missing_features=("kospi_close", "kospi_ma20", "kospi_return_10d_pct")
        ),
        evidence_ids=("kis:context",),
        quality=DataQualityStatus.UNAVAILABLE,
        conflicts=(),
        missing_fields=("index_state", "regime_features"),
    )


def _known_context() -> KRMarketContext:
    regime = classify_kr_regime(
        (
            RegimeFeature(name="kospi_close", value=Decimal("110"), unit="index_points", evidence_ids=("kis:index",)),
            RegimeFeature(name="kospi_ma20", value=Decimal("100"), unit="index_points", evidence_ids=("kis:index",)),
            RegimeFeature(name="kospi_return_10d_pct", value=Decimal("5.1"), unit="percent", evidence_ids=("kis:index",)),
        )
    )
    return KRMarketContext(
        timing=MarketContextTiming(
            session_date=date(2026, 7, 29),
            session_state=SessionState.COMPLETE,
            as_of=AS_OF,
            ingested_at=AS_OF,
        ),
        source_clocks=(
            SourceClock(
                source="KIS",
                role=SourceRole.PRIMARY,
                observed_at=datetime(2026, 7, 29, 15, 31, tzinfo=KST),
                available_at=datetime(2026, 7, 29, 15, 32, tzinfo=KST),
                ingested_at=AS_OF,
                quality=DataQualityStatus.FRESH,
                evidence_ids=("kis:context",),
            ),
        ),
        index_state=(),
        breadth=(),
        investor_flows=(),
        macro_indicators=(),
        group_leadership=(),
        regime=regime,
        evidence_ids=("kis:context", "kis:index"),
        quality=DataQualityStatus.FRESH,
        conflicts=(),
        missing_fields=(),
    )


def _batch(rows: int = 5) -> LegacyCandidateBatch:
    frame = pd.DataFrame(
        {
            "stock_name": [f"name-{index}" for index in range(rows)],
            "composite_score": [index / 10 for index in range(rows)],
            "Close": [10_000 + index for index in range(rows)],
        },
        index=[f"{index:06d}" for index in range(1, rows + 1)],
    )
    return LegacyCandidateBatch(
        trade_date="20260729",
        source="krx",
        source_snapshot_id="krx:20260729:snapshot",
        observed_at=datetime(2026, 7, 29, 15, 30, tzinfo=KST),
        available_at=datetime(2026, 7, 29, 15, 35, tzinfo=KST),
        ingested_at=datetime(2026, 7, 29, 15, 35, tzinfo=KST),
        evidence_ids=("krx:20260729:snapshot",),
        triggers={"일중 상승률 상위주": frame},
    )


def test_unknown_context_is_propagated_exactly_and_exports_every_candidate_report_only() -> None:
    context = _unknown_context()
    seen = []

    def discover(trigger_time, legacy_context):
        seen.append((trigger_time, legacy_context))
        return _batch()

    source = KRCandidateSource(
        legacy_discover=discover,
        screening_signals=lambda *_args: {
            "return_nd": 4.2,
            "extension_score": 0.8,
            "extension_in_adr": 1.1,
        },
        legacy_select=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("UNKNOWN must not enter legacy action scoring")
        ),
    )

    result = source.discover(trigger_time="afternoon", market_context=context)

    assert result.market_context is context
    assert seen == [
        (
            "afternoon",
            {
                "market_regime": "unknown",
                "session_state": "COMPLETE",
                "context_session_date": "20260729",
                "market_observed_date": "20260729",
                "leading_sectors": [],
                "sector_map": {},
                "context_id": context.content_hash,
            },
        )
    ]
    assert len(result.snapshots) == 5
    assert len(result.reconciliation.included) == 5
    assert result.reconciliation.truncated_candidate_count == 0
    assert {snapshot.status for snapshot in result.snapshots} == {
        CandidateStatus.REPORT_ONLY
    }
    assert all("SIDEWAYS" not in issue for snapshot in result.snapshots for issue in snapshot.issues)


def test_known_context_preserves_scores_clocks_identity_and_reports_legacy_cap_parity() -> None:
    context = _known_context()
    batch = _batch()
    batch.triggers["일중 상승률 상위주"].loc["000001", "stock_name"] = None
    selected = {
        "일중 상승률 상위주": batch.triggers["일중 상승률 상위주"].iloc[:3]
    }
    source = KRCandidateSource(
        legacy_discover=lambda *_args: batch,
        screening_signals=lambda *_args: {
            "return_nd": "4.20",
            "extension_score": "0.80",
            "extension_in_adr": "1.10",
            "oneil_raw": "0.37",
        },
        legacy_select=lambda *_args, **_kwargs: selected,
    )

    result = source.discover(trigger_time="afternoon", market_context=context)

    assert len(result.snapshots) == 5
    assert result.reconciliation.included_identity_count == 5
    assert result.legacy_selected_symbols == ("000001", "000002", "000003")
    assert [item.provider_symbol for item in result.parity_discrepancies] == [
        "000004",
        "000005",
    ]
    first = result.snapshots[0]
    assert first.display_name == "000001"
    assert first.status is CandidateStatus.ELIGIBLE
    assert first.raw_scores == {
        "일중 상승률 상위주.composite_score": Decimal("0.0"),
        "일중 상승률 상위주.extension_in_adr": Decimal("1.10"),
        "일중 상승률 상위주.extension_score": Decimal("0.80"),
        "일중 상승률 상위주.oneil_raw": Decimal("0.37"),
        "일중 상승률 상위주.return_nd": Decimal("4.20"),
    }
    assert first.observed_at == batch.observed_at
    assert first.available_at == batch.available_at
    assert first.ingested_at == batch.ingested_at
    assert "kis:context" in first.evidence_ids

    renamed = _batch(1)
    renamed.triggers["일중 상승률 상위주"].loc["000001", "stock_name"] = "renamed"
    renamed_source = KRCandidateSource(
        legacy_discover=lambda *_args: renamed,
        screening_signals=lambda *_args: {},
        legacy_select=lambda *_args, **_kwargs: renamed.triggers,
    )
    renamed_result = renamed_source.discover(
        trigger_time="afternoon", market_context=context
    )
    assert renamed_result.snapshots[0].security_id == first.security_id


def test_screening_signal_failure_isolated_per_identity_without_truncation() -> None:
    batch = _batch(3)

    def signals(symbol, *_args):
        if symbol == "000002":
            raise TimeoutError("read-only history unavailable")
        return {"return_nd": "1", "extension_score": "1", "extension_in_adr": "0"}

    source = KRCandidateSource(
        legacy_discover=lambda *_args: batch,
        screening_signals=signals,
        legacy_select=lambda *_args, **_kwargs: batch.triggers,
    )

    result = source.discover(
        trigger_time="afternoon", market_context=_known_context()
    )

    assert len(result.snapshots) == 3
    failed = next(item for item in result.snapshots if item.provider_symbol == "000002")
    assert failed.status is CandidateStatus.REPORT_ONLY
    assert "SCREENING_SIGNALS_UNAVAILABLE" in failed.issues


def test_invalid_discovered_identity_is_excluded_without_aborting_valid_siblings() -> None:
    batch = _batch(1)
    frame = batch.triggers["일중 상승률 상위주"]
    invalid = frame.iloc[[0]].copy()
    invalid.index = [""]
    batch = replace(
        batch,
        triggers={"일중 상승률 상위주": pd.concat([invalid, frame])},
    )
    source = KRCandidateSource(
        legacy_discover=lambda *_args: batch,
        screening_signals=lambda *_args: {},
        legacy_select=lambda *_args, **_kwargs: batch.triggers,
    )

    result = source.discover(
        trigger_time="afternoon", market_context=_known_context()
    )

    assert [item.provider_symbol for item in result.snapshots] == ["000001"]
    assert result.reconciliation.input_count == 2
    assert result.reconciliation.invalid_record_count == 1
    assert result.reconciliation.excluded[0].exclusion_reason.startswith(
        "INVALID_CANDIDATE:"
    )


def test_leadership_context_uses_authoritative_fields_and_injected_sector_map() -> None:
    captured = []
    context = _known_context().model_copy(
        update={
            "group_leadership": (
                GroupLeadership(
                    group_id="KRX:SECTOR:SEMICONDUCTOR",
                    rank=1,
                    concentration_pct=Decimal("42.5"),
                    source="KIS",
                    evidence_ids=("kis:sector:semiconductor",),
                ),
            )
        }
    )
    source = KRCandidateSource(
        legacy_discover=lambda _trigger_time, macro_context: captured.append(
            macro_context
        )
        or _batch(1),
        legacy_select=lambda *_args, **_kwargs: {},
        screening_signals=lambda *_args, **_kwargs: {},
        sector_map={"005930": "KRX:SECTOR:SEMICONDUCTOR"},
    )

    source.discover(trigger_time="afternoon", market_context=context)

    assert captured[0]["leading_sectors"] == [
        {"sector": "KRX:SECTOR:SEMICONDUCTOR", "confidence": 0.425}
    ]
    assert captured[0]["sector_map"] == {
        "005930": "KRX:SECTOR:SEMICONDUCTOR"
    }
