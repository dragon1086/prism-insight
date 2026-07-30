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
    select_analysis_cohort,
)
from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import Market


AS_OF = datetime(2026, 7, 30, 6, 30, tzinfo=timezone.utc)
SECURITY_ID = SecurityId(value=UUID("11111111-1111-4111-8111-111111111111"))


def _candidate(
    channel: CandidateChannel,
    *,
    security_id: SecurityId = SECURITY_ID,
    status: CandidateStatus = CandidateStatus.ELIGIBLE,
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
        status=status,
        issues=() if status is CandidateStatus.ELIGIBLE else ("SOURCE_INELIGIBLE",),
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


def test_fresh_supplement_analyzes_only_cross_confirmed_core_candidates() -> None:
    cross_confirmed = SECURITY_ID
    core_only = SecurityId(
        value=UUID("22222222-2222-4222-8222-222222222222")
    )
    supplement_only = SecurityId(
        value=UUID("33333333-3333-4333-8333-333333333333")
    )
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates(
            (
                _candidate(CandidateChannel.CORE_PRISM, security_id=cross_confirmed),
                _candidate(
                    CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
                    security_id=cross_confirmed,
                ),
                _candidate(CandidateChannel.CORE_PRISM, security_id=core_only),
                _candidate(
                    CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
                    security_id=supplement_only,
                ),
            )
        )
    )

    cohort = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.FRESH,
        supplement_state=CandidateSourceState.FRESH,
    )

    decisions = {
        member.candidate.security_id: member for member in cohort.members
    }
    assert decisions[cross_confirmed].disposition is CohortDisposition.ANALYZE
    assert decisions[cross_confirmed].selection_reasons == (
        CohortSelectionReason.CROSS_CONFIRMED,
    )
    assert decisions[core_only].disposition is CohortDisposition.OBSERVE_ONLY
    assert decisions[core_only].selection_reasons == (
        CohortSelectionReason.CORE_ONLY_SUPPLEMENT_REQUIRED,
    )
    assert decisions[supplement_only].disposition is CohortDisposition.OBSERVE_ONLY
    assert decisions[supplement_only].selection_reasons == (
        CohortSelectionReason.SUPPLEMENT_ONLY_CORE_REQUIRED,
    )


@pytest.mark.parametrize(
    ("supplement_state", "expected_disposition", "expected_reason"),
    (
        (
            CandidateSourceState.NOT_INGESTED,
            CohortDisposition.ANALYZE,
            CohortSelectionReason.CORE_FALLBACK_SUPPLEMENT_NOT_INGESTED,
        ),
        (
            CandidateSourceState.UNAVAILABLE,
            CohortDisposition.ANALYZE,
            CohortSelectionReason.CORE_FALLBACK_SUPPLEMENT_UNAVAILABLE,
        ),
        (
            CandidateSourceState.FRESH,
            CohortDisposition.OBSERVE_ONLY,
            CohortSelectionReason.CORE_ONLY_SUPPLEMENT_REQUIRED,
        ),
        (
            CandidateSourceState.PARTIAL,
            CohortDisposition.OBSERVE_ONLY,
            CohortSelectionReason.SUPPLEMENT_NOT_FRESH,
        ),
        (
            CandidateSourceState.STALE,
            CohortDisposition.OBSERVE_ONLY,
            CohortSelectionReason.SUPPLEMENT_NOT_FRESH,
        ),
        (
            CandidateSourceState.CONFLICT,
            CohortDisposition.OBSERVE_ONLY,
            CohortSelectionReason.SOURCE_CONFLICT,
        ),
    ),
)
def test_core_candidate_uses_explicit_supplement_state_policy(
    supplement_state: CandidateSourceState,
    expected_disposition: CohortDisposition,
    expected_reason: CohortSelectionReason,
) -> None:
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates((_candidate(CandidateChannel.CORE_PRISM),))
    )

    member = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.FRESH,
        supplement_state=supplement_state,
    ).members[0]

    assert member.disposition is expected_disposition
    assert member.selection_reasons == (expected_reason,)


def test_fresh_supplement_cannot_upgrade_ineligible_core_candidate() -> None:
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates(
            (
                _candidate(
                    CandidateChannel.CORE_PRISM,
                    status=CandidateStatus.REPORT_ONLY,
                ),
                _candidate(CandidateChannel.SUPPLEMENTAL_LEADERSHIP),
            )
        )
    )

    member = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.FRESH,
        supplement_state=CandidateSourceState.FRESH,
    ).members[0]

    assert member.disposition is CohortDisposition.OBSERVE_ONLY
    assert member.selection_reasons == (CohortSelectionReason.CORE_INELIGIBLE,)


def test_stale_core_source_fails_closed_despite_fresh_cross_confirmation() -> None:
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates(
            (
                _candidate(CandidateChannel.CORE_PRISM),
                _candidate(CandidateChannel.SUPPLEMENTAL_LEADERSHIP),
            )
        )
    )

    member = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.STALE,
        supplement_state=CandidateSourceState.FRESH,
    ).members[0]

    assert member.disposition is CohortDisposition.OBSERVE_ONLY
    assert member.selection_reasons == (CohortSelectionReason.CORE_INELIGIBLE,)


@pytest.mark.parametrize(
    ("supplement_state", "expected_reason"),
    (
        (
            CandidateSourceState.STALE,
            CohortSelectionReason.SUPPLEMENT_NOT_FRESH,
        ),
        (
            CandidateSourceState.CONFLICT,
            CohortSelectionReason.SOURCE_CONFLICT,
        ),
    ),
)
def test_nonfresh_supplement_cannot_cross_confirm_core_candidate(
    supplement_state: CandidateSourceState,
    expected_reason: CohortSelectionReason,
) -> None:
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates(
            (
                _candidate(CandidateChannel.CORE_PRISM),
                _candidate(CandidateChannel.SUPPLEMENTAL_LEADERSHIP),
            )
        )
    )

    member = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.FRESH,
        supplement_state=supplement_state,
    ).members[0]

    assert member.disposition is CohortDisposition.OBSERVE_ONLY
    assert member.selection_reasons == (expected_reason,)


def test_reconciled_source_conflict_is_retained_observation_only() -> None:
    supplement = _candidate(CandidateChannel.SUPPLEMENTAL_LEADERSHIP)
    conflicting_supplement = supplement.model_copy(
        update={
            "status": CandidateStatus.REPORT_ONLY,
            "issues": ("CONFLICTING_SUPPLEMENT_ASSERTION",),
        }
    )
    universe = ObservationUniverse.from_reconciliation(
        reconcile_candidates(
            (
                _candidate(CandidateChannel.CORE_PRISM),
                supplement,
                conflicting_supplement,
            )
        )
    )

    member = select_analysis_cohort(
        universe,
        core_state=CandidateSourceState.FRESH,
        supplement_state=CandidateSourceState.FRESH,
    ).members[0]

    assert member.disposition is CohortDisposition.OBSERVE_ONLY
    assert member.selection_reasons == (CohortSelectionReason.SOURCE_CONFLICT,)
