from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.candidates import CandidateChannel, CandidateSnapshot, CandidateStatus
from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import Market


def candidate_payload(**updates):
    payload = {
        "market": Market.KR,
        "security_id": SecurityId(value=UUID("11111111-1111-4111-8111-111111111111")),
        "provider": "KRX",
        "provider_symbol": "005930",
        "display_name": "Samsung Electronics",
        "channel": CandidateChannel.CORE_PRISM,
        "source_id": "legacy-afternoon-daily-rise",
        "source_snapshot_id": "krx:2026-07-27:close",
        "observed_at": datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc),
        "available_at": datetime(2026, 7, 27, 6, 31, tzinfo=timezone.utc),
        "ingested_at": datetime(2026, 7, 27, 6, 32, tzinfo=timezone.utc),
        "as_of": datetime(2026, 7, 27, 6, 31, tzinfo=timezone.utc),
        "trigger_ids": ("afternoon-daily-rise",),
        "raw_scores": {"afternoon-daily-rise.composite_score": Decimal("0.91")},
        "evidence_ids": ("krx:ohlcv:005930:2026-07-27",),
        "status": CandidateStatus.ELIGIBLE,
    }
    payload.update(updates)
    return payload


def test_candidate_identity_is_stable_market_and_security_id():
    candidate = CandidateSnapshot(**candidate_payload())

    assert candidate.identity == (Market.KR, candidate.security_id)
    assert candidate.source_identity == (
        Market.KR,
        candidate.security_id,
        CandidateChannel.CORE_PRISM,
        "KRX",
        "legacy-afternoon-daily-rise",
        "krx:2026-07-27:close",
    )


def test_candidate_rejects_naive_or_non_pit_timestamps():
    base = candidate_payload()

    with pytest.raises(ValidationError, match="timezone info"):
        CandidateSnapshot(**{**base, "observed_at": datetime(2026, 7, 27, 6, 30)})

    with pytest.raises(ValidationError, match="observed_at"):
        CandidateSnapshot(
            **{
                **base,
                "observed_at": datetime(2026, 7, 27, 6, 32, tzinfo=timezone.utc),
            }
        )

    with pytest.raises(ValidationError, match="as_of"):
        CandidateSnapshot(
            **{
                **base,
                "as_of": datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc),
            }
        )

    with pytest.raises(ValidationError, match="ingested_at"):
        CandidateSnapshot(
            **{
                **base,
                "ingested_at": datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc),
            }
        )


def test_candidate_is_deeply_immutable_and_serializes_stably():
    first = CandidateSnapshot(
        **candidate_payload(
            raw_scores={
                "z-trigger.raw": Decimal("2.0"),
                "a-trigger.raw": Decimal("1.0"),
            }
        )
    )
    second = CandidateSnapshot(
        **candidate_payload(
            raw_scores={
                "a-trigger.raw": Decimal("1.0"),
                "z-trigger.raw": Decimal("2.0"),
            }
        )
    )

    with pytest.raises(TypeError):
        first.raw_scores["new-score"] = Decimal("3")

    assert first.model_dump_json() == second.model_dump_json()
    assert CandidateSnapshot.model_validate_json(first.model_dump_json()) == first


@pytest.mark.parametrize("field", ["trigger_ids", "evidence_ids"])
def test_candidate_rejects_duplicate_provenance_ids(field):
    with pytest.raises(ValidationError, match="unique"):
        CandidateSnapshot(**candidate_payload(**{field: ("duplicate", "duplicate")}))


def test_ineligible_candidate_requires_an_explicit_issue_and_forbids_execution_fields():
    with pytest.raises(ValidationError, match="issues"):
        CandidateSnapshot(
            **candidate_payload(status=CandidateStatus.DATA_UNAVAILABLE, issues=())
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CandidateSnapshot(**candidate_payload(quantity=10))
