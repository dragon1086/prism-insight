from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.candidates import CandidateChannel, CandidateSnapshot, CandidateStatus
from prism_core.candidates.reconcile import CandidateReconciliation, reconcile_candidates
from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import Market


def make_candidate(
    *,
    channel,
    source_id,
    provider_symbol,
    trigger_id,
    evidence_id,
    security_id=UUID("11111111-1111-4111-8111-111111111111"),
    status=CandidateStatus.ELIGIBLE,
    issues=(),
):
    return CandidateSnapshot(
        market=Market.KR,
        security_id=SecurityId(value=security_id),
        provider="KRX" if channel is CandidateChannel.CORE_PRISM else "LEADERSHIP_EXPORT",
        provider_symbol=provider_symbol,
        display_name="Samsung Electronics",
        channel=channel,
        source_id=source_id,
        source_snapshot_id=f"{source_id}:2026-07-27",
        observed_at=datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc),
        available_at=datetime(2026, 7, 27, 6, 31, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 7, 27, 6, 32, tzinfo=timezone.utc),
        as_of=datetime(2026, 7, 27, 6, 31, tzinfo=timezone.utc),
        trigger_ids=(trigger_id,),
        raw_scores={f"{trigger_id}.raw": Decimal("0.91")},
        evidence_ids=(evidence_id,),
        status=status,
        issues=issues,
    )


def test_overlap_merges_all_channels_sources_triggers_and_evidence():
    core = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    supplemental = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="A005930",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:high:005930",
    )

    result = reconcile_candidates((supplemental, core))

    assert len(result.included) == 1
    assert result.excluded == ()
    merged = result.included[0]
    assert merged.channels == (
        CandidateChannel.CORE_PRISM,
        CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
    )
    assert merged.provider_symbols == ("005930", "A005930")
    assert merged.source_ids == ("legacy-afternoon-rise", "leadership-highs")
    assert merged.trigger_ids == ("afternoon-rise", "leadership-52w-high")
    assert merged.evidence_ids == ("krx:rise:005930", "leadership:high:005930")
    assert merged.snapshots == (core, supplemental)


@pytest.mark.parametrize(
    "channel",
    (CandidateChannel.CORE_PRISM, CandidateChannel.SUPPLEMENTAL_LEADERSHIP),
)
def test_single_channel_candidate_is_preserved(channel):
    candidate = make_candidate(
        channel=channel,
        source_id=f"{channel.value.lower()}-source",
        provider_symbol="005930",
        trigger_id="source-trigger",
        evidence_id="source:evidence:005930",
    )

    result = reconcile_candidates((candidate,))

    assert result.included_source_count == 1
    assert result.included[0].snapshots == (candidate,)
    assert result.excluded == ()


def test_empty_candidate_set_reconciles_to_explicit_zero_counts():
    result = reconcile_candidates(())

    assert result.input_count == 0
    assert result.unique_identity_count == 0
    assert result.included == ()
    assert result.excluded == ()


def test_reconciliation_has_no_candidate_cap_and_orders_without_global_score_comparison():
    candidates = tuple(
        make_candidate(
            channel=(
                CandidateChannel.CORE_PRISM
                if index % 2 == 0
                else CandidateChannel.SUPPLEMENTAL_LEADERSHIP
            ),
            source_id=f"source-{index % 7}",
            provider_symbol=f"{index:06d}",
            trigger_id=f"trigger-{index % 11}",
            evidence_id=f"evidence-{index}",
            security_id=UUID(int=index + 1),
        )
        for index in reversed(range(500))
    )

    result = reconcile_candidates(candidates)

    assert result.input_count == 500
    assert result.unique_identity_count == 500
    assert result.included_source_count == 500
    assert result.truncated_candidate_count == 0
    assert result.score_policy == "TRIGGER_LOCAL_UNRANKED"
    assert result.ordering_policy == "CHANNEL_THEN_STABLE_IDENTITY"
    assert len(result.included) == 500
    assert result.excluded == ()
    expected = sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.channel is CandidateChannel.CORE_PRISM else 1,
            str(candidate.security_id.value),
        ),
    )
    assert [item.security_id for item in result.included] == [
        item.security_id for item in expected
    ]


def test_only_deterministically_ineligible_identity_is_excluded_with_reason():
    unavailable = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:unavailable:005930",
        status=CandidateStatus.DATA_UNAVAILABLE,
        issues=("SOURCE_CLOCK_MISSING",),
    )
    report_only = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="000660",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:partial:000660",
        security_id=UUID("22222222-2222-4222-8222-222222222222"),
        status=CandidateStatus.REPORT_ONLY,
        issues=("SUPPLEMENTAL_CONTEXT_STALE",),
    )

    result = reconcile_candidates((report_only, unavailable))

    assert result.included_identity_count == 1
    assert result.excluded_identity_count == 1
    assert tuple(item.security_id for item in result.included) == (
        report_only.security_id,
    )
    assert result.included[0].status is CandidateStatus.REPORT_ONLY
    assert len(result.excluded) == 1
    assert result.excluded[0].security_id == unavailable.security_id
    assert result.excluded[0].exclusion_reason == (
        "DATA_UNAVAILABLE: SOURCE_CLOCK_MISSING"
    )
    assert result.included_source_count == 1
    assert result.excluded_source_count == 1


def test_exact_duplicate_source_assertion_is_merged_not_excluded():
    core = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    supplemental = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="A005930",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:high:005930",
    )

    result = reconcile_candidates((core, supplemental, core))

    assert result.input_count == 3
    assert result.duplicate_source_count == 1
    assert result.included_source_count == 2
    assert result.excluded == ()
    assert result.included[0].snapshots == (core, supplemental)


def test_distinct_providers_are_distinct_source_assertions():
    krx = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="daily-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    kis = krx.model_copy(
        update={
            "provider": "KIS",
            "evidence_ids": ("kis:rise:005930",),
        }
    )

    result = reconcile_candidates((krx, kis))

    assert result.excluded == ()
    assert result.included_source_count == 2
    assert tuple(item.provider for item in result.included[0].snapshots) == (
        "KIS",
        "KRX",
    )
    assert result.included[0].evidence_ids == (
        "kis:rise:005930",
        "krx:rise:005930",
    )


def test_malformed_candidate_is_excluded_without_aborting_valid_siblings():
    valid = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    malformed = valid.model_dump(mode="python")
    malformed["security_id"] = {"value": "not-a-uuid"}

    result = reconcile_candidates((malformed, valid))

    assert result.input_count == 2
    assert result.invalid_record_count == 1
    assert result.unique_identity_count == 1
    assert result.included[0].security_id == valid.security_id
    assert len(result.excluded) == 1
    assert result.excluded[0].input_index == 0
    assert result.excluded[0].exclusion_code == "INVALID_CANDIDATE"
    assert "security_id" in result.excluded[0].exclusion_reason


def test_conflicting_duplicate_source_identity_fails_closed_without_losing_evidence():
    first = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930:v1",
    )
    conflicting = first.model_copy(
        update={"evidence_ids": ("krx:rise:005930:conflicting",)}
    )

    result = reconcile_candidates((first, conflicting))

    assert result.included == ()
    assert result.duplicate_source_count == 0
    assert result.excluded_source_count == 2
    assert len(result.excluded) == 1
    excluded = result.excluded[0]
    assert excluded.exclusion_code == "SOURCE_CONFLICT"
    assert excluded.candidate is not None
    assert excluded.candidate.snapshots == tuple(
        sorted((first, conflicting), key=lambda item: item.model_dump_json())
    )
    assert "CONFLICTING_SOURCE_ASSERTION" in excluded.candidate.issues


def test_reconciliation_serialization_preserves_auditable_count_invariants():
    candidate = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    result = reconcile_candidates((candidate, candidate))

    assert CandidateReconciliation.model_validate_json(result.model_dump_json()) == result

    inconsistent = result.model_dump(mode="python")
    inconsistent["unique_identity_count"] = 99
    with pytest.raises(ValidationError, match="identity counts"):
        CandidateReconciliation.model_validate(inconsistent)


def test_conflict_retains_each_distinct_source_version_once():
    first = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930:v1",
    )
    second = first.model_copy(update={"evidence_ids": ("krx:rise:005930:v2",)})

    result = reconcile_candidates((first, second, first))

    assert result.duplicate_source_count == 1
    assert result.excluded_source_count == 2
    assert result.excluded[0].candidate is not None
    assert result.excluded[0].candidate.snapshots == (first, second)


def test_conflict_serialization_is_invariant_to_input_order():
    first = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930:v1",
    )
    second = first.model_copy(update={"evidence_ids": ("krx:rise:005930:v2",)})

    forward = reconcile_candidates((first, second))
    reverse = reconcile_candidates((second, first))

    assert forward.model_dump_json() == reverse.model_dump_json()


def test_source_conflict_excludes_identity_even_with_nonconflicting_sibling_source():
    first = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930:v1",
    )
    conflicting = first.model_copy(
        update={"evidence_ids": ("krx:rise:005930:v2",)}
    )
    sibling = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="A005930",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:high:005930",
    )

    result = reconcile_candidates((sibling, conflicting, first))

    assert result.included == ()
    assert result.excluded[0].exclusion_code == "SOURCE_CONFLICT"
    assert result.excluded[0].candidate is not None
    assert len(result.excluded[0].candidate.snapshots) == 3


def test_eligible_source_keeps_identity_included_while_retaining_unavailable_issue():
    eligible = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:rise:005930",
    )
    unavailable = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="A005930",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:missing:005930",
        status=CandidateStatus.DATA_UNAVAILABLE,
        issues=("SUPPLEMENTAL_SOURCE_UNAVAILABLE",),
    )

    result = reconcile_candidates((unavailable, eligible))

    assert result.excluded == ()
    assert result.included[0].status is CandidateStatus.ELIGIBLE
    assert result.included[0].issues == ("SUPPLEMENTAL_SOURCE_UNAVAILABLE",)
    assert result.included[0].snapshots == (eligible, unavailable)


def test_any_eligible_channel_keeps_candidate_selected_for_downstream_quality_gate():
    core_unavailable = make_candidate(
        channel=CandidateChannel.CORE_PRISM,
        source_id="legacy-afternoon-rise",
        provider_symbol="005930",
        trigger_id="afternoon-rise",
        evidence_id="krx:missing:005930",
        status=CandidateStatus.DATA_UNAVAILABLE,
        issues=("CORE_SOURCE_UNAVAILABLE",),
    )
    supplemental_eligible = make_candidate(
        channel=CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
        source_id="leadership-highs",
        provider_symbol="A005930",
        trigger_id="leadership-52w-high",
        evidence_id="leadership:high:005930",
    )

    result = reconcile_candidates((supplemental_eligible, core_unavailable))

    assert result.excluded == ()
    assert result.included[0].status is CandidateStatus.ELIGIBLE
    assert result.included[0].issues == ("CORE_SOURCE_UNAVAILABLE",)
