"""Deterministic KIS-primary and KRX-official KR sector leadership."""

from __future__ import annotations

from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from typing import Annotated, Iterable, Literal

from pydantic import Field, field_validator

from prism_core.data.contracts import (
    ContractModel,
    DataQualityStatus,
    NonEmptyStr,
    NonNegativeDecimal,
    PositiveDecimal,
)
from prism_core.market.context import (
    FiniteDecimal,
    GroupLeadership,
    GroupLeadershipExclusion,
)

_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class GroupTaxonomyAssignment(ContractModel):
    provider_symbol: Annotated[str, Field(pattern=r"^\d{6}$")]
    group_id: NonEmptyStr


class GroupSecurityObservation(ContractModel):
    """Official KRX complete-universe observation and classification member."""

    provider_symbol: Annotated[str, Field(pattern=r"^\d{6}$")]
    current_close: PositiveDecimal
    previous_close: PositiveDecimal
    turnover_krw: NonNegativeDecimal
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("observation evidence IDs must be unique")
        return tuple(sorted(value))


class KISGroupSecurityObservation(ContractModel):
    """Primary KIS numeric evidence available in the volume-rank response."""

    provider_symbol: Annotated[str, Field(pattern=r"^\d{6}$")]
    return_pct: FiniteDecimal
    turnover_krw: NonNegativeDecimal
    evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator("evidence_ids", mode="after")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("KIS observation evidence IDs must be unique")
        return tuple(sorted(value))


class GroupExclusionReason(str, Enum):
    LOW_TURNOVER = "LOW_TURNOVER"


class KRGroupLeadershipComputation(ContractModel):
    version: Literal["kr_group_leadership_v1"] = "kr_group_leadership_v1"
    session_date: date
    taxonomy_version: NonEmptyStr
    ranked_groups: tuple[GroupLeadership, ...]
    excluded_groups: tuple[GroupLeadershipExclusion, ...]
    quality: DataQualityStatus
    conflicts: tuple[NonEmptyStr, ...]
    missing_fields: tuple[NonEmptyStr, ...]
    reason_codes: tuple[NonEmptyStr, ...] = Field(min_length=1)
    source_evidence_ids: tuple[NonEmptyStr, ...] = Field(min_length=1)

    @field_validator(
        "conflicts", "missing_fields", "reason_codes", "source_evidence_ids", mode="after"
    )
    @classmethod
    def normalize_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("leadership identifiers must be unique")
        return tuple(sorted(value))


class _GroupAggregate:
    def __init__(
        self,
        *,
        group_id: str,
        krx_observations: tuple[GroupSecurityObservation, ...],
        kis_by_symbol: dict[str, KISGroupSecurityObservation],
        benchmark_return_pct: Decimal,
    ) -> None:
        self.group_id = group_id
        selected_returns: list[Decimal] = []
        selected_turnovers: list[Decimal] = []
        evidence_ids: set[str] = set()
        kis_count = 0
        with localcontext(_DECIMAL_CONTEXT):
            krx_returns = {
                item.provider_symbol: (
                    item.current_close / item.previous_close - Decimal(1)
                )
                * Decimal(100)
                for item in krx_observations
            }
            for item in krx_observations:
                evidence_ids.update(item.evidence_ids)
                kis_item = kis_by_symbol.get(item.provider_symbol)
                if kis_item is None:
                    selected_returns.append(krx_returns[item.provider_symbol])
                    selected_turnovers.append(item.turnover_krw)
                    continue
                kis_count += 1
                selected_returns.append(kis_item.return_pct)
                selected_turnovers.append(kis_item.turnover_krw)
                evidence_ids.update(kis_item.evidence_ids)
            self.member_count = len(selected_returns)
            self.advance_count = sum(value > 0 for value in krx_returns.values())
            self.breadth_pct = (
                Decimal(self.advance_count) / Decimal(self.member_count) * Decimal(100)
            )
            self.momentum_pct = sum(selected_returns, start=Decimal(0)) / Decimal(
                self.member_count
            )
            outperform_count = sum(
                value > benchmark_return_pct for value in selected_returns
            )
            self.relative_strength_pct = (
                Decimal(outperform_count) / Decimal(self.member_count) * Decimal(100)
            )
            self.turnover_krw = sum(selected_turnovers, start=Decimal(0))
        self.kis_numeric_member_count = kis_count
        self.evidence_ids = tuple(sorted(evidence_ids))
        self.source = "KIS+KRX" if kis_count else "KRX"


def _normalized_component(value: Decimal, values: tuple[Decimal, ...]) -> Decimal:
    low = min(values)
    high = max(values)
    if high == low:
        return Decimal("50")
    with localcontext(_DECIMAL_CONTEXT):
        return (value - low) / (high - low) * Decimal(100)


def _concentration_pct(turnover: Decimal, total_turnover: Decimal) -> Decimal:
    if not total_turnover:
        return Decimal(0)
    with localcontext(_DECIMAL_CONTEXT):
        return turnover / total_turnover * Decimal(100)


def _result(
    *,
    session_date: date,
    taxonomy_version: str,
    quality: DataQualityStatus,
    source_evidence_ids: tuple[str, ...],
    ranked_groups: tuple[GroupLeadership, ...] = (),
    excluded_groups: tuple[GroupLeadershipExclusion, ...] = (),
    conflicts: tuple[str, ...] = (),
    missing_fields: tuple[str, ...] = (),
    reason_codes: tuple[str, ...],
) -> KRGroupLeadershipComputation:
    with localcontext(_DECIMAL_CONTEXT):
        return KRGroupLeadershipComputation(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            ranked_groups=ranked_groups,
            excluded_groups=excluded_groups,
            quality=quality,
            conflicts=conflicts,
            missing_fields=missing_fields,
            reason_codes=reason_codes,
            source_evidence_ids=source_evidence_ids,
        )


def compute_kr_group_leadership(
    *,
    session_date: date,
    taxonomy_version: str,
    taxonomy_assignments: Iterable[GroupTaxonomyAssignment],
    krx_observations: Iterable[GroupSecurityObservation],
    kis_observations: Iterable[KISGroupSecurityObservation],
    benchmark_return_pct: Decimal,
    source_quality: DataQualityStatus,
    source_evidence_ids: tuple[str, ...],
    min_group_turnover_krw: Decimal,
) -> KRGroupLeadershipComputation:
    """Rank sectors with KIS numeric values where present and KRX official coverage."""

    if not taxonomy_version:
        raise ValueError("taxonomy_version must not be empty")
    if not source_evidence_ids or any(not item for item in source_evidence_ids):
        raise ValueError("source_evidence_ids must contain non-empty identifiers")
    if len(set(source_evidence_ids)) != len(source_evidence_ids):
        raise ValueError("source_evidence_ids must be unique")
    if not benchmark_return_pct.is_finite():
        raise ValueError("benchmark_return_pct must be finite")
    if not min_group_turnover_krw.is_finite() or min_group_turnover_krw < 0:
        raise ValueError("min_group_turnover_krw must be non-negative and finite")

    assignments = tuple(taxonomy_assignments)
    krx_items = tuple(krx_observations)
    kis_items = tuple(kis_observations)
    conflicts: set[str] = set()
    groups_by_symbol: dict[str, str] = {}
    for assignment in assignments:
        prior = groups_by_symbol.setdefault(assignment.provider_symbol, assignment.group_id)
        if prior != assignment.group_id:
            conflicts.add(f"TAXONOMY_CONFLICT:{assignment.provider_symbol}")
    krx_by_symbol: dict[str, GroupSecurityObservation] = {}
    for item in krx_items:
        if item.provider_symbol in krx_by_symbol:
            conflicts.add(f"DUPLICATE_KRX_OBSERVATION:{item.provider_symbol}")
        else:
            krx_by_symbol[item.provider_symbol] = item
    kis_by_symbol: dict[str, KISGroupSecurityObservation] = {}
    for item in kis_items:
        if item.provider_symbol in kis_by_symbol:
            conflicts.add(f"DUPLICATE_KIS_OBSERVATION:{item.provider_symbol}")
        else:
            kis_by_symbol[item.provider_symbol] = item
    if source_quality is DataQualityStatus.CONFLICT:
        conflicts.add("SOURCE_QUALITY_CONFLICT")
    if conflicts:
        return _result(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            quality=DataQualityStatus.CONFLICT,
            source_evidence_ids=tuple(sorted(source_evidence_ids)),
            conflicts=tuple(sorted(conflicts)),
            reason_codes=("AUTHORITATIVE_SOURCE_CONFLICT",),
        )
    if source_quality is not DataQualityStatus.FRESH:
        return _result(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            quality=source_quality,
            source_evidence_ids=tuple(sorted(source_evidence_ids)),
            missing_fields=("source_quality",),
            reason_codes=(f"AUTHORITATIVE_SOURCE_{source_quality.value}",),
        )
    missing = tuple(
        sorted(
            f"observation:{symbol}"
            for symbol in groups_by_symbol
            if symbol not in krx_by_symbol
        )
    )
    if missing:
        return _result(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            quality=DataQualityStatus.PARTIAL,
            source_evidence_ids=tuple(sorted(source_evidence_ids)),
            missing_fields=missing,
            reason_codes=("AUTHORITATIVE_OBSERVATIONS_MISSING",),
        )
    members_by_group: dict[str, list[GroupSecurityObservation]] = {}
    for symbol, group_id in groups_by_symbol.items():
        members_by_group.setdefault(group_id, []).append(krx_by_symbol[symbol])
    if not members_by_group:
        return _result(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            quality=DataQualityStatus.PARTIAL,
            source_evidence_ids=tuple(sorted(source_evidence_ids)),
            missing_fields=("taxonomy_assignments",),
            reason_codes=("AUTHORITATIVE_TAXONOMY_EMPTY",),
        )

    aggregates = tuple(
        _GroupAggregate(
            group_id=group_id,
            krx_observations=tuple(sorted(items, key=lambda item: item.provider_symbol)),
            kis_by_symbol=kis_by_symbol,
            benchmark_return_pct=benchmark_return_pct,
        )
        for group_id, items in sorted(members_by_group.items())
    )
    eligible = tuple(
        item for item in aggregates if item.turnover_krw >= min_group_turnover_krw
    )
    excluded = tuple(
        GroupLeadershipExclusion(
            group_id=item.group_id,
            reason_codes=(GroupExclusionReason.LOW_TURNOVER.value,),
            turnover_krw=item.turnover_krw,
            member_count=item.member_count,
        )
        for item in aggregates
        if item.turnover_krw < min_group_turnover_krw
    )
    if not eligible:
        return _result(
            session_date=session_date,
            taxonomy_version=taxonomy_version,
            quality=DataQualityStatus.PARTIAL,
            source_evidence_ids=tuple(sorted(source_evidence_ids)),
            excluded_groups=excluded,
            missing_fields=("eligible_group_turnover",),
            reason_codes=("ALL_GROUPS_BELOW_TURNOVER_FLOOR",),
        )

    breadth_values = tuple(item.breadth_pct for item in eligible)
    momentum_values = tuple(item.momentum_pct for item in eligible)
    strength_values = tuple(item.relative_strength_pct for item in eligible)
    turnover_values = tuple(item.turnover_krw for item in eligible)
    scored: list[tuple[_GroupAggregate, Decimal]] = []
    for item in eligible:
        with localcontext(_DECIMAL_CONTEXT):
            score = sum(
                (
                    _normalized_component(item.breadth_pct, breadth_values),
                    _normalized_component(item.momentum_pct, momentum_values),
                    _normalized_component(item.relative_strength_pct, strength_values),
                    _normalized_component(item.turnover_krw, turnover_values),
                ),
                start=Decimal(0),
            ) / Decimal(4)
        scored.append((item, score))
    scored.sort(key=lambda value: (-value[1], -value[0].turnover_krw, value[0].group_id))
    with localcontext(_DECIMAL_CONTEXT):
        total_turnover = sum(
            (item.turnover_krw for item in aggregates), start=Decimal(0)
        )
        ranked = tuple(
            GroupLeadership(
                group_id=item.group_id,
                rank=rank,
                concentration_pct=_concentration_pct(item.turnover_krw, total_turnover),
                source=item.source,
                evidence_ids=item.evidence_ids,
                session_date=session_date,
                taxonomy_version=taxonomy_version,
                leadership_score=score,
                member_count=item.member_count,
                advance_count=item.advance_count,
                breadth_pct=item.breadth_pct,
                momentum_pct=item.momentum_pct,
                relative_strength_pct=item.relative_strength_pct,
                turnover_krw=item.turnover_krw,
                kis_numeric_member_count=item.kis_numeric_member_count,
                quality=DataQualityStatus.FRESH,
            )
            for rank, (item, score) in enumerate(scored, start=1)
        )
    return _result(
        session_date=session_date,
        taxonomy_version=taxonomy_version,
        quality=DataQualityStatus.FRESH,
        source_evidence_ids=tuple(sorted(source_evidence_ids)),
        ranked_groups=ranked,
        excluded_groups=excluded,
        reason_codes=(
            ("LOW_TURNOVER_GROUPS_EXCLUDED",)
            if excluded
            else ("AUTHORITATIVE_GROUPS_RANKED",)
        ),
    )
