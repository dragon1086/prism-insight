from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.candidates import (
    AnalysisCohort,
    AnalysisCohortMember,
    CandidateChannel,
    CandidateSnapshot,
    CandidateSourceState,
    CandidateStatus,
    CohortDisposition,
    CohortSelectionReason,
    ObservationUniverse,
    reconcile_candidates,
)
from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import Market


AS_OF = datetime(2026, 7, 30, 6, 30, tzinfo=timezone.utc)
SECURITY_ID = SecurityId(value=UUID("11111111-1111-4111-8111-111111111111"))


def _candidate(
    channel: CandidateChannel,
    *,
    security_id: SecurityId = SECURITY_ID,
) -> CandidateSnapshot:
    source = "CORE" if channel is CandidateChannel.CORE_PRISM else "SUPPLEMENT"
    return CandidateSnapshot(
        market=Market.KR,
        security_id=security_id,
        provider=source,
        provider_symbol="005930",
        display_name="Samsung Electronics",
        channel=channel,
        source_id=f"{source}:LEADERSHIP",
        source_snapshot_id=f"{source}:2026-07-30",
        observed_at=AS_OF,
        available_at=AS_OF,
        ingested_at=AS_OF,
        as_of=AS_OF,
        trigger_ids=(f"{source.lower()}_leader",),
        raw_scores={"source_local": Decimal("1")},
        evidence_ids=(f"evidence:{source.lower()}",),
        status=CandidateStatus.ELIGIBLE,
    )


def test_observation_universe_deduplicates_only_stable_identity_and_retains_sources() -> None:
    core = _candidate(CandidateChannel.CORE_PRISM)
    supplement = _candidate(CandidateChannel.SUPPLEMENTAL_LEADERSHIP)

    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates((supplement, core, core))
    )

    assert universe.identity_count == 1
    assert universe.truncated_candidate_count == 0
    assert universe.members[0].snapshots == (core, supplement)
    assert universe.reconciliation.duplicate_source_count == 1


def test_absent_supplement_is_explicit_and_core_candidate_remains_auditable() -> None:
    core = _candidate(CandidateChannel.CORE_PRISM)
    universe = ObservationUniverse.from_reconciliation(reconcile_candidates((core,)))
    member = AnalysisCohortMember(
        candidate=universe.members[0],
        disposition=CohortDisposition.ANALYZE,
        selection_reasons=(
            CohortSelectionReason.CORE_FALLBACK_SUPPLEMENT_NOT_INGESTED,
        ),
        core_state=CandidateSourceState.FRESH,
        supplement_state=CandidateSourceState.NOT_INGESTED,
    )

    cohort = AnalysisCohort(observation_universe=universe, members=(member,))

    assert cohort.analysis_members == (member,)
    assert cohort.observation_only_members == ()
    assert cohort.members[0].supplement_state is CandidateSourceState.NOT_INGESTED
    assert cohort.observation_universe.members == (universe.members[0],)


def test_analysis_cohort_requires_one_explicit_decision_per_observed_identity() -> None:
    first = _candidate(CandidateChannel.CORE_PRISM)
    second = _candidate(
        CandidateChannel.CORE_PRISM,
        security_id=SecurityId(
            value=UUID("22222222-2222-4222-8222-222222222222")
        ),
    )
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates((first, second))
    )
    only_first = AnalysisCohortMember(
        candidate=universe.members[0],
        disposition=CohortDisposition.OBSERVE_ONLY,
        selection_reasons=(CohortSelectionReason.CORE_ONLY_SUPPLEMENT_REQUIRED,),
        core_state=CandidateSourceState.FRESH,
        supplement_state=CandidateSourceState.FRESH,
    )

    with pytest.raises(ValidationError, match="exactly one decision"):
        AnalysisCohort(observation_universe=universe, members=(only_first,))
