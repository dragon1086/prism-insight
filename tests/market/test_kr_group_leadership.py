from __future__ import annotations

from datetime import date
from decimal import Decimal

from prism_core.data.contracts import DataQualityStatus
from prism_core.market.kr_group_leadership import (
    GroupSecurityObservation,
    GroupTaxonomyAssignment,
    KISGroupSecurityObservation,
    compute_kr_group_leadership,
)


def _krx_observation(
    symbol: str,
    *,
    current: str,
    previous: str,
    turnover: str,
) -> GroupSecurityObservation:
    return GroupSecurityObservation(
        provider_symbol=symbol,
        current_close=Decimal(current),
        previous_close=Decimal(previous),
        turnover_krw=Decimal(turnover),
        evidence_ids=(f"KRX:equity:{symbol}",),
    )


def _kis_observation(
    symbol: str,
    *,
    return_pct: str,
    turnover: str,
) -> KISGroupSecurityObservation:
    return KISGroupSecurityObservation(
        provider_symbol=symbol,
        return_pct=Decimal(return_pct),
        turnover_krw=Decimal(turnover),
        evidence_ids=("KIS:volume-rank:fixture",),
    )


def test_ranks_with_independent_rs_and_truthful_kis_primary_numeric_provenance() -> None:
    assignments = tuple(
        GroupTaxonomyAssignment(provider_symbol=symbol, group_id=group)
        for symbol, group in (
            ("000001", "KRX:SECTOR:TECH"),
            ("000002", "KRX:SECTOR:TECH"),
            ("000003", "KRX:SECTOR:AUTO"),
            ("000004", "KRX:SECTOR:AUTO"),
            ("000005", "KRX:SECTOR:BIO"),
            ("000006", "KRX:SECTOR:BIO"),
        )
    )
    result = compute_kr_group_leadership(
        session_date=date(2026, 7, 29),
        taxonomy_version="KRX_SECTOR_2026-07-29",
        taxonomy_assignments=assignments,
        krx_observations=(
            _krx_observation("000001", current="110", previous="100", turnover="500000000"),
            _krx_observation("000002", current="120", previous="100", turnover="400000000"),
            _krx_observation("000003", current="105", previous="100", turnover="300000000"),
            _krx_observation("000004", current="95", previous="100", turnover="200000000"),
            _krx_observation("000005", current="101", previous="100", turnover="200000000"),
            _krx_observation("000006", current="102", previous="100", turnover="100000000"),
        ),
        kis_observations=(
            _kis_observation("000001", return_pct="12", turnover="600000000"),
            _kis_observation("000002", return_pct="18", turnover="300000000"),
            _kis_observation("000003", return_pct="6", turnover="350000000"),
        ),
        benchmark_return_pct=Decimal("2"),
        source_quality=DataQualityStatus.FRESH,
        source_evidence_ids=("KRX:market:fixture", "KIS:volume-rank:fixture"),
        min_group_turnover_krw=Decimal("100000000"),
    )

    assert [group.group_id for group in result.ranked_groups] == [
        "KRX:SECTOR:TECH",
        "KRX:SECTOR:BIO",
        "KRX:SECTOR:AUTO",
    ]
    assert [group.rank for group in result.ranked_groups] == [1, 2, 3]
    by_id = {group.group_id: group for group in result.ranked_groups}
    tech = by_id["KRX:SECTOR:TECH"]
    assert tech.source == "KIS+KRX"
    assert tech.momentum_pct == Decimal("15")
    assert tech.relative_strength_pct == Decimal("100")
    assert tech.turnover_krw == Decimal("900000000")
    assert "KIS:volume-rank:fixture" in tech.evidence_ids
    bio = by_id["KRX:SECTOR:BIO"]
    auto = by_id["KRX:SECTOR:AUTO"]
    assert bio.source == "KRX"
    assert "KIS:volume-rank:fixture" not in bio.evidence_ids
    assert auto.momentum_pct < bio.momentum_pct
    assert auto.relative_strength_pct > bio.relative_strength_pct
    assert result.quality is DataQualityStatus.FRESH
    assert result.missing_fields == ()
