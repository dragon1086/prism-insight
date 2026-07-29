"""Compose optional leadership evidence without changing KIS/KRX authority."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from prism_core.candidates import (
    CandidateChannel,
    CandidateReconciliation,
    CandidateSnapshot,
    CandidateStatus,
    reconcile_candidates,
)
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.contracts.leadership_supplement import (
    LeadershipCandidateNomination,
    LeadershipSupplementSnapshot,
)
from prism_core.strategies.contracts import Market


@dataclass(frozen=True)
class LeadershipAuthorityConflict:
    comparison_key: str
    authoritative_provider: str
    authoritative_value: Decimal | str
    supplemental_value: Decimal | str
    issue: str = "SUPPLEMENTAL_AUTHORITY_CONFLICT"


@dataclass(frozen=True)
class LeadershipSupplementComposition:
    supplement: LeadershipSupplementSnapshot
    supplemental_candidates: tuple[CandidateSnapshot, ...]
    reconciliation: CandidateReconciliation
    authoritative_values: Mapping[str, Decimal | str]
    authority_conflicts: tuple[LeadershipAuthorityConflict, ...]


class LeadershipSupplementComposer:
    """Union CORE and optional nominations without ranking or truncation."""

    def compose(
        self,
        *,
        core_candidates: Iterable[CandidateSnapshot],
        supplement: LeadershipSupplementSnapshot,
        authoritative_values: Mapping[str, Decimal | str],
    ) -> LeadershipSupplementComposition:
        authoritative = MappingProxyType(dict(authoritative_values))
        conflicts = tuple(
            LeadershipAuthorityConflict(
                comparison_key=item.comparison_key,
                authoritative_provider="KIS_KRX",
                authoritative_value=authoritative[item.comparison_key],
                supplemental_value=item.value,
            )
            for item in supplement.observations
            if item.comparison_key in authoritative
            and _normalized(authoritative[item.comparison_key]) != _normalized(item.value)
        )
        effective_supplement = supplement
        if conflicts:
            effective_supplement = LeadershipSupplementSnapshot.model_validate(
                {
                    **supplement.model_dump(),
                    "quality": DataQualityStatus.CONFLICT,
                    "issues": tuple(
                        dict.fromkeys(
                            (*supplement.issues, "SUPPLEMENTAL_AUTHORITY_CONFLICT")
                        )
                    ),
                }
            )
        conflict_keys = {item.comparison_key for item in conflicts}
        observations_by_evidence = {
            item.evidence_id: item for item in supplement.observations
        }
        candidates = tuple(
            _candidate_snapshot(
                effective_supplement,
                nomination,
                conflict=any(
                    observations_by_evidence[evidence_id].comparison_key in conflict_keys
                    for evidence_id in nomination.evidence_ids
                ),
            )
            for nomination in supplement.candidate_nominations
        )
        reconciliation = reconcile_candidates((*tuple(core_candidates), *candidates))
        return LeadershipSupplementComposition(
            supplement=effective_supplement,
            supplemental_candidates=candidates,
            reconciliation=reconciliation,
            authoritative_values=authoritative,
            authority_conflicts=conflicts,
        )


def _candidate_snapshot(
    supplement: LeadershipSupplementSnapshot,
    nomination: LeadershipCandidateNomination,
    *,
    conflict: bool,
) -> CandidateSnapshot:
    report_only = supplement.quality is not DataQualityStatus.FRESH or conflict
    issues = tuple(
        dict.fromkeys(
            (
                *supplement.issues,
                *(("SUPPLEMENTAL_AUTHORITY_CONFLICT",) if conflict else ()),
            )
        )
    )
    return CandidateSnapshot(
        market=Market.KR,
        security_id=nomination.security_id,
        provider=supplement.provider,
        provider_symbol=nomination.provider_symbol,
        display_name=nomination.display_name,
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id=(
            f"SUPPLEMENTAL_LEADERSHIP:{nomination.trigger_id}:"
            f"{_evidence_set_hash(nomination.evidence_ids)}"
        ),
        source_snapshot_id=supplement.snapshot_id,
        observed_at=supplement.timing.observed_at,
        available_at=supplement.timing.available_at,
        ingested_at=supplement.timing.ingested_at,
        as_of=supplement.timing.as_of_date,
        trigger_ids=(nomination.trigger_id,),
        raw_scores={},
        evidence_ids=nomination.evidence_ids,
        status=(CandidateStatus.REPORT_ONLY if report_only else CandidateStatus.ELIGIBLE),
        issues=issues,
    )


def _normalized(value: Decimal | str) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value.strip().casefold()


def _evidence_set_hash(evidence_ids: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(evidence_ids).encode("utf-8")).hexdigest()
