from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_app.leadership_supplement import LeadershipSupplementComposer
from prism_core.candidates import CandidateChannel, CandidateSnapshot, CandidateStatus
from prism_core.data.contracts import DataQualityStatus, ObservationTime, SecurityId
from prism_core.data.contracts.leadership_supplement import (
    LeadershipCandidateNomination,
    LeadershipCaptureMethod,
    LeadershipObservation,
    LeadershipObservationKind,
    LeadershipScope,
    LeadershipSection,
    LeadershipSupplementSnapshot,
)
from prism_core.strategies.contracts import Market


UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000777"))


def _timing() -> ObservationTime:
    return ObservationTime(
        observed_at=AS_OF - timedelta(minutes=5),
        available_at=AS_OF - timedelta(minutes=4),
        ingested_at=AS_OF,
        as_of_date=AS_OF,
    )


def _snapshot(**overrides: object) -> LeadershipSupplementSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "leadership:" + "a" * 64,
        "provider": "STOCKEASY_SANITIZED_UI_EXPORT",
        "section": LeadershipSection.SECURITY_LEADERSHIP,
        "source_scope_id": "generic-security-leadership",
        "source_snapshot_id": "approved-export:2026-07-29",
        "capture_method": LeadershipCaptureMethod.APPROVED_EXPORT,
        "permission_record_id": "permission:2026-07-29",
        "timing": _timing(),
        "content_hash": "a" * 64,
        "image_hash": None,
        "quality": DataQualityStatus.FRESH,
        "observations": (
            LeadershipObservation(
                observation_id="obs:rs:005930",
                kind=LeadershipObservationKind.RELATIVE_STRENGTH_RANK,
                scope=LeadershipScope.SECURITY,
                security_id=SECURITY_ID,
                provider_symbol="005930",
                group_id=None,
                value=Decimal("7"),
                unit="rank",
                evidence_id="evidence:rs:005930",
            ),
        ),
        "candidate_nominations": (
            LeadershipCandidateNomination(
                security_id=SECURITY_ID,
                provider_symbol="005930",
                display_name="삼성전자",
                trigger_id="supplemental_relative_strength",
                evidence_ids=("evidence:rs:005930",),
            ),
        ),
        "issues": (),
    }
    values.update(overrides)
    return LeadershipSupplementSnapshot.model_validate(values)


def test_contract_preserves_sanitized_pit_provenance_and_stable_hashes() -> None:
    snapshot = _snapshot()

    assert snapshot.timing.available_at <= snapshot.timing.as_of_date
    assert snapshot.content_hash == "a" * 64
    assert snapshot.observations[0].security_id == SECURITY_ID
    assert snapshot.candidate_nominations[0].provider_symbol == "005930"


def test_contract_rejects_unexpected_keys_and_invalid_scope_identity() -> None:
    with pytest.raises(ValidationError):
        _snapshot(cookie="forbidden")

    with pytest.raises(ValidationError):
        LeadershipObservation(
            observation_id="obs:market",
            kind=LeadershipObservationKind.MARKET_BREADTH,
            scope=LeadershipScope.MARKET,
            security_id=SECURITY_ID,
            provider_symbol="005930",
            group_id=None,
            value=Decimal("1"),
            unit="count",
            evidence_id="evidence:market",
        )


def test_composition_preserves_authoritative_values_conflicts_channels_and_all_unique_candidates() -> None:
    observations = tuple(
        LeadershipObservation(
            observation_id=f"obs:rs:{index:06d}",
            kind=LeadershipObservationKind.RELATIVE_STRENGTH_RANK,
            scope=LeadershipScope.SECURITY,
            security_id=SecurityId(
                value=UUID(f"00000000-0000-0000-0000-{index + 1:012d}")
            ),
            provider_symbol=f"{index + 1:06d}",
            group_id=None,
            value=Decimal(index + 1),
            unit="rank",
            evidence_id=f"evidence:rs:{index:06d}",
        )
        for index in range(75)
    )
    nominations = tuple(
        LeadershipCandidateNomination(
            security_id=SecurityId(
                value=UUID(f"00000000-0000-0000-0000-{index + 1:012d}")
            ),
            provider_symbol=f"{index + 1:06d}",
            display_name=f"candidate-{index}",
            trigger_id="supplemental_relative_strength",
            evidence_ids=(item.evidence_id,),
        )
        for index, item in enumerate(observations)
    )
    supplement = _snapshot(
        observations=observations, candidate_nominations=nominations
    )
    first = nominations[0]
    core = CandidateSnapshot(
        market=Market.KR,
        security_id=first.security_id,
        provider="KRX",
        provider_symbol=first.provider_symbol,
        display_name=first.display_name,
        channel=CandidateChannel.CORE_PRISM,
        source_id="CORE_TRIGGER",
        source_snapshot_id="krx:snapshot",
        observed_at=_timing().observed_at,
        available_at=_timing().available_at,
        ingested_at=_timing().ingested_at,
        as_of=AS_OF,
        trigger_ids=("core_trigger",),
        raw_scores={},
        evidence_ids=("krx:evidence",),
        status=CandidateStatus.ELIGIBLE,
    )
    comparison_key = observations[0].comparison_key

    result = LeadershipSupplementComposer().compose(
        core_candidates=(core,),
        supplement=supplement,
        authoritative_values={comparison_key: Decimal("8")},
    )

    assert result.reconciliation.included_identity_count == 75
    assert result.reconciliation.input_count == 76
    assert result.reconciliation.truncated_candidate_count == 0
    merged = result.reconciliation.included[0]
    assert merged.channels == (
        CandidateChannel.CORE_PRISM,
        CandidateChannel.SUPPLEMENTAL_LEADERSHIP,
    )
    assert result.authoritative_values[comparison_key] == Decimal("8")
    assert result.authority_conflicts[0].comparison_key == comparison_key
    assert result.authority_conflicts[0].authoritative_provider == "KIS_KRX"
    assert result.supplement.quality is DataQualityStatus.CONFLICT
    assert "SUPPLEMENTAL_AUTHORITY_CONFLICT" in result.supplement.issues


def test_same_trigger_nominations_for_one_identity_are_preserved_without_false_source_conflict() -> None:
    observations = (
        LeadershipObservation(
            observation_id="obs:rs:005930",
            kind=LeadershipObservationKind.RELATIVE_STRENGTH_RANK,
            scope=LeadershipScope.SECURITY,
            security_id=SECURITY_ID,
            provider_symbol="005930",
            value=Decimal("7"),
            unit="rank",
            evidence_id="evidence:rs:005930",
        ),
        LeadershipObservation(
            observation_id="obs:momentum:005930",
            kind=LeadershipObservationKind.MOMENTUM_RANK,
            scope=LeadershipScope.SECURITY,
            security_id=SECURITY_ID,
            provider_symbol="005930",
            value=Decimal("4"),
            unit="rank",
            evidence_id="evidence:momentum:005930",
        ),
    )
    nominations = tuple(
        LeadershipCandidateNomination(
            security_id=SECURITY_ID,
            provider_symbol="005930",
            display_name="삼성전자",
            trigger_id="supplemental_leader",
            evidence_ids=(item.evidence_id,),
        )
        for item in observations
    )

    result = LeadershipSupplementComposer().compose(
        core_candidates=(),
        supplement=_snapshot(
            observations=observations, candidate_nominations=nominations
        ),
        authoritative_values={},
    )

    assert result.reconciliation.input_count == 2
    assert result.reconciliation.included_identity_count == 1
    assert result.reconciliation.excluded_identity_count == 0
    assert len(result.reconciliation.included[0].snapshots) == 2
